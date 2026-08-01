"""Request and response schemas for User Service."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


class UserProfileUpdateRequest(BaseModel):
    """Update user profile request."""

    avatar_url: str | None = Field(None, max_length=2048)
    bio: str | None = Field(None, max_length=500)
    phone_number: str | None = Field(None, max_length=20)
    date_of_birth: datetime | None = None
    country: str | None = Field(None, max_length=2)  # ISO country code
    language: str | None = Field(None, max_length=5)
    timezone: str | None = Field(None, max_length=50)
    public_profile: bool | None = None
    newsletter_subscribed: bool | None = None
    marketing_emails: bool | None = None

    class Config:
        json_schema_extra = {
            "example": {
                "avatar_url": "https://example.com/avatar.jpg",
                "bio": "Movie enthusiast",
                "phone_number": "+1234567890",
                "country": "US",
                "language": "en-US",
                "public_profile": True,
            }
        }


class UserProfileResponse(BaseModel):
    """User profile response."""

    id: UUID
    user_id: UUID
    avatar_url: str | None
    bio: str | None
    phone_number: str | None
    date_of_birth: datetime | None
    country: str | None
    language: str
    timezone: str | None
    public_profile: bool
    newsletter_subscribed: bool
    marketing_emails: bool
    completed_onboarding: bool
    profile_completeness: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserDeviceRegisterRequest(BaseModel):
    """Register new device request."""

    device_id: str = Field(..., max_length=255)
    device_name: str = Field(..., max_length=255)
    device_type: str = Field(..., pattern="^(web|ios|android|smart_tv)$")
    os_name: str | None = Field(None, max_length=50)
    os_version: str | None = Field(None, max_length=50)
    browser_name: str | None = Field(None, max_length=50)
    browser_version: str | None = Field(None, max_length=50)
    user_agent: str | None = None

    class Config:
        json_schema_extra = {
            "example": {
                "device_id": "device-uuid-123",
                "device_name": "Chrome on MacBook",
                "device_type": "web",
                "os_name": "macOS",
                "browser_name": "Chrome",
            }
        }


class UserDeviceUpdateRequest(BaseModel):
    """Update device request."""

    device_name: str | None = Field(None, max_length=255)
    is_trusted: bool | None = None
    can_stream: bool | None = None
    can_download: bool | None = None

    class Config:
        json_schema_extra = {
            "example": {
                "device_name": "Chrome on MacBook Pro",
                "is_trusted": True,
            }
        }


class UserDeviceResponse(BaseModel):
    """Device response."""

    id: UUID
    device_id: str
    device_name: str
    device_type: str
    os_name: str | None
    os_version: str | None
    browser_name: str | None
    browser_version: str | None
    ip_address: str | None
    is_active: bool
    is_trusted: bool
    can_stream: bool
    can_download: bool
    last_active_at: datetime | None
    registration_date: datetime
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserPreferenceUpdateRequest(BaseModel):
    """Update user preferences request."""

    theme: str | None = Field(None, pattern="^(dark|light|auto)$")
    language: str | None = Field(None, max_length=5)
    subtitle_language: str | None = Field(None, max_length=5)
    subtitle_size: str | None = Field(None, pattern="^(small|medium|large)$")
    closed_captions: bool | None = None
    autoplay: bool | None = None
    autoplay_next_episode: bool | None = None
    default_video_quality: str | None = Field(None, pattern="^(adaptive|720p|1080p|4k)$")
    default_audio_language: str | None = Field(None, max_length=5)
    content_rating: str | None = Field(None, pattern="^(G|PG|PG-13|R|NC-17)$")
    allow_explicit_content: bool | None = None
    share_viewing_activity: bool | None = None
    allow_recommendations: bool | None = None
    data_collection: bool | None = None
    email_new_content: bool | None = None
    email_recommendations: bool | None = None
    push_notifications: bool | None = None

    class Config:
        json_schema_extra = {
            "example": {
                "theme": "dark",
                "language": "en-US",
                "autoplay": True,
                "default_video_quality": "1080p",
                "push_notifications": True,
            }
        }


class UserPreferenceResponse(BaseModel):
    """User preferences response."""

    id: UUID
    user_id: UUID
    theme: str
    language: str
    subtitle_language: str
    subtitle_size: str
    closed_captions: bool
    autoplay: bool
    autoplay_next_episode: bool
    default_video_quality: str
    default_audio_language: str
    content_rating: str
    allow_explicit_content: bool
    share_viewing_activity: bool
    allow_recommendations: bool
    data_collection: bool
    email_new_content: bool
    email_recommendations: bool
    push_notifications: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserSubscriptionProfileResponse(BaseModel):
    """User subscription profile response."""

    id: UUID
    user_id: UUID
    subscription_tier: str
    subscription_status: str
    max_concurrent_streams: int
    can_download: bool
    can_use_4k: bool
    ad_free: bool
    current_period_start: datetime | None
    current_period_end: datetime | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserProfileCompleteResponse(BaseModel):
    """Complete user profile with all related data."""

    profile: UserProfileResponse
    devices: list[UserDeviceResponse]
    preferences: UserPreferenceResponse
    subscription: UserSubscriptionProfileResponse

    class Config:
        from_attributes = True


class ErrorResponse(BaseModel):
    """Error response."""

    error: str
    message: str
    details: dict | None = None


class HealthCheckResponse(BaseModel):
    """Health check response."""

    status: str
    service: str
    version: str
    timestamp: datetime
    checks: dict[str, dict] | None = None
