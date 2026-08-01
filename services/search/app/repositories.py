"""Search service repositories."""
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SearchIndex, SearchQuery


class SearchQueryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
    async def create(self, user_id: UUID, query_text: str, result_count: int = 0) -> SearchQuery:
        q = SearchQuery(user_id=user_id, query_text=query_text, result_count=result_count)
        self.session.add(q)
        await self.session.flush()
        return q
    async def get_recent(self, user_id: UUID, limit: int = 20) -> list[SearchQuery]:
        stmt = select(SearchQuery).where(SearchQuery.user_id == user_id).order_by(desc(SearchQuery.created_at)).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

class SearchIndexRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
    async def create(self, content_id: UUID, title: str, content_type: str, description: str = "") -> SearchIndex:
        idx = SearchIndex(content_id=content_id, title=title, content_type=content_type, description=description)
        self.session.add(idx)
        await self.session.flush()
        return idx
    async def get_by_content_id(self, content_id: UUID) -> SearchIndex | None:
        stmt = select(SearchIndex).where(SearchIndex.content_id == content_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
