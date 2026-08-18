"""Recommendation service business logic."""

import logging
from datetime import datetime, timezone
from uuid import UUID

import httpx

from app.core.settings import settings
from app.repositories import RecommendationRepository, UserPreferencesRepository

logger = logging.getLogger(__name__)


class ContentCatalogClient:
    """Fetches genres and published content from content-service.

    Uses an httpx.AsyncClient with connection pooling and bounded
    connection limits. One client is shared process-wide (see
    get_catalog_client) rather than created per request.
    """

    def __init__(
        self,
        base_url: str = settings.CONTENT_SERVICE_URL,
        timeout: float | None = None,
        limits: httpx.Limits | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout if timeout is not None else settings.CONTENT_CATALOG_TIMEOUT_SECONDS
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            limits=limits
            or httpx.Limits(
                max_connections=settings.CONTENT_CATALOG_MAX_CONNECTIONS,
                max_keepalive_connections=settings.CONTENT_CATALOG_MAX_KEEPALIVE,
            ),
        )

    async def fetch_genres(self) -> list[dict]:
        resp = await self.client.get("/api/v1/genres")
        resp.raise_for_status()
        return list(resp.json())

    async def fetch_by_genre(self, genre_id, page_size: int = 100) -> list[dict]:
        resp = await self.client.get(
            "/api/v1/content",
            params={"page": 1, "page_size": page_size, "genre_id": genre_id},
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
    """Return the process-wide shared catalog client, creating it lazily.

    The client is reused across all recommendation generations so HTTP
    connection pooling and TLS sessions are amortized. The class identity
    check exists so tests that patch ``ContentCatalogClient`` still get a
    client built from the patched class (production never patches, so a
    single shared client is used there).
    """
    global _catalog_client, _catalog_client_class
    if _catalog_client is None or ContentCatalogClient is not _catalog_client_class:
        _catalog_client = ContentCatalogClient()
        _catalog_client_class = ContentCatalogClient
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
        prefs = await self.pref_repo.get_or_create(user_id)
        recommendations = await self.rec_repo.get_for_user(user_id, limit)
        if recommendations:
            latest_generated = await self.rec_repo.latest_created_at(user_id)
            if latest_generated is not None and latest_generated >= prefs.updated_at:
                return [
                    {
                        "content_id": str(r.content_id),
                        "score": r.score,
                        "reason": r.reason,
                    }
                    for r in recommendations
                ]
        try:
            await self.generate(
                user_id, prefs.liked_genres or [], prefs.disliked_genres or [], limit
            )
            recommendations = await self.rec_repo.get_for_user(user_id, limit)
        except Exception:
            logger.exception("Recommendation generation failed; returning stored rows")
        return [
            {"content_id": str(r.content_id), "score": r.score, "reason": r.reason}
            for r in recommendations
        ]

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
        try:
            await self.generate(user_id, prefs.liked_genres or [], prefs.disliked_genres or [])
        except Exception:
            logger.exception("Recommendation refresh failed after preference update")
        return prefs
