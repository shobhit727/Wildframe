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
    """Validated API input corresponding to the AdConfig persistence model."""

    content_id: UUID
    consent_gated: bool = True
    minor_safe: bool = True
    tcf_required: bool = True


@router.post("", status_code=201)
async def create_ad(request: AdConfigRequest, db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    """Create an ad configuration from validated API data."""
    ad = AdConfig(
        content_id=request.content_id,
        consent_gated=request.consent_gated,
        minor_safe=request.minor_safe,
        tcf_required=request.tcf_required,
    )
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
