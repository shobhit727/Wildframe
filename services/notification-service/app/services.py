"""Notification service business logic."""

import json
import logging
from uuid import UUID

from app.channels import CHANNELS, ChannelUnavailable, DeliveryError, deliver_with_retry
from app.models import NotificationPreference, utcnow_naive
from app.repositories import NotificationRepository
from app.templates import sanitize_text

logger = logging.getLogger(__name__)

TITLE_MAX_LENGTH = 255
MESSAGE_MAX_LENGTH = 1000

_CHANNEL_PREF_ATTR = {
    "in-app": "in_app_enabled",
    "email": "email_enabled",
    "push": "push_enabled",
    "sms": "sms_enabled",
}


class NotificationService:
    def __init__(self, notif_repo: NotificationRepository):
        self.notif_repo = notif_repo

    async def send_notification(
        self,
        user_id: UUID,
        title: str,
        message: str,
        channel: str = "in-app",
        event_id: UUID | None = None,
        channels: list[str] | None = None,
        email_address: str | None = None,
        template: str = "generic",
    ) -> dict:
        """Create and deliver a notification.

        Idempotent under duplicate domain events: re-sending with the same
        event_id returns the existing row without re-delivering. Preferences
        are enforced server-side: disabled channels are skipped. Channels are
        dispatched independently — one failing channel never drops the rest.
        """
        title = sanitize_text(title, max_length=TITLE_MAX_LENGTH)
        message = sanitize_text(message, max_length=MESSAGE_MAX_LENGTH)
        requested = channels if channels else [channel]
        pref = await self.notif_repo.get_preference(user_id)
        enabled = [name for name in requested if self._preference_allows(pref, name)]
        skipped = [name for name in requested if name not in enabled]
        primary_channel = enabled[0] if enabled else channel

        if event_id is not None:
            existing = await self.notif_repo.get_by_event_id(event_id)
            if existing is not None:
                return {"status": existing.delivery_status}

        if not enabled:
            return {"status": "skipped"}

        notif = await self.notif_repo.create(user_id, title, message, primary_channel, event_id)
        outcomes = await self._dispatch(
            notif, enabled, email_address=email_address, template=template
        )
        self._record_outcomes(notif, outcomes)
        await self.notif_repo.session.commit()

        # "partial" if some requested channels were skipped/disabled
        if skipped and notif.delivery_status == "sent":
            return {"status": "partial"}
        return {"status": notif.delivery_status}

    async def retry_delivery(self, notification_id: UUID, user_id: UUID) -> dict | None:
        """Re-dispatch only the channels that previously failed.

        Returns None when the notification does not exist or belongs to
        another user. Never creates a duplicate row (idempotency key).
        """
        notif = await self.notif_repo.get_by_id(notification_id, user_id=user_id)
        if notif is None:
            return None
        if notif.delivery_status not in ("failed", "partial"):
            return {"status": notif.delivery_status}

        outcomes = self.notif_repo.parse_delivery_errors(notif)
        failed = [name for name, outcome in outcomes.items() if outcome.startswith("failed")]
        if not failed:
            return {"status": notif.delivery_status}

        pref = await self.notif_repo.get_preference(user_id)
        retryable = []
        for name in failed:
            if self._preference_allows(pref, name):
                retryable.append(name)
            else:
                outcomes[name] = "skipped: preference disabled"

        if retryable:
            new_outcomes = await self._dispatch(
                notif, retryable, email_address=None, template="generic"
            )
            outcomes.update(new_outcomes)

        notif.delivery_errors = json.dumps(outcomes)  # type: ignore[assignment]
        failed_after = [o for o in outcomes.values() if o.startswith("failed")]
        if not failed_after:
            notif.delivery_status = "sent"  # type: ignore[assignment]
            notif.delivered_at = utcnow_naive()  # type: ignore[assignment]
        elif len(failed_after) == len(outcomes):
            notif.delivery_status = "failed"  # type: ignore[assignment]
        else:
            notif.delivery_status = "partial"  # type: ignore[assignment]
        await self.notif_repo.session.commit()
        return {"status": notif.delivery_status}

    async def _dispatch(
        self,
        notif,
        channels: list[str],
        email_address: str | None,
        template: str,
    ) -> dict[str, str]:
        """Deliver to each channel, isolating failures per channel."""
        outcomes: dict[str, str] = {}
        for name in channels:
            chan = CHANNELS.get(name)
            if chan is None:
                outcomes[name] = "failed: unknown channel"
                continue
            recipient = email_address if name == "email" else None
            try:
                await deliver_with_retry(chan, notif, recipient=recipient, template=template)
                outcomes[name] = "sent"
            except ChannelUnavailable as exc:
                outcomes[name] = f"skipped: {exc}"
            except DeliveryError as exc:
                outcomes[name] = f"failed: {exc}"
                logger.warning(
                    "delivery to channel %s failed for notification %s: %s",
                    name,
                    notif.id,
                    exc,
                )
        return outcomes

    @staticmethod
    def _record_outcomes(notif, outcomes: dict[str, str]) -> None:
        notif.delivery_errors = json.dumps(outcomes)
        failed = [o for o in outcomes.values() if o.startswith("failed")]
        if not failed:
            notif.delivery_status = "sent"
            notif.delivered_at = utcnow_naive()
        elif len(failed) == len(outcomes):
            notif.delivery_status = "failed"
        else:
            notif.delivery_status = "partial"

    async def get_unread(self, user_id: UUID, limit: int | None = None, offset: int = 0):
        """Return unread notifications belonging to the user."""
        kwargs: dict = {}
        if limit is not None:
            kwargs["limit"] = limit
        if offset:
            kwargs["offset"] = offset
        return await self.notif_repo.get_unread(user_id, **kwargs)

    async def get_unread_count(self, user_id: UUID) -> int:
        """Return the number of unread notifications for the user."""
        return await self.notif_repo.count_unread(user_id)

    async def mark_as_read(self, notification_id: UUID, user_id: UUID) -> bool:
        """Mark one of the user's notifications as read."""
        marked = await self.notif_repo.mark_as_read(notification_id, user_id)
        if marked:
            await self.notif_repo.session.commit()
        return marked

    async def delete_notification(self, notification_id: UUID, user_id: UUID) -> bool:
        """Soft-delete one of the user's notifications."""
        deleted = await self.notif_repo.soft_delete(notification_id, user_id)
        if deleted:
            await self.notif_repo.session.commit()
        return deleted

    async def get_preferences(self, user_id: UUID) -> dict:
        pref = await self.notif_repo.get_preference(user_id)
        return self._pref_dict(pref)

    async def update_preferences(self, user_id: UUID, flags: dict) -> dict:
        pref = await self.notif_repo.update_preference(user_id, **flags)
        await self.notif_repo.session.commit()
        return self._pref_dict(pref)

    @staticmethod
    def _preference_allows(pref: NotificationPreference, channel: str) -> bool:
        attr = _CHANNEL_PREF_ATTR.get(channel)
        if attr is None:
            return True  # unknown channels fall through to dispatch validation
        return bool(getattr(pref, attr))

    @staticmethod
    def _pref_dict(pref: NotificationPreference) -> dict:
        return {
            "in_app_enabled": pref.in_app_enabled,
            "email_enabled": pref.email_enabled,
            "push_enabled": pref.push_enabled,
            "sms_enabled": pref.sms_enabled,
        }
