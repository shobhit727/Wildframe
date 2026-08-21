"""Notification service repositories."""

import json
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, NotificationPreference, utcnow_naive

_PREFERENCE_FIELDS = {"in_app_enabled", "email_enabled", "push_enabled", "sms_enabled"}


class NotificationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: UUID,
        title: str,
        message: str,
        channel: str = "in-app",
        event_id: UUID | None = None,
    ) -> Notification:
        """Create a notification, deduplicated on event_id.

        Re-sending the same domain event id returns the existing row instead of
        creating a duplicate notification (idempotency under retries).
        """
        if event_id is not None:
            existing = await self.get_by_event_id(event_id)
            if existing is not None:
                return existing
        notif = Notification(
            user_id=user_id, title=title, message=message, channel=channel, event_id=event_id
        )
        self.session.add(notif)
        try:
            await self.session.flush()
        except IntegrityError:
            # Concurrent duplicate: another request created the row first.
            await self.session.rollback()
            existing = await self.get_by_event_id(event_id)  # type: ignore[arg-type]
            if existing is not None:
                return existing
            raise
        return notif

    async def get_by_event_id(self, event_id: UUID) -> Notification | None:
        stmt = select(Notification).where(Notification.event_id == event_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(
        self, notification_id: UUID, user_id: UUID | None = None
    ) -> Notification | None:
        stmt = select(Notification).where(
            Notification.id == notification_id,
            Notification.deleted_at.is_(None),
        )
        if user_id is not None:
            stmt = stmt.where(Notification.user_id == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_unread(
        self, user_id: UUID, limit: int | None = None, offset: int = 0
    ) -> list[Notification]:
        stmt = (
            select(Notification)
            .where(
                (Notification.user_id == user_id)
                & (Notification.is_read == False)  # noqa: E712
                & (Notification.deleted_at.is_(None))
            )
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .offset(offset)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_unread(self, user_id: UUID) -> int:
        """Unread count via SQL COUNT — cannot drift negative by construction."""
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                (Notification.user_id == user_id)
                & (Notification.is_read == False)  # noqa: E712
                & (Notification.deleted_at.is_(None))
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def mark_as_read(self, notification_id: UUID, user_id: UUID) -> bool:
        """Mark a notification read only when it belongs to the caller.

        Idempotent: an already-read owned notification still counts as found.
        """
        stmt = (
            update(Notification)
            .where(
                (Notification.id == notification_id)
                & (Notification.user_id == user_id)
                & (Notification.deleted_at.is_(None))
            )
            .values(is_read=True, read_at=datetime.now(UTC).replace(tzinfo=None))
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return cast(CursorResult, result).rowcount == 1

    async def soft_delete(self, notification_id: UUID, user_id: UUID) -> bool:
        """Soft-delete a notification owned by the caller (retention semantics).

        Returns True only when this call performed the delete; a row that is
        already deleted (or belongs to another user) returns False so the API
        answers 404 on repeat DELETE — the same result every time, which keeps
        clients idempotent-safe (#210).
        """
        # First check if notification exists and is owned by user
        stmt = select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing is None or existing.deleted_at is not None:
            return False

        # Not deleted yet — perform the soft delete
        stmt = (  # type: ignore[unreachable]
            update(Notification)
            .where(Notification.id == notification_id)
            .values(deleted_at=utcnow_naive())
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return cast(CursorResult, result).rowcount == 1

    async def get_preference(self, user_id: UUID) -> NotificationPreference:
        stmt = select(NotificationPreference).where(NotificationPreference.user_id == user_id)
        pref = (await self.session.execute(stmt)).scalar_one_or_none()
        if pref is None:
            pref = NotificationPreference(user_id=user_id)
            self.session.add(pref)
            try:
                await self.session.flush()
            except IntegrityError:
                await self.session.rollback()
                pref = (await self.session.execute(stmt)).scalar_one_or_none()
                assert pref is not None
        return pref

    async def update_preference(self, user_id: UUID, **flags: bool) -> NotificationPreference:
        unknown = set(flags) - _PREFERENCE_FIELDS
        if unknown:
            raise ValueError(f"unknown preference fields: {sorted(unknown)}")
        pref = await self.get_preference(user_id)
        for key, value in flags.items():
            setattr(pref, key, bool(value))
        pref.updated_at = utcnow_naive()  # type: ignore[assignment]
        await self.session.flush()
        return pref

    @staticmethod
    def parse_delivery_errors(notification: Notification) -> dict[str, str]:
        """Per-channel outcome map stored on the notification row."""
        if not notification.delivery_errors:
            return {}
        try:
            parsed = json.loads(notification.delivery_errors)  # type: ignore[arg-type]
        except ValueError:
            return {}
        return {str(key): str(value) for key, value in parsed.items()}
