from fastapi import HTTPException

"""
Service layer for Streaming Service business logic.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import (
    CDNRegionRepository,
    DownloadSessionRepository,
    PlaybackSessionRepository,
    QualityProfileRepository,
    TranscodingJobRepository,
    VideoManifestRepository,
)
from app.schemas import (
    CDNRegionCreateRequest,
    DownloadSessionCreateRequest,
    ManifestGenerationRequest,
    PlaybackSessionCreateRequest,
    PlaybackSessionUpdateRequest,
    QualityProfileCreateRequest,
    TranscodingJobCreateRequest,
)

logger = logging.getLogger(__name__)


class StreamingService:
    """Service for video streaming operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.playback_repo = PlaybackSessionRepository(session)
        self.manifest_repo = VideoManifestRepository(session)
        self.transcoding_repo = TranscodingJobRepository(session)
        self.quality_repo = QualityProfileRepository(session)
        self.cdn_repo = CDNRegionRepository(session)
        self.download_repo = DownloadSessionRepository(session)
    
    # Playback session operations
    
    async def start_playback_session(self, request: PlaybackSessionCreateRequest):
        """Start a new playback session."""
        try:
            session = await self.playback_repo.create(
                user_id=request.user_id,
                content_id=request.content_id,
                episode_id=request.episode_id,
                device_id=request.device_id,
                protocol=request.protocol,
                resolution=request.resolution,
                bitrate_kbps=request.bitrate_kbps,
                total_duration_seconds=0
            )
            await self.playback_repo.commit()
            return session
        except Exception as e:
            await self.playback_repo.rollback()
            logger.error(f"Failed to start playback session: {e}")
            raise
    
    async def get_playback_session(self, session_id: UUID):
        """Get playback session by ID."""
        return await self.playback_repo.get_by_id(session_id)
    
    async def get_active_sessions(self, user_id: UUID):
        """Get active sessions for user."""
        return await self.playback_repo.get_active_sessions(user_id)
    
    async def update_playback_session(self, session_id: UUID, request: PlaybackSessionUpdateRequest):
        """Update playback session."""
        try:
            update_data = request.model_dump(exclude_unset=True)
            session = await self.playback_repo.update(session_id, **update_data)
            await self.playback_repo.commit()
            return session
        except Exception as e:
            await self.playback_repo.rollback()
            logger.error(f"Failed to update playback session: {e}")
            raise
    
    async def end_playback_session(self, session_id: UUID):
        """End playback session."""
        try:
            session = await self.playback_repo.mark_completed(session_id)
            await self.playback_repo.commit()
            return session
        except Exception as e:
            await self.playback_repo.rollback()
            logger.error(f"Failed to end playback session: {e}")
            raise
    
    # Video manifest operations
    
    async def generate_manifest(self, request: ManifestGenerationRequest):
        """Generate video manifest for streaming."""
        try:
            # Check if manifest already exists
            existing = await self.manifest_repo.get_by_episode_and_protocol(
                request.episode_id, request.protocol
            )
            if existing:
                return existing
            
            # Generate manifest content (simplified)
            manifest_content = self._generate_manifest_content(request)
            manifest_url = f"/manifests/{request.episode_id}/{request.protocol}.m3u8"
            
            manifest = await self.manifest_repo.create(
                episode_id=request.episode_id,
                content_id=request.content_id,
                protocol=request.protocol,
                manifest_url=manifest_url,
                manifest_content=manifest_content,
                variants=request.variants,
                available_bitrates=[5000, 2500, 1000]
            )
            await self.manifest_repo.commit()
            return manifest
        except Exception as e:
            await self.manifest_repo.rollback()
            logger.error(f"Failed to generate manifest: {e}")
            raise
    
    def _generate_manifest_content(self, request: ManifestGenerationRequest) -> str:
        """Generate M3U8/MPD manifest content."""
        if request.protocol == "hls":
            return """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
segment-001.ts
#EXT-X-ENDLIST
"""
        return ""  # Simplified
    
    async def get_manifest(self, manifest_id: UUID):
        """Get manifest by ID."""
        return await self.manifest_repo.get_by_id(manifest_id)
    
    async def get_manifest_for_episode(self, episode_id: UUID, protocol: str):
        """Get manifest for episode and protocol."""
        return await self.manifest_repo.get_by_episode_and_protocol(episode_id, protocol)
    
    # Transcoding operations
    
    async def create_transcoding_job(self, request: TranscodingJobCreateRequest):
        """Create a new transcoding job."""
        try:
            job = await self.transcoding_repo.create(
                episode_id=request.episode_id,
                content_id=request.content_id,
                input_file_path=request.input_file_path,
                target_resolutions=request.target_resolutions,
                target_bitrates=request.target_bitrates,
                priority=request.priority
            )
            await self.transcoding_repo.commit()
            return job
        except Exception as e:
            await self.transcoding_repo.rollback()
            logger.error(f"Failed to create transcoding job: {e}")
            raise
    
    async def get_transcoding_job(self, job_id: UUID):
        """Get transcoding job by ID."""
        return await self.transcoding_repo.get_by_id(job_id)
    
    async def get_pending_jobs(self, limit: int = 10):
        """Get pending transcoding jobs."""
        return await self.transcoding_repo.get_pending_jobs(limit)
    
    async def update_transcoding_progress(self, job_id: UUID, progress_percent: int, error_message: str | None = None):
        """Update transcoding job progress."""
        try:
            job = await self.transcoding_repo.update(job_id, progress_percent=progress_percent, error_message=error_message)
            await self.transcoding_repo.commit()
            return job
        except Exception as e:
            await self.transcoding_repo.rollback()
            logger.error(f"Failed to update transcoding job: {e}")
            raise
    
    async def complete_transcoding_job(self, job_id: UUID, output_paths: dict):
        """Mark transcoding job as completed."""
        try:
            job = await self.transcoding_repo.update(
                job_id,
                status="completed",
                progress_percent=100,
                completed_at=datetime.now(UTC),
                output_paths=output_paths
            )
            await self.transcoding_repo.commit()
            return job
        except Exception as e:
            await self.transcoding_repo.rollback()
            logger.error(f"Failed to complete transcoding job: {e}")
            raise
    
    # Quality profile operations
    
    async def create_quality_profile(self, request: QualityProfileCreateRequest):
        """Create a new quality profile."""
        try:
            profile = await self.quality_repo.create(
                name=request.name,
                resolution=request.resolution,
                bitrate_kbps=request.bitrate_kbps,
                min_bandwidth_kbps=request.min_bandwidth_kbps,
                max_bandwidth_kbps=request.max_bandwidth_kbps,
                description=request.description,
                fps=request.fps,
                video_codec=request.video_codec,
                audio_codec=request.audio_codec,
                audio_bitrate_kbps=request.audio_bitrate_kbps,
                supported_devices=request.supported_devices
            )
            await self.quality_repo.commit()
            return profile
        except Exception as e:
            await self.quality_repo.rollback()
            logger.error(f"Failed to create quality profile: {e}")
            raise
    
    async def get_quality_profile(self, profile_id: UUID):
        """Get quality profile by ID."""
        return await self.quality_repo.get_by_id(profile_id)
    
    async def get_quality_profiles_for_bandwidth(self, bandwidth_kbps: int):
        """Get quality profiles suitable for given bandwidth."""
        all_profiles = await self.quality_repo.get_all_active()
        return [p for p in all_profiles if p.min_bandwidth_kbps <= bandwidth_kbps <= p.max_bandwidth_kbps]
    
    # CDN region operations
    
    async def create_cdn_region(self, request: CDNRegionCreateRequest):
        """Create a new CDN region."""
        try:
            region = await self.cdn_repo.create(
                region_code=request.region_code,
                region_name=request.region_name,
                country=request.country,
                cdn_provider=request.cdn_provider,
                bandwidth_capacity_gbps=request.bandwidth_capacity_gbps,
                latitude=request.latitude,
                longitude=request.longitude,
                max_concurrent_streams=request.max_concurrent_streams
            )
            await self.cdn_repo.commit()
            return region
        except Exception as e:
            await self.cdn_repo.rollback()
            logger.error(f"Failed to create CDN region: {e}")
            raise
    
    async def get_cdn_region(self, region_id: UUID):
        """Get CDN region by ID."""
        return await self.cdn_repo.get_by_id(region_id)
    
    async def get_all_cdn_regions(self):
        """Get all active CDN regions."""
        return await self.cdn_repo.get_all_active()
    
    # Download operations
    
    async def create_download_session(self, request: DownloadSessionCreateRequest):
        """Create a new download session."""
        try:
            download = await self.download_repo.create(
                user_id=request.user_id,
                episode_id=request.episode_id,
                device_id=request.device_id,
                resolution=request.resolution,
                total_bytes=0  # Would be set from file size
            )
            await self.download_repo.commit()
            return download
        except Exception as e:
            await self.download_repo.rollback()
            logger.error(f"Failed to create download session: {e}")
            raise
    
    async def get_download_session(self, download_id: UUID):
        """Get download session by ID."""
        return await self.download_repo.get_by_id(download_id)
    
    async def get_user_downloads(self, user_id: UUID):
        """Get all downloads for user."""
        return await self.download_repo.get_user_downloads(user_id)
    
    async def update_download_progress(self, download_id: UUID, bytes_downloaded: int):
        """Update download progress."""
        try:
            download = await self.download_repo.get_by_id(download_id)
            if not download:
                return None
            
            progress_percent = int((bytes_downloaded / download.total_bytes) * 100) if download.total_bytes > 0 else 0
            download = await self.download_repo.update(
                download_id,
                bytes_downloaded=bytes_downloaded,
                progress_percent=progress_percent
            )
            await self.download_repo.commit()
            return download
        except Exception as e:
            await self.download_repo.rollback()
            logger.error(f"Failed to update download progress: {e}")
            raise
