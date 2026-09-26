"""Creators onboarding routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import CreatorOnboarding
from app.schemas.onboarding import OnboardingCreate, OnboardingResponse

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.post("", response_model=OnboardingResponse, status_code=201)
async def create_onboarding(
    request: OnboardingCreate, db: Annotated[AsyncSession, Depends(get_db)]
) -> OnboardingResponse:
    rec = CreatorOnboarding(
        user_id=request.user_id,
        kyc_type=request.kyc_type,
        stripe_account_id=request.stripe_account_id,
        tax_form_type=request.tax_form_type,
    )
    db.add(rec)
    try:
        await db.flush()
        await db.commit()
    except IntegrityError:
        # A creator can only have one onboarding row; duplicate submissions are a conflict.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Creator onboarding already exists",
        )
    await db.refresh(rec)
    return OnboardingResponse.model_validate(rec)
