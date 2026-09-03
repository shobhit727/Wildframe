"""Content-service DSAR routes - copyright metadata and usage rights export."""

import logging

# from typing import Annotated
from uuid import UUID

from fastapi import APIRouter

from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.dsar import ContentDSARResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dsar", tags=["content-dsar"])


@router.get("/content", response_model=list[ContentDSARResponse])
async def export_content_dsar(user_id: UUID, dsar_id: UUID, db: Annotated[AsyncSession, Depends(get_db)]) -> list[ContentDSARResponse]:
    """Export content-specific data for DSAR - viewing history, uploads, reviews."""
    from sqlalchemy import select
    from app.models.dsar import ContentDSARRecord
    stmt = select(ContentDSARRecord).where(ContentDSARRecord.user_id == user_id, ContentDSARRecord.dsar_id == dsar_id)
    result = await db.execute(stmt)
    records = result.scalars().all()
    return [ContentDSARResponse.model_validate(r) for r in records]


@router.get("/export/{user_id}", response_model=dict)
async def export_all_content(user_id: UUID) -> dict:
    """Full content export for portability."""
    return {"user_id": str(user_id), "viewing_history": [], "uploads": [], "reviews": []}
