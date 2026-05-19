"""Streaming service repositories."""

import logging
from uuid import UUID
from typing import Optional, List, Tuple
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.streaming import (
    StreamingSession,
    ManifestCache,
    StreamingMetrics,
    Subtitle,
    AudioTrack,
    CDNEdge
)

logger = logging.getLogger(__name__)


class StreamingSessionRepository:
    """Repository for streaming session data access."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create(self, user_id: UUID, content_id: UUID, **kwargs) -> StreamingSession:
        """Create new streaming session."""
        session = StreamingSession(user_id=user_id, content_id=content_id, is_active=True, **kwargs)
        self.db.add(session)
        await self.db.flush()
        return session
    
    async def get_by_token(self, session_token: str) -> Optional[StreamingSession]:
        """Get session by token."""
        result = await self.db.execute(
            select(StreamingSession).where(StreamingSession.session_token == session_token).where(StreamingSession.is_active)
        )
        return result.scalar_one_or_none()
    
    async def get_by_id(self, session_id: UUID) -> Optional[StreamingSession]:
        """Get session by ID."""
        result = await self.db.execute(
            select(StreamingSession).where(StreamingSession.id == session_id).where(StreamingSession.is_active)
        )
        return result.scalar_one_or_none()
    
    async def list_active_for_user(self, user_id: UUID) -> List[StreamingSession]:
        """List active sessions for user."""
        result = await self.db.execute(
            select(StreamingSession).where(StreamingSession.user_id == user_id).where(StreamingSession.is_active)
        )
        return result.scalars().all()
    
    async def update_heartbeat(self, session_id: UUID, played_until_seconds: int, 
                              bandwidth_mbps: Optional[float] = None,
                              current_bitrate: Optional[int] = None) -> Optional[StreamingSession]:
        """Update session heartbeat."""
        session = await self.get_by_id(session_id)
        if session:
            session.last_heartbeat = datetime.now(timezone.utc)
            session.played_until_seconds = played_until_seconds
            if bandwidth_mbps:
                session.bandwidth_mbps = bandwidth_mbps
            if current_bitrate:
                session.current_bitrate = current_bitrate
            await self.db.flush()
        return session
    
    async def end_session(self, session_id: UUID) -> Optional[StreamingSession]:
        """End streaming session."""
        session = await self.get_by_id(session_id)
        if session:
            session.is_active = False
            session.ended_at = datetime.now(timezone.utc)
            await self.db.flush()
        return session
    
    async def record_buffering(self, session_id: UUID, buffer_seconds: float) -> None:
        """Record buffering event."""
        session = await self.get_by_id(session_id)
        if session:
            session.buffering_count += 1
            session.total_buffer_seconds += int(buffer_seconds)
            await self.db.flush()


class ManifestCacheRepository:
    """Repository for manifest cache."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create(self, media_key: str, **kwargs) -> ManifestCache:
        """Create manifest cache entry."""
        manifest = ManifestCache(media_key=media_key, **kwargs)
        self.db.add(manifest)
        await self.db.flush()
        return manifest
    
    async def get_by_media_key(self, media_key: str) -> Optional[ManifestCache]:
        """Get manifest by media key."""
        result = await self.db.execute(
            select(ManifestCache).where(ManifestCache.media_key == media_key)
        )
        return result.scalar_one_or_none()
    
    async def is_valid(self, media_key: str) -> bool:
        """Check if manifest is still valid."""
        manifest = await self.get_by_media_key(media_key)
        if not manifest:
            return False
        return manifest.expires_at > datetime.now(timezone.utc)
    
    async def invalidate(self, media_key: str) -> None:
        """Invalidate manifest."""
        manifest = await self.get_by_media_key(media_key)
        if manifest:
            manifest.expires_at = datetime.now(timezone.utc)
            await self.db.flush()


class StreamingMetricsRepository:
    """Repository for streaming metrics."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def record(self, session_id: UUID, user_id: UUID, content_id: UUID, **kwargs) -> StreamingMetrics:
        """Record streaming metrics."""
        metrics = StreamingMetrics(session_id=session_id, user_id=user_id, content_id=content_id, **kwargs)
        self.db.add(metrics)
        await self.db.flush()
        return metrics
    
    async def get_session_metrics(self, session_id: UUID) -> List[StreamingMetrics]:
        """Get all metrics for session."""
        result = await self.db.execute(
            select(StreamingMetrics).where(StreamingMetrics.session_id == session_id).order_by(StreamingMetrics.timestamp)
        )
        return result.scalars().all()
    
    async def get_user_recent_metrics(self, user_id: UUID, hours: int = 24) -> List[StreamingMetrics]:
        """Get recent metrics for user."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        result = await self.db.execute(
            select(StreamingMetrics).where(
                StreamingMetrics.user_id == user_id
            ).where(
                StreamingMetrics.timestamp >= cutoff
            ).order_by(desc(StreamingMetrics.timestamp))
        )
        return result.scalars().all()
    
    async def get_average_metrics(self, session_id: UUID) -> dict:
        """Get average metrics for session."""
        metrics = await self.get_session_metrics(session_id)
        if not metrics:
            return {}
        
        avg_bandwidth = sum(m.bandwidth_mbps for m in metrics) / len(metrics)
        avg_bitrate = sum(m.bitrate_kbps for m in metrics) / len(metrics)
        total_rebuffer = sum(m.rebuffering_seconds for m in metrics)
        
        return {
            "avg_bandwidth_mbps": avg_bandwidth,
            "avg_bitrate_kbps": avg_bitrate,
            "total_rebuffer_seconds": total_rebuffer,
            "packet_loss_total": sum(m.packets_lost for m in metrics),
            "avg_latency_ms": sum(m.latency_ms for m in metrics) / len(metrics),
        }


class SubtitleRepository:
    """Repository for subtitles."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create(self, media_key: str, **kwargs) -> Subtitle:
        """Create subtitle entry."""
        subtitle = Subtitle(media_key=media_key, **kwargs)
        self.db.add(subtitle)
        await self.db.flush()
        return subtitle
    
    async def list_by_media_key(self, media_key: str) -> List[Subtitle]:
        """List subtitles for media."""
        result = await self.db.execute(
            select(Subtitle).where(Subtitle.media_key == media_key)
        )
        return result.scalars().all()
    
    async def get_default(self, media_key: str) -> Optional[Subtitle]:
        """Get default subtitle for media."""
        result = await self.db.execute(
            select(Subtitle).where(
                Subtitle.media_key == media_key
            ).where(
                Subtitle.is_default
            )
        )
        return result.scalar_one_or_none()


class AudioTrackRepository:
    """Repository for audio tracks."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create(self, media_key: str, **kwargs) -> AudioTrack:
        """Create audio track entry."""
        track = AudioTrack(media_key=media_key, **kwargs)
        self.db.add(track)
        await self.db.flush()
        return track
    
    async def list_by_media_key(self, media_key: str) -> List[AudioTrack]:
        """List audio tracks for media."""
        result = await self.db.execute(
            select(AudioTrack).where(AudioTrack.media_key == media_key)
        )
        return result.scalars().all()
    
    async def get_default(self, media_key: str) -> Optional[AudioTrack]:
        """Get default audio track for media."""
        result = await self.db.execute(
            select(AudioTrack).where(
                AudioTrack.media_key == media_key
            ).where(
                AudioTrack.is_default
            )
        )
        return result.scalar_one_or_none()
    
    async def get_by_language(self, media_key: str, language: str) -> Optional[AudioTrack]:
        """Get audio track by language."""
        result = await self.db.execute(
            select(AudioTrack).where(
                AudioTrack.media_key == media_key
            ).where(
                AudioTrack.language == language
            )
        )
        return result.scalar_one_or_none()


class CDNEdgeRepository:
    """Repository for CDN edges."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create(self, edge_name: str, region: str, hostname: str) -> CDNEdge:
        """Create CDN edge."""
        edge = CDNEdge(edge_name=edge_name, region=region, hostname=hostname, is_active=True)
        self.db.add(edge)
        await self.db.flush()
        return edge
    
    async def get_by_name(self, edge_name: str) -> Optional[CDNEdge]:
        """Get CDN edge by name."""
        result = await self.db.execute(
            select(CDNEdge).where(CDNEdge.edge_name == edge_name)
        )
        return result.scalar_one_or_none()
    
    async def list_active(self, region: Optional[str] = None) -> List[CDNEdge]:
        """List active CDN edges."""
        query = select(CDNEdge).where(CDNEdge.is_active)
        if region:
            query = query.where(CDNEdge.region == region)
        result = await self.db.execute(query.order_by(CDNEdge.edge_name))
        return result.scalars().all()
    
    async def update_health(self, edge_name: str, health_status: str) -> Optional[CDNEdge]:
        """Update CDN edge health status."""
        edge = await self.get_by_name(edge_name)
        if edge:
            edge.health_status = health_status
            edge.last_health_check = datetime.now(timezone.utc)
            await self.db.flush()
        return edge
