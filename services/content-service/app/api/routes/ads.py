"""Ads routes."""
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.ads import AdConfig
router = APIRouter(prefix="/ads", tags=["ads"])
@router.post("", status_code=201)
async def create_ad(request: AdConfig, db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    db.add(request)
    await db.flush()
    await db.commit()
    return {"id": str(request.id)}
@router.get("/check")
async def check_ad(content_id: UUID, consent: str | None = Header(None, alias="X-Consent")) -> dict:
    if not consent:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Consent required")
    return {"allowed": True}
