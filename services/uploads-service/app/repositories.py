"""Uploads service repositories."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    OutboxEvent,
    OutboxEventStatus,
    UploadChunk,
    UploadSession,
    UploadSessionStatus,
)


class UploadChunkRepository:
    """Persistence for upload chunks and sessions.

    Kept deliberately thin: the service owns all business rules (status
    transitions, chunk-plan math, checksum verification). The repository just
    loads and persists rows.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # -- UploadSession -------------------------------------------------------

    async def create(self, session: UploadSession) -> UploadSession:
        self.session.add(session)
        await self.session.flush()
        return session

    async def get(self, session_id: UUID) -> UploadSession | None:
        result = await self.session.execute(
            select(UploadSession).where(UploadSession.id == session_id)
        )
        return result.scalar_one_or_none()

    async def get_for_update(self, session_id: UUID) -> UploadSession | None:
        result = await self.session.execute(
            select(UploadSession)
            .where(UploadSession.id == session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_by_creator(self, creator_id: UUID, limit: int = 50) -> list[UploadSession]:
        result = await self.session.execute(
            select(UploadSession)
            .where(UploadSession.creator_id == creator_id)
            .order_by(UploadSession.created_at.desc(), UploadSession.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def save(self, session: UploadSession) -> UploadSession:
        session.updated_at = datetime.now(UTC)
        await self.session.flush()
        return session

    # -- UploadChunk ---------------------------------------------------------

    async def add_chunk(self, chunk: UploadChunk) -> UploadChunk:
        self.session.add(chunk)
        await self.session.flush()
        return chunk

    async def count_chunks(self, session_id: UUID) -> int:
        result = await self.session.execute(
            select(UploadChunk).where(UploadChunk.session_id == session_id)
        )
        return len(result.scalars().all())

    async def received_indices(self, session_id: UUID) -> list[int]:
        result = await self.session.execute(
            select(UploadChunk.index)
            .where(UploadChunk.session_id == session_id)
            .order_by(UploadChunk.index)
        )
        return [row[0] for row in result.all()]

    # -- Outbox --------------------------------------------------------------

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

    # -- Reaper --------------------------------------------------------------

    async def expired_sessions(self, now: datetime) -> list[UploadSession]:
        """Sessions still in flight past their expiry (stale and safe to reap)."""
        result = await self.session.execute(
            select(UploadSession)
            .where(
                UploadSession.expires_at < now,
                UploadSession.status.in_(
                    [UploadSessionStatus.INITIATED, UploadSessionStatus.UPLOADING]
                ),
            )
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())

    async def uncleaned_aborted(self, now: datetime, grace: timedelta) -> list[UploadSession]:
        """Aborted sessions whose storage cleanup never completed (retry)."""
        result = await self.session.execute(
            select(UploadSession)
            .where(
                UploadSession.status == UploadSessionStatus.ABORTED,
                UploadSession.storage_cleaned_at.is_(None),
                UploadSession.updated_at < now - grace,
            )
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())
