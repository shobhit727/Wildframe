"""Streaming service business logic."""

import logging
import secrets
from uuid import UUID
from typing import Optional, List, Tuple
from datetime import datetime, timezone, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.streaming import (
    StreamingSession,
    ManifestCache,
    StreamingMetrics,
    Subtitle,
    AudioTrack
)
from app.repositories.streaming import (
    StreamingSessionRepository,
    ManifestCacheRepository,
    StreamingMetricsRepository,
    SubtitleRepository,
    AudioTrackRepository,
    CDNEdgeRepository
)

logger = logging.getLogger(__name__)


class StreamingService:
    """Service for streaming and session management."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.session_repo = StreamingSessionRepository(db)
        self.manifest_repo = ManifestCacheRepository(db)
        self.metrics_repo = StreamingMetricsRepository(db)
        self.subtitle_repo = SubtitleRepository(db)
        self.audio_repo = AudioTrackRepository(db)
        self.cdn_repo = CDNEdgeRepository(db)
    
    async def start_streaming(self, user_id: UUID, content_id: UUID, media_key: str,
                             content_type: str, device_type: str, ip_address: str,
                             user_agent: str, preferred_quality: Optional[str] = None,
                             preferred_audio_lang: Optional[str] = None,
                             preferred_subtitle_lang: Optional[str] = None) -> StreamingSession:
        """Start new streaming session."""
        
        # Get or generate manifest
        manifest = await self.manifest_repo.get_by_media_key(media_key)
        if not manifest or not await self.manifest_repo.is_valid(media_key):
            raise ValueError(f"Manifest not available for {media_key}")
        
        # Generate session token
        session_token = secrets.token_urlsafe(32)
        
        # Create session
        session = await self.session_repo.create(
            user_id=user_id,
            content_id=content_id,
            content_type=content_type,
            session_token=session_token,
            ip_address=ip_address,
            device_type=device_type,
            user_agent=user_agent,
            duration_seconds=manifest.duration_seconds,
            stream_quality=preferred_quality or "auto"
        )
        
        logger.info(f"Started streaming session {session.id} for user {user_id}")
        return session
    
    async def get_streaming_session(self, session_token: str) -> Optional[StreamingSession]:
        """Get streaming session."""
        return await self.session_repo.get_by_token(session_token)
    
    async def heartbeat(self, session_token: str, played_until_seconds: int,
                       bandwidth_mbps: Optional[float] = None,
                       current_bitrate: Optional[int] = None) -> Optional[StreamingSession]:
        """Update session heartbeat."""
        session = await self.get_streaming_session(session_token)
        if not session:
            raise ValueError("Session not found")
        
        return await self.session_repo.update_heartbeat(
            session.id,
            played_until_seconds,
            bandwidth_mbps,
            current_bitrate
        )
    
    async def record_metrics(self, session_token: str, bandwidth_mbps: float,
                            bitrate_kbps: int, quality: str, rebuffering_seconds: float = 0,
                            packets_lost: int = 0, latency_ms: int = 0,
                            cpu_usage: Optional[float] = None,
                            memory_usage: Optional[float] = None) -> StreamingMetrics:
        """Record streaming metrics."""
        session = await self.get_streaming_session(session_token)
        if not session:
            raise ValueError("Session not found")
        
        metrics = await self.metrics_repo.record(
            session_id=session.id,
            user_id=session.user_id,
            content_id=session.content_id,
            bandwidth_mbps=bandwidth_mbps,
            bitrate_kbps=bitrate_kbps,
            quality=quality,
            rebuffering_seconds=rebuffering_seconds,
            packets_lost=packets_lost,
            latency_ms=latency_ms,
            cpu_usage_percent=cpu_usage,
            memory_usage_mb=memory_usage
        )
        
        logger.info(f"Recorded metrics for session {session.id}")
        return metrics
    
    async def record_buffering(self, session_token: str, buffer_seconds: float) -> None:
        """Record buffering event."""
        session = await self.get_streaming_session(session_token)
        if not session:
            raise ValueError("Session not found")
        
        await self.session_repo.record_buffering(session.id, buffer_seconds)
        logger.info(f"Recorded buffering for session {session.id}")
    
    async def end_streaming(self, session_token: str, played_until_seconds: int) -> Optional[StreamingSession]:
        """End streaming session."""
        session = await self.get_streaming_session(session_token)
        if not session:
            raise ValueError("Session not found")
        
        session = await self.session_repo.end_session(session.id)
        logger.info(f"Ended streaming session {session.id}")
        return session
    
    async def get_session_stats(self, session_token: str) -> dict:
        """Get session statistics."""
        session = await self.get_streaming_session(session_token)
        if not session:
            raise ValueError("Session not found")
        
        avg_metrics = await self.metrics_repo.get_average_metrics(session.id)
        
        duration_ms = int((session.last_heartbeat - session.started_at).total_seconds() * 1000)
        watched_percent = (session.played_until_seconds / session.duration_seconds * 100) if session.duration_seconds else 0
        
        return {
            "session_id": session.id,
            "total_watched_seconds": session.played_until_seconds,
            "total_duration_seconds": session.duration_seconds,
            "completion_percentage": watched_percent,
            "average_bandwidth_mbps": avg_metrics.get("avg_bandwidth_mbps", 0),
            "average_bitrate_kbps": avg_metrics.get("avg_bitrate_kbps", 0),
            "buffer_events": session.buffering_count,
            "total_buffer_seconds": session.total_buffer_seconds,
            "video_quality": session.stream_quality,
            "duration_seconds": duration_ms // 1000,
        }
    
    async def get_active_sessions_for_user(self, user_id: UUID) -> List[StreamingSession]:
        """Get active sessions for user."""
        return await self.session_repo.list_active_for_user(user_id)
    
    async def add_subtitle(self, media_key: str, language: str, language_name: str,
                          subtitle_url: str, format: str, is_default: bool = False,
                          is_forced: bool = False) -> Subtitle:
        """Add subtitle track."""
        subtitle = await self.subtitle_repo.create(
            media_key=media_key,
            language=language,
            language_name=language_name,
            subtitle_url=subtitle_url,
            format=format,
            is_default=is_default,
            is_forced=is_forced
        )
        logger.info(f"Added subtitle {language} for {media_key}")
        return subtitle
    
    async def list_subtitles(self, media_key: str) -> List[Subtitle]:
        """List subtitles for media."""
        return await self.subtitle_repo.list_by_media_key(media_key)
    
    async def add_audio_track(self, media_key: str, language: str, language_name: str,
                             codec: str, bitrate_kbps: int, channels: int,
                             is_default: bool = False) -> AudioTrack:
        """Add audio track."""
        track = await self.audio_repo.create(
            media_key=media_key,
            language=language,
            language_name=language_name,
            codec=codec,
            bitrate_kbps=bitrate_kbps,
            channels=channels,
            is_default=is_default
        )
        logger.info(f"Added audio track {language} for {media_key}")
        return track
    
    async def list_audio_tracks(self, media_key: str) -> List[AudioTrack]:
        """List audio tracks for media."""
        return await self.audio_repo.list_by_media_key(media_key)
    
    async def get_manifest(self, media_key: str) -> Optional[ManifestCache]:
        """Get manifest for media."""
        return await self.manifest_repo.get_by_media_key(media_key)
    
    async def create_manifest(self, media_key: str, content_type: str,
                             hls_url: str, dash_url: str, bitrates: List[int],
                             duration_seconds: int, segment_duration_seconds: int = 10,
                             cache_ttl_hours: int = 24) -> ManifestCache:
        """Create manifest cache entry."""
        expires_at = datetime.now(timezone.utc) + timedelta(hours=cache_ttl_hours)
        
        manifest = await self.manifest_repo.create(
            media_key=media_key,
            content_type=content_type,
            hls_master_url=hls_url,
            dash_mpd_url=dash_url,
            available_bitrates=bitrates,
            available_subtitles=[],
            duration_seconds=duration_seconds,
            total_segments=(duration_seconds + segment_duration_seconds - 1) // segment_duration_seconds,
            segment_duration_seconds=segment_duration_seconds,
            expires_at=expires_at
        )
        
        logger.info(f"Created manifest for {media_key}")
        return manifest
