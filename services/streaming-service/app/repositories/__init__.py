"""
Repository layer for Streaming Service data access.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc
from uuid import UUID
from typing import List, Optional
from datetime import datetime
import logging

from app.models import (
    PlaybackSession, PlaybackSessionStatus, VideoManifest, TranscodingJob,
    TranscodingStatus, StreamingQualityProfile, CDNRegion, StreamingStatistics,
    DownloadSession
)

logger = logging.getLogger(__name__)


class BaseRepository:
    """Base repository with common transaction management."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def commit(self):
        await self.session.commit()
    
    async def rollback(self):
        await self.session.rollback()
    
    async def flush(self):
        await self.session.flush()


class PlaybackSessionRepository(BaseRepository):
    """Repository for playback session operations."""
    
    async def create(self, user_id: UUID, content_id: UUID, episode_id: Optional[UUID],
                    device_id: str, protocol: str, resolution: str, bitrate_kbps: int,
                    total_duration_seconds: int) -> PlaybackSession:
        """Create a new playback session."""
        session = PlaybackSession(
            user_id=user_id, content_id=content_id, episode_id=episode_id,
            device_id=device_id, protocol=protocol, resolution=resolution,
            bitrate_kbps=bitrate_kbps, total_duration_seconds=total_duration_seconds
        )
        self.session.add(session)
        await self.flush()
        return session
    
    async def get_by_id(self, session_id: UUID) -> Optional[PlaybackSession]:
        """Get session by ID."""
        return await self.session.get(PlaybackSession, session_id)
    
    async def get_active_sessions(self, user_id: UUID) -> List[PlaybackSession]:
        """Get active sessions for user."""
        result = await self.session.execute(
            select(PlaybackSession)
            .where(and_(PlaybackSession.user_id == user_id,
                       PlaybackSession.status == PlaybackSessionStatus.ACTIVE))
        )
        return result.scalars().all()
    
    async def update(self, session_id: UUID, **kwargs) -> Optional[PlaybackSession]:
        """Update session."""
        session = await self.get_by_id(session_id)
        if session:
            for key, value in kwargs.items():
                if hasattr(session, key) and value is not None:
                    setattr(session, key, value)
            await self.flush()
        return session
    
    async def mark_completed(self, session_id: UUID) -> Optional[PlaybackSession]:
        """Mark session as completed."""
        from datetime import datetime
        return await self.update(session_id, status=PlaybackSessionStatus.COMPLETED, ended_at=datetime.utcnow())


class VideoManifestRepository(BaseRepository):
    """Repository for video manifest operations."""
    
    async def create(self, episode_id: UUID, content_id: UUID, protocol: str,
                    manifest_url: str, manifest_content: str, variants: List[str],
                    available_bitrates: List[int]) -> VideoManifest:
        """Create a new manifest."""
        manifest = VideoManifest(
            episode_id=episode_id, content_id=content_id, protocol=protocol,
            manifest_url=manifest_url, manifest_content=manifest_content,
            variants=variants, available_bitrates=available_bitrates
        )
        self.session.add(manifest)
        await self.flush()
        return manifest
    
    async def get_by_id(self, manifest_id: UUID) -> Optional[VideoManifest]:
        """Get manifest by ID."""
        return await self.session.get(VideoManifest, manifest_id)
    
    async def get_by_episode_and_protocol(self, episode_id: UUID, protocol: str) -> Optional[VideoManifest]:
        """Get manifest by episode and protocol."""
        result = await self.session.execute(
            select(VideoManifest)
            .where(and_(VideoManifest.episode_id == episode_id, VideoManifest.protocol == protocol))
        )
        return result.scalars().first()


class TranscodingJobRepository(BaseRepository):
    """Repository for transcoding job operations."""
    
    async def create(self, episode_id: UUID, content_id: UUID, input_file_path: str,
                    target_resolutions: List[str], target_bitrates: List[int],
                    priority: int = 5) -> TranscodingJob:
        """Create a new transcoding job."""
        job = TranscodingJob(
            episode_id=episode_id, content_id=content_id, input_file_path=input_file_path,
            target_resolutions=target_resolutions, target_bitrates=target_bitrates,
            priority=priority
        )
        self.session.add(job)
        await self.flush()
        return job
    
    async def get_by_id(self, job_id: UUID) -> Optional[TranscodingJob]:
        """Get job by ID."""
        return await self.session.get(TranscodingJob, job_id)
    
    async def get_pending_jobs(self, limit: int = 10) -> List[TranscodingJob]:
        """Get pending transcoding jobs ordered by priority."""
        result = await self.session.execute(
            select(TranscodingJob)
            .where(TranscodingJob.status == TranscodingStatus.PENDING)
            .order_by(desc(TranscodingJob.priority))
            .limit(limit)
        )
        return result.scalars().all()
    
    async def update(self, job_id: UUID, **kwargs) -> Optional[TranscodingJob]:
        """Update job."""
        job = await self.get_by_id(job_id)
        if job:
            for key, value in kwargs.items():
                if hasattr(job, key) and value is not None:
                    setattr(job, key, value)
            await self.flush()
        return job


class QualityProfileRepository(BaseRepository):
    """Repository for quality profile operations."""
    
    async def create(self, name: str, resolution: str, bitrate_kbps: int,
                    min_bandwidth_kbps: int, max_bandwidth_kbps: int, **kwargs) -> StreamingQualityProfile:
        """Create a new quality profile."""
        profile = StreamingQualityProfile(
            name=name, resolution=resolution, bitrate_kbps=bitrate_kbps,
            min_bandwidth_kbps=min_bandwidth_kbps, max_bandwidth_kbps=max_bandwidth_kbps,
            **kwargs
        )
        self.session.add(profile)
        await self.flush()
        return profile
    
    async def get_by_id(self, profile_id: UUID) -> Optional[StreamingQualityProfile]:
        """Get profile by ID."""
        return await self.session.get(StreamingQualityProfile, profile_id)
    
    async def get_by_name(self, name: str) -> Optional[StreamingQualityProfile]:
        """Get profile by name."""
        result = await self.session.execute(
            select(StreamingQualityProfile).where(StreamingQualityProfile.name == name)
        )
        return result.scalars().first()
    
    async def get_all_active(self) -> List[StreamingQualityProfile]:
        """Get all active quality profiles."""
        result = await self.session.execute(
            select(StreamingQualityProfile).where(StreamingQualityProfile.is_active == True)
        )
        return result.scalars().all()


class CDNRegionRepository(BaseRepository):
    """Repository for CDN region operations."""
    
    async def create(self, region_code: str, region_name: str, country: str,
                    cdn_provider: str, bandwidth_capacity_gbps: float, **kwargs) -> CDNRegion:
        """Create a new CDN region."""
        region = CDNRegion(
            region_code=region_code, region_name=region_name, country=country,
            cdn_provider=cdn_provider, bandwidth_capacity_gbps=bandwidth_capacity_gbps,
            **kwargs
        )
        self.session.add(region)
        await self.flush()
        return region
    
    async def get_by_id(self, region_id: UUID) -> Optional[CDNRegion]:
        """Get region by ID."""
        return await self.session.get(CDNRegion, region_id)
    
    async def get_all_active(self) -> List[CDNRegion]:
        """Get all active CDN regions."""
        result = await self.session.execute(
            select(CDNRegion).where(CDNRegion.is_active == True)
        )
        return result.scalars().all()


class StreamingMetricsRepository(BaseRepository):
    """Repository for streaming quality metrics operations."""

    async def create(self, session_id: UUID, bandwidth_mbps: float, resolution: str,
                     bitrate_kbps: int, buffer_seconds: float, stalls: int) -> StreamingStatistics:
        """Record a metrics sample as an aggregated statistics row."""
        stats = StreamingStatistics(
            content_id=session_id,
            period_start=datetime.utcnow().replace(minute=0, second=0, microsecond=0),
            period_end=datetime.utcnow(),
            period_type="hourly",
            total_streams=1,
            average_bitrate_kbps=bitrate_kbps,
            average_buffer_ratio=buffer_seconds,
            stalls_per_session=float(stalls),
        )
        self.session.add(stats)
        await self.flush()
        return stats

    async def get_by_id(self, stats_id: UUID) -> Optional[StreamingStatistics]:
        """Get statistics by ID."""
        return await self.session.get(StreamingStatistics, stats_id)


class DownloadSessionRepository(BaseRepository):
    """Repository for download session operations."""
    
    async def create(self, user_id: UUID, episode_id: UUID, device_id: str,
                    resolution: str, total_bytes: int) -> DownloadSession:
        """Create a new download session."""
        download = DownloadSession(
            user_id=user_id, episode_id=episode_id, device_id=device_id,
            resolution=resolution, total_bytes=total_bytes, status="queued"
        )
        self.session.add(download)
        await self.flush()
        return download
    
    async def get_by_id(self, download_id: UUID) -> Optional[DownloadSession]:
        """Get download by ID."""
        return await self.session.get(DownloadSession, download_id)
    
    async def get_user_downloads(self, user_id: UUID) -> List[DownloadSession]:
        """Get all downloads for user."""
        result = await self.session.execute(
            select(DownloadSession).where(DownloadSession.user_id == user_id)
        )
        return result.scalars().all()
    
    async def update(self, download_id: UUID, **kwargs) -> Optional[DownloadSession]:
        """Update download session."""
        download = await self.get_by_id(download_id)
        if download:
            for key, value in kwargs.items():
                if hasattr(download, key) and value is not None:
                    setattr(download, key, value)
            await self.flush()
        return download


# The streaming API routes construct the service with these canonical names.
# Keep them as aliases onto the existing repository classes.
StreamingSessionRepository = PlaybackSessionRepository
VideoManifestRepository = VideoManifestRepository
WatchHistoryRepository = DownloadSessionRepository
