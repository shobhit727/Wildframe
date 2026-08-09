"""Recommendation service business logic."""

import logging
from datetime import datetime, timezone
from uuid import UUID

import httpx

from app.core.settings import settings
from app.repositories import RecommendationRepository, UserPreferencesRepository

logger = logging.getLogger(__name__)


class ContentCatalogClient:
    """Fetches genres and published content from content-service."""

    def __init__(self, base_url: str = settings.CONTENT_SERVICE_URL, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def fetch_genres(self) -> list[dict]:
        resp = await self.client.get("/api/v1/genres")
        resp.raise_for_status()
        return resp.json()

    async def fetch_by_genre(self, genre_id, page_size: int = 100) -> list[dict]:
        resp = await self.client.get(
            "/api/v1/content",
            params={"page": 1, "page_size": page_size, "genre_id": genre_id},
        )
        resp.raise_for_status()
        return resp.json()

    async def fetch_global(self, page_size: int = 100) -> list[dict]:
        resp = await self.client.get("/api/v1/content", params={"page": 1, "page_size": page_size})
        resp.raise_for_status()
        return resp.json()

    async def aclose(self) -> None:
        await self.client.aclose()


class RecommendationService:
    def __init__(self, pref_repo: UserPreferencesRepository, rec_repo: RecommendationRepository):
        self.pref_repo = pref_repo
        self.rec_repo = rec_repo

    async def get_recommendations(self, user_id: UUID, limit: int = 20) -> list[dict]:
        """Personalized recommendations for a user.

        Returns stored rows; if none exist yet, generates them from the
        user's genre preferences (plus popular catalog content as filler)
        so the endpoint is useful out of the box.
        """
        prefs = await self.pref_repo.get_or_create(user_id)
        recommendations = await self.rec_repo.get_for_user(user_id, limit)
        if not recommendations:
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
        catalog = ContentCatalogClient()
        try:
            genres = await catalog.fetch_genres()
            by_slug = {str(g.get("slug", "")).lower(): g for g in genres}
            by_name = {str(g.get("name", "")).lower(): g for g in genres}
            disliked = {str(s).strip().lower() for s in disliked_genres if s}

            def resolve(genre_ref: str) -> dict | None:
                ref = str(genre_ref).strip().lower()
                return by_slug.get(ref) or by_name.get(ref)

            liked = [g for g in (resolve(x) for x in liked_genres) if g]

            scored: dict[str, tuple[float, str]] = {}
            seen: set[str] = set()

            async def score_genre(genre) -> None:
                try:
                    items = await catalog.fetch_by_genre(str(genre["id"]))
                except Exception:
                    logger.warning("Failed to fetch content for genre %s", genre.get("slug"))
                    return
                for item in items:
                    cid = str(item["id"])
                    genre_slugs = {
                        str(g.get("slug", "")).lower()
                        for g in (item.get("genres") or [])
                        if g.get("slug")
                    }
                    if genre_slugs & disliked:
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
                    cid = str(item["id"])
                    genre_slugs = {
                        str(g.get("slug", "")).lower()
                        for g in (item.get("genres") or [])
                        if g.get("slug")
                    }
                    if genre_slugs & disliked:
                        continue
                    if cid in seen:
                        continue
                    seen.add(cid)
                    score = float(item.get("audience_score") or item.get("imdb_rating") or 50.0)
                    scored[cid] = (score, "Popular on Wildframe")

            ranked = sorted(scored.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
            await self.rec_repo.clear_for_user(user_id)
            for cid, (score, reason) in ranked:
                await self.rec_repo.create(
                    user_id, UUID(cid), score, reason=reason, algorithm="genre-based"
                )
            await self.pref_repo.session.commit()
            return len(ranked)
        finally:
            await catalog.aclose()

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
