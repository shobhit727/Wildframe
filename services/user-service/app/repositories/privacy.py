"""User-service privacy repository."""

import logging
# from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.privacy import UserConsentRecord

logger = logging.getLogger(__name__)


class UserConsentRepository:
    """Repository for user consent records."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, consent: UserConsentRecord) -> UserConsentRecord:
        self.db.add(consent)
        await self.db.flush()
        logger.info(f"User consent created: {consent.consent_type} for {consent.user_id}")
        return consent

    async def get_by_user_type_jurisdiction(
        self, user_id: UUID, consent_type: str, jurisdiction: str
    ) -> UserConsentRecord | None:
        stmt = select(UserConsentRecord).where(
            UserConsentRecord.user_id == user_id,
            UserConsentRecord.consent_type == consent_type,
            UserConsentRecord.jurisdiction == jurisdiction,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

        stmt = select(UserConsentRecord).where(
            UserConsentRecord.user_id == user_id
        ).order_by(UserConsentRecord.created_at.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
