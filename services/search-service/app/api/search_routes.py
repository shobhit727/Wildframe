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

# Shared ES client (created lazily on first request, closed on shutdown).
_es_client: AsyncElasticsearch | None = None


def es_client() -> AsyncElasticsearch:
    global _es_client
    if _es_client is None:
        _es_client = AsyncElasticsearch(hosts=[settings.ELASTICSEARCH_URL])
    return _es_client


async def close_es_client() -> None:
    global _es_client
    if _es_client is not None:
        await _es_client.close()
        _es_client = None


async def get_search_service(db: Annotated[AsyncSession, Depends(get_db)]) -> SearchService:
    return SearchService(es_client(), SearchQueryRepository(db), SearchIndexRepository(db))


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
async def get_trending(
    service: Annotated[SearchService, Depends(get_search_service)],
    content_type: str | None = None,
    limit: int = 10,
):
    """Get trending content (top-rated published titles in the index)."""
    results = await service.trending(content_type, limit)
    return {"trending": results, "total": len(results)}


@router.post("/reindex")
async def reindex(
    service: Annotated[SearchService, Depends(get_search_service)],
):
    """Re-index published content from the catalog service into Elasticsearch."""
    count = await service.reindex_catalog()
    return {"indexed": count}
