"""
Request and response schemas for Auth Service.
Implements Pydantic models for input validation and API contracts.
"""

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.security import COMMON_PASSWORDS


class TokenResponse(BaseModel):
    """JWT token response.

    Attributes:
        access_token: Short-lived JWT access token
        refresh_token: Long-lived refresh token
        token_type: Token type (always "bearer")
        expires_in: Access token expiration in seconds
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "expires_in": 900,
            }
        }
    )


def validate_password_strength(v: str) -> str:
    """NIST 800-63B style password rules shared by all password fields.

    Length is the primary strength signal; mandatory composition classes are
    deliberately not enforced because they reject strong generated
    passphrases like "correct-horse-battery-staple-42!". A two-class minimum
    blocks single-class passwords and the breach-list check blocks common
    credentials (#164).
    """
    if len(v) < 12:
        raise ValueError("Password must be at least 12 characters")
    classes = sum(
        bool(re.search(pat, v)) for pat in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]")
    )
    if classes < 2:
        raise ValueError("Password must use at least two character types")
    if v.strip().casefold() in COMMON_PASSWORDS:
        raise ValueError("Password is too common")
    return v


class UserRegisterRequest(BaseModel):
    """User registration request.

    Attributes:
        email: User email address
        password: User password
        first_name: User's first name
        last_name: User's last name
    """

    email: EmailStr
    password: str = Field(..., min_length=12, max_length=128)
    first_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate via shared NIST-style rules."""
        return validate_password_strength(v)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "user@example.com",
                "password": "SecurePass123!",
                "first_name": "John",
                "last_name": "Doe",
            }
        }
    )


class UserLoginRequest(BaseModel):
    """User login request.

    Attributes:
        email: User email address
        password: User password
        device_id: Optional device identifier for multi-device tracking
    """

    email: EmailStr
    password: str
    device_id: str | None = Field(None, max_length=255)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "user@example.com",
                "password": "SecurePass123!",
                "device_id": "device-123",
            }
        }
    )


class RefreshTokenRequest(BaseModel):
    """Refresh token request.

    Attributes:
        refresh_token: The refresh token
    """

    refresh_token: str

    model_config = ConfigDict(
        json_schema_extra={"example": {"refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}}
    )


class UserResponse(BaseModel):
    """User profile response.

    Attributes:
        id: User ID
        email: User email
        first_name: User's first name
        last_name: User's last name
        email_verified: Whether email is verified
        last_login_at: Last login timestamp
        created_at: Account creation timestamp
    """

    id: UUID
    email: str
    first_name: str | None
    last_name: str | None
    email_verified: bool
    last_login_at: datetime | None
    created_at: datetime
    role: str = "user"

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "email": "user@example.com",
                "first_name": "John",
                "last_name": "Doe",
                "email_verified": True,
                "last_login_at": "2026-05-12T10:30:00Z",
                "created_at": "2026-05-01T10:30:00Z",
            }
        },
    )


class ChangePasswordRequest(BaseModel):
    """Change password request.

    Attributes:
        current_password: Current password
        new_password: New password
    """

    current_password: str
    new_password: str = Field(..., min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate via shared NIST-style rules."""
        return validate_password_strength(v)


class VerifyEmailRequest(BaseModel):
    """Email verification request.

    Attributes:
        email: Email to verify
        token: Verification token
    """

    email: EmailStr
    token: str


class MFASetupRequest(BaseModel):
    """MFA setup request."""

    method: str = Field(..., description="MFA method (totp, sms)")


class MFAVerifyRequest(BaseModel):
    """MFA verification request."""

    code: str = Field(..., min_length=6, description="Verification code")


class MFALoginVerifyRequest(BaseModel):
    """Complete an MFA-gated login: challenge token + TOTP code."""

    mfa_challenge: str = Field(..., description="Short-lived token from /login")
    code: str = Field(..., min_length=6, description="TOTP verification code")


class ResendVerificationRequest(BaseModel):
    """Request to (re)issue an email verification token."""

    email: EmailStr


class ErrorResponse(BaseModel):
    """Error response.

    Attributes:
        error: Error code
        message: Error description
        details: Optional error details
    """

    error: str
    message: str
    details: dict | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": "INVALID_CREDENTIALS",
                "message": "Invalid email or password",
            }
        }
    )


class HealthCheckResponse(BaseModel):
    """Health check response.

    Attributes:
        status: Service status (healthy/unhealthy)
        service: Service name
        version: Service version
        timestamp: Health check timestamp
        checks: Detailed health check results
    """

    status: str
    service: str
    version: str
    timestamp: datetime
    checks: dict[str, dict]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "healthy",
                "service": "auth-service",
                "version": "1.0.0",
                "timestamp": "2026-05-12T10:30:00Z",
                "checks": {
                    "database": {"status": "healthy", "latency_ms": 2},
                    "redis": {"status": "healthy", "latency_ms": 1},
                },
            }
        }
    )


class PrivacyNoticeCreate(BaseModel):
    """Privacy notice creation request.

    Attributes:
        version: Notice version (semver format: MAJOR.MINOR.PATCH)
        jurisdiction: Jurisdiction this notice applies to
        title: Notice title
        content: Full notice content (markdown supported)
        language: Language code (ISO 639-1)
        effective_date: When this version becomes effective
        notice_metadata: Additional metadata as JSON string
    """

    version: str = Field(
        ..., pattern=r"^\d+\.\d+\.\d+$", description="Semver format: MAJOR.MINOR.PATCH"
    )
    jurisdiction: str = Field(..., min_length=2, max_length=100)
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)
    language: str = Field(
        default="en", pattern=r"^[a-z]{2}$", description="ISO 639-1 language code"
    )
    effective_date: datetime
    notice_metadata: str | None = Field(None, description="Additional metadata as JSON string")


class PrivacyNoticeUpdate(BaseModel):
    """Privacy notice update request.

    Attributes:
        title: Notice title
        content: Full notice content (markdown supported)
        deprecated_date: When this version was deprecated
        is_current: Whether this is the current version
        notice_metadata: Additional metadata as JSON string
    """

    title: str | None = Field(None, min_length=1, max_length=255)
    content: str | None = Field(None, min_length=1)
    deprecated_date: datetime | None = None
    is_current: bool | None = None
    notice_metadata: str | None = None


class PrivacyNoticeResponse(BaseModel):
    """Privacy notice response.

    Attributes:
        id: Unique notice identifier
        version: Notice version
        jurisdiction: Jurisdiction this notice applies to
        title: Notice title
        content: Full notice content
        language: Language code
        effective_date: When this version becomes effective
        deprecated_date: When this version was deprecated
        is_current: Whether this is the current version
        notice_metadata: Additional metadata
        created_at: Record creation timestamp
        updated_at: Record update timestamp
    """

    id: UUID
    version: str
    jurisdiction: str
    title: str
    content: str
    language: str
    effective_date: datetime
    deprecated_date: datetime | None
    is_current: bool
    notice_metadata: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConsentRecordCreate(BaseModel):
    """Consent record creation request.

    Attributes:
        user_id: User ID
        consent_type: Type of consent (marketing, analytics, profiling, etc.)
        jurisdiction: Jurisdiction this consent applies to
        granted: Whether consent was granted
        version: Privacy notice version this consent refers to
        ip_address: IP address when consent was given
        user_agent: User agent string
        consent_metadata: Additional metadata as JSON string
    """

    user_id: UUID
    consent_type: str = Field(..., min_length=1, max_length=100)
    jurisdiction: str = Field(..., min_length=2, max_length=100)
    granted: bool
    version: str = Field(..., min_length=1, max_length=50)
    ip_address: str | None = Field(None, max_length=45)
    user_agent: str | None = None
    consent_metadata: str | None = None


class ConsentRecordUpdate(BaseModel):
    """Consent record update request.

    Attributes:
        granted: Whether consent was granted
        withdrawn_at: When consent was withdrawn
        withdrawal_reason: Reason for withdrawal
        consent_metadata: Additional metadata as JSON string
    """

    granted: bool | None = None
    withdrawn_at: datetime | None = None
    withdrawal_reason: str | None = Field(None, max_length=255)
    consent_metadata: str | None = None


class ConsentRecordResponse(BaseModel):
    """Consent record response.

    Attributes:
        id: Unique consent record identifier
        user_id: User ID
        consent_type: Type of consent
        jurisdiction: Jurisdiction this consent applies to
        granted: Whether consent was granted
        granted_at: When consent was granted
        withdrawn_at: When consent was withdrawn
        withdrawal_reason: Reason for withdrawal
        version: Privacy notice version this consent refers to
        ip_address: IP address when consent was given
        user_agent: User agent string
        consent_metadata: Additional metadata
        created_at: Record creation timestamp
        updated_at: Record update timestamp
    """

    id: UUID
    user_id: UUID
    consent_type: str
    jurisdiction: str
    granted: bool
    granted_at: datetime | None
    withdrawn_at: datetime | None
    withdrawal_reason: str | None
    version: str
    ip_address: str | None
    user_agent: str | None
    consent_metadata: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PrivacyPreferenceCenterResponse(BaseModel):
    """Privacy preference center response for user-facing UI.

    Attributes:
        user_id: User ID
        current_notices: Current privacy notices by jurisdiction
        consent_records: User's consent records
        available_consent_types: Available consent types with descriptions
    """

    user_id: UUID
    current_notices: dict[str, PrivacyNoticeResponse]
    consent_records: list[ConsentRecordResponse]
    available_consent_types: dict[str, str]
