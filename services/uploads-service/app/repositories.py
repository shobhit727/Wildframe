"""Uploads service repositories."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UploadChunk, UploadSession


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

    async def list_by_creator(self, creator_id: UUID, limit: int = 50) -> list[UploadSession]:
        result = await self.session.execute(
            select(UploadSession)
            .where(UploadSession.creator_id == creator_id)
            .order_by(UploadSession.created_at.desc())
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
