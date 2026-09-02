"""
Repository layer for privacy compliance data access operations.
Uses SQLAlchemy 2.0 async patterns with proper error handling.
"""

import logging
from datetime import UTC, datetime
from typing import Sequence
from uuid import UUID

from app.models import ConsentRecord, PrivacyNotice
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class PrivacyNoticeRepository:
    """Repository for privacy notice data access operations."""

    def __init__(self, db: AsyncSession):
        """Initialize repository with database session.

        Args:
            db: Async SQLAlchemy session
        """
        self.db = db

    async def create(self, notice: PrivacyNotice) -> PrivacyNotice:
        """Create a new privacy notice.

        Args:
            notice: PrivacyNotice instance to create

        Returns:
            Created PrivacyNotice instance
        """
        self.db.add(notice)
        await self.db.flush()
        logger.info(f"Privacy notice created: {notice.version} for {notice.jurisdiction}")
        return notice

    async def get_by_version_jurisdiction_language(
        self, version: str, jurisdiction: str, language: str
    ) -> PrivacyNotice | None:
        """Get privacy notice by version, jurisdiction, and language.

        Args:
            version: Notice version
            jurisdiction: Jurisdiction
            language: Language code

        Returns:
            PrivacyNotice instance or None if not found
        """
        stmt = select(PrivacyNotice).where(
            PrivacyNotice.version == version,
            PrivacyNotice.jurisdiction == jurisdiction,
            PrivacyNotice.language == language,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_current(self, jurisdiction: str, language: str = "en") -> PrivacyNotice | None:
        """Get current privacy notice for jurisdiction and language.

        Args:
            jurisdiction: Jurisdiction
            language: Language code (default: en)

        Returns:
            Current PrivacyNotice instance or None if not found
        """
        stmt = select(PrivacyNotice).where(
            PrivacyNotice.jurisdiction == jurisdiction,
            PrivacyNotice.language == language,
            PrivacyNotice.is_current.is_(True),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_current(self) -> Sequence[PrivacyNotice]:
        """Get all current privacy notices.

        Returns:
            List of current PrivacyNotice instances
        """
        stmt = select(PrivacyNotice).where(PrivacyNotice.is_current.is_(True))
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_by_jurisdiction(self, jurisdiction: str) -> Sequence[PrivacyNotice]:
        """Get all privacy notices for a jurisdiction.

        Args:
            jurisdiction: Jurisdiction

        Returns:
            List of PrivacyNotice instances
        """
        stmt = select(PrivacyNotice).where(PrivacyNotice.jurisdiction == jurisdiction).order_by(
            PrivacyNotice.effective_date.desc()
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def set_current(self, notice: PrivacyNotice) -> None:
        """Set a notice as current for its jurisdiction/language, deprecating others.

        Args:
            notice: PrivacyNotice to set as current
        """
        # First, deprecate all current notices for this jurisdiction/language
        stmt = select(PrivacyNotice).where(
            PrivacyNotice.jurisdiction == notice.jurisdiction,
            PrivacyNotice.language == notice.language,
            PrivacyNotice.is_current.is_(True),
        )
        result = await self.db.execute(stmt)
        current_notices = result.scalars().all()

        for current in current_notices:
            if current.id != notice.id:
                current.is_current = False
                current.deprecated_date = datetime.now(UTC)

        # Set the new notice as current
        notice.is_current = True
        notice.deprecated_date = None

        await self.db.flush()
        logger.info(f"Privacy notice set as current: {notice.version} for {notice.jurisdiction}")

    async def deprecate(self, notice: PrivacyNotice) -> None:
        """Deprecate a privacy notice.

        Args:
            notice: PrivacyNotice to deprecate
        """
        notice.is_current = False
        notice.deprecated_date = datetime.now(UTC)
        await self.db.flush()
        logger.info(f"Privacy notice deprecated: {notice.version} for {notice.jurisdiction}")


class ConsentRecordRepository:
    """Repository for consent record data access operations."""

    def __init__(self, db: AsyncSession):
        """Initialize repository with database session.

        Args:
            db: Async SQLAlchemy session
        """
        self.db = db

    async def create(self, consent: ConsentRecord) -> ConsentRecord:
        """Create a new consent record.

        Args:
            consent: ConsentRecord instance to create

        Returns:
            Created ConsentRecord instance
        """
        self.db.add(consent)
        await self.db.flush()
        logger.info(f"Consent record created: {consent.consent_type} for user {consent.user_id}")
        return consent

    async def get_by_user_type_jurisdiction(
        self, user_id: UUID, consent_type: str, jurisdiction: str
    ) -> ConsentRecord | None:
        """Get consent record by user, type, and jurisdiction.

        Args:
            user_id: User ID
            consent_type: Consent type
            jurisdiction: Jurisdiction

        Returns:
            ConsentRecord instance or None if not found
        """
        stmt = select(ConsentRecord).where(
            ConsentRecord.user_id == user_id,
            ConsentRecord.consent_type == consent_type,
            ConsentRecord.jurisdiction == jurisdiction,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user(self, user_id: UUID) -> Sequence[ConsentRecord]:
        """Get all consent records for a user.

        Args:
            user_id: User ID

        Returns:
            List of ConsentRecord instances
        """
        stmt = select(ConsentRecord).where(ConsentRecord.user_id == user_id).order_by(
            ConsentRecord.created_at.desc()
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_active_by_user(self, user_id: UUID) -> Sequence[ConsentRecord]:
        """Get active (granted, not withdrawn) consent records for a user.

        Args:
            user_id: User ID

        Returns:
            List of active ConsentRecord instances
        """
        stmt = select(ConsentRecord).where(
            ConsentRecord.user_id == user_id,
            ConsentRecord.granted.is_(True),
            ConsentRecord.withdrawn_at.is_(None),
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def update(self, consent: ConsentRecord) -> ConsentRecord:
        """Update a consent record.

        Args:
            consent: ConsentRecord to update

        Returns:
            Updated ConsentRecord instance
        """
        await self.db.flush()
        logger.info(f"Consent record updated: {consent.consent_type} for user {consent.user_id}")
        return consent

    async def withdraw(self, consent: ConsentRecord, reason: str | None = None) -> ConsentRecord:
        """Withdraw consent.

        Args:
            consent: ConsentRecord to withdraw
            reason: Reason for withdrawal

        Returns:
            Updated ConsentRecord instance
        """
        consent.granted = False
        consent.withdrawn_at = datetime.now(UTC)
        consent.withdrawal_reason = reason
        await self.db.flush()
        logger.info(f"Consent withdrawn: {consent.consent_type} for user {consent.user_id}")
        return consent

    async def grant(self, consent: ConsentRecord) -> ConsentRecord:
        """Grant consent (re-grant after withdrawal).

        Args:
            consent: ConsentRecord to grant

        Returns:
            Updated ConsentRecord instance
        """
        consent.granted = True
        consent.granted_at = datetime.now(UTC)
        consent.withdrawn_at = None
        consent.withdrawal_reason = None
        await self.db.flush()
        logger.info(f"Consent granted: {consent.consent_type} for user {consent.user_id}")
        return consent