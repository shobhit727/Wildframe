"""Request and response schemas for User Service."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_validator
from uuid import UUID


class UserProfileUpdateRequest(BaseModel):
    """Update user profile request."""

    avatar_url: Optional[str] = Field(None, max_length=2048)
    bio: Optional[str] = Field(None, max_length=500)
    phone_number: Optional[str] = Field(None, max_length=20)
    date_of_birth: Optional[datetime] = None
    country: Optional[str] = Field(None, max_length=2)  # ISO country code
    language: Optional[str] = Field(None, max_length=5)
    timezone: Optional[str] = Field(None, max_length=50)
    public_profile: Optional[bool] = None
    newsletter_subscribed: Optional[bool] = None
    marketing_emails: Optional[bool] = None

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
    avatar_url: Optional[str]
    bio: Optional[str]
    phone_number: Optional[str]
    date_of_birth: Optional[datetime]
    country: Optional[str]
    language: str
    timezone: Optional[str]
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
    device_type: str = Field(..., regex="^(web|ios|android|smart_tv)$")
    os_name: Optional[str] = Field(None, max_length=50)
    os_version: Optional[str] = Field(None, max_length=50)
    browser_name: Optional[str] = Field(None, max_length=50)
    browser_version: Optional[str] = Field(None, max_length=50)
    user_agent: Optional[str] = None

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

    device_name: Optional[str] = Field(None, max_length=255)
    is_trusted: Optional[bool] = None
    can_stream: Optional[bool] = None
    can_download: Optional[bool] = None

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
    os_name: Optional[str]
    os_version: Optional[str]
    browser_name: Optional[str]
    browser_version: Optional[str]
    ip_address: Optional[str]
    is_active: bool
    is_trusted: bool
    can_stream: bool
    can_download: bool
    last_active_at: Optional[datetime]
    registration_date: datetime
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserPreferenceUpdateRequest(BaseModel):
    """Update user preferences request."""

    theme: Optional[str] = Field(None, regex="^(dark|light|auto)$")
    language: Optional[str] = Field(None, max_length=5)
    subtitle_language: Optional[str] = Field(None, max_length=5)
    subtitle_size: Optional[str] = Field(None, regex="^(small|medium|large)$")
    closed_captions: Optional[bool] = None
    autoplay: Optional[bool] = None
    autoplay_next_episode: Optional[bool] = None
    default_video_quality: Optional[str] = Field(None, regex="^(adaptive|720p|1080p|4k)$")
    default_audio_language: Optional[str] = Field(None, max_length=5)
    content_rating: Optional[str] = Field(None, regex="^(G|PG|PG-13|R|NC-17)$")
    allow_explicit_content: Optional[bool] = None
    share_viewing_activity: Optional[bool] = None
    allow_recommendations: Optional[bool] = None
    data_collection: Optional[bool] = None
    email_new_content: Optional[bool] = None
    email_recommendations: Optional[bool] = None
    push_notifications: Optional[bool] = None

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
    current_period_start: Optional[datetime]
    current_period_end: Optional[datetime]
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
    details: Optional[dict] = None


class HealthCheckResponse(BaseModel):
    """Health check response."""

    status: str
    service: str
    version: str
    timestamp: datetime
    checks: Optional[dict[str, dict]] = None
