"""DRM routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.drm import DRMConfig
from app.schemas.drm import DRMCreate

router = APIRouter(prefix="/drm", tags=["drm"])


@router.post("/license", status_code=201)
async def create_license(request: DRMCreate, db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    rec = DRMConfig(**request.model_dump())
    db.add(rec)
    await db.flush()
    await db.commit()
    return {"id": str(rec.id)}


@router.get("/{content_id}")
async def get_drm(content_id: UUID, db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    from sqlalchemy import select

    stmt = select(DRMConfig).where(DRMConfig.content_id == content_id)
    result = await db.execute(stmt)
    rec = result.scalar_one_or_none()
    if not rec:
        return {"error": "not found"}
    return {"content_id": str(rec.content_id), "fairplay": rec.fairplay_enabled, "widevine": rec.widevine_enabled}
