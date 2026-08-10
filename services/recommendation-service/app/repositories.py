"""Recommendation service repositories."""

from uuid import UUID

from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Recommendation, UserPreferences


class UserPreferencesRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, user_id: UUID) -> UserPreferences:
        stmt = select(UserPreferences).where(UserPreferences.user_id == user_id)
        result = await self.session.execute(stmt)
        pref = result.scalar_one_or_none()
        if not pref:
            pref = UserPreferences(user_id=user_id)
            self.session.add(pref)
            await self.session.flush()
        return pref


class RecommendationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, user_id: UUID, content_id: UUID, score: float, reason: str = "", algorithm: str = "cf"
    ) -> Recommendation:
        rec = Recommendation(
            user_id=user_id, content_id=content_id, score=score, reason=reason, algorithm=algorithm
        )
        self.session.add(rec)
        await self.session.flush()
        return rec

    async def clear_for_user(self, user_id: UUID) -> None:
        stmt = delete(Recommendation).where(Recommendation.user_id == user_id)
        await self.session.execute(stmt)

    async def get_for_user(self, user_id: UUID, limit: int = 20) -> list[Recommendation]:
        stmt = (
            select(Recommendation)
            .where(Recommendation.user_id == user_id)
            .order_by(desc(Recommendation.score))
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
