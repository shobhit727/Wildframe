"""Recommendation service repositories."""

from typing import cast
from uuid import UUID

from sqlalchemy import delete, desc, select
from sqlalchemy.engine import CursorResult
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
        import math

        if not math.isfinite(score):
            raise ValueError("score must be a finite number (not NaN or infinity)")
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
            .order_by(desc(Recommendation.score), Recommendation.id.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def latest_created_at(self, user_id: UUID):
        """Most recent generation timestamp for a user's stored rows (None if none)."""
        stmt = (
            select(Recommendation.created_at)
            .where(Recommendation.user_id == user_id)
            .order_by(desc(Recommendation.created_at), Recommendation.id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_for_content(self, content_id: UUID) -> int:
        """Evict every stored recommendation for a title (all users).

        Called from the content.deleted / content.unpublished event
        handlers; idempotent (deleting absent rows is a no-op) (#228 F3).
        """
        stmt = delete(Recommendation).where(Recommendation.content_id == content_id)
        result = cast(CursorResult, await self.session.execute(stmt))
        return result.rowcount
