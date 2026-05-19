"""
User profile and preference data models using SQLAlchemy 2.0.
Includes user profiles, devices, sessions, and preferences.
"""

from datetime import datetime
from typing import Optional, List
from sqlalchemy import Column, String, DateTime, Boolean, Integer, Index, Text, ForeignKey, Enum, JSON
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.dialects.postgresql import UUID
import uuid
import enum

Base = declarative_base()


class UserProfile(Base):
    """Extended user profile information."""
    
    __tablename__ = "user_profiles"
    
    # Primary Key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), unique=True, nullable=False, index=True)
    
    # Profile Information
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    display_name = Column(String(200), nullable=True)
    bio = Column(Text, nullable=True)
    avatar_url = Column(String(500), nullable=True)
    
    # Account Settings
    language = Column(String(10), default="en", nullable=False)
    timezone = Column(String(50), default="UTC", nullable=False)
    theme = Column(String(20), default="light", nullable=False)  # light, dark
    
    # Content Preferences
    mature_content_allowed = Column(Boolean, default=False, nullable=False)
    preferred_subtitle_language = Column(String(10), nullable=True)
    auto_play = Column(Boolean, default=True, nullable=False)
    
    # Privacy Settings
    is_public = Column(Boolean, default=False, nullable=False)
    allow_recommendations = Column(Boolean, default=True, nullable=False)
    
    # Audit
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    
    __table_args__ = (
        Index("ix_user_profiles_user_id_deleted_at", user_id, deleted_at),
    )


class DeviceType(str, enum.Enum):
    """Device type enumeration."""
    WEB = "web"
    MOBILE_IOS = "mobile_ios"
    MOBILE_ANDROID = "mobile_android"
    TABLET_IOS = "tablet_ios"
    TABLET_ANDROID = "tablet_android"
    TV = "tv"
    SMART_TV = "smart_tv"
    OTHER = "other"


class Device(Base):
    """User device information for multi-device tracking."""
    
    __tablename__ = "devices"
    
    # Primary Key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    
    # Device Information
    device_id = Column(String(255), unique=True, nullable=False)  # Generated client-side or server-side
    device_type = Column(Enum(DeviceType), default=DeviceType.WEB, nullable=False)
    device_name = Column(String(255), nullable=True)  # "iPhone 12", "Chrome on Mac", etc.
    
    # System Information
    os_name = Column(String(50), nullable=True)  # iOS, Android, Windows, macOS, Linux
    os_version = Column(String(50), nullable=True)
    browser_name = Column(String(50), nullable=True)  # Chrome, Firefox, Safari, etc.
    browser_version = Column(String(50), nullable=True)
    
    # Hardware
    screen_resolution = Column(String(20), nullable=True)  # "1920x1080"
    device_model = Column(String(100), nullable=True)
    
    # Network
    ip_address = Column(String(45), nullable=True)  # IPv4 or IPv6
    user_agent = Column(Text, nullable=True)
    
    # Status
    is_active = Column(Boolean, default=True, nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    
    # Audit
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    
    __table_args__ = (
        Index("ix_devices_user_id_is_active", user_id, is_active),
        Index("ix_devices_user_id_deleted_at", user_id, deleted_at),
    )


class UserSession(Base):
    """User session tracking for multi-device login management."""
    
    __tablename__ = "user_sessions"
    
    # Primary Key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    device_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    
    # Session Data
    session_token_hash = Column(String(255), unique=True, nullable=False, index=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    
    # Session Lifecycle
    started_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    last_activity_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    
    # Session Status
    is_active = Column(Boolean, default=True, nullable=False)
    is_current = Column(Boolean, default=False, nullable=False)  # Last active session
    end_reason = Column(String(50), nullable=True)  # logout, timeout, device_loss, security_alert
    
    # Audit
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index("ix_user_sessions_user_id_is_active", user_id, is_active),
        Index("ix_user_sessions_user_id_created_at", user_id, created_at),
        Index("ix_user_sessions_device_id_is_active", device_id, is_active),
    )


class WatchHistory(Base):
    """User watch history for resume functionality and recommendations."""
    
    __tablename__ = "watch_history"
    
    # Primary Key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    content_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    
    # Content Information
    content_type = Column(String(20), nullable=False)  # movie, show, episode
    
    # Watch Progress
    duration_seconds = Column(Integer, nullable=False)  # Total duration
    progress_seconds = Column(Integer, default=0, nullable=False)  # Where they left off
    progress_percentage = Column(Integer, default=0, nullable=False)  # 0-100
    
    # Watch Status
    is_completed = Column(Boolean, default=False, nullable=False)
    watch_count = Column(Integer, default=1, nullable=False)  # How many times watched
    
    # Audit
    first_watched_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    last_watched_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index("ix_watch_history_user_id_updated_at", user_id, updated_at),
        Index("ix_watch_history_user_id_content_id", user_id, content_id),
    )


class UserPreference(Base):
    """User viewing preferences and customization."""
    
    __tablename__ = "user_preferences"
    
    # Primary Key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    
    # Video Preferences
    preferred_quality = Column(String(20), default="auto", nullable=False)  # auto, 720p, 1080p, 4k
    preferred_video_codec = Column(String(20), nullable=True)  # h264, h265, vp9, av1
    
    # Audio Preferences
    preferred_audio_language = Column(String(10), default="en", nullable=False)
    preferred_audio_track = Column(String(20), nullable=True)  # stereo, 5.1, 7.1, etc.
    
    # Playback Preferences
    autoplay_next_episode = Column(Boolean, default=True, nullable=False)
    skip_intro = Column(Boolean, default=False, nullable=False)
    skip_credits = Column(Boolean, default=False, nullable=False)
    playback_speed = Column(String(10), default="1.0", nullable=False)  # 0.5, 0.75, 1.0, 1.25, 1.5, 2.0
    
    # Subtitle Preferences
    subtitle_language = Column(String(10), nullable=True)
    subtitle_size = Column(String(20), default="medium", nullable=False)  # small, medium, large
    subtitle_style = Column(String(50), nullable=True)
    
    # Miscellaneous
    data = Column(JSON, default={}, nullable=False)  # Custom preferences JSON
    
    # Audit
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
