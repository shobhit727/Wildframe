"""Moderation service repositories.

Thin persistence layer: the service owns all business rules (strike
thresholds, status transitions, event emission). The repository just loads
and persists rows.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ContentFlag,
    CreatorStrike,
    ModerationDecision,
    OutboxEvent,
    OutboxEventStatus,
)


class ContentFlagRepository:
    """Persistence for content flags."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, flag: ContentFlag) -> ContentFlag:
        self.session.add(flag)
        await self.session.flush()
        return flag

    async def get(self, flag_id: UUID) -> ContentFlag | None:
        result = await self.session.execute(select(ContentFlag).where(ContentFlag.id == flag_id))
        return result.scalar_one_or_none()

    async def get_for_update(self, flag_id: UUID) -> ContentFlag | None:
        """Get a flag with row-level lock (SELECT ... FOR UPDATE)."""
        result = await self.session.execute(
            select(ContentFlag).where(ContentFlag.id == flag_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_pending(self, limit: int = 50) -> list[ContentFlag]:
        """List pending flags ordered by creation time (oldest first).

        Oldest-first gives moderators a fair FIFO queue so no flag sits
        forever while newer ones jump ahead.
        """
        result = await self.session.execute(
            select(ContentFlag)
            .where(ContentFlag.status == "pending")
            .order_by(ContentFlag.created_at.asc(), ContentFlag.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def save(self, flag: ContentFlag) -> ContentFlag:
        flag.updated_at = datetime.now(UTC)
        await self.session.flush()
        return flag

    # -- Transactional outbox -------------------------------------------------

    async def enqueue_event(self, topic: str, event_key: str, payload: dict) -> OutboxEvent:
        """Persist an event row in the current transaction (transactional outbox)."""
        row = OutboxEvent(topic=topic, event_key=event_key, payload=payload)
        self.session.add(row)
        await self.session.flush()
        return row

    async def pending_events(self, limit: int = 100) -> list[OutboxEvent]:
        result = await self.session.execute(
            select(OutboxEvent)
            .where(OutboxEvent.status == OutboxEventStatus.PENDING)
            .order_by(OutboxEvent.created_at.asc(), OutboxEvent.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_dispatched(self, event_id: UUID) -> None:
        row = await self.session.get(OutboxEvent, event_id)
        if row is not None:
            row.status = OutboxEventStatus.DISPATCHED
            row.dispatched_at = datetime.now(UTC)
            await self.session.flush()


class ModerationDecisionRepository:
    """Persistence for moderation decisions (audit trail)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, decision: ModerationDecision) -> ModerationDecision:
        self.session.add(decision)
        await self.session.flush()
        return decision

    async def list_by_flag(self, flag_id: UUID) -> list[ModerationDecision]:
        result = await self.session.execute(
            select(ModerationDecision)
            .where(ModerationDecision.flag_id == flag_id)
            .order_by(ModerationDecision.created_at.asc(), ModerationDecision.id.asc())
        )
        return list(result.scalars().all())


class CreatorStrikeRepository:
    """Persistence for creator strikes."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, strike: CreatorStrike) -> CreatorStrike:
        self.session.add(strike)
        await self.session.flush()
        return strike

    async def list_active(
        self, creator_id: UUID, now: datetime | None = None
    ) -> list[CreatorStrike]:
        """List active (non-expired) strikes for a creator."""
        now = now or datetime.now(UTC)
        result = await self.session.execute(
            select(CreatorStrike)
            .where(
                CreatorStrike.creator_id == creator_id,
                CreatorStrike.is_active.is_(True),
                or_(
                    CreatorStrike.expires_at.is_(None),
                    CreatorStrike.expires_at > now,
                ),
            )
            .order_by(CreatorStrike.created_at.desc(), CreatorStrike.id.desc())
        )
        return list(result.scalars().all())

    async def count_active(self, creator_id: UUID) -> int:
        """Count active strikes for a creator (used for suspension check)."""
        strikes = await self.list_active(creator_id)
        return len(strikes)

    async def list_active_for_update(
        self, creator_id: UUID, now: datetime | None = None
    ) -> list[CreatorStrike]:
        """List active (non-expired) strikes with row-level lock (SELECT ... FOR UPDATE)."""
        now = now or datetime.now(UTC)
        result = await self.session.execute(
            select(CreatorStrike)
            .where(
                CreatorStrike.creator_id == creator_id,
                CreatorStrike.is_active.is_(True),
                or_(
                    CreatorStrike.expires_at.is_(None),
                    CreatorStrike.expires_at > now,
                ),
            )
            .order_by(CreatorStrike.created_at.desc(), CreatorStrike.id.desc())
            .with_for_update()
        )
        return list(result.scalars().all())

    async def count_active_for_update(self, creator_id: UUID) -> int:
        """Count active strikes with row-level lock (SELECT ... FOR UPDATE)."""
        strikes = await self.list_active_for_update(creator_id)
        return len(strikes)

    async def list_all(self, creator_id: UUID) -> list[CreatorStrike]:
        """List all strikes (active + expired) for a creator."""
        result = await self.session.execute(
            select(CreatorStrike)
            .where(CreatorStrike.creator_id == creator_id)
            .order_by(CreatorStrike.created_at.desc(), CreatorStrike.id.desc())
        )
        return list(result.scalars().all())
