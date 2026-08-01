"""Integration tests for Streaming Service."""
import pytest
import pytest_asyncio
from uuid import UUID, uuid4
from datetime import datetime
from httpx import AsyncClient
from app.main import app
from app.services import StreamingService
from app.repositories import (
    PlaybackSessionRepository, VideoManifestRepository, TranscodingJobRepository,
    QualityProfileRepository, CDNRegionRepository, DownloadSessionRepository
)
from app.schemas import (
    PlaybackSessionCreateRequest, PlaybackSessionUpdateRequest,
    ManifestGenerationRequest, TranscodingJobCreateRequest,
    QualityProfileCreateRequest, CDNRegionCreateRequest, DownloadSessionCreateRequest
)
from app.models import PlaybackSessionStatus, DeliveryProtocol, TranscodingStatus


@pytest_asyncio.fixture
async def streaming_service(db_session):
    """StreamingService instance with test DB."""
    return StreamingService(db_session)


class TestPlaybackSessionIntegration:
    """Integration tests for playback sessions."""

    async def test_start_playback_session(self, streaming_service, db_session):
        """Test starting a playback session."""
        from app.schemas import PlaybackSessionCreateRequest
        
        request = PlaybackSessionCreateRequest(
            user_id=uuid4(),
            content_id=uuid4(),
            device_id="device-123",
            protocol="hls",
            resolution="1080p",
            bitrate_kbps=5000,
        )
        
        session = await streaming_service.start_playback_session(request)
        
        assert session.user_id == request.user_id
        assert session.content_id == request.content_id
        assert session.device_id == request.device_id
        assert session.protocol == request.protocol
        assert session.status.value == "active"
        
        # Verify in DB
        from app.repositories import PlaybackSessionRepository
        repo = PlaybackSessionRepository(db_session)
        db_session_obj = await repo.get_by_id(session.id)
        assert db_session_obj is not None

    async def test_get_playback_session(self, streaming_service, db_session):
        """Test getting a playback session."""
        from app.schemas import PlaybackSessionCreateRequest
        
        request = PlaybackSessionCreateRequest(
            user_id=uuid4(),
            content_id=uuid4(),
            device_id="device-456",
            protocol="dash",
            resolution="720p",
            bitrate_kbps=2500,
        )
        
        session = await streaming_service.start_playback_session(request)
        retrieved = await streaming_service.get_playback_session(session.id)
        
        assert retrieved.id == session.id

    async def test_update_playback_session(self, streaming_service, db_session):
        """Test updating playback session."""
        from app.schemas import PlaybackSessionCreateRequest, PlaybackSessionUpdateRequest
        
        request = PlaybackSessionCreateRequest(
            user_id=uuid4(),
            content_id=uuid4(),
            device_id="device-789",
        )
        
        session = await streaming_service.start_playback_session(request)
        
        update_request = PlaybackSessionUpdateRequest(
            current_position_seconds=3600,
            resolution="720p",
        )
        
        updated = await streaming_service.update_playback_session(session.id, update_request)
        
        assert updated.current_position_seconds == 3600
        assert updated.resolution == "720p"

    async def test_end_playback_session(self, streaming_service, db_session):
        """Test ending a playback session."""
        from app.schemas import PlaybackSessionCreateRequest
        
        request = PlaybackSessionCreateRequest(
            user_id=uuid4(),
            content_id=uuid4(),
            device_id="device-end",
        )
        
        session = await streaming_service.start_playback_session(request)
        ended = await streaming_service.end_playback_session(session.id)
        
        assert ended.status.value == "completed"
        assert ended.ended_at is not None


class TestVideoManifestIntegration:
    """Integration tests for video manifests."""

    async def test_generate_manifest(self, streaming_service, db_session):
        """Test generating a video manifest."""
        from app.schemas import ManifestGenerationRequest
        from app.models import DeliveryProtocol
        
        request = ManifestGenerationRequest(
            episode_id=uuid4(),
            content_id=uuid4(),
            protocol=DeliveryProtocol.HLS,
            variants=["1080p", "720p", "480p"],
        )
        
        manifest = await streaming_service.generate_manifest(request)
        
        assert manifest.episode_id == request.episode_id
        assert manifest.protocol == request.protocol
        assert manifest.variants == request.variants

    async def test_get_manifest_for_episode(self, streaming_service, db_session):
        """Test getting manifest for episode."""
        from app.schemas import ManifestGenerationRequest
        from app.models import DeliveryProtocol
        
        episode_id = uuid4()
        request = ManifestGenerationRequest(
            episode_id=episode_id,
            content_id=uuid4(),
            protocol=DeliveryProtocol.HLS,
        )
        
        await streaming_service.generate_manifest(request)
        manifest = await streaming_service.get_manifest_for_episode(episode_id, "hls")
        
        assert manifest is not None
        assert manifest.episode_id == episode_id


class TestTranscodingIntegration:
    """Integration tests for transcoding jobs."""

    async def test_create_transcoding_job(self, streaming_service, db_session):
        """Test creating a transcoding job."""
        from app.schemas import TranscodingJobCreateRequest
        
        request = TranscodingJobCreateRequest(
            episode_id=uuid4(),
            content_id=uuid4(),
            input_file_path="/input/video.mp4",
            target_resolutions=["1080p", "720p"],
            target_bitrates=[5000, 2500],
            priority=5,
        )
        
        job = await streaming_service.create_transcoding_job(request)
        
        assert job.episode_id == request.episode_id
        assert job.target_resolutions == request.target_resolutions
        assert job.status.value == "pending"

    async def test_get_pending_jobs(self, streaming_service, db_session):
        """Test getting pending transcoding jobs."""
        from app.schemas import TranscodingJobCreateRequest
        
        for i in range(3):
            request = TranscodingJobCreateRequest(
                episode_id=uuid4(),
                content_id=uuid4(),
                input_file_path=f"/input/video{i}.mp4",
                priority=5 - i,
            )
            await streaming_service.create_transcoding_job(request)
        
        jobs = await streaming_service.get_pending_jobs(limit=10)
        
        assert len(jobs) >= 3


class TestQualityProfileIntegration:
    """Integration tests for quality profiles."""

    async def test_create_quality_profile(self, streaming_service, db_session):
        """Test creating a quality profile."""
        from app.schemas import QualityProfileCreateRequest
        
        request = QualityProfileCreateRequest(
            name="1080p High",
            resolution="1080p",
            bitrate_kbps=5000,
            min_bandwidth_kbps=6000,
            max_bandwidth_kbps=10000,
            description="High quality 1080p",
            fps=30,
            video_codec="h264",
            audio_codec="aac",
            audio_bitrate_kbps=128,
            supported_devices=["web", "ios", "android"],
        )
        
        profile = await streaming_service.create_quality_profile(request)
        
        assert profile.name == "1080p High"
        assert profile.resolution == "1080p"
        assert profile.bitrate_kbps == 5000


class TestCDNIntegration:
    """Integration tests for CDN regions."""

    async def test_create_cdn_region(self, streaming_service, db_session):
        """Test creating a CDN region."""
        from app.schemas import CDNRegionCreateRequest
        
        request = CDNRegionCreateRequest(
            region_code="us-east",
            region_name="US East",
            country="United States",
            cdn_provider="Cloudflare",
            bandwidth_capacity_gbps=100.0,
            latitude=40.7128,
            longitude=-74.0060,
            max_concurrent_streams=10000,
        )
        
        region = await streaming_service.create_cdn_region(request)
        
        assert region.region_code == "us-east"
        assert region.region_name == "US East"
        assert region.bandwidth_capacity_gbps == 100.0


class TestDownloadIntegration:
    """Integration tests for downloads."""

    async def test_create_download_session(self, streaming_service, db_session):
        """Test creating a download session."""
        from app.schemas import DownloadSessionCreateRequest
        
        request = DownloadSessionCreateRequest(
            user_id=uuid4(),
            episode_id=uuid4(),
            device_id="device-download",
            resolution="720p",
            download_ttl_days=30,
        )
        
        download = await streaming_service.create_download_session(request)
        
        assert download.user_id == request.user_id
        assert download.resolution == "720p"
        assert download.status == "queued"

    async def test_update_download_progress(self, streaming_service, db_session):
        """Test updating download progress."""
        from app.schemas import DownloadSessionCreateRequest
        
        request = DownloadSessionCreateRequest(
            user_id=uuid4(),
            episode_id=uuid4(),
            device_id="device-progress",
            resolution="1080p",
            total_bytes=1000000000,
        )
        
        download = await streaming_service.create_download_session(request)
        
        updated = await streaming_service.update_download_progress(download.id, 500000000)
        
        assert updated.bytes_downloaded == 500000000
        assert updated.progress_percent == 50