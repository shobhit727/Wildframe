"""
SQLAlchemy ORM models for Streaming Service.
Manages playback sessions, video manifests, transcoding, and delivery.
"""


import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class PlaybackSessionStatus(str, Enum):
    """Playback session status enumeration."""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"


class TranscodingStatus(str, enum.Enum):
    """Transcoding job status enumeration."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class DeliveryProtocol(str, enum.Enum):
    """Video delivery protocol enumeration."""
    HLS = "hls"
    DASH = "dash"
    SMOOTH_STREAMING = "smooth_streaming"


class PlaybackSession(Base):
    """Represents an active or historical video playback session."""
    __tablename__ = 'playback_session'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    content_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    episode_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    device_id = Column(String(255), nullable=False, index=True)
    
    # Playback info
    status = Column(Enum(PlaybackSessionStatus), nullable=False, default=PlaybackSessionStatus.ACTIVE)
    current_position_seconds = Column(Integer, default=0)  # Current playback position
    total_duration_seconds = Column(Integer, nullable=False)
    
    # Delivery info
    protocol = Column(Enum(DeliveryProtocol), nullable=False, default=DeliveryProtocol.HLS)
    resolution = Column(String(20), nullable=False)  # e.g., "1080p", "720p", "480p"
    bitrate_kbps = Column(Integer, nullable=False)  # Target bitrate
    
    # Quality metrics
    estimated_bandwidth_kbps = Column(Integer, nullable=True)
    buffer_health_seconds = Column(Float, default=0.0)  # Buffered content in seconds
    dropped_frames = Column(Integer, default=0)
    stalls_count = Column(Integer, default=0)
    
    # Subtitle/Audio
    subtitle_language = Column(String(10), nullable=True)
    audio_language = Column(String(10), nullable=True)
    audio_codec = Column(String(50), nullable=True)
    
    # CDN info
    cdn_provider = Column(String(100), nullable=True)
    server_ip = Column(String(50), nullable=True)
    client_ip = Column(String(50), nullable=True)
    
    # Timestamps
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_activity_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    ended_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('ix_playback_session_user_content', 'user_id', 'content_id'),
        Index('ix_playback_session_device', 'user_id', 'device_id'),
        Index('ix_playback_session_status', 'status'),
    )


class VideoManifest(Base):
    """Represents HLS/DASH video manifests for streaming."""
    __tablename__ = 'video_manifest'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    episode_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    content_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    
    # Manifest info
    protocol = Column(Enum(DeliveryProtocol), nullable=False, index=True)
    manifest_url = Column(String(500), nullable=False)
    manifest_content = Column(Text, nullable=False)  # M3U8 or MPD content
    
    # Available variants (resolutions)
    variants = Column(ARRAY(String), nullable=False, default=[])  # ["1080p", "720p", "480p"]
    available_bitrates = Column(ARRAY(Integer), nullable=False, default=[])  # [5000, 2500, 1000]
    
    # Manifest options
    include_subtitles = Column(Boolean, default=True)
    include_closed_captions = Column(Boolean, default=True)
    live_edge_seconds = Column(Integer, default=6)  # For live/VOD edge
    target_segment_duration_seconds = Column(Integer, default=10)
    
    # Metadata
    generated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)  # Cache expiration
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        UniqueConstraint('episode_id', 'protocol', name='_manifest_episode_protocol_uc'),
        Index('ix_manifest_content_protocol', 'content_id', 'protocol'),
    )


class TranscodingJob(Base):
    """Represents video transcoding jobs."""
    __tablename__ = 'transcoding_job'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    episode_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    content_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    
    # Job info
    status = Column(Enum(TranscodingStatus), nullable=False, default=TranscodingStatus.PENDING, index=True)
    priority = Column(Integer, default=5)  # 1-10, higher = more important
    
    # Input file
    input_file_path = Column(String(500), nullable=False)
    input_duration_seconds = Column(Integer, nullable=True)
    input_file_size_mb = Column(Float, nullable=True)
    
    # Transcoding targets
    target_resolutions = Column(ARRAY(String), nullable=False)  # ["1080p", "720p", "480p"]
    target_bitrates = Column(ARRAY(Integer), nullable=False)  # [5000, 2500, 1000]
    
    # Output files
    output_paths = Column(JSONB, default={})  # {"1080p": "/path/...", "720p": "/path/..."}
    
    # Processing info
    worker_id = Column(String(100), nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    # Metrics
    progress_percent = Column(Integer, default=0)  # 0-100
    error_message = Column(Text, nullable=True)
    estimated_time_remaining_seconds = Column(Integer, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('ix_transcoding_job_status_priority', 'status', 'priority'),
        Index('ix_transcoding_job_worker', 'worker_id', 'status'),
    )


class StreamingQualityProfile(Base):
    """Predefined quality profiles for adaptive bitrate streaming."""
    __tablename__ = 'streaming_quality_profile'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    
    # Quality tiers
    resolution = Column(String(20), nullable=False)  # "1080p", "720p", etc.
    bitrate_kbps = Column(Integer, nullable=False)  # Target bitrate
    fps = Column(Integer, default=24)  # Frames per second
    
    # Codec settings
    video_codec = Column(String(50), default="h264")  # h264, h265, vp9, av1
    audio_codec = Column(String(50), default="aac")  # aac, opus, etc.
    audio_bitrate_kbps = Column(Integer, default=128)
    
    # Compatibility
    supported_devices = Column(ARRAY(String), nullable=False, default=[])  # ["web", "ios", "android"]
    
    # Bandwidth range for adaptive streaming
    min_bandwidth_kbps = Column(Integer, nullable=False)
    max_bandwidth_kbps = Column(Integer, nullable=False)
    
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('ix_quality_profile_resolution_bitrate', 'resolution', 'bitrate_kbps'),
    )


class CDNRegion(Base):
    """CDN edge server regions for content delivery."""
    __tablename__ = 'cdn_region'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    region_code = Column(String(10), nullable=False, unique=True)  # "us-east", "eu-west", etc.
    region_name = Column(String(100), nullable=False)
    
    # Geographic info
    country = Column(String(100), nullable=False)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    
    # CDN provider
    cdn_provider = Column(String(100), nullable=False)  # Cloudflare, Akamai, etc.
    edge_server_ips = Column(ARRAY(String), nullable=False, default=[])
    
    # Capacity
    max_concurrent_streams = Column(Integer, default=10000)
    current_active_streams = Column(Integer, default=0)
    bandwidth_capacity_gbps = Column(Float, nullable=False)
    
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class StreamingStatistics(Base):
    """Aggregated streaming statistics for analytics."""
    __tablename__ = 'streaming_statistics'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    
    # Aggregation period (daily/hourly)
    period_start = Column(DateTime, nullable=False, index=True)
    period_end = Column(DateTime, nullable=False)
    period_type = Column(String(20), nullable=False)  # "hourly", "daily", "weekly"
    
    # Metrics
    total_streams = Column(Integer, default=0)
    unique_viewers = Column(Integer, default=0)
    total_watch_time_hours = Column(Float, default=0.0)
    average_watch_time_minutes = Column(Float, default=0.0)
    completion_rate = Column(Float, default=0.0)  # 0-100%
    
    # Quality metrics
    average_resolution = Column(String(20), nullable=True)
    average_bitrate_kbps = Column(Integer, nullable=True)
    average_buffer_ratio = Column(Float, default=0.0)  # % of time spent buffering
    
    # Errors
    total_errors = Column(Integer, default=0)
    stalls_per_session = Column(Float, default=0.0)
    
    # Geographic distribution
    top_regions = Column(JSONB, default={})  # {"us-east": 5000, "eu-west": 3000}
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        UniqueConstraint('content_id', 'period_start', 'period_type', name='_stats_period_uc'),
        Index('ix_stats_period', 'period_start', 'period_type'),
    )


class DownloadSession(Base):
    """Represents video download sessions for offline viewing."""
    __tablename__ = 'download_session'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    episode_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    device_id = Column(String(255), nullable=False, index=True)
    
    # Download info
    status = Column(String(20), nullable=False)  # "queued", "downloading", "completed", "failed"
    resolution = Column(String(20), nullable=False)  # Download quality
    
    # Progress
    progress_percent = Column(Integer, default=0)
    bytes_downloaded = Column(Integer, default=0)
    total_bytes = Column(Integer, nullable=False)
    
    # Timestamps
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)  # Download expiration date
    
    # Expiration
    download_ttl_days = Column(Integer, default=30)  # How long download is valid
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('ix_download_session_user_device', 'user_id', 'device_id'),
        Index('ix_download_session_status', 'status'),
    )
