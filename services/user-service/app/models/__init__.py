import uuid

"""SQLAlchemy models for User Service."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base (mypy-friendly vs declarative_base())."""


class UserProfile(Base):
    """User profile model - extends auth user with additional profile data."""

    __tablename__ = "user_profiles"
    __table_args__ = (
        Index("idx_user_profiles_user_id", "user_id", unique=True),
        Index("idx_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)

    # Profile information
    avatar_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    bio: Mapped[str] = mapped_column(Text, default="", nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    date_of_birth: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    language: Mapped[str] = mapped_column(String(5), default="en-US", nullable=False)
    timezone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Account settings
    public_profile: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    newsletter_subscribed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    marketing_emails: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Profile metadata
    completed_onboarding: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    profile_completeness: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Soft delete support
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class UserDevice(Base):
    """User device model - track devices for multi-device support."""

    __tablename__ = "user_devices"
    __table_args__ = (
        Index("idx_user_devices_user_id", "user_id"),
        Index("idx_device_id", "device_id", unique=True),
        Index("idx_last_active_at", "last_active_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Device identification
    device_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    device_name: Mapped[str] = mapped_column(String(255), nullable=False)
    device_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Device details
    os_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    browser_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    browser_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Network info
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Device status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_trusted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Playback permissions
    can_stream: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_download: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Metadata
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    registration_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class UserPreference(Base):
    """User preference model - store user settings and preferences."""

    __tablename__ = "user_preferences"
    __table_args__ = (Index("idx_user_preferences_user_id", "user_id", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)

    # Display preferences
    theme: Mapped[str] = mapped_column(String(20), default="dark", nullable=False)
    language: Mapped[str] = mapped_column(String(5), default="en-US", nullable=False)
    subtitle_language: Mapped[str] = mapped_column(String(5), default="en-US", nullable=False)
    subtitle_size: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    closed_captions: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Playback preferences
    autoplay: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    autoplay_next_episode: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_video_quality: Mapped[str] = mapped_column(String(20), default="adaptive", nullable=False)
    default_audio_language: Mapped[str] = mapped_column(String(5), default="en-US", nullable=False)

    # Maturity rating
    content_rating: Mapped[str] = mapped_column(String(20), default="PG-13", nullable=False)
    allow_explicit_content: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Privacy preferences
    share_viewing_activity: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_recommendations: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    data_collection: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Notification preferences
    email_new_content: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_recommendations: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    push_notifications: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class UserSubscriptionProfile(Base):
    """User subscription profile - link to subscription tier."""

    __tablename__ = "user_subscription_profiles"
    __table_args__ = (
        Index("idx_user_subscription_profiles_user_id", "user_id", unique=True),
        Index("idx_subscription_tier", "subscription_tier"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)

    # Subscription info
    subscription_tier: Mapped[str] = mapped_column(String(50), default="free", nullable=False)
    subscription_status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)

    # Limits
    max_concurrent_streams: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    can_download: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_use_4k: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ad_free: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Subscription dates
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )