"""Uploads service repositories."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
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
        await self.session.commit()
        return session

    async def get(self, session_id: UUID) -> UploadSession | None:
        result = await self.session.execute(
            select(UploadSession).where(UploadSession.id == session_id)
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
        session.updated_at = datetime.now(UTC)  # type: ignore[assignment]
        await self.session.flush()
        await self.session.commit()
        return session

    # -- UploadChunk ---------------------------------------------------------

    async def add_chunk(self, chunk: UploadChunk) -> UploadChunk:
        self.session.add(chunk)
        await self.session.flush()
        await self.session.commit()
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
        await self.session.commit()
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
            row.status = OutboxEventStatus.DISPATCHED  # type: ignore[assignment]
            row.dispatched_at = datetime.now(UTC)  # type: ignore[assignment]
            await self.session.flush()
            await self.session.commit()

    # -- Reaper --------------------------------------------------------------

    async def expired_sessions(self, now: datetime) -> list[UploadSession]:
        """Sessions still in flight past their expiry (stale and safe to reap)."""
        result = await self.session.execute(
            select(UploadSession).where(
                UploadSession.expires_at < now,
                UploadSession.status.in_(
                    [UploadSessionStatus.INITIATED, UploadSessionStatus.UPLOADING]
                ),
            )
        )
        return list(result.scalars().all())

    async def uncleaned_aborted(self, now: datetime, grace: timedelta) -> list[UploadSession]:
        """Aborted sessions whose storage cleanup never completed (retry)."""
        result = await self.session.execute(
            select(UploadSession).where(
                UploadSession.status == UploadSessionStatus.ABORTED,
                UploadSession.storage_cleaned_at.is_(None),
                UploadSession.updated_at < now - grace,
            )
        )
        return list(result.scalars().all())
