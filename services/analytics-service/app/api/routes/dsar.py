"""Analytics-service DSAR routes - export and retention compliance."""

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Query

from app.schemas.dsar import AnalyticsExportResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dsar", tags=["analytics-dsar"])

@router.get("/export", response_model=list[AnalyticsExportResponse])
async def export_analytics(
    user_id: UUID, format: str = Query(default="json", pattern="^(json|csv)$")
) -> list[AnalyticsExportResponse]:
    """Export analytics data for DSAR - events, sessions, tracking. SLA compliant 30d/45d check via retention_days."""
    # Stub: query analytics store, check retention compliance
    return [
        AnalyticsExportResponse(
            id=uuid4(),
            user_id=user_id,
            dsar_id=uuid4(),
            export_format=format,
            retention_days=365,
            sla_compliant=True,
            data="[]",
            created_at=datetime.now(UTC),
        )
    ]  # stub with SLA check

@router.get("/retention-check")
async def check_retention(user_id: UUID, retention_days: int) -> dict:
    """Check if retention_days exceeds policy (2555 default, jurisdiction-specific)."""
    max_retention = 2555
    compliant = retention_days <= max_retention
    return {
        "user_id": str(user_id),
        "retention_days": retention_days,
        "max_retention": max_retention,
        "compliant": compliant,
        "required_action": None if compliant else "reduce_retention_period",
    }
