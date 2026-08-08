"""Search service API routes."""

from typing import Annotated
from uuid import UUID

from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.settings import settings
from app.repositories import SearchIndexRepository, SearchQueryRepository
from app.services import SearchService

router = APIRouter(prefix="/api/v1/search", tags=["search"])


async def get_search_service(db: Annotated[AsyncSession, Depends(get_db)]) -> SearchService:
    es = AsyncElasticsearch(hosts=[settings.ELASTICSEARCH_URL])
    return SearchService(es, SearchQueryRepository(db), SearchIndexRepository(db))


@router.get("/query")
async def search_content(
    service: Annotated[SearchService, Depends(get_search_service)],
    q: str,
    user_id: UUID | None = None,
    content_type: str | None = None,
    limit: int = 20,
):
    """Search for content.

    ``user_id`` is optional and passed as a query parameter: the gateway
    only forwards request bodies for POST/PUT/PATCH, so a GET body (the
    previous contract) would make this endpoint unreachable through it.
    """
    results = await service.search(user_id, q, content_type, limit)
    return {"query": q, "results": results, "total": len(results)}


@router.get("/trending")
async def get_trending(content_type: str | None = None, limit: int = 10):
    """Get trending content."""
    return {"trending": [], "total": 0}
