"""
Database models for Auth Service.
Implements User and RefreshToken entities with audit columns.
"""

import uuid
from datetime import UTC, datetime, timedelta
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


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base (mypy-friendly vs declarative_base())."""


class BaseModel:
    """Base model with common audit columns.

    Provides id + audit timestamps shared across auth-service entities.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )


class User(Base, BaseModel):
    """User entity for authentication.

    Attributes:
        id: Unique user identifier
        email: User email (unique)
        password_hash: Bcrypt hashed password
        first_name: User's first name
        last_name: User's last name
        email_verified: Whether email is verified
        email_verified_at: Timestamp of email verification
        last_login_at: Last successful login timestamp
        last_login_ip: Last login IP address
        login_attempts: Failed login attempts counter
        last_login_attempt_at: Timestamp of the last failed login attempt
        locked_until: Account lock expiration
        is_locked: Property — True when the account is currently locked
        is_active: Soft delete flag
        created_at: Record creation timestamp
        updated_at: Record update timestamp
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Email verification
    email_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    email_verification_code: Mapped[str | None] = mapped_column(
        String(6),
        nullable=True,
    )
    email_verification_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Login tracking
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_login_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    login_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    last_login_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # MFA
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    mfa_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    backup_codes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Indexes for common queries
    __table_args__ = (
        Index("idx_users_email_active", "email", "is_active"),
        Index("idx_users_created_at", "created_at"),
        UniqueConstraint("email", "is_active", name="uq_users_email_active"),
    )

    @property
    def is_locked(self) -> bool:
        """Whether the account is currently locked.

        An account is locked when ``locked_until`` is in the future.
        Comparisons use naive UTC (the column is ``TIMESTAMP WITHOUT TIME
        ZONE``); drop tzinfo before comparing to mirror the column storage
        convention used across the platform.
        """
        if self.locked_until is None:
            return False
        now = datetime.now(UTC).replace(tzinfo=None)
        locked_until = self.locked_until
        if locked_until.tzinfo is not None:
            locked_until = locked_until.replace(tzinfo=None)
        return locked_until > now

    @is_locked.setter
    def is_locked(self, value: bool) -> None:
        """Setter for ``is_locked`` — compatibility for callers that toggle
        the property. Setting ``True`` without a duration locks for the
        default 1h; ``False`` clears the lock immediately.
        """
        if value:
            self.locked_until = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
        else:
            self.locked_until = None


class RefreshToken(Base, BaseModel):
    """Refresh token entity for token rotation.

    Attributes:
        id: Unique token identifier
        user_id: Reference to user
        token: The actual refresh token
        device_id: Device identifier for multi-device tracking
        ip_address: IP address where token was issued
        user_agent: User agent string
        expires_at: Token expiration timestamp
        revoked_at: Token revocation timestamp (if revoked)
        is_active: Soft delete flag
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    device_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index("idx_refresh_tokens_user_expires", "user_id", "expires_at"),
        Index("idx_refresh_tokens_device", "device_id", "user_id"),
    )


class TokenBlacklist(Base, BaseModel):
    """Token blacklist for revoked access tokens.

    Attributes:
        id: Unique entry identifier
        jti: JWT ID (unique token identifier)
        user_id: Reference to user
        revoked_at: Revocation timestamp
        expires_at: When blacklist entry can be purged
    """

    __tablename__ = "token_blacklist"

    token_hash: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    __table_args__ = (Index("idx_token_blacklist_user_expires", "user_id", "expires_at"),)


class LoginAudit(Base, BaseModel):
    """Audit log for login attempts.

    Attributes:
        id: Unique audit entry identifier
        user_id: Reference to user (nullable for failed attempts)
        email: Email attempted to login
        success: Whether login was successful
        ip_address: IP address of login attempt
        user_agent: User agent string
        failure_reason: Reason for login failure
    """

    __tablename__ = "login_audit"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index("idx_login_audit_user_created", "user_id", "created_at"),
        Index("idx_login_audit_email_created", "email", "created_at"),
    )


__all__: Sequence[str] = (
    "Base",
    "BaseModel",
    "User",
    "RefreshToken",
    "TokenBlacklist",
    "LoginAudit",
)
