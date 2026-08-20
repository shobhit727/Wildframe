"""
Pydantic v2 schemas for Streaming Service API requests/responses.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PlaybackSessionResponse(BaseModel):
    """Playback session response schema."""

    id: UUID
    user_id: UUID
    content_id: UUID
    episode_id: UUID | None = None
    device_id: str
    status: str
    current_position_seconds: int
    total_duration_seconds: int
    protocol: str
    resolution: str
    bitrate_kbps: int
    buffer_health_seconds: float
    stalls_count: int
    started_at: datetime
    last_activity_at: datetime
    ended_at: datetime | None = None

    model_config = {"from_attributes": True}


class PlaybackSessionCreateRequest(BaseModel):
    """Playback session creation request schema."""

    user_id: UUID
    content_id: UUID
    episode_id: UUID | None = None
    device_id: str = Field(..., min_length=1, max_length=255)
    protocol: str = Field(default="hls", pattern="^(hls|dash|smooth_streaming)$")
    resolution: str = Field(default="720p", pattern="^[0-9]+p$")
    bitrate_kbps: int = Field(default=2500, ge=100)
    subtitle_language: str | None = None
    audio_language: str | None = None


class PlaybackSessionUpdateRequest(BaseModel):
    """Playback session update request schema."""

    current_position_seconds: int | None = Field(None, ge=0)
    status: str | None = None
    resolution: str | None = None
    bitrate_kbps: int | None = Field(None, ge=100)
    buffer_health_seconds: float | None = None
    dropped_frames: int | None = None


class VideoManifestResponse(BaseModel):
    """Video manifest response schema."""

    id: UUID
    episode_id: UUID
    content_id: UUID
    protocol: str
    manifest_url: str
    variants: list[str]
    available_bitrates: list[int]
    generated_at: datetime
    expires_at: datetime | None = None

    model_config = {"from_attributes": True}


class TranscodingJobResponse(BaseModel):
    """Transcoding job response schema."""

    id: UUID
    episode_id: UUID
    content_id: UUID
    status: str
    priority: int
    input_file_path: str
    target_resolutions: list[str]
    target_bitrates: list[int]
    progress_percent: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    estimated_time_remaining_seconds: int | None = None

    model_config = {"from_attributes": True}


class TranscodingJobCreateRequest(BaseModel):
    """Transcoding job creation request schema."""

    episode_id: UUID
    content_id: UUID
    input_file_path: str = Field(..., min_length=1)
    target_resolutions: list[str] = Field(default=["1080p", "720p", "480p"])
    target_bitrates: list[int] = Field(default=[5000, 2500, 1000])
    priority: int = Field(default=5, ge=1, le=10)


class QualityProfileResponse(BaseModel):
    """Quality profile response schema."""

    id: UUID
    name: str
    description: str | None = None
    resolution: str
    bitrate_kbps: int
    fps: int
    video_codec: str
    audio_codec: str
    audio_bitrate_kbps: int
    supported_devices: list[str]
    min_bandwidth_kbps: int
    max_bandwidth_kbps: int
    is_active: bool

    model_config = {"from_attributes": True}


class QualityProfileCreateRequest(BaseModel):
    """Quality profile creation request schema."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    resolution: str = Field(..., pattern="^[0-9]+p$")
    bitrate_kbps: int = Field(..., ge=100)
    fps: int = Field(default=24, ge=1, le=60)
    video_codec: str = Field(default="h264")
    audio_codec: str = Field(default="aac")
    audio_bitrate_kbps: int = Field(default=128, ge=32)
    supported_devices: list[str] = Field(default=["web", "ios", "android"])
    min_bandwidth_kbps: int
    max_bandwidth_kbps: int


class CDNRegionResponse(BaseModel):
    """CDN region response schema."""

    id: UUID
    region_code: str
    region_name: str
    country: str
    cdn_provider: str
    max_concurrent_streams: int
    current_active_streams: int
    bandwidth_capacity_gbps: float
    is_active: bool

    model_config = {"from_attributes": True}


class CDNRegionCreateRequest(BaseModel):
    """CDN region creation request schema."""

    region_code: str = Field(..., min_length=1, max_length=10)
    region_name: str = Field(..., min_length=1, max_length=100)
    country: str = Field(..., min_length=1, max_length=100)
    latitude: float | None = None
    longitude: float | None = None
    cdn_provider: str = Field(..., min_length=1, max_length=100)
    max_concurrent_streams: int = Field(default=10000, ge=1)
    bandwidth_capacity_gbps: float = Field(..., ge=0.1)


class StreamingStatisticsResponse(BaseModel):
    """Streaming statistics response schema."""

    id: UUID
    content_id: UUID
    period_start: datetime
    period_end: datetime
    period_type: str
    total_streams: int
    unique_viewers: int
    total_watch_time_hours: float
    average_watch_time_minutes: float
    completion_rate: float
    average_resolution: str | None = None
    average_bitrate_kbps: int | None = None
    average_buffer_ratio: float
    total_errors: int
    top_regions: dict[str, int]

    model_config = {"from_attributes": True}


class DownloadSessionResponse(BaseModel):
    """Download session response schema."""

    id: UUID
    user_id: UUID
    episode_id: UUID
    device_id: str
    status: str
    resolution: str
    progress_percent: int
    bytes_downloaded: int
    total_bytes: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    expires_at: datetime | None = None

    model_config = {"from_attributes": True}


class DownloadSessionCreateRequest(BaseModel):
    """Download session creation request schema."""

    user_id: UUID
    episode_id: UUID
    device_id: str = Field(..., min_length=1, max_length=255)
    resolution: str = Field(default="720p", pattern="^[0-9]+p$")
    download_ttl_days: int = Field(default=30, ge=1, le=180)


class ManifestGenerationRequest(BaseModel):
    """Request to generate video manifest."""

    episode_id: UUID
    content_id: UUID
    protocol: str = Field(default="hls", pattern="^(hls|dash|smooth_streaming)$")
    variants: list[str] = Field(default=["1080p", "720p", "480p"])
    include_subtitles: bool = True
    include_closed_captions: bool = True


class ErrorResponse(BaseModel):
    """Error response schema."""

    status_code: int
    message: str
    detail: str | None = None


class HealthCheckResponse(BaseModel):
    """Health check response schema."""

    status: str
    version: str
    database: str
    redis: str


class SignedPlaybackUrlRequest(BaseModel):
    """Request to generate a signed playback URL."""

    session_id: UUID
    content_id: UUID
    ttl_seconds: int | None = Field(default=None, ge=60, le=86400)


class SignedPlaybackUrlResponse(BaseModel):
    """Signed playback URL response schema."""

    signed_url: str
    expires_at: datetime
    session_id: UUID
    content_id: UUID


class ManifestAccessRequest(BaseModel):
    """Request to access manifest with signed token."""

    session_id: UUID
    content_id: UUID
    signature: str
    expires_at: int  # Unix timestamp
