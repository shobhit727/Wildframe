"""Search service repositories."""

from uuid import UUID

from sqlalchemy import delete, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
        stmt = (
            select(SearchQuery)
            .where(SearchQuery.user_id == user_id)
            .order_by(desc(SearchQuery.created_at), SearchQuery.id.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()  # type: ignore[return-value]


class SearchIndexRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(
        self,
        content_id: UUID,
        *,
        title: str,
        content_type: str,
        description: str = "",
        genres: list[str] | None = None,
        actors: list[str] | None = None,
        director: str | None = None,
        release_year: int | None = None,
        rating: float | None = None,
    ) -> SearchIndex:
        """Upsert the search_index mirror row (PostgreSQL ON CONFLICT)."""
        stmt = (
            pg_insert(SearchIndex)
            .values(
                content_id=content_id,
                title=title,
                content_type=content_type,
                description=description,
                genres=genres or [],
                actors=actors or [],
                director=director or "",
                release_year=release_year,
                rating=int(rating) if rating is not None else None,  # column is Integer
            )
            .on_conflict_do_update(
                index_elements=[SearchIndex.content_id],
                set_={
                    "title": title,
                    "content_type": content_type,
                    "description": description,
                    "genres": genres or [],
                    "actors": actors or [],
                    "director": director or "",
                    "release_year": release_year,
                    "rating": int(rating) if rating is not None else None,
                    "updated_at": (
                        SearchIndex.updated_at.default.arg()  # type: ignore[union-attr]
                        if callable(SearchIndex.updated_at.default.arg)  # type: ignore[union-attr]
                        else None
                    ),
                },
            )
            .returning(SearchIndex)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one()

    async def get_by_content_id(self, content_id: UUID) -> SearchIndex | None:
        stmt = select(SearchIndex).where(SearchIndex.content_id == content_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete(self, content_id: UUID) -> None:
        stmt = delete(SearchIndex).where(SearchIndex.content_id == content_id)
        await self.session.execute(stmt)
        await self.session.flush()
