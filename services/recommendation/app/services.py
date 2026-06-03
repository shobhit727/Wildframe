"""Recommendation service business logic."""
from uuid import UUID
from typing import List, Dict
from app.repositories import UserPreferencesRepository, RecommendationRepository

class RecommendationService:
    def __init__(self, pref_repo: UserPreferencesRepository, rec_repo: RecommendationRepository):
        self.pref_repo = pref_repo
        self.rec_repo = rec_repo
    
    async def get_recommendations(self, user_id: UUID, limit: int = 20) -> List[Dict]:
        """Get personalized recommendations for user using collaborative filtering."""
        prefs = await self.pref_repo.get_or_create(user_id)
        recommendations = await self.rec_repo.get_for_user(user_id, limit)
        return [{"content_id": str(r.content_id), "score": r.score, "reason": r.reason} for r in recommendations]
    
    async def update_preferences(self, user_id: UUID, liked_genres: List[str] = None, disliked_genres: List[str] = None):
        """Update user preferences."""
        prefs = await self.pref_repo.get_or_create(user_id)
        if liked_genres:
            prefs.liked_genres = liked_genres
        if disliked_genres:
            prefs.disliked_genres = disliked_genres
        prefs.updated_at = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
        await self.session.flush()
        return prefs
