"""
Database models for Auth Service.
Implements User and RefreshToken entities with audit columns.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class BaseModel:
    """Base model with common audit columns."""

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    created_at = Column(
        DateTime,
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
    is_active = Column(Boolean, default=True, nullable=False)


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
        last_login_at: Last login timestamp
        last_login_ip: Last login IP address
        login_attempts: Failed login attempts counter
        locked_until: Account lock expiration
        is_active: Soft delete flag
        created_at: Record creation timestamp
        updated_at: Record update timestamp
    """

    __tablename__ = "users"

    email = Column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    password_hash = Column(String(255), nullable=False)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)

    # Email verification
    email_verified = Column(Boolean, default=False, nullable=False)
    email_verified_at = Column(DateTime, nullable=True)

    # Login tracking
    last_login_at = Column(DateTime, nullable=True)
    last_login_ip = Column(String(45), nullable=True)  # Supports IPv6
    login_attempts = Column(Integer, default=0, nullable=False)
    locked_until = Column(DateTime, nullable=True)

    # MFA
    mfa_enabled = Column(Boolean, default=False, nullable=False)
    mfa_secret = Column(String(255), nullable=True)
    backup_codes = Column(Text, nullable=True)  # JSON array of backup codes

    # Indexes for common queries
    __table_args__ = (
        Index("idx_users_email_active", "email", "is_active"),
        Index("idx_users_created_at", "created_at"),
        UniqueConstraint("email", "is_active", name="uq_users_email_active"),
    )


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

    user_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    token_hash = Column(String(255), unique=True, nullable=False, index=True)
    device_id = Column(String(255), nullable=True, index=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True)

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

    token_hash = Column(String(255), unique=True, nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    revoked_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)

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

    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    email = Column(String(255), nullable=True, index=True)
    status = Column(String(50), nullable=False, index=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    failure_reason = Column(String(255), nullable=True)

    __table_args__ = (
        Index("idx_login_audit_user_created", "user_id", "created_at"),
        Index("idx_login_audit_email_created", "email", "created_at"),
    )
