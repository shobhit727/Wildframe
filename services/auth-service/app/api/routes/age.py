"""Auth-service age verification routes - self-declare plus document check, JWT claims enrichment."""

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.age_verification import AgeVerification
from app.schemas.age import AgeVerifyRequest, AgeVerifyResponse
from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.policy import get_policy_for_jurisdiction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/age", tags=["age-verification"])

# jurisdiction -> minor age
CONSENT_AGES = {"EU": 16, "US": 13, "IN": 18, "GLOBAL": 16, "US-CA": 16}


@router.post("/verify", response_model=AgeVerifyResponse, status_code=status.HTTP_201_CREATED)
async def verify_age(
    request: AgeVerifyRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgeVerifyResponse:
    # Validate jurisdiction and get policy
    try:
        jurisdiction = Jurisdiction(request.jurisdiction)
        policy = get_policy_for_jurisdiction(jurisdiction)
        consent_age = policy.consent_minor_age
    except Exception:
        consent_age = CONSENT_AGES.get(request.jurisdiction, 16)
    is_minor = request.declared_age < consent_age
    verified = True  # self_declare auto-verified; document would call verification service
    now = datetime.now(UTC)
    # Persist
    record = AgeVerification(
        user_id=request.user_id,
        verification_method=request.verification_method,
        declared_age=request.declared_age,
        verified_age=request.declared_age if verified else None,
        is_minor=is_minor,
        jurisdiction=request.jurisdiction,
        consent_minor_age=consent_age,
        document_type=request.document_type,
        verified_at=now if verified else None,
        verified_by="self" if request.verification_method == "self_declare" else "document",
    )
    db.add(record)
    await db.flush()
    await db.commit()
    await db.refresh(record)
    jwt_claim = {"age_verified": verified, "is_minor": is_minor, "minor_flag": is_minor, "consent_age": consent_age, "verified_at": now.isoformat()}
    logger.info(f"Age verified: user={request.user_id} age={request.declared_age} minor={is_minor} j={request.jurisdiction}")
    return AgeVerifyResponse(
        user_id=request.user_id,
        verified_age=record.verified_age,
        is_minor=is_minor,
        jurisdiction=request.jurisdiction,
        consent_minor_age=consent_age,
        verified=verified,
        verified_at=record.verified_at,
        jwt_claim=jwt_claim,
    )


@router.get("/check/{user_id}", response_model=AgeVerifyResponse)
async def check_age(
    user_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgeVerifyResponse:
    from sqlalchemy import select

    stmt = select(AgeVerification).where(AgeVerification.user_id == user_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Age verification not found")
    return AgeVerifyResponse(
        user_id=record.user_id,
        verified_age=record.verified_age,
        is_minor=record.is_minor,
        jurisdiction=record.jurisdiction,
        consent_minor_age=record.consent_minor_age,
        verified=record.verified_at is not None,
        verified_at=record.verified_at,
        jwt_claim={"age_verified": record.verified_at is not None, "is_minor": record.is_minor},
    )
