"""Search service API routes."""
from typing import Annotated
from uuid import UUID

from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories import SearchIndexRepository, SearchQueryRepository
from app.services import SearchService

router = APIRouter(prefix="/search", tags=["search"])

async def get_search_service(db: Annotated[AsyncSession, Depends(get_db)]) -> SearchService:
    es = AsyncElasticsearch(hosts=["elasticsearch:9200"])
    return SearchService(es, SearchQueryRepository(db), SearchIndexRepository(db))

@router.get("/query")
async def search_content(
    service: Annotated[SearchService, Depends(get_search_service)],
    user_id: Annotated[UUID, Body()],
    q: str,
    content_type: str | None = None,
    limit: int = 20,
):
    """Search for content."""
    results = await service.search(user_id, q, content_type, limit)
    return {"query": q, "results": results, "total": len(results)}

@router.get("/trending")
async def get_trending(content_type: str | None = None, limit: int = 10):
    """Get trending content."""
    return {"trending": [], "total": 0}
