"""Recommendation service business logic."""

from datetime import datetime, timezone
from uuid import UUID

from app.repositories import RecommendationRepository, UserPreferencesRepository


class RecommendationService:
    def __init__(self, pref_repo: UserPreferencesRepository, rec_repo: RecommendationRepository):
        self.pref_repo = pref_repo
        self.rec_repo = rec_repo

    async def get_recommendations(self, user_id: UUID, limit: int = 20) -> list[dict]:
        """Get personalized recommendations for user using collaborative filtering."""
        await self.pref_repo.get_or_create(user_id)
        recommendations = await self.rec_repo.get_for_user(user_id, limit)
        return [
            {"content_id": str(r.content_id), "score": r.score, "reason": r.reason}
            for r in recommendations
        ]

    async def update_preferences(
        self,
        user_id: UUID,
        liked_genres: list[str] | None = None,
        disliked_genres: list[str] | None = None,
    ):
        """Update user preferences."""
        prefs = await self.pref_repo.get_or_create(user_id)
        if liked_genres:
            prefs.liked_genres = liked_genres
        if disliked_genres:
            prefs.disliked_genres = disliked_genres
        prefs.updated_at = datetime.now(timezone.utc)
        await self.pref_repo.session.commit()
        return prefs
