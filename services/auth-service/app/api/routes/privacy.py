"""
Privacy compliance API routes.
Implements privacy notice management, consent collection, and preference center.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from wildframe_compliance.jurisdiction import Jurisdiction

from app.core.database import get_db
from app.models import ConsentRecord, PrivacyNotice
from app.repositories import ConsentRecordRepository, PrivacyNoticeRepository
from app.schemas import (
    ConsentRecordCreate,
    ConsentRecordResponse,
    ConsentRecordUpdate,
    PrivacyNoticeCreate,
    PrivacyNoticeResponse,
    PrivacyNoticeUpdate,
    PrivacyPreferenceCenterResponse,
)
from app.security import role_for_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/privacy", tags=["privacy"])


# Dependency injection
async def get_privacy_notice_repo(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PrivacyNoticeRepository:
    """Get privacy notice repository."""
    return PrivacyNoticeRepository(db)


async def get_consent_record_repo(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ConsentRecordRepository:
    """Get consent record repository."""
    return ConsentRecordRepository(db)


async def get_current_user_id(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UUID:
    """Extract current user ID from JWT token."""
    from app.api.routes.auth import get_current_user

    return await get_current_user(db, authorization)


async def require_admin(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UUID:
    """Require admin role."""
    from app.repositories import UserRepository

    user = await UserRepository(db).get_by_id(user_id)
    if not user or role_for_email(user.email) != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return user_id


def _validate_jurisdiction(jurisdiction: str) -> str:
    """Validate jurisdiction against known enum, allow string but warn if unknown."""
    try:
        # Normalize: try to match enum value case-insensitive
        Jurisdiction(jurisdiction)
    except ValueError:
        # Allow custom jurisdictions but log warning - still store normalized
        logger.warning(f"Unknown jurisdiction requested: {jurisdiction}")
    return jurisdiction


# Privacy Notice Management (Admin endpoints)

@router.post(
    "/notices",
    response_model=PrivacyNoticeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_privacy_notice(
    request: PrivacyNoticeCreate,
    repo: Annotated[PrivacyNoticeRepository, Depends(get_privacy_notice_repo)],
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[UUID, Depends(require_admin)],
) -> PrivacyNoticeResponse:
    """Create a new privacy notice version (admin only)."""
    jurisdiction = _validate_jurisdiction(request.jurisdiction)
    existing = await repo.get_by_version_jurisdiction_language(
        request.version, jurisdiction, request.language
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Privacy notice version {request.version} already exists for {jurisdiction}/{request.language}",
        )

    notice = PrivacyNotice(
        version=request.version,
        jurisdiction=jurisdiction,
        title=request.title,
        content=request.content,
        language=request.language,
        effective_date=request.effective_date,
        notice_metadata=request.notice_metadata,
        is_current=False,
    )

    created = await repo.create(notice)
    current = await repo.get_current(jurisdiction, request.language)
    if not current:
        await repo.set_current(created)
    await db.commit()
    await db.refresh(created)
    return PrivacyNoticeResponse.model_validate(created)


@router.get("/notices", response_model=list[PrivacyNoticeResponse])
async def list_privacy_notices(
    jurisdiction: str | None = None,
    repo: Annotated[PrivacyNoticeRepository, Depends(get_privacy_notice_repo)] = None,
) -> list[PrivacyNoticeResponse]:
    """List privacy notices."""
    if jurisdiction:
        jurisdiction = _validate_jurisdiction(jurisdiction)
        notices = await repo.get_by_jurisdiction(jurisdiction)
    else:
        notices = await repo.get_all_current()
    return [PrivacyNoticeResponse.model_validate(n) for n in notices]


@router.get("/notices/current", response_model=dict[str, PrivacyNoticeResponse])
async def get_current_notices(
    repo: Annotated[PrivacyNoticeRepository, Depends(get_privacy_notice_repo)],
) -> dict[str, PrivacyNoticeResponse]:
    """Get current privacy notices for all jurisdictions."""
    notices = await repo.get_all_current()
    return {n.jurisdiction: PrivacyNoticeResponse.model_validate(n) for n in notices}


@router.get("/notices/{version}/{jurisdiction}/{language}", response_model=PrivacyNoticeResponse)
async def get_privacy_notice(
    version: str,
    jurisdiction: str,
    language: str,
    repo: Annotated[PrivacyNoticeRepository, Depends(get_privacy_notice_repo)],
) -> PrivacyNoticeResponse:
    """Get a specific privacy notice by version, jurisdiction, and language."""
    jurisdiction = _validate_jurisdiction(jurisdiction)
    notice = await repo.get_by_version_jurisdiction_language(version, jurisdiction, language)
    if not notice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Privacy notice not found: {version}/{jurisdiction}/{language}",
        )
    return PrivacyNoticeResponse.model_validate(notice)


@router.patch("/notices/{version}/{jurisdiction}/{language}", response_model=PrivacyNoticeResponse)
async def update_privacy_notice(
    version: str,
    jurisdiction: str,
    language: str,
    request: PrivacyNoticeUpdate,
    repo: Annotated[PrivacyNoticeRepository, Depends(get_privacy_notice_repo)],
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[UUID, Depends(require_admin)],
) -> PrivacyNoticeResponse:
    """Update a privacy notice (admin only)."""
    jurisdiction = _validate_jurisdiction(jurisdiction)
    notice = await repo.get_by_version_jurisdiction_language(version, jurisdiction, language)
    if not notice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Privacy notice not found: {version}/{jurisdiction}/{language}",
        )
    if request.title is not None:
        notice.title = request.title
    if request.content is not None:
        notice.content = request.content
    if request.deprecated_date is not None:
        notice.deprecated_date = request.deprecated_date
    if request.is_current is not None:
        if request.is_current:
            await repo.set_current(notice)
        else:
            notice.is_current = False
    if request.notice_metadata is not None:
        notice.notice_metadata = request.notice_metadata
    await db.commit()
    await db.refresh(notice)
    logger.info(f"Privacy notice updated: {version}/{jurisdiction}/{language}")
    return PrivacyNoticeResponse.model_validate(notice)


@router.post("/notices/{version}/{jurisdiction}/{language}/set-current", response_model=PrivacyNoticeResponse)
async def set_notice_current(
    version: str,
    jurisdiction: str,
    language: str,
    repo: Annotated[PrivacyNoticeRepository, Depends(get_privacy_notice_repo)],
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[UUID, Depends(require_admin)],
) -> PrivacyNoticeResponse:
    """Set a privacy notice as the current version (admin only)."""
    jurisdiction = _validate_jurisdiction(jurisdiction)
    notice = await repo.get_by_version_jurisdiction_language(version, jurisdiction, language)
    if not notice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Privacy notice not found: {version}/{jurisdiction}/{language}",
        )
    await repo.set_current(notice)
    await db.commit()
    await db.refresh(notice)
    return PrivacyNoticeResponse.model_validate(notice)


@router.post("/notices/{version}/{jurisdiction}/{language}/deprecate", response_model=PrivacyNoticeResponse)
async def deprecate_notice(
    version: str,
    jurisdiction: str,
    language: str,
    repo: Annotated[PrivacyNoticeRepository, Depends(get_privacy_notice_repo)],
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[UUID, Depends(require_admin)],
) -> PrivacyNoticeResponse:
    """Deprecate a privacy notice (admin only)."""
    jurisdiction = _validate_jurisdiction(jurisdiction)
    notice = await repo.get_by_version_jurisdiction_language(version, jurisdiction, language)
    if not notice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Privacy notice not found: {version}/{jurisdiction}/{language}",
        )
    await repo.deprecate(notice)
    await db.commit()
    await db.refresh(notice)
    return PrivacyNoticeResponse.model_validate(notice)


# Consent Management (User endpoints)

@router.post(
    "/consent",
    response_model=ConsentRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_consent(
    request: ConsentRecordCreate,
    repo: Annotated[ConsentRecordRepository, Depends(get_consent_record_repo)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ConsentRecordResponse:
    """Record user consent."""
    jurisdiction = _validate_jurisdiction(request.jurisdiction)
    existing = await repo.get_by_user_type_jurisdiction(
        request.user_id, request.consent_type, jurisdiction
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Consent record already exists for user {request.user_id}, type {request.consent_type}, jurisdiction {jurisdiction}",
        )
    consent = ConsentRecord(
        user_id=request.user_id,
        consent_type=request.consent_type,
        jurisdiction=jurisdiction,
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
async def list_user_consent(
    user_id: UUID,
    repo: Annotated[ConsentRecordRepository, Depends(get_consent_record_repo)],
) -> list[ConsentRecordResponse]:
    """Get all consent records for a user."""
    records = await repo.get_by_user(user_id)
    return [ConsentRecordResponse.model_validate(r) for r in records]


@router.get("/consent/active", response_model=list[ConsentRecordResponse])
async def get_active_consent(
    user_id: UUID,
    repo: Annotated[ConsentRecordRepository, Depends(get_consent_record_repo)],
) -> list[ConsentRecordResponse]:
    """Get active (granted, not withdrawn) consent records for a user."""
    records = await repo.get_active_by_user(user_id)
    return [ConsentRecordResponse.model_validate(r) for r in records]


@router.get("/consent/{user_id}/{consent_type}/{jurisdiction}", response_model=ConsentRecordResponse)
async def get_consent(
    user_id: UUID,
    consent_type: str,
    jurisdiction: str,
    repo: Annotated[ConsentRecordRepository, Depends(get_consent_record_repo)],
) -> ConsentRecordResponse:
    """Get a specific consent record."""
    jurisdiction = _validate_jurisdiction(jurisdiction)
    record = await repo.get_by_user_type_jurisdiction(user_id, consent_type, jurisdiction)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consent record not found for user {user_id}, type {consent_type}, jurisdiction {jurisdiction}",
        )
    return ConsentRecordResponse.model_validate(record)


@router.patch("/consent/{user_id}/{consent_type}/{jurisdiction}", response_model=ConsentRecordResponse)
async def update_consent(
    user_id: UUID,
    consent_type: str,
    jurisdiction: str,
    request: ConsentRecordUpdate,
    repo: Annotated[ConsentRecordRepository, Depends(get_consent_record_repo)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ConsentRecordResponse:
    """Update a consent record."""
    jurisdiction = _validate_jurisdiction(jurisdiction)
    record = await repo.get_by_user_type_jurisdiction(user_id, consent_type, jurisdiction)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consent record not found for user {user_id}, type {consent_type}, jurisdiction {jurisdiction}",
        )
    if request.granted is not None:
        if request.granted and not record.granted:
            record = await repo.grant(record)
        elif not request.granted and record.granted:
            record = await repo.withdraw(record, request.withdrawal_reason)
    if request.withdrawn_at is not None:
        record.withdrawn_at = request.withdrawn_at
    if request.withdrawal_reason is not None:
        record.withdrawal_reason = request.withdrawal_reason
    if request.consent_metadata is not None:
        record.consent_metadata = request.consent_metadata
    updated = await repo.update(record)
    await db.commit()
    await db.refresh(updated)
    return ConsentRecordResponse.model_validate(updated)


@router.post("/consent/{user_id}/{consent_type}/{jurisdiction}/withdraw", response_model=ConsentRecordResponse)
async def withdraw_consent(
    user_id: UUID,
    consent_type: str,
    jurisdiction: str,
    reason: str | None = None,
    repo: Annotated[ConsentRecordRepository, Depends(get_consent_record_repo)] = None,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
) -> ConsentRecordResponse:
    """Withdraw user consent."""
    jurisdiction = _validate_jurisdiction(jurisdiction)
    record = await repo.get_by_user_type_jurisdiction(user_id, consent_type, jurisdiction)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consent record not found for user {user_id}, type {consent_type}, jurisdiction {jurisdiction}",
        )
    if not record.granted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Consent is already withdrawn",
        )
    withdrawn = await repo.withdraw(record, reason)
    await db.commit()
    await db.refresh(withdrawn)
    return ConsentRecordResponse.model_validate(withdrawn)


@router.post("/consent/{user_id}/{consent_type}/{jurisdiction}/grant", response_model=ConsentRecordResponse)
async def grant_consent(
    user_id: UUID,
    consent_type: str,
    jurisdiction: str,
    repo: Annotated[ConsentRecordRepository, Depends(get_consent_record_repo)] = None,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
) -> ConsentRecordResponse:
    """Grant (or re-grant) user consent."""
    jurisdiction = _validate_jurisdiction(jurisdiction)
    record = await repo.get_by_user_type_jurisdiction(user_id, consent_type, jurisdiction)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consent record not found for user {user_id}, type {consent_type}, jurisdiction {jurisdiction}",
        )
    if record.granted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Consent is already granted",
        )
    granted = await repo.grant(record)
    await db.commit()
    await db.refresh(granted)
    return ConsentRecordResponse.model_validate(granted)


# Privacy Preference Center (User-facing)

@router.get("/preferences", response_model=PrivacyPreferenceCenterResponse)
async def get_privacy_preferences(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    notice_repo: Annotated[PrivacyNoticeRepository, Depends(get_privacy_notice_repo)],
    consent_repo: Annotated[ConsentRecordRepository, Depends(get_consent_record_repo)],
) -> PrivacyPreferenceCenterResponse:
    """Get privacy preference center data for current user."""
    current_notices = await notice_repo.get_all_current()
    notices_dict = {n.jurisdiction: PrivacyNoticeResponse.model_validate(n) for n in current_notices}
    available_consent_types = {
        "marketing": "Marketing communications and promotional offers",
        "analytics": "Analytics and usage tracking for service improvement",
        "profiling": "Automated profiling and personalized recommendations",
        "third_party_sharing": "Sharing data with third-party partners",
        "cookies": "Non-essential cookies and tracking technologies",
        "location": "Precise location data collection",
        "biometric": "Biometric data processing",
    }
    return PrivacyPreferenceCenterResponse(
        user_id=user_id,
        current_notices=notices_dict,
        consent_records=[ConsentRecordResponse.model_validate(r) for r in await consent_repo.get_by_user(user_id)],
        available_consent_types=available_consent_types,
    )
