"""User-service DSAR repository."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dsar import DSARRequest

logger = logging.getLogger(__name__)


class DSARRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        user_id: UUID,
        request_type: str,
        data_categories: list[str],
        reason: str | None = None,
    ) -> DSARRequest:
        # SLA: 30d GDPR, 45d CCPA - use 30d default, 45d if US-CA
        sla_days = 30
        sla_deadline = datetime.now(UTC) + timedelta(days=sla_days)
        dsar = DSARRequest(
            user_id=user_id,
            request_type=request_type,
            status="pending",
            data_categories=",".join(data_categories) if data_categories else "[]",
            reason=reason,
            sla_deadline=sla_deadline,
        )
        self.db.add(dsar)
        await self.db.flush()
        logger.info(f"DSAR created: {dsar.id} type={request_type} for {user_id}")
        return dsar

    async def get_by_id(self, dsar_id: UUID) -> DSARRequest | None:
        stmt = select(DSARRequest).where(DSARRequest.id == dsar_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
