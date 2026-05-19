"""Streaming service Pydantic schemas."""

from datetime import datetime
from uuid import UUID
from typing import Optional, List
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class StreamQualityEnum(str, Enum):
    """Stream quality enumeration."""
    AUTO = "auto"
    SD = "720p"
    HD = "1080p"
    UHD = "4k"


class BitrateProfileEnum(str, Enum):
    """Bitrate profile enumeration."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    ULTRA = "ultra"


class SubtitleResponse(BaseModel):
    """Subtitle response."""
    id: UUID
    language: str
    language_name: str
    format: str
    is_default: bool
    is_forced: bool
    
    class Config:
        from_attributes = True


class AudioTrackResponse(BaseModel):
    """Audio track response."""
    id: UUID
    language: str
    language_name: str
    codec: str
    bitrate_kbps: int
    channels: int
    is_default: bool
    
    class Config:
        from_attributes = True


class ManifestResponse(BaseModel):
    """Manifest response."""
    id: UUID
    media_key: str
    hls_master_url: str
    dash_mpd_url: str
    available_bitrates: List[int]
    available_subtitles: List[SubtitleResponse] = []
    available_audio: List[AudioTrackResponse] = []
    duration_seconds: int
    segment_duration_seconds: int
    
    class Config:
        from_attributes = True


class StartStreamingRequest(BaseModel):
    """Start streaming request."""
    content_id: UUID
    content_type: str = Field(..., min_length=1, max_length=50)
    device_type: str = Field(..., min_length=1)
    preferred_quality: Optional[StreamQualityEnum] = None
    preferred_audio_language: Optional[str] = Field(None, min_length=2, max_length=10)
    preferred_subtitle_language: Optional[str] = Field(None, min_length=2, max_length=10)


class StreamingSessionResponse(BaseModel):
    """Streaming session response."""
    id: UUID
    session_token: str
    manifest: ManifestResponse
    current_playback_position: int
    duration_seconds: int
    stream_quality: StreamQualityEnum
    available_subtitles: List[SubtitleResponse]
    available_audio: List[AudioTrackResponse]
    cdn_edge: str
    created_at: datetime
    
    class Config:
        from_attributes = True


class HeartbeatRequest(BaseModel):
    """Heartbeat request."""
    session_token: str
    played_until_seconds: int = Field(..., ge=0)
    bandwidth_mbps: Optional[float] = Field(None, gt=0)
    stream_quality: Optional[StreamQualityEnum] = None
    buffering_seconds: Optional[float] = Field(None, ge=0)
    current_bitrate: Optional[int] = Field(None, gt=0)


class StreamingMetricsResponse(BaseModel):
    """Streaming metrics response."""
    session_id: UUID
    timestamp: datetime
    bandwidth_mbps: float
    bitrate_kbps: int
    quality: StreamQualityEnum
    rebuffering_seconds: float
    packets_lost: int
    latency_ms: int
    
    class Config:
        from_attributes = True


class RecordMetricsRequest(BaseModel):
    """Record metrics request."""
    session_token: str
    bandwidth_mbps: float = Field(..., gt=0)
    bitrate_kbps: int = Field(..., gt=0)
    quality: StreamQualityEnum
    rebuffering_seconds: float = Field(default=0, ge=0)
    packets_lost: int = Field(default=0, ge=0)
    latency_ms: int = Field(..., ge=0)
    cpu_usage_percent: Optional[float] = Field(None, ge=0, le=100)
    memory_usage_mb: Optional[float] = Field(None, gt=0)


class EndStreamingRequest(BaseModel):
    """End streaming request."""
    session_token: str
    played_until_seconds: int = Field(..., ge=0)
    total_watch_time_seconds: int = Field(..., ge=0)
    was_completed: bool = False


class SubtitleRequest(BaseModel):
    """Add subtitle request."""
    language: str = Field(..., min_length=2, max_length=10)
    language_name: str = Field(..., min_length=1, max_length=50)
    subtitle_url: str = Field(..., min_length=5)
    format: str = Field(..., regex="^(vtt|srt|ass|ssa)$")
    is_default: bool = False
    is_forced: bool = False


class AudioTrackRequest(BaseModel):
    """Add audio track request."""
    language: str = Field(..., min_length=2, max_length=10)
    language_name: str = Field(..., min_length=1, max_length=50)
    codec: str = Field(..., regex="^(aac|ac3|ec3|mp3|flac|opus)$")
    bitrate_kbps: int = Field(..., gt=0)
    channels: int = Field(..., ge=1, le=8)
    is_default: bool = False


class StreamingStatsResponse(BaseModel):
    """Streaming statistics response."""
    session_id: UUID
    total_watched_seconds: int
    average_bandwidth_mbps: float
    average_bitrate_kbps: int
    buffer_events: int
    total_buffer_seconds: float
    video_quality: StreamQualityEnum
    audio_language: str
    subtitle_language: Optional[str] = None
    completion_percentage: float
    
    class Config:
        from_attributes = True


class ErrorResponse(BaseModel):
    """Error response."""
    error: str
    detail: Optional[str] = None
    status_code: int
