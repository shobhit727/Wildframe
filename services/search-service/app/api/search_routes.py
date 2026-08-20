"""Search service API routes."""

import asyncio
from typing import Annotated
from uuid import UUID

from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from wildframe_observability.logging import correlation_id_var

from app.core.database import get_db
from app.core.security import (
    get_admin_identity,
    get_optional_identity,
    decode_cursor,
    encode_cursor,
)
from app.core.settings import settings
from app.repositories import SearchIndexRepository, SearchQueryRepository
from app.services import (
    SearchService,
    ContentCatalogClient,
    ReindexResult,
    CatalogFetchError,
    IndexingError,
)

router = APIRouter(prefix="/api/v1/search", tags=["search"])


def _error(status_code: int, message: str) -> HTTPException:
    """HTTPException whose detail carries the stable request correlation ID."""
    return HTTPException(
        status_code=status_code,
        detail={"message": message, "correlation_id": correlation_id_var.get()},
    )


# Hard limits enforced at the route layer.
MAX_SEARCH_LIMIT = 100
MAX_TRENDING_LIMIT = 50
MAX_QUERY_LENGTH = 200

# Reindex concurrency lock (#116).
_reindex_lock = asyncio.Lock()


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
    request: Request,
    service: Annotated[SearchService, Depends(get_search_service)],
    q: Annotated[str, Query(min_length=1, max_length=MAX_QUERY_LENGTH)],
    content_type: Annotated[str | None, Query(max_length=50)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_SEARCH_LIMIT)] = 20,
    cursor: str | None = None,
):
    """Search for content.

    Identity is derived from the bearer token; anonymous callers get None.
    The gateway forwards Authorization headers transparently.
    """
    identity = await get_optional_identity(request)
    user_id = identity.user_id if identity else None

    search_after = None
    if cursor:
        try:
            search_after = decode_cursor(cursor, q, content_type, limit)
        except ValueError as e:
            raise _error(422, str(e))

    result = await service.search(user_id, q, content_type, limit, search_after=search_after)

    next_cursor = None
    if result.next_sort is not None:
        next_cursor = encode_cursor(q, content_type, limit, result.next_sort)

    return {
        "query": q,
        "results": result.results,
        "total": len(result.results),
        "next_cursor": next_cursor,
    }


@router.get("/trending")
async def get_trending(
    service: Annotated[SearchService, Depends(get_search_service)],
    content_type: Annotated[str | None, Query(max_length=50)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_TRENDING_LIMIT)] = 10,
):
    """Get trending content (top-rated published titles in the index)."""
    results = await service.trending(content_type, limit)
    return {"trending": results, "total": len(results)}


@router.post("/reindex")
async def reindex(
    request: Request,
    service: Annotated[SearchService, Depends(get_search_service)],
):
    """Re-index published content from the catalog service into Elasticsearch.

    Admin-only, single-concurrency, async job semantics via alias switch.
    """
    await get_admin_identity(request)

    if _reindex_lock.locked():
        raise _error(409, "A reindex job is already running")

    async with _reindex_lock:
        try:
            catalog = ContentCatalogClient()
            result: ReindexResult = await service.reindex_catalog(catalog)
        except CatalogFetchError as e:
            raise _error(502, f"Content service unavailable: {e}") from e
        except IndexingError as e:
            raise _error(502, f"Bulk indexing failed: {e}") from e
        except Exception as e:  # noqa: BLE001 - unexpected internal errors
            logger = __import__("logging").getLogger(__name__)
            logger.exception("Reindex failed unexpectedly")
            raise _error(500, "Reindex failed") from e

    return {"indexed": result.count, "index": result.index_name, "switched": result.switched}


@router.delete("/content/{content_id}")
async def remove_content(
    content_id: UUID,
    request: Request,
    service: Annotated[SearchService, Depends(get_search_service)],
):
    """Delete a document from the search index (admin only)."""
    await get_admin_identity(request)
    await service.delete_content(content_id)
    return {"deleted": str(content_id)}


@router.delete("/index/{index_name}")
async def delete_index(
    index_name: str,
    request: Request,
    service: Annotated[SearchService, Depends(get_search_service)],
    confirm: Annotated[bool, Query()] = False,
):
    """Delete a versioned Elasticsearch index (admin only).

    Requires explicit confirmation query parameter `confirm=true`.
    Refuses to delete the search alias or the currently aliased index.
    """
    await get_admin_identity(request)
    if not confirm:
        raise _error(400, "Deletion requires explicit confirmation: add ?confirm=true")
    try:
        await service.delete_index(index_name)
    except ValueError as e:
        raise _error(400, str(e))
    return {"deleted": index_name}
