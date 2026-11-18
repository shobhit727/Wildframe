"""Moderation service repositories.

Thin persistence layer: the service owns all business rules (strike
thresholds, status transitions, event emission). The repository just loads
and persists rows.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ContentFlag,
    CreatorStrike,
    ModerationDecision,
    OutboxEvent,
    OutboxEventStatus,
)


class DuplicateContentFlag(Exception):
    """The reporter has already flagged this content."""


class ContentFlagRepository:
    """Persistence for content flags."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, flag: ContentFlag) -> ContentFlag:
        result = await self.session.execute(
            insert(ContentFlag)
            .values(
                content_id=flag.content_id,
                content_creator_id=flag.content_creator_id,
                flag_reason=flag.flag_reason,
                reported_by=flag.reported_by,
                status=flag.status,
            )
            .on_conflict_do_nothing(constraint="uq_content_flag_content_reporter")
            .returning(ContentFlag)
        )
        created = result.scalar_one_or_none()
        if created is None:
            raise DuplicateContentFlag("content already reported by this user")
        return created

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

    async def claim_pending_events(
        self, claimed_by: str, limit: int, claim_ttl: timedelta
    ) -> list[OutboxEvent]:
        """Claim up to ``limit`` PENDING rows for ``claimed_by``, oldest first.

        Every gunicorn worker of every replica runs a drain loop, so the claim
        must be exclusive (#1145). One ``UPDATE ... WHERE id IN (SELECT ...
        FOR UPDATE SKIP LOCKED) RETURNING`` stamps ``claimed_by``/``claimed_at``
        on rows that are unclaimed or whose claim is older than ``claim_ttl``
        (a crashed drain); rows a concurrent claim has locked are skipped.

        The caller must commit right after this call: the claim, not a held
        row lock, is what keeps other drains off the rows while they publish.
        A lock held across a publish dies with the backend when
        ``idle_in_transaction_session_timeout`` fires.
        """
        now = datetime.now(UTC)
        claimable = (
            select(OutboxEvent.id)
            .where(
                OutboxEvent.status == OutboxEventStatus.PENDING,
                or_(
                    OutboxEvent.claimed_at.is_(None),
                    OutboxEvent.claimed_at < now - claim_ttl,
                ),
            )
            .order_by(OutboxEvent.created_at.asc(), OutboxEvent.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id.in_(claimable))
            .values(claimed_by=claimed_by, claimed_at=now)
            .returning(OutboxEvent),
            execution_options={"synchronize_session": False, "populate_existing": True},
        )
        # RETURNING carries no order; restore the FIFO the subquery picked by.
        return sorted(result.scalars().all(), key=lambda row: (row.created_at, row.id))

    async def mark_dispatched(self, event_id: UUID, claimed_by: str) -> bool:
        """Retire a row this drain still holds the claim on.

        Returns False (and changes nothing) when the row is no longer PENDING
        or another drain has re-claimed it after the claim expired.
        """
        result = await self.session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.id == event_id,
                OutboxEvent.claimed_by == claimed_by,
                OutboxEvent.status == OutboxEventStatus.PENDING,
            )
            .values(status=OutboxEventStatus.DISPATCHED, dispatched_at=datetime.now(UTC))
            .returning(OutboxEvent.id)
        )
        return result.scalar_one_or_none() is not None

    async def release_claim(self, event_id: UUID, claimed_by: str) -> None:
        """Drop this drain's claim on a row it failed to publish.

        The row becomes claimable by the next drain at once instead of after
        the claim TTL. A claim another drain has taken over is left alone.
        """
        await self.session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.id == event_id,
                OutboxEvent.claimed_by == claimed_by,
                OutboxEvent.status == OutboxEventStatus.PENDING,
            )
            .values(claimed_by=None, claimed_at=None)
        )


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

    async def lock_creator(self, creator_id: UUID) -> None:
        """Serialize first and later strikes until transaction commit/rollback."""
        # A stable signed 64-bit key; collisions only serialize unrelated creators.
        key = int.from_bytes(creator_id.bytes[:8], byteorder="big", signed=True)
        await self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

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
