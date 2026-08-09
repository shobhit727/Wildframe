"""Search service business logic."""

import logging
from uuid import UUID

from elasticsearch import AsyncElasticsearch
import httpx

from app.core.settings import settings
from app.repositories import SearchIndexRepository, SearchQueryRepository

logger = logging.getLogger(__name__)

CONTENT_INDEX = "content"

# Index mapping: searchable text fields + filters used by the route layer.
CONTENT_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "title": {"type": "text"},
            "description": {"type": "text"},
            "content_type": {"type": "keyword"},
            "genres": {"type": "keyword"},
            "actors": {"type": "keyword"},
            "director": {"type": "keyword"},
            "release_year": {"type": "integer"},
            "rating": {"type": "float"},
            "status": {"type": "keyword"},
        }
    }
}


class ContentCatalogClient:
    """Fetches published content from content-service for indexing."""

    def __init__(self, base_url: str = settings.CONTENT_SERVICE_URL, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def fetch_published(self, page_size: int = 100) -> list[dict]:
        """Fetch all published content (paginated)."""
        items: list[dict] = []
        page = 1
        while True:
            resp = await self.client.get(
                "/api/v1/content",
                params={"page": page, "page_size": page_size, "status": "published"},
            )
            resp.raise_for_status()
            batch = resp.json()
            items.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
        return items

    async def aclose(self) -> None:
        await self.client.aclose()


def content_to_doc(item: dict) -> dict:
    """Map a content-service payload to an ES document."""
    genres = [
        g.get("name") or g.get("slug")
        for g in (item.get("genres") or [])
        if (g.get("name") or g.get("slug"))
    ]
    return {
        "title": item.get("title") or "",
        "description": item.get("description") or "",
        "content_type": item.get("content_type") or "",
        "genres": genres,
        "actors": [],
        "director": "",
        "release_year": None,
        "rating": item.get("audience_score") or item.get("imdb_rating") or 0.0,
        "status": item.get("status") or "published",
    }


class SearchService:
    def __init__(
        self,
        es_client: AsyncElasticsearch,
        query_repo: SearchQueryRepository,
        index_repo: SearchIndexRepository,
    ):
        self.es = es_client
        self.query_repo = query_repo
        self.index_repo = index_repo

    async def ensure_index(self) -> None:
        """Create the content index with mapping if it doesn't exist."""
        if not await self.es.indices.exists(index=CONTENT_INDEX):
            await self.es.indices.create(index=CONTENT_INDEX, body=CONTENT_INDEX_MAPPING)
            logger.info("Created Elasticsearch index %s", CONTENT_INDEX)

    async def search(
        self, user_id: UUID | None, query: str, content_type: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Full-text search via Elasticsearch."""
        must_clauses = [
            {
                "multi_match": {
                    "query": query,
                    "fields": ["title^2", "description", "genres", "actors", "director"],
                }
            }
        ]
        if content_type:
            must_clauses.append({"term": {"content_type": content_type}})

        body = {
            "query": {"bool": {"must": must_clauses}},
            "size": limit,
            "sort": [{"rating": {"order": "desc"}}],
        }
        try:
            results = await self.es.search(index=CONTENT_INDEX, body=body)
        except Exception:
            logger.exception("Elasticsearch search failed; returning empty results")
            return []
        hits = results["hits"]["hits"]

        # Log search query (only for authenticated callers)
        if user_id is not None:
            await self.query_repo.create(user_id, query, len(hits))

        return [hit["_source"] for hit in hits]

    async def index_content(
        self, content_id: UUID, title: str, description: str, content_type: str, **metadata
    ):
        """Index content in Elasticsearch."""
        doc = {"title": title, "description": description, "content_type": content_type, **metadata}
        await self.es.index(index=CONTENT_INDEX, id=str(content_id), document=doc)
        await self.index_repo.create(content_id, title, content_type, description)

    async def reindex_catalog(self, catalog: ContentCatalogClient | None = None) -> int:
        """Backfill the index from content-service's published catalog.

        Tolerant: if content-service is unreachable or the catalog is empty,
        the index is left as-is (no errors surfaced to callers).
        """
        catalog = catalog or ContentCatalogClient()
        await self.ensure_index()
        try:
            items = await catalog.fetch_published()
        except Exception:
            logger.exception("Failed to fetch published content from content-service")
            return 0

        count = 0
        for item in items:
            await self.es.index(
                index=CONTENT_INDEX,
                id=str(item["id"]),
                document={"id": str(item["id"]), **content_to_doc(item)},
            )
            count += 1
        logger.info("Indexed %d published content items", count)
        return count

    async def trending(self, content_type: str | None = None, limit: int = 10) -> list[dict]:
        """Top-rated published content from the search index."""
        must_clauses = [{"term": {"status": "published"}}]
        if content_type:
            must_clauses.append({"term": {"content_type": content_type}})
        body = {
            "query": {"bool": {"must": must_clauses}},
            "size": limit,
            "sort": [{"rating": {"order": "desc"}}],
        }
        try:
            results = await self.es.search(index=CONTENT_INDEX, body=body)
        except Exception:
            logger.exception("Elasticsearch trending failed; returning empty results")
            return []
        return [hit["_source"] for hit in results["hits"]["hits"]]

    async def flush(self) -> None:
        """Close the Elasticsearch client (shutdown hook)."""
        await self.es.close()
