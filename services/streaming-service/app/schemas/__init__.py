"""
Pydantic v2 schemas for Streaming Service API requests/responses.
"""

from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from uuid import UUID
from typing import Optional, List, Dict, Any
import re


class PlaybackSessionResponse(BaseModel):
    """Playback session response schema."""
    id: UUID
    user_id: UUID
    content_id: UUID
    episode_id: Optional[UUID] = None
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
    ended_at: Optional[datetime] = None
    
    model_config = {"from_attributes": True}


class PlaybackSessionCreateRequest(BaseModel):
    """Playback session creation request schema."""
    user_id: UUID
    content_id: UUID
    episode_id: Optional[UUID] = None
    device_id: str = Field(..., min_length=1, max_length=255)
    protocol: str = Field(default="hls", pattern="^(hls|dash|smooth_streaming)$")
    resolution: str = Field(default="720p", pattern="^[0-9]+p$")
    bitrate_kbps: int = Field(default=2500, ge=100)
    subtitle_language: Optional[str] = None
    audio_language: Optional[str] = None


class PlaybackSessionUpdateRequest(BaseModel):
    """Playback session update request schema."""
    current_position_seconds: Optional[int] = Field(None, ge=0)
    status: Optional[str] = None
    resolution: Optional[str] = None
    bitrate_kbps: Optional[int] = Field(None, ge=100)
    buffer_health_seconds: Optional[float] = None
    dropped_frames: Optional[int] = None


class VideoManifestResponse(BaseModel):
    """Video manifest response schema."""
    id: UUID
    episode_id: UUID
    content_id: UUID
    protocol: str
    manifest_url: str
    variants: List[str]
    available_bitrates: List[int]
    generated_at: datetime
    expires_at: Optional[datetime] = None
    
    model_config = {"from_attributes": True}


class TranscodingJobResponse(BaseModel):
    """Transcoding job response schema."""
    id: UUID
    episode_id: UUID
    content_id: UUID
    status: str
    priority: int
    input_file_path: str
    target_resolutions: List[str]
    target_bitrates: List[int]
    progress_percent: int
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    estimated_time_remaining_seconds: Optional[int] = None
    
    model_config = {"from_attributes": True}


class TranscodingJobCreateRequest(BaseModel):
    """Transcoding job creation request schema."""
    episode_id: UUID
    content_id: UUID
    input_file_path: str = Field(..., min_length=1)
    target_resolutions: List[str] = Field(default=["1080p", "720p", "480p"])
    target_bitrates: List[int] = Field(default=[5000, 2500, 1000])
    priority: int = Field(default=5, ge=1, le=10)


class QualityProfileResponse(BaseModel):
    """Quality profile response schema."""
    id: UUID
    name: str
    description: Optional[str] = None
    resolution: str
    bitrate_kbps: int
    fps: int
    video_codec: str
    audio_codec: str
    audio_bitrate_kbps: int
    supported_devices: List[str]
    min_bandwidth_kbps: int
    max_bandwidth_kbps: int
    is_active: bool
    
    model_config = {"from_attributes": True}


class QualityProfileCreateRequest(BaseModel):
    """Quality profile creation request schema."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    resolution: str = Field(..., pattern="^[0-9]+p$")
    bitrate_kbps: int = Field(..., ge=100)
    fps: int = Field(default=24, ge=1, le=60)
    video_codec: str = Field(default="h264")
    audio_codec: str = Field(default="aac")
    audio_bitrate_kbps: int = Field(default=128, ge=32)
    supported_devices: List[str] = Field(default=["web", "ios", "android"])
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
    latitude: Optional[float] = None
    longitude: Optional[float] = None
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
    average_resolution: Optional[str] = None
    average_bitrate_kbps: Optional[int] = None
    average_buffer_ratio: float
    total_errors: int
    top_regions: Dict[str, int]
    
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
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    
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
    variants: List[str] = Field(default=["1080p", "720p", "480p"])
    include_subtitles: bool = True
    include_closed_captions: bool = True


class ErrorResponse(BaseModel):
    """Error response schema."""
    status_code: int
    message: str
    detail: Optional[str] = None


class HealthCheckResponse(BaseModel):
    """Health check response schema."""
    status: str
    version: str
    database: str
    redis: str
