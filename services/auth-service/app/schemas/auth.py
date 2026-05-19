"""
Pydantic schemas for request/response validation.
Uses Pydantic v2 with proper field validation.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_validator
import re
from uuid import UUID


class TokenResponse(BaseModel):
    """JWT token response with expiration info."""
    
    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="Refresh token for obtaining new access token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(..., description="Access token expiration in seconds")
    
    class Config:
        json_schema_extra = {
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "refresh_token": "refresh_token_hash_here",
                "token_type": "bearer",
                "expires_in": 900
            }
        }


class UserResponse(BaseModel):
    """User data response (without sensitive info)."""
    
    id: UUID = Field(..., description="User ID")
    email: str = Field(..., description="User email")
    email_verified: bool = Field(..., description="Email verification status")
    is_active: bool = Field(..., description="Account status")
    mfa_enabled: bool = Field(..., description="MFA status")
    last_login_at: Optional[datetime] = Field(None, description="Last login timestamp")
    created_at: datetime = Field(..., description="Account creation timestamp")
    
    class Config:
        from_attributes = True


class UserRegisterRequest(BaseModel):
    """User registration request."""
    
    email: EmailStr = Field(..., description="User email")
    password: str = Field(..., min_length=8, description="User password")
    
    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password strength.
        
        Requirements:
        - At least 8 characters
        - At least one uppercase letter
        - At least one digit
        - At least one special character
        """
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", v):
            raise ValueError("Password must contain at least one special character")
        return v


class UserLoginRequest(BaseModel):
    """User login request."""
    
    email: EmailStr = Field(..., description="User email")
    password: str = Field(..., description="User password")
    device_id: Optional[str] = Field(None, description="Device identifier")


class RefreshTokenRequest(BaseModel):
    """Refresh token request."""
    
    refresh_token: str = Field(..., description="Refresh token")
    device_id: Optional[str] = Field(None, description="Device identifier")


class ChangePasswordRequest(BaseModel):
    """Change password request."""
    
    current_password: str = Field(..., description="Current password")
    new_password: str = Field(..., min_length=8, description="New password")
    
    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        """Validate new password strength."""
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", v):
            raise ValueError("Password must contain at least one special character")
        return v


class VerifyEmailRequest(BaseModel):
    """Email verification request."""
    
    email: EmailStr = Field(..., description="Email to verify")
    code: str = Field(..., min_length=6, max_length=6, description="Verification code")


class ErrorResponse(BaseModel):
    """Error response format."""
    
    error: str = Field(..., description="Error code")
    status_code: int = Field(..., description="HTTP status code")
    details: Optional[str] = Field(None, description="Detailed error message")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Error timestamp")
    
    class Config:
        json_schema_extra = {
            "example": {
                "error": "INVALID_CREDENTIALS",
                "status_code": 401,
                "details": "Email or password is incorrect",
                "timestamp": "2024-01-15T10:30:00Z"
            }
        }


class HealthCheckResponse(BaseModel):
    """Health check response."""
    
    status: str = Field(..., description="Service status")
    service: str = Field(..., description="Service name")
    version: str = Field(..., description="Service version")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Check timestamp")


class MFASetupRequest(BaseModel):
    """MFA setup request."""
    
    method: str = Field(..., description="MFA method (totp, sms)")
    

class MFAVerifyRequest(BaseModel):
    """MFA verification request."""
    
    code: str = Field(..., min_length=6, description="Verification code")
