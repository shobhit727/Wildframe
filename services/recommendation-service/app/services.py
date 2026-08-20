"""Recommendation service business logic."""

import json
import logging
import random
from datetime import datetime, timezone
from uuid import UUID

import httpx
import redis.asyncio as redis_async

from app.core.settings import settings
from app.repositories import RecommendationRepository, UserPreferencesRepository

logger = logging.getLogger(__name__)

# Cache TTL with jitter to prevent cache stampede (#456)
RECOMMENDATION_CACHE_TTL_SECONDS = 300  # 5 minutes base
RECOMMENDATION_CACHE_JITTER_SECONDS = 60  # ±60 seconds jitter


_redis_client: redis_async.Redis | None = None


async def get_redis_client() -> redis_async.Redis | None:
    """Lazily create the shared redis.asyncio client; fail-open if unavailable."""
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = await redis_async.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
            )
            # Verify connection
            await _redis_client.ping()
        except Exception:
            logger.warning("Redis unavailable; recommendation cache disabled")
            _redis_client = None
    return _redis_client


async def close_redis_client() -> None:
    """Close the shared Redis client."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


def _cache_key(user_id: UUID) -> str:
    """Generate cache key for user recommendations."""
    return f"wf:rec:user:{user_id}"


def _jittered_ttl() -> int:
    """Return TTL with random jitter to prevent synchronized expiry."""
    return RECOMMENDATION_CACHE_TTL_SECONDS + random.randint(
        -RECOMMENDATION_CACHE_JITTER_SECONDS, RECOMMENDATION_CACHE_JITTER_SECONDS
    )


async def _cache_get(user_id: UUID) -> list[dict] | None:
    """Get cached recommendations from Redis."""
    client = await get_redis_client()
    if client is None:
        return None
    try:
        data = await client.get(_cache_key(user_id))
        if data:
            return list(json.loads(data))
    except Exception:
        logger.warning("Redis cache get failed for user %s", user_id)
    return None


async def _cache_set(user_id: UUID, recommendations: list[dict]) -> None:
    """Set cached recommendations in Redis with jittered TTL."""
    client = await get_redis_client()
    if client is None:
        return
    try:
        await client.set(
            _cache_key(user_id),
            json.dumps(recommendations),
            ex=_jittered_ttl(),
        )
    except Exception:
        logger.warning("Redis cache set failed for user %s", user_id)


async def _cache_invalidate(user_id: UUID) -> None:
    """Invalidate cached recommendations for a user."""
    client = await get_redis_client()
    if client is None:
        return
    try:
        await client.delete(_cache_key(user_id))
    except Exception:
        logger.warning("Redis cache invalidate failed for user %s", user_id)


class ContentCatalogClient:
    """Fetches genres and published content from content-service."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        max_connections: int = 20,
        max_keepalive: int = 10,
    ):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=max_connections, max_keepalive_connections=max_keepalive
            ),
        )

    async def fetch_genres(self) -> list[dict]:
        resp = await self.client.get("/api/v1/genres")
        resp.raise_for_status()
        return list(resp.json())

    async def fetch_by_genre(self, genre_id, page_size: int = 100) -> list[dict]:
        resp = await self.client.get(
            f"/api/v1/genres/{genre_id}/content", params={"limit": page_size}
        )
        resp.raise_for_status()
        return list(resp.json())

    async def fetch_global(self, page_size: int = 100) -> list[dict]:
        resp = await self.client.get("/api/v1/content/trending", params={"limit": page_size})
        resp.raise_for_status()
        return list(resp.json())

    async def aclose(self) -> None:
        await self.client.aclose()


_catalog_client: ContentCatalogClient | None = None
_catalog_client_class: type | None = None


def get_catalog_client() -> ContentCatalogClient:
    """Return the process-wide shared catalog client, creating it lazily."""
    global _catalog_client, _catalog_client_class
    if _catalog_client is None:
        _catalog_client_class = ContentCatalogClient
        _catalog_client = ContentCatalogClient(
            base_url=settings.CONTENT_SERVICE_URL,
            timeout=settings.CONTENT_CATALOG_TIMEOUT_SECONDS,
            max_connections=settings.CONTENT_CATALOG_MAX_CONNECTIONS,
            max_keepalive=settings.CONTENT_CATALOG_MAX_KEEPALIVE,
        )
    return _catalog_client


async def close_catalog_client() -> None:
    """Close the shared catalog client and release pooled connections."""
    global _catalog_client, _catalog_client_class
    if _catalog_client is not None:
        await _catalog_client.aclose()
        _catalog_client = None
        _catalog_client_class = None


class RecommendationService:
    def __init__(self, pref_repo: UserPreferencesRepository, rec_repo: RecommendationRepository):
        self.pref_repo = pref_repo
        self.rec_repo = rec_repo

    async def get_recommendations(self, user_id: UUID, limit: int = 20) -> list[dict]:
        """Personalized recommendations for a user.

        Returns stored rows; if none exist yet, generates them from the
        user's genre preferences (plus popular catalog content as filler)
        so the endpoint is useful out of the box.

        Stored rows are a per-user cache of the generation output, so every
        input that affects personalization (liked/disliked genres, preferred
        languages, watch frequency — anything that bumps
        ``prefs.updated_at``) must invalidate it: when the preferences are
        newer than the newest stored row, the rows are regenerated before
        serving (#228 F1/F2). Limit is clamped so a hostile caller can
        never request an unbounded result set (#228 F4).
        """
        limit = max(1, min(limit, settings.MAX_RECOMMENDATION_LIMIT))

        # Try Redis cache first (#456)
        cached = await _cache_get(user_id)
        if cached is not None:
            return cached[:limit]

        prefs = await self.pref_repo.get_or_create(user_id)
        recommendations = await self.rec_repo.get_for_user(user_id, limit)
        if recommendations:
            latest_generated = await self.rec_repo.latest_created_at(user_id)
            if latest_generated is not None and latest_generated >= prefs.updated_at:
                result = [
                    {
                        "content_id": str(r.content_id),
                        "score": r.score,
                        "reason": r.reason,
                    }
                    for r in recommendations
                ]
                await _cache_set(user_id, result)
                return result
        try:
            await self.generate(
                user_id, prefs.liked_genres or [], prefs.disliked_genres or [], limit
            )
            recommendations = await self.rec_repo.get_for_user(user_id, limit)
        except Exception:
            logger.exception("Recommendation generation failed; returning stored rows")
        result = [
            {"content_id": str(r.content_id), "score": r.score, "reason": r.reason}
            for r in recommendations
        ]
        await _cache_set(user_id, result)
        return result

    async def generate(
        self,
        user_id: UUID,
        liked_genres: list[str],
        disliked_genres: list[str],
        limit: int = 20,
    ) -> int:
        """Score catalog content against the user's genre preferences.

        Content matching a liked genre ranks high; disliked-genre content is
        excluded. Without expressed preferences, the most popular catalog
        content is used so the user always sees a personalized-feel rail.
        """
        catalog = get_catalog_client()
        try:
            # Bounded inputs (#228 F4): a caller-supplied preference list or
            # limit can never fan out unbounded work or unbounded rows.
            liked_genres = liked_genres[: settings.MAX_PREFERENCE_GENRES]
            disliked_genres = disliked_genres[: settings.MAX_PREFERENCE_GENRES]
            limit = max(1, min(limit, settings.MAX_RECOMMENDATION_LIMIT))
            genres = await catalog.fetch_genres()
            by_slug = {str(g.get("slug", "")).lower(): g for g in genres}
            by_name = {str(g.get("name", "")).lower(): g for g in genres}

            # Resolve a user-supplied genre reference by either slug or name
            # so a preference like "Science Fiction" matches the genre whose
            # slug is "science-fiction" and vice versa. This resolution was
            # previously applied only to liked genres; disliked genres were
            # compared against slugs only, leaking content from name-only
            # dislikes (see test_generate_excludes_disliked_genre_*).
            def resolve(genre_ref: str) -> dict | None:
                ref = str(genre_ref).strip().lower()
                return by_slug.get(ref) or by_name.get(ref)

            liked = [g for g in (resolve(x) for x in liked_genres) if g]

            disliked_genres_resolved = [g for g in (resolve(x) for x in disliked_genres) if g]
            disliked_ids = {str(g.get("id")) for g in disliked_genres_resolved if g.get("id")}
            disliked_slugs = {
                str(g.get("slug", "")).lower() for g in disliked_genres_resolved if g.get("slug")
            }

            scored: dict[str, tuple[float, str]] = {}
            seen: set[str] = set()

            async def score_genre(genre) -> None:
                try:
                    items = await catalog.fetch_by_genre(
                        str(genre["id"]), page_size=settings.MAX_CATALOG_PAGE_SIZE
                    )
                except Exception:
                    logger.warning("Failed to fetch content for genre %s", genre.get("slug"))
                    return
                for item in items:
                    if len(scored) >= settings.MAX_CANDIDATES:
                        return
                    cid = str(item["id"])
                    genre_slugs = {
                        str(g.get("slug", "")).lower()
                        for g in (item.get("genres") or [])
                        if g.get("slug")
                    }
                    item_genre_ids = {
                        str(g.get("id")) for g in (item.get("genres") or []) if g.get("id")
                    }
                    if genre_slugs & disliked_slugs or item_genre_ids & disliked_ids:
                        continue
                    if cid in seen:
                        continue
                    seen.add(cid)
                    base = float(item.get("audience_score") or item.get("imdb_rating") or 50.0)
                    scored[cid] = (
                        base,
                        f"Because you like {genre.get('name') or genre.get('slug')}",
                    )

            for genre in liked:
                await score_genre(genre)

            if not liked:
                try:
                    items = await catalog.fetch_global()
                except Exception:
                    items = []
                for item in items:
                    if len(scored) >= settings.MAX_CANDIDATES:
                        break
                    cid = str(item["id"])
                    genre_slugs = {
                        str(g.get("slug", "")).lower()
                        for g in (item.get("genres") or [])
                        if g.get("slug")
                    }
                    item_genre_ids = {
                        str(g.get("id")) for g in (item.get("genres") or []) if g.get("id")
                    }
                    if genre_slugs & disliked_slugs or item_genre_ids & disliked_ids:
                        continue
                    if cid in seen:
                        continue
                    seen.add(cid)
                    score = float(item.get("audience_score") or item.get("imdb_rating") or 50.0)
                    scored[cid] = (score, "Popular on Wildframe")

            ranked = sorted(
                scored.items(),
                key=lambda kv: (-kv[1][0], str(kv[0])),
            )[:limit]
            await self.rec_repo.clear_for_user(user_id)
            for cid, (score, reason) in ranked:
                await self.rec_repo.create(
                    user_id, UUID(cid), score, reason=reason, algorithm="genre-based"
                )
            await self.pref_repo.session.commit()
            return len(ranked)
        except Exception:
            logger.exception("Recommendation generation failed")
            raise

    async def update_preferences(
        self,
        user_id: UUID,
        liked_genres: list[str] | None = None,
        disliked_genres: list[str] | None = None,
    ):
        """Update user preferences and regenerate recommendations."""
        prefs = await self.pref_repo.get_or_create(user_id)
        if liked_genres is not None:
            prefs.liked_genres = liked_genres
        if disliked_genres is not None:
            prefs.disliked_genres = disliked_genres
        prefs.updated_at = datetime.now(timezone.utc)
        await self.pref_repo.session.commit()
        # Invalidate Redis cache on preference change (#456)
        await _cache_invalidate(user_id)
        try:
            await self.generate(user_id, prefs.liked_genres or [], prefs.disliked_genres or [])
        except Exception:
            logger.exception("Recommendation refresh failed after preference update")
        return prefs
