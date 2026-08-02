"""
Request and response schemas for Auth Service.
Implements Pydantic models for input validation and API contracts.
"""
import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


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

    class Config:
        json_schema_extra = {
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "expires_in": 900,
            }
        }


class UserRegisterRequest(BaseModel):
    """User registration request.
    
    Attributes:
        email: User email address
        password: User password
        first_name: User's first name
        last_name: User's last name
    """

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password strength.
        
        Args:
            v: Password to validate
        
        Returns:
            str: Validated password
        
        Raises:
            ValueError: If password doesn't meet requirements
        """
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain uppercase letter")
        if not re.search(r"[0-9]", v):
            raise ValueError("Password must contain digit")
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", v):
            raise ValueError("Password must contain special character")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "SecurePass123!",
                "first_name": "John",
                "last_name": "Doe",
            }
        }


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

    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "SecurePass123!",
                "device_id": "device-123",
            }
        }


class RefreshTokenRequest(BaseModel):
    """Refresh token request.
    
    Attributes:
        refresh_token: The refresh token
    """

    refresh_token: str

    class Config:
        json_schema_extra = {"example": {"refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}}


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

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "email": "user@example.com",
                "first_name": "John",
                "last_name": "Doe",
                "email_verified": True,
                "last_login_at": "2026-05-12T10:30:00Z",
                "created_at": "2026-05-01T10:30:00Z",
            }
        }


class ChangePasswordRequest(BaseModel):
    """Change password request.
    
    Attributes:
        old_password: Current password
        new_password: New password
    """

    old_password: str
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password strength."""
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain uppercase letter")
        if not re.search(r"[0-9]", v):
            raise ValueError("Password must contain digit")
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", v):
            raise ValueError("Password must contain special character")
        return v


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

    class Config:
        json_schema_extra = {
            "example": {
                "error": "INVALID_CREDENTIALS",
                "message": "Invalid email or password",
            }
        }


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

    class Config:
        json_schema_extra = {
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
