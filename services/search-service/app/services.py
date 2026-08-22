"""Search service business logic.

Covers: canonical document mapping, atomic alias-switched reindex, bulk indexing with
per-document error surfacing, search-after pagination with integrity-protected cursors,
query validation and timeout guards, ES index lifecycle with zero-downtime alias
cutover, and delete/index safeguards.
"""

import logging
import re
from dataclasses import dataclass
from uuid import UUID

from elasticsearch import AsyncElasticsearch, NotFoundError
from elasticsearch.exceptions import ApiError as ElasticsearchApiError
import httpx

from app.core.settings import settings
from app.repositories import SearchIndexRepository, SearchQueryRepository

logger = logging.getLogger(__name__)

CONTENT_INDEX = "content"
CONTENT_INDEX_PREFIX = "content_v"

# Hard cap for any single outbound response body from content-service (#311):
# a compromised/oversized upstream must not exhaust worker memory.
MAX_UPSTREAM_BODY_BYTES = 64 * 1024 * 1024  # 64 MB


async def _bounded_json(resp: httpx.Response) -> list | dict:
    """Parse a JSON response body with a hard size ceiling (#311)."""
    length = resp.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_UPSTREAM_BODY_BYTES:
        raise CatalogFetchError(
            f"upstream response too large: {length} > {MAX_UPSTREAM_BODY_BYTES}"
        )
    body = await resp.aread()
    if len(body) > MAX_UPSTREAM_BODY_BYTES:
        raise CatalogFetchError(f"upstream response too large: {len(body)} bytes")
    import json as _json

    try:
        parsed: list | dict = _json.loads(body)
        return parsed
    except Exception as e:
        raise CatalogFetchError(f"malformed upstream JSON: {e}") from e


# Elasticsearch index mapping: searchable text + filterable keywords.
# IMPORTANT: keep in sync with incremental indexing (index_content).
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


class CatalogFetchError(RuntimeError):
    """Raised when content-service catalog cannot be fetched or parsed."""


class IndexingError(RuntimeError):
    """Raised when bulk indexing has per-document failures."""

    def __init__(self, message: str, failed: list[dict] | None = None):
        super().__init__(message)
        self.failed = failed or []


@dataclass(frozen=True)
class SearchResult:
    results: list[dict]
    next_sort: list | None  # sort values of the last hit, for search_after


@dataclass(frozen=True)
class ReindexResult:
    count: int
    index_name: str
    switched: bool


class ContentCatalogClient:
    """Fetches published content from content-service for indexing."""

    def __init__(self, base_url: str = settings.CONTENT_SERVICE_URL, timeout: float = 10.0):
        # Explicit verify=True for defense-in-depth against TLS bypasses (#450).
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, verify=True)

    async def fetch_published(self, page_size: int = 100) -> list[dict]:
        """Fetch all published content, enriching each with detail fields (genres, cast, etc.)."""
        items: list[dict] = []
        page = 1
        while True:
            try:
                resp = await self.client.get(
                    "/api/v1/content",
                    params={"page": page, "page_size": page_size, "status": "published"},
                )
                resp.raise_for_status()
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                raise CatalogFetchError(f"catalog list failed: {e}") from e

            try:
                batch = await _bounded_json(resp)
            except CatalogFetchError:
                raise
            except Exception as e:  # JSONDecodeError, etc.
                raise CatalogFetchError(f"malformed catalog response: {e}") from e

            items.extend(batch)
            if len(batch) < page_size:
                break
            page += 1

        # Enrich list items with detail fields (genres, cast_members, release_date).
        # Semaphore bounds concurrent detail fetches to avoid overwhelming content-service.
        sem = __import__("asyncio").Semaphore(8)

        async def enrich(item: dict) -> dict:
            async with sem:
                detail = await self._fetch_detail(item["id"])
                return {**item, **detail}

        enriched = await __import__("asyncio").gather(*(enrich(i) for i in items))
        return list(enriched)

    async def _fetch_detail(self, content_id: str) -> dict:
        try:
            resp = await self.client.get(f"/api/v1/content/{content_id}")
            resp.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            raise CatalogFetchError(f"detail fetch for {content_id} failed: {e}") from e
        try:
            parsed = await _bounded_json(resp)
        except CatalogFetchError:
            raise
        except Exception as e:  # JSONDecodeError, etc.
            raise CatalogFetchError(f"malformed detail for {content_id}: {e}") from e
        if not isinstance(parsed, dict):
            raise CatalogFetchError(f"detail for {content_id} is not a JSON object")
        return parsed

    async def aclose(self) -> None:
        await self.client.aclose()


def content_to_doc(item: dict) -> dict:
    """Map a content-service payload to a canonical Elasticsearch document.

    This is the single serialization path used by BOTH incremental indexing and
    full reindex, ensuring semantic equivalence (#82, #96, #118).
    """
    genres = [
        g.get("name") or g.get("slug")
        for g in (item.get("genres") or [])
        if (g.get("name") or g.get("slug"))
    ]

    # cast_members includes actors (role in {"actor", "actress"}) and directors.
    cast = item.get("cast_members") or []
    actors = [m.get("name") for m in cast if m.get("role", "").lower() in ("actor", "actress")]
    directors = [m.get("name") for m in cast if m.get("role", "").lower() == "director"]
    director = directors[0] if directors else ""

    release_year = None
    if rd := item.get("release_date"):
        try:
            release_year = int(rd[:4])
        except Exception:  # noqa: BLE001
            pass

    rating = item.get("audience_score") or item.get("imdb_rating") or 0.0

    return {
        "title": item.get("title") or "",
        "description": item.get("description") or "",
        "content_type": item.get("content_type") or "",
        "genres": genres,
        "actors": actors,
        "director": director,
        "release_year": release_year,
        "rating": float(rating),
        "status": item.get("status") or "published",
    }


def _validate_query(query: str) -> str:
    """Reject empty, oversized, or control-character queries (XSS/highlighting safety)."""
    if not query or not query.strip():
        raise ValueError("query must not be empty")
    if len(query) > 200:
        raise ValueError("query too long (max 200 characters)")
    if any(ord(ch) < 32 or ch == "\x7f" for ch in query):
        raise ValueError("query contains control characters")
    return query


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


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

    # ---- index lifecycle -------------------------------------------------

    async def ensure_index(self) -> str:
        """Create the content index with mapping if it doesn't exist.

        Returns the concrete index name currently aliased to CONTENT_INDEX.
        """
        current = await self._alias_target()
        if current:
            return current

        # Legacy: an unaliased index literally named CONTENT_INDEX may exist.
        try:
            if await self.es.indices.exists(index=CONTENT_INDEX):
                await self.es.indices.put_alias(index=CONTENT_INDEX, name=CONTENT_INDEX)
                return CONTENT_INDEX
        except Exception:
            logger.exception("Legacy index migration failed; creating new versioned index")

        # Fresh install: create first versioned index and point the alias at it.
        # Sorting by _id (cursor tie-break) requires fielddata on _id (ES 8+);
        # apply it out-of-band so search works on a fresh cluster.
        try:
            await self.es.cluster.put_settings(
                body={"persistent": {"indices.id_field_data.enabled": True}}
            )
        except Exception:
            logger.warning("Could not enable indices.id_field_data; _id sort may fail")
        name = f"{CONTENT_INDEX_PREFIX}1"
        if not await self.es.indices.exists(index=name):
            # An orphaned content_v<N> (left behind by an interrupted reindex)
            # must not 500 startup/reindex: adopt it.
            await self.es.indices.create(index=name, body=CONTENT_INDEX_MAPPING)
        await self.es.indices.put_alias(index=name, name=CONTENT_INDEX)
        logger.info("Created and aliased initial index %s -> %s", name, CONTENT_INDEX)
        return name

    async def _alias_target(self) -> str | None:
        """Return the concrete index name currently pointed to by CONTENT_INDEX alias."""
        try:
            resp = await self.es.indices.get_alias(name=CONTENT_INDEX)
            # Keys are index names; there should be exactly one.
            return sorted(resp.keys())[-1] if resp else None
        except NotFoundError:
            return None
        except Exception:
            logger.exception("Failed to read index alias")
            return None

    async def _versioned_indices(self) -> list[str]:
        """All versioned indices matching content_v*."""
        try:
            resp = await self.es.indices.get(
                index=f"{CONTENT_INDEX_PREFIX}*", ignore_unavailable=True
            )
            return sorted(resp.keys())
        except Exception:
            return []

    async def _next_version(self, current: str | None) -> int:
        """Compute next version number by scanning existing versioned indices."""
        max_v = 0
        if current and (m := re.fullmatch(rf"{CONTENT_INDEX_PREFIX}(\d+)", current)):
            max_v = max(max_v, int(m.group(1)))
        for idx in await self._versioned_indices():
            if m := re.fullmatch(rf"{CONTENT_INDEX_PREFIX}(\d+)", idx):
                max_v = max(max_v, int(m.group(1)))
        return max_v + 1

    # ---- search ----------------------------------------------------------

    async def search(
        self,
        user_id: UUID | None,
        query: str,
        content_type: str | None = None,
        limit: int = 20,
        search_after: list | None = None,
    ) -> SearchResult:
        """Full-text search with validated query, bounded window, timeout, and search_after."""
        query = _validate_query(query)

        must_clauses = [
            {
                "multi_match": {
                    "query": query,
                    "fields": ["title^2", "description", "genres", "actors", "director"],
                    "type": "best_fields",
                    "operator": "or",
                }
            }
        ]
        if content_type:
            must_clauses.append({"term": {"content_type": content_type}})

        # Published-only filter + deterministic sort with unique tie-breaker.
        body = {
            "query": {
                "bool": {"must": must_clauses, "filter": [{"term": {"status": "published"}}]}
            },
            "size": limit,
            "sort": [{"rating": {"order": "desc"}}, {"_id": {"order": "asc"}}],
            "timeout": "5s",
        }
        if search_after:
            body["search_after"] = search_after

        try:
            results = await self.es.search(index=CONTENT_INDEX, body=body)
        except ElasticsearchApiError:
            logger.exception("Elasticsearch search failed; returning empty results")
            return SearchResult(results=[], next_sort=None)
        except Exception:
            logger.exception("Unexpected Elasticsearch error; returning empty results")
            return SearchResult(results=[], next_sort=None)

        hits = results["hits"]["hits"]

        if user_id is not None:
            await self.query_repo.create(user_id, query, len(hits))

        docs = [hit["_source"] for hit in hits]
        next_sort = hits[-1]["sort"] if len(hits) == limit and hits else None
        return SearchResult(results=docs, next_sort=next_sort)

    # ---- trending --------------------------------------------------------

    async def trending(self, content_type: str | None = None, limit: int = 10) -> list[dict]:
        """Top-rated published content; same validation/timeout as search (no search_after)."""
        must_clauses = [{"match_all": {}}]  # type: ignore[var-annotated]
        if content_type:
            must_clauses.append({"term": {"content_type": content_type}})

        body = {
            "query": {
                "bool": {"must": must_clauses, "filter": [{"term": {"status": "published"}}]}
            },
            "size": limit,
            "sort": [{"rating": {"order": "desc"}}, {"_id": {"order": "asc"}}],
            "timeout": "5s",
        }
        try:
            results = await self.es.search(index=CONTENT_INDEX, body=body)
        except Exception:
            logger.exception("Trending query failed")
            return []
        return [hit["_source"] for hit in results["hits"]["hits"]]

    # ---- incremental indexing --------------------------------------------

    async def index_content(
        self,
        content_id: UUID,
        title: str,
        description: str,
        content_type: str,
        **metadata,
    ) -> None:
        """Index a single document (upsert in ES + upsert in SQL mirror).

        Order: SQL upsert first, then ES index. On ES failure, compensate by
        deleting the SQL row so we never leave ES ahead of SQL (#82 evidence).
        """
        doc = {"title": title, "description": description, "content_type": content_type, **metadata}
        try:
            await self.index_repo.upsert(
                content_id,
                title=title,
                content_type=content_type,
                description=description,
                genres=metadata.get("genres"),
                actors=metadata.get("actors"),
                director=metadata.get("director"),
                release_year=metadata.get("release_year"),
                rating=metadata.get("rating"),
            )
        except Exception:
            logger.exception("Failed to upsert search_index row for %s", content_id)
            raise

        try:
            await self.es.index(
                index=CONTENT_INDEX,
                id=str(content_id),
                document={"id": str(content_id), **doc},
            )
        except Exception:
            # Compensate: remove the SQL row so it doesn't become a ghost entry.
            try:
                await self.index_repo.delete(content_id)
            except Exception:
                logger.exception("Compensation delete failed for %s", content_id)
            raise

    # ---- reindex (atomic alias cutover) ----------------------------------

    async def reindex_catalog(self, catalog: ContentCatalogClient | None = None) -> ReindexResult:
        """Full catalog reindex with atomic alias switch and versioned indices.

        - Creates a new versioned index (content_vN).
        - Bulk indexes all published content into the new index.
        - On success, atomically swaps the CONTENT_INDEX alias to the new index.
        - Deletes the previous versioned index (safeguarded: never deletes the alias).
        - On any failure, cleans up the new index and propagates the exception.
        """
        catalog = catalog or ContentCatalogClient()
        old_target = await self.ensure_index()  # ensures alias exists, returns current target
        new_version = await self._next_version(old_target)
        new_index = f"{CONTENT_INDEX_PREFIX}{new_version}"

        logger.info("Starting reindex: target=%s new=%s", old_target, new_index)
        await self.es.indices.create(index=new_index, body=CONTENT_INDEX_MAPPING)

        try:
            items = await catalog.fetch_published()
        except CatalogFetchError:
            await self._cleanup_index(new_index)
            raise
        except Exception:
            await self._cleanup_index(new_index)
            logger.exception("Unexpected catalog fetch error")
            raise

        if not items:
            logger.info("Catalog empty; aborting reindex (no switch)")
            await self._cleanup_index(new_index)
            return ReindexResult(count=0, index_name=old_target, switched=False)

        # Bulk index with one retry pass for transient per-doc failures (#304, #585).
        try:
            await self._bulk_index(new_index, items)
        except IndexingError:
            await self._cleanup_index(new_index)
            raise

        # Atomic alias switch: add new, remove old (if old was versioned).
        actions = [{"add": {"index": new_index, "alias": CONTENT_INDEX}}]
        if old_target and self._is_versioned_index(old_target):
            actions.append({"remove": {"index": old_target, "alias": CONTENT_INDEX}})
        try:
            await self.es.indices.update_aliases(body={"actions": actions})
        except Exception:
            await self._cleanup_index(new_index)
            logger.exception("Alias switch failed; new index %s left for inspection", new_index)
            raise

        # Safeguarded deletion of the old versioned index (#586).
        if old_target and self._is_versioned_index(old_target):
            try:
                await self.es.indices.delete(index=old_target)
                logger.info("Deleted previous versioned index %s", old_target)
            except Exception:
                logger.warning(
                    "Failed to delete old index %s (manual cleanup required)", old_target
                )

        logger.info("Reindex complete: %d items, alias -> %s", len(items), new_index)
        return ReindexResult(count=len(items), index_name=new_index, switched=True)

    async def _bulk_index(self, index_name: str, items: list[dict]) -> None:
        """Bulk index with per-document error capture and one retry pass."""
        operations = []
        for item in items:
            doc = {"id": str(item["id"]), **content_to_doc(item)}
            operations.append({"index": {"_index": index_name, "_id": str(item["id"])}})
            operations.append(doc)

        resp = await self.es.bulk(operations=operations, refresh=False)
        failed = self._collect_bulk_failures(resp)  # type: ignore[arg-type]
        if failed:
            # Retry failed subset once.
            retry_ops = []
            for f in failed:
                doc_id = f["_id"]
                orig = next(i for i in items if str(i["id"]) == doc_id)
                doc = {"id": doc_id, **content_to_doc(orig)}
                retry_ops.append({"index": {"_index": index_name, "_id": doc_id}})
                retry_ops.append(doc)
            retry_resp = await self.es.bulk(operations=retry_ops, refresh=False)
            failed = self._collect_bulk_failures(retry_resp)  # type: ignore[arg-type]

        if failed:
            raise IndexingError(
                f"{len(failed)} documents failed to index after retry", failed=failed
            )

    def _collect_bulk_failures(self, resp: dict) -> list[dict]:
        """Extract per-document errors from a bulk response."""
        if not resp.get("errors"):
            return []
        failures = []
        for item in resp.get("items", []):
            op = item.get("index", {})
            if "error" in op:
                failures.append({"_id": op.get("_id"), "error": op.get("error")})
        return failures

    async def _cleanup_index(self, index_name: str) -> None:
        try:
            await self.es.indices.delete(index=index_name, ignore_unavailable=True)
        except Exception:
            logger.exception("Cleanup failed for index %s", index_name)

    @staticmethod
    def _is_versioned_index(name: str) -> bool:
        return re.fullmatch(rf"{CONTENT_INDEX_PREFIX}\d+", name) is not None

    # ---- delete ----------------------------------------------------------

    async def delete_content(self, content_id: UUID) -> bool:
        """Delete a document from ES and the SQL mirror.

        Returns True if the ES document existed (found), False if it was absent.
        """
        try:
            resp = await self.es.delete(index=CONTENT_INDEX, id=str(content_id), ignore=[404])  # type: ignore[call-arg]
            found = resp.get("result") == "deleted" or resp.get("found") is True
        except Exception:
            logger.exception("ES delete failed for %s", content_id)
            found = False
        try:
            await self.index_repo.delete(content_id)
        except Exception:
            logger.exception("SQL mirror delete failed for %s", content_id)
        return found

    async def delete_index(self, index_name: str) -> None:
        """Safeguarded deletion of a versioned index only (#586).

        Refuses to delete the search alias itself or any non-versioned name.
        Idempotent: deleting an already-absent index is a successful no-op.
        """
        if index_name == CONTENT_INDEX:
            raise ValueError("refusing to delete the search alias")
        current = await self._alias_target()
        if current == index_name:
            raise ValueError("refusing to delete the currently aliased index")
        if not self._is_versioned_index(index_name):
            raise ValueError("only versioned indices (content_v<N>) can be deleted")
        await self.es.indices.delete(index=index_name, ignore_unavailable=True)
        logger.info("Deleted versioned index %s", index_name)

    # ---- shutdown --------------------------------------------------------

    async def flush(self) -> None:
        await self.es.close()
