"""Streaming-service maturity routes - content gating by maturity rating (AVMS) plus purchase restrictions."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.maturity import ContentMaturity
from app.schemas.maturity import (
    MaturityCheckRequest,
    MaturityCheckResponse,
    MaturityCreate,
    MaturityResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/maturity", tags=["maturity"])


@router.post("", response_model=MaturityResponse, status_code=status.HTTP_201_CREATED)
async def create_maturity(
    request: MaturityCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MaturityResponse:
    record = ContentMaturity(**request.model_dump())
    db.add(record)
    await db.flush()
    await db.commit()
    await db.refresh(record)
    return MaturityResponse.model_validate(record)


@router.post("/check", response_model=MaturityCheckResponse)
async def check_maturity(
    request: MaturityCheckRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MaturityCheckResponse:
    stmt = select(ContentMaturity).where(ContentMaturity.content_id == request.content_id)
    result = await db.execute(stmt)
    maturity = result.scalar_one_or_none()
    if not maturity:
        return MaturityCheckResponse(
            allowed=True, reason="No maturity restriction", requires_consent=False, min_age=0
        )
    if request.user_age < maturity.min_age and not request.parental_consent:
        return MaturityCheckResponse(
            allowed=False,
            reason=f"Requires age {maturity.min_age} or parental consent",
            requires_consent=True,
            min_age=maturity.min_age,
        )
    # Check bedtime / screen time would go here
    return MaturityCheckResponse(
        allowed=True,
        reason=None,
        requires_consent=maturity.requires_parental_consent,
        min_age=maturity.min_age,
    )


@router.get("/{content_id}", response_model=MaturityResponse)
async def get_maturity(
    content_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MaturityResponse:
    stmt = select(ContentMaturity).where(ContentMaturity.content_id == content_id)
    result = await db.execute(stmt)
    maturity = result.scalar_one_or_none()
    if not maturity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Maturity not found")
    return MaturityResponse.model_validate(maturity)
