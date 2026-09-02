"""
Database models for privacy compliance.
Implements PrivacyNotice entity with versioning and jurisdiction awareness.
"""

import uuid
from datetime import UTC, datetime
from typing import Sequence

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models import Base, BaseModel


class PrivacyNotice(Base, BaseModel):
    """Privacy notice entity for versioned, jurisdiction-aware notices.

    Attributes:
        id: Unique notice identifier
        version: Notice version (semver format: MAJOR.MINOR.PATCH)
        jurisdiction: Jurisdiction this notice applies to
        title: Notice title
        content: Full notice content (markdown supported)
        language: Language code (ISO 639-1)
        effective_date: When this version becomes effective
        deprecated_date: When this version was deprecated (if applicable)
        is_current: Whether this is the current version for the jurisdiction/language
        metadata: Additional metadata as JSON string
    """

    __tablename__ = "privacy_notices"

    version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )
    jurisdiction: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    language: Mapped[str] = mapped_column(
        String(10),
        default="en",
        nullable=False,
        index=True,
    )
    effective_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    deprecated_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )
    notice_metadata: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("version", "jurisdiction", "language", name="uq_privacy_notice_version_jurisdiction_lang"),
        Index("idx_privacy_notice_current", "jurisdiction", "language", "is_current"),
        Index("idx_privacy_notice_effective", "jurisdiction", "language", "effective_date"),
    )


class ConsentRecord(Base, BaseModel):
    """User consent record for granular, withdrawable consent.

    Attributes:
        id: Unique consent record identifier
        user_id: Reference to user
        consent_type: Type of consent (marketing, analytics, profiling, etc.)
        jurisdiction: Jurisdiction this consent applies to
        granted: Whether consent was granted
        granted_at: When consent was granted
        withdrawn_at: When consent was withdrawn (if applicable)
        withdrawal_reason: Reason for withdrawal
        version: Privacy notice version this consent refers to
        ip_address: IP address when consent was given
        user_agent: User agent string
        metadata: Additional metadata as JSON string
    """

    __tablename__ = "consent_records"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    consent_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    jurisdiction: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    granted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    granted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    withdrawal_reason: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_consent_record_user_type", "user_id", "consent_type"),
        Index("idx_consent_record_jurisdiction", "jurisdiction", "granted"),
        Index("idx_consent_record_withdrawn", "withdrawn_at"),
    )


__all__: Sequence[str] = (
    "PrivacyNotice",
    "ConsentRecord",
)