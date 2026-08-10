import uuid
"""SQLAlchemy models for User Service."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    mapped_column,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declarative_base

Base = declarative_base()


class UserProfile(Base):
    """User profile model - extends auth user with additional profile data."""

    __tablename__ = "user_profiles"
    __table_args__ = (
        Index("idx_user_profiles_user_id", "user_id", unique=True),
        Index("idx_created_at", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=False)  # Reference to auth service user

    # Profile information
    avatar_url = Column(String(2048), nullable=True)  # Profile picture
    bio = Column(Text, nullable=True, default="")  # User bio
    phone_number = Column(String(20), nullable=True)
    date_of_birth = Column(DateTime, nullable=True)  # Age verification
    country = Column(String(2), nullable=True)  # ISO country code
    language = Column(String(5), default="en-US")  # Preferred language
    timezone = Column(String(50), nullable=True)  # User timezone

    # Account settings
    public_profile = Column(Boolean, default=False)  # Can other users see profile
    newsletter_subscribed = Column(Boolean, default=True)
    marketing_emails = Column(Boolean, default=False)

    # Profile metadata
    completed_onboarding = Column(Boolean, default=False)
    profile_completeness = Column(Integer, default=0)  # 0-100%

    # Soft delete support
    is_active = Column(Boolean, default=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class UserDevice(Base):
    """User device model - track devices for multi-device support."""

    __tablename__ = "user_devices"
    __table_args__ = (
        Index("idx_user_devices_user_id", "user_id"),
        Index("idx_device_id", "device_id", unique=True),
        Index("idx_last_active_at", "last_active_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)

    # Device identification
    device_id = Column(String(255), nullable=False, unique=True)  # Browser/app fingerprint
    device_name = Column(String(255), nullable=False)  # e.g., "Chrome on MacOS"
    device_type = Column(String(50), nullable=False)  # "web", "ios", "android", "smart_tv"

    # Device details
    os_name = Column(String(50), nullable=True)  # "macOS", "iOS", "Android", "Windows"
    os_version = Column(String(50), nullable=True)
    browser_name = Column(String(50), nullable=True)
    browser_version = Column(String(50), nullable=True)

    # Network info
    ip_address = Column(String(45), nullable=True)  # IPv4 or IPv6
    user_agent = Column(Text, nullable=True)

    # Device status
    is_active = Column(Boolean, default=True)
    is_trusted = Column(Boolean, default=False)  # Trusted device (skip 2FA)

    # Playback permissions
    can_stream = Column(Boolean, default=True)
    can_download = Column(Boolean, default=False)

    # Metadata
    last_active_at = Column(DateTime, nullable=True)
    registration_date = Column(DateTime, default=datetime.utcnow, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class UserPreference(Base):
    """User preference model - store user settings and preferences."""

    __tablename__ = "user_preferences"
    __table_args__ = (Index("idx_user_preferences_user_id", "user_id", unique=True),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, unique=True)

    # Display preferences
    theme = Column(String(20), default="dark")  # "dark", "light", "auto"
    language = Column(String(5), default="en-US")
    subtitle_language = Column(String(5), default="en-US")
    subtitle_size = Column(String(20), default="medium")  # "small", "medium", "large"
    closed_captions = Column(Boolean, default=False)

    # Playback preferences
    autoplay = Column(Boolean, default=True)
    autoplay_next_episode = Column(Boolean, default=True)
    default_video_quality = Column(
        String(20), default="adaptive"
    )  # "adaptive", "720p", "1080p", "4k"
    default_audio_language = Column(String(5), default="en-US")

    # Maturity rating
    content_rating = Column(String(20), default="PG-13")  # "G", "PG", "PG-13", "R", "NC-17"
    allow_explicit_content = Column(Boolean, default=True)

    # Privacy preferences
    share_viewing_activity = Column(Boolean, default=False)
    allow_recommendations = Column(Boolean, default=True)
    data_collection = Column(Boolean, default=False)  # For analytics

    # Notification preferences
    email_new_content = Column(Boolean, default=True)
    email_recommendations = Column(Boolean, default=False)
    push_notifications = Column(Boolean, default=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class UserSubscriptionProfile(Base):
    """User subscription profile - link to subscription tier."""

    __tablename__ = "user_subscription_profiles"
    __table_args__ = (
        Index("idx_user_subscription_profiles_user_id", "user_id", unique=True),
        Index("idx_subscription_tier", "subscription_tier"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, unique=True)

    # Subscription info
    subscription_tier = Column(String(50), default="free")  # "free", "basic", "standard", "premium"
    subscription_status = Column(
        String(50), default="active"
    )  # "active", "inactive", "suspended", "canceled"

    # Limits
    max_concurrent_streams = Column(Integer, default=1)
    can_download = Column(Boolean, default=False)
    can_use_4k = Column(Boolean, default=False)
    ad_free = Column(Boolean, default=False)

    # Subscription dates
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
