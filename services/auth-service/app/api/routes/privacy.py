"""
Privacy compliance API routes.
Implements privacy notice management, consent collection, and preference center.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

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
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

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
    authorization: Annotated[str | None, None] = None,
) -> UUID:
    """Extract current user ID from JWT token.

    Args:
        authorization: Authorization header
        db: Database session

    Returns:
        User ID

    Raises:
        HTTPException: If token invalid or missing
    """
    from app.api.routes.auth import get_current_user

    return await get_current_user(db, authorization)


# Privacy Notice Management (Admin endpoints)


@router.post(
    "/notices",
    response_model=PrivacyNoticeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_privacy_notice(
    request: PrivacyNoticeCreate,
    repo: Annotated[PrivacyNoticeRepository, Depends(get_privacy_notice_repo)],
) -> PrivacyNoticeResponse:
    """Create a new privacy notice version.

    Args:
        request: Privacy notice creation request
        repo: Privacy notice repository

    Returns:
        Created privacy notice

    Raises:
        HTTPException: If version already exists for jurisdiction/language
    """
    # Check if version already exists
    existing = await repo.get_by_version_jurisdiction_language(
        request.version, request.jurisdiction, request.language
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Privacy notice version {request.version} "
                f"already exists for {request.jurisdiction}/{request.language}",
            ),
        )

    # Create notice
    notice = PrivacyNotice(
        version=request.version,
        jurisdiction=request.jurisdiction,
        title=request.title,
        content=request.content,
        language=request.language,
        effective_date=request.effective_date,
        metadata=request.metadata,
        is_current=False,  # Will be set to current if requested
    )

    created = await repo.create(notice)

    # If this is the first notice for this jurisdiction/language, set as current
    current = await repo.get_current(request.jurisdiction, request.language)
    if not current:
        await repo.set_current(created)

    return PrivacyNoticeResponse.model_validate(created)


@router.get("/notices", response_model=list[PrivacyNoticeResponse])
async def list_privacy_notices(
    repo: Annotated[PrivacyNoticeRepository, Depends(get_privacy_notice_repo)],
    jurisdiction: str | None = None,
) -> list[PrivacyNoticeResponse]:
    """List privacy notices.

    Args:
        jurisdiction: Optional jurisdiction filter
        repo: Privacy notice repository

    Returns:
        List of privacy notices
    """
    if jurisdiction:
        notices = await repo.get_by_jurisdiction(jurisdiction)
    else:
        notices = await repo.get_all_current()

    return [PrivacyNoticeResponse.model_validate(n) for n in notices]


@router.get("/notices/current", response_model=dict[str, PrivacyNoticeResponse])
async def get_current_notices(
    repo: Annotated[PrivacyNoticeRepository, Depends(get_privacy_notice_repo)],
) -> dict[str, PrivacyNoticeResponse]:
    """Get current privacy notices for all jurisdictions.

    Args:
        repo: Privacy notice repository

    Returns:
        Dict mapping jurisdiction to current notice
    """
    notices = await repo.get_all_current()
    return {n.jurisdiction: PrivacyNoticeResponse.model_validate(n) for n in notices}


@router.get("/notices/{version}/{jurisdiction}/{language}", response_model=PrivacyNoticeResponse)
async def get_privacy_notice(
    version: str,
    jurisdiction: str,
    language: str,
    repo: Annotated[PrivacyNoticeRepository, Depends(get_privacy_notice_repo)],
) -> PrivacyNoticeResponse:
    """Get a specific privacy notice by version, jurisdiction, and language.

    Args:
        version: Notice version
        jurisdiction: Jurisdiction
        language: Language code
        repo: Privacy notice repository

    Returns:
        Privacy notice

    Raises:
        HTTPException: If notice not found
    """
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
) -> PrivacyNoticeResponse:
    """Update a privacy notice.

    Args:
        version: Notice version
        jurisdiction: Jurisdiction
        language: Language code
        request: Update request
        repo: Privacy notice repository

    Returns:
        Updated privacy notice

    Raises:
        HTTPException: If notice not found
    """
    notice = await repo.get_by_version_jurisdiction_language(version, jurisdiction, language)
    if not notice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Privacy notice not found: {version}/{jurisdiction}/{language}",
        )

    # Update fields
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
    if request.metadata is not None:
        notice.metadata = request.metadata

    await repo.db.flush()
    logger.info(f"Privacy notice updated: {version}/{jurisdiction}/{language}")

    return PrivacyNoticeResponse.model_validate(notice)


@router.post(
    "/notices/{version}/{jurisdiction}/{language}/set-current", response_model=PrivacyNoticeResponse
)
async def set_notice_current(
    version: str,
    jurisdiction: str,
    language: str,
    repo: Annotated[PrivacyNoticeRepository, Depends(get_privacy_notice_repo)],
) -> PrivacyNoticeResponse:
    """Set a privacy notice as the current version for its jurisdiction/language.

    Args:
        version: Notice version
        jurisdiction: Jurisdiction
        language: Language code
        repo: Privacy notice repository

    Returns:
        Updated privacy notice

    Raises:
        HTTPException: If notice not found
    """
    notice = await repo.get_by_version_jurisdiction_language(version, jurisdiction, language)
    if not notice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Privacy notice not found: {version}/{jurisdiction}/{language}",
        )

    await repo.set_current(notice)
    return PrivacyNoticeResponse.model_validate(notice)


@router.post(
    "/notices/{version}/{jurisdiction}/{language}/deprecate", response_model=PrivacyNoticeResponse
)
async def deprecate_notice(
    version: str,
    jurisdiction: str,
    language: str,
    repo: Annotated[PrivacyNoticeRepository, Depends(get_privacy_notice_repo)],
) -> PrivacyNoticeResponse:
    """Deprecate a privacy notice.

    Args:
        version: Notice version
        jurisdiction: Jurisdiction
        language: Language code
        repo: Privacy notice repository

    Returns:
        Deprecated privacy notice

    Raises:
        HTTPException: If notice not found
    """
    notice = await repo.get_by_version_jurisdiction_language(version, jurisdiction, language)
    if not notice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Privacy notice not found: {version}/{jurisdiction}/{language}",
        )

    await repo.deprecate(notice)
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
) -> ConsentRecordResponse:
    """Record user consent.

    Args:
        request: Consent creation request
        repo: Consent record repository

    Returns:
        Created consent record

    Raises:
        HTTPException: If consent already exists for user/type/jurisdiction
    """
    # Check if consent already exists
    existing = await repo.get_by_user_type_jurisdiction(
        request.user_id, request.consent_type, request.jurisdiction
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Consent record already exists for user {request.user_id}, "
                f"type {request.consent_type}, jurisdiction {request.jurisdiction}",
            ),
        )

    consent = ConsentRecord(
        user_id=request.user_id,
        consent_type=request.consent_type,
        jurisdiction=request.jurisdiction,
        granted=request.granted,
        granted_at=datetime.now(UTC) if request.granted else None,
        version=request.version,
        ip_address=request.ip_address,
        user_agent=request.user_agent,
        metadata=request.metadata,
    )

    created = await repo.create(consent)
    return ConsentRecordResponse.model_validate(created)


@router.get("/consent", response_model=list[ConsentRecordResponse])
async def list_user_consent(
    user_id: UUID,
    repo: Annotated[ConsentRecordRepository, Depends(get_consent_record_repo)],
) -> list[ConsentRecordResponse]:
    """Get all consent records for a user.

    Args:
        user_id: User ID
        repo: Consent record repository

    Returns:
        List of consent records
    """
    records = await repo.get_by_user(user_id)
    return [ConsentRecordResponse.model_validate(r) for r in records]


@router.get("/consent/active", response_model=list[ConsentRecordResponse])
async def get_active_consent(
    user_id: UUID,
    repo: Annotated[ConsentRecordRepository, Depends(get_consent_record_repo)],
) -> list[ConsentRecordResponse]:
    """Get active (granted, not withdrawn) consent records for a user.

    Args:
        user_id: User ID
        repo: Consent record repository

    Returns:
        List of active consent records
    """
    records = await repo.get_active_by_user(user_id)
    return [ConsentRecordResponse.model_validate(r) for r in records]


@router.get(
    "/consent/{user_id}/{consent_type}/{jurisdiction}", response_model=ConsentRecordResponse
)
async def get_consent(
    user_id: UUID,
    consent_type: str,
    jurisdiction: str,
    repo: Annotated[ConsentRecordRepository, Depends(get_consent_record_repo)],
) -> ConsentRecordResponse:
    """Get a specific consent record.

    Args:
        user_id: User ID
        consent_type: Consent type
        jurisdiction: Jurisdiction
        repo: Consent record repository

    Returns:
        Consent record

    Raises:
        HTTPException: If consent record not found
    """
    record = await repo.get_by_user_type_jurisdiction(user_id, consent_type, jurisdiction)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consent record not found for user {user_id}, type {consent_type}, jurisdiction {jurisdiction}",
        )

    return ConsentRecordResponse.model_validate(record)


@router.patch(
    "/consent/{user_id}/{consent_type}/{jurisdiction}", response_model=ConsentRecordResponse
)
async def update_consent(
    user_id: UUID,
    consent_type: str,
    jurisdiction: str,
    request: ConsentRecordUpdate,
    repo: Annotated[ConsentRecordRepository, Depends(get_consent_record_repo)],
) -> ConsentRecordResponse:
    """Update a consent record.

    Args:
        user_id: User ID
        consent_type: Consent type
        jurisdiction: Jurisdiction
        request: Update request
        repo: Consent record repository

    Returns:
        Updated consent record

    Raises:
        HTTPException: If consent record not found
    """
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
    if request.metadata is not None:
        record.metadata = request.metadata

    updated = await repo.update(record)
    return ConsentRecordResponse.model_validate(updated)


@router.post(
    "/consent/{user_id}/{consent_type}/{jurisdiction}/withdraw",
    response_model=ConsentRecordResponse,
)
async def withdraw_consent(
    user_id: UUID,
    consent_type: str,
    jurisdiction: str,
    repo: Annotated[ConsentRecordRepository, Depends(get_consent_record_repo)],
    reason: str | None = None,
) -> ConsentRecordResponse:
    """Withdraw user consent.

    Args:
        user_id: User ID
        consent_type: Consent type
        jurisdiction: Jurisdiction
        reason: Optional withdrawal reason
        repo: Consent record repository

    Returns:
        Updated consent record

    Raises:
        HTTPException: If consent record not found or already withdrawn
    """
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
    return ConsentRecordResponse.model_validate(withdrawn)


@router.post(
    "/consent/{user_id}/{consent_type}/{jurisdiction}/grant", response_model=ConsentRecordResponse
)
async def grant_consent(
    user_id: UUID,
    consent_type: str,
    jurisdiction: str,
    repo: Annotated[ConsentRecordRepository, Depends(get_consent_record_repo)],
) -> ConsentRecordResponse:
    """Grant (or re-grant) user consent.

    Args:
        user_id: User ID
        consent_type: Consent type
        jurisdiction: Jurisdiction
        repo: Consent record repository

    Returns:
        Updated consent record

    Raises:
        HTTPException: If consent record not found or already granted
    """
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
    return ConsentRecordResponse.model_validate(granted)


# Privacy Preference Center (User-facing)


@router.get("/preferences", response_model=PrivacyPreferenceCenterResponse)
async def get_privacy_preferences(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    notice_repo: Annotated[PrivacyNoticeRepository, Depends(get_privacy_notice_repo)],
    consent_repo: Annotated[ConsentRecordRepository, Depends(get_consent_record_repo)],
) -> PrivacyPreferenceCenterResponse:
    """Get privacy preference center data for current user.

    Args:
        user_id: Current user ID
        notice_repo: Privacy notice repository
        consent_repo: Consent record repository

    Returns:
        Privacy preference center data
    """
    # Get current notices for all jurisdictions
    current_notices = await notice_repo.get_all_current()
    notices_dict = {
        n.jurisdiction: PrivacyNoticeResponse.model_validate(n) for n in current_notices
    }

    # Get user's consent records
    consent_records = await consent_repo.get_by_user(user_id)
    consent_list = [ConsentRecordResponse.model_validate(r) for r in consent_records]

    # Available consent types with descriptions
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
        current_notices={
            k: PrivacyNoticeResponse.model_validate(v) for k, v in notices_dict.items()
        },
        consent_records=consent_list,
        available_consent_types=available_consent_types,
    )
