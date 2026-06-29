"""Streaming service database models."""

from datetime import datetime, timezone
from uuid import uuid4
from enum import Enum

from sqlalchemy import String, Integer, Float, Boolean, DateTime, Enum as SQLEnum, ForeignKey, Index, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, Mapped, mapped_column

Base = declarative_base()


class StreamQuality(str, Enum):
    """Video stream quality."""
    AUTO = "auto"
    SD = "720p"
    HD = "1080p"
    UHD = "4k"


class BitrateProfile(str, Enum):
    """Bitrate encoding profile."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    ULTRA = "ultra"


class StreamingSession(Base):
    """Active streaming session."""
    __tablename__ = "streaming_sessions"
    
    id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    content_id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    session_token: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)
    device_type: Mapped[str] = mapped_column(String(50), nullable=False)
    user_agent: Mapped[str] = mapped_column(String(500), nullable=False)
    bandwidth_mbps: Mapped[float] = mapped_column(Float, nullable=True)
    current_bitrate: Mapped[int] = mapped_column(Integer, nullable=True)
    played_until_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    playback_rate: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    buffering_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_buffer_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stream_quality: Mapped[str] = mapped_column(SQLEnum(StreamQuality), default=StreamQuality.AUTO, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    
    __table_args__ = (
        Index("idx_session_user_active", "user_id", "is_active"),
        Index("idx_session_content", "content_id"),
        Index("idx_session_token", "session_token"),
    )


class ManifestCache(Base):
    """Cached HLS/DASH manifests."""
    __tablename__ = "manifest_cache"
    
    id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    media_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    hls_master_url: Mapped[str] = mapped_column(Text, nullable=False)
    dash_mpd_url: Mapped[str] = mapped_column(Text, nullable=False)
    available_bitrates: Mapped[list] = mapped_column(JSON, nullable=False)
    available_subtitles: Mapped[list] = mapped_column(JSON, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    total_segments: Mapped[int] = mapped_column(Integer, nullable=False)
    segment_duration_seconds: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    __table_args__ = (
        Index("idx_manifest_media_key", "media_key"),
        Index("idx_manifest_expires", "expires_at"),
    )


class StreamingMetrics(Base):
    """Streaming quality metrics."""
    __tablename__ = "streaming_metrics"
    
    id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), ForeignKey("streaming_sessions.id"), nullable=False, index=True)
    user_id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    content_id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    bandwidth_mbps: Mapped[float] = mapped_column(Float, nullable=False)
    bitrate_kbps: Mapped[int] = mapped_column(Integer, nullable=False)
    quality: Mapped[str] = mapped_column(SQLEnum(StreamQuality), nullable=False)
    rebuffering_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    packets_lost: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    cpu_usage_percent: Mapped[float] = mapped_column(Float, nullable=True)
    memory_usage_mb: Mapped[float] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    
    __table_args__ = (
        Index("idx_metrics_session", "session_id"),
        Index("idx_metrics_user_content", "user_id", "content_id"),
        Index("idx_metrics_timestamp", "timestamp"),
    )


class Subtitle(Base):
    """Video subtitles."""
    __tablename__ = "subtitles"
    
    id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    media_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    language_name: Mapped[str] = mapped_column(String(50), nullable=False)
    subtitle_url: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str] = mapped_column(String(20), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_forced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    
    __table_args__ = (
        Index("idx_subtitle_media_lang", "media_key", "language"),
    )


class AudioTrack(Base):
    """Audio tracks."""
    __tablename__ = "audio_tracks"
    
    id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    media_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    language_name: Mapped[str] = mapped_column(String(50), nullable=False)
    codec: Mapped[str] = mapped_column(String(50), nullable=False)
    bitrate_kbps: Mapped[int] = mapped_column(Integer, nullable=False)
    channels: Mapped[int] = mapped_column(Integer, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    
    __table_args__ = (
        Index("idx_audio_media_lang", "media_key", "language"),
    )


class CDNEdge(Base):
    """CDN edge server status."""
    __tablename__ = "cdn_edges"

    id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    edge_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    region: Mapped[str] = mapped_column(String(50), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    health_status: Mapped[str] = mapped_column(String(50), default="healthy", nullable=False)
    last_health_check: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)


# ---------------------------------------------------------------------------
# Compatibility re-exports.
#
# The streaming service has two model layers: the modern Mapped-style models in
# this file (StreamingSession, ManifestCache, StreamingMetrics, ...) and the
# legacy Column-style models in app/models/__init__.py (PlaybackSession,
# VideoManifest, TranscodingJob, ...). The repositories layer consumes the
# legacy models, while the service layer consumes the modern ones. Tests import
# a mix, so re-export the names they expect here to keep both worlds working
# without duplicating ORM definitions.
# ---------------------------------------------------------------------------

from app.models import VideoManifest as VideoManifest  # noqa: F401


class WatchHistory(Base):
    """A user's watch-history entry (resume points + completed plays)."""
    __tablename__ = "watch_history"

    id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    content_id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    episode_id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    position_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    watched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("idx_watch_history_user", "user_id", "watched_at"),
        Index("idx_watch_history_content", "content_id"),
    )
