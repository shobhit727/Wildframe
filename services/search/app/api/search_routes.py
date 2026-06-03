"""Search service API routes."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.repositories import SearchQueryRepository, SearchIndexRepository
from app.services import SearchService
from elasticsearch import AsyncElasticsearch

router = APIRouter(prefix="/search", tags=["search"])

async def get_search_service(db: AsyncSession = Depends(get_db_session)) -> SearchService:
    es = AsyncElasticsearch(hosts=["elasticsearch:9200"])
    return SearchService(es, SearchQueryRepository(db), SearchIndexRepository(db))

@router.get("/query")
async def search_content(q: str, content_type: str = None, limit: int = 20, 
                        user_id: UUID = Body(...), service: SearchService = Depends(get_search_service)):
    """Search for content."""
    results = await service.search(user_id, q, content_type, limit)
    return {"query": q, "results": results, "total": len(results)}

@router.get("/trending")
async def get_trending(content_type: str = None, limit: int = 10):
    """Get trending content."""
    return {"trending": [], "total": 0}
