"""User-service privacy routes - consent collection and preference center."""

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.privacy import UserConsentRecord
from app.repositories.privacy import UserConsentRepository
from app.schemas.privacy import ConsentRecordCreate, ConsentRecordResponse, PreferenceCenterResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/privacy", tags=["privacy"])


async def get_consent_repo(db: Annotated[AsyncSession, Depends(get_db)]) -> UserConsentRepository:
    return UserConsentRepository(db)


@router.post("/consent", response_model=ConsentRecordResponse, status_code=status.HTTP_201_CREATED)
async def create_consent(
    request: ConsentRecordCreate,
    repo: Annotated[UserConsentRepository, Depends(get_consent_repo)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ConsentRecordResponse:
    existing = await repo.get_by_user_type_jurisdiction(request.user_id, request.consent_type, request.jurisdiction)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Consent already exists")
    consent = UserConsentRecord(
        user_id=request.user_id,
        consent_type=request.consent_type,
        jurisdiction=request.jurisdiction,
        granted=request.granted,
        granted_at=datetime.now(UTC) if request.granted else None,
        version=request.version,
        ip_address=request.ip_address,
        user_agent=request.user_agent,
        consent_metadata=request.consent_metadata,
    )
    created = await repo.create(consent)
    await db.commit()
    await db.refresh(created)
    return ConsentRecordResponse.model_validate(created)


@router.get("/consent", response_model=list[ConsentRecordResponse])
async def list_consent(user_id: UUID, repo: Annotated[UserConsentRepository, Depends(get_consent_repo)]) -> list[ConsentRecordResponse]:
    records = await repo.get_by_user(user_id)
    return [ConsentRecordResponse.model_validate(r) for r in records]


@router.get("/preferences", response_model=PreferenceCenterResponse)
async def get_preferences(user_id: UUID, repo: Annotated[UserConsentRepository, Depends(get_consent_repo)]) -> PreferenceCenterResponse:
    records = await repo.get_by_user(user_id)
    return PreferenceCenterResponse(
        user_id=user_id,
        consent_records=[ConsentRecordResponse.model_validate(r) for r in records],
        available_consent_types={
            "marketing": "Marketing communications",
            "analytics": "Analytics and usage tracking",
            "profiling": "Automated profiling",
            "cookies": "Non-essential cookies",
        },
    )
