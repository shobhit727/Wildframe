"""Ads routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.ads import AdConfig

router = APIRouter(prefix="/ads", tags=["ads"])


class AdConfigRequest(BaseModel):
    """API input model; SQLAlchemy ORM classes must never be used as request schemas."""

    name: str = Field(..., min_length=1, max_length=255)
    enabled: bool = False
    # Keep the API body aligned with the persisted ad configuration fields.
    config: dict = Field(default_factory=dict)


@router.post("", status_code=201)
async def create_ad(request: AdConfigRequest, db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    """Create an ad configuration from validated API data."""
    ad = AdConfig(name=request.name, enabled=request.enabled, config=request.config)
    db.add(ad)
    await db.flush()
    await db.commit()
    return {"id": str(ad.id)}


@router.get("/check")
async def check_ad(content_id: UUID, consent: str | None = Header(None, alias="X-Consent")) -> dict:
    """Return whether ad delivery is permitted for the requested content."""
    if not consent:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Consent required")
    return {"allowed": True}
