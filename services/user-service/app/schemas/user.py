"""User service request and response schemas using Pydantic v2."""

from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field, EmailStr, field_validator

class DeviceResponse(BaseModel):
    """Device information response."""
    id: UUID
    device_id: str
    device_type: str
    device_name: Optional[str] = None
    os_name: Optional[str] = None
    is_active: bool
    last_seen_at: Optional[datetime] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class UserSessionResponse(BaseModel):
    """User session response."""
    id: UUID
    device_id: UUID
    ip_address: Optional[str] = None
    started_at: datetime
    last_activity_at: datetime
    expires_at: datetime
    is_active: bool
    is_current: bool
    
    class Config:
        from_attributes = True


class UserProfileResponse(BaseModel):
    """Complete user profile response."""
    id: UUID
    user_id: UUID
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    language: str
    timezone: str
    theme: str
    mature_content_allowed: bool
    is_public: bool
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class UpdateProfileRequest(BaseModel):
    """Update user profile request."""
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    display_name: Optional[str] = Field(None, max_length=200)
    bio: Optional[str] = Field(None, max_length=500)
    avatar_url: Optional[str] = Field(None, max_length=500)
    language: Optional[str] = Field(None, pattern="^[a-z]{2}(?:-[A-Z]{2})?$")
    timezone: Optional[str] = None
    theme: Optional[str] = Field(None, pattern="^(light|dark)$")
    mature_content_allowed: Optional[bool] = None


class RegisterDeviceRequest(BaseModel):
    """Register a new device."""
    device_id: str
    device_type: str
    device_name: Optional[str] = None
    os_name: Optional[str] = None
    os_version: Optional[str] = None
    browser_name: Optional[str] = None
    browser_version: Optional[str] = None
    screen_resolution: Optional[str] = None


class ListDevicesResponse(BaseModel):
    """List of user devices."""
    devices: list[DeviceResponse]
    total: int


class ListSessionsResponse(BaseModel):
    """List of user sessions."""
    sessions: list[UserSessionResponse]
    total: int


class WatchHistoryItemResponse(BaseModel):
    """Watch history item response."""
    id: UUID
    content_id: UUID
    content_type: str
    progress_percentage: int
    last_watched_at: datetime
    is_completed: bool
    watch_count: int
    
    class Config:
        from_attributes = True


class ListWatchHistoryResponse(BaseModel):
    """List of watch history items."""
    items: list[WatchHistoryItemResponse]
    total: int


class UserPreferenceResponse(BaseModel):
    """User preferences response."""
    id: UUID
    user_id: UUID
    preferred_quality: str
    preferred_audio_language: str
    autoplay_next_episode: bool
    skip_intro: bool
    skip_credits: bool
    subtitle_language: Optional[str] = None
    
    class Config:
        from_attributes = True


class UpdatePreferenceRequest(BaseModel):
    """Update user preferences."""
    preferred_quality: Optional[str] = Field(None, pattern="^(auto|720p|1080p|4k)$")
    preferred_audio_language: Optional[str] = None
    autoplay_next_episode: Optional[bool] = None
    skip_intro: Optional[bool] = None
    skip_credits: Optional[bool] = None
    subtitle_language: Optional[str] = None
    playback_speed: Optional[str] = Field(None, pattern="^(0.5|0.75|1.0|1.25|1.5|2.0)$")


class ErrorResponse(BaseModel):
    """Standard error response."""
    error: str
    status_code: int
    details: Optional[dict] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
