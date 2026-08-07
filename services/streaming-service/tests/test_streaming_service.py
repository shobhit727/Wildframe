"""Streaming Service tests — covers the actual StreamingService API."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.services import StreamingService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def user_id():
    """Generate test user ID."""
    return uuid4()


@pytest.fixture
def content_id():
    """Generate test content ID."""
    return uuid4()


@pytest.fixture
def episode_id():
    """Generate test episode ID."""
    return uuid4()


@pytest.fixture
def mock_db():
    """Create mock database session."""
    return AsyncMock()


@pytest.fixture
def mock_repositories():
    """Create mock repositories."""
    return {
        "playback_repo": AsyncMock(),
        "manifest_repo": AsyncMock(),
        "transcoding_repo": AsyncMock(),
        "quality_repo": AsyncMock(),
        "cdn_repo": AsyncMock(),
        "download_repo": AsyncMock(),
    }


@pytest.fixture
def streaming_service(mock_db, mock_repositories):
    """Create StreamingService with mocked repositories."""
    service = StreamingService(mock_db)
    service.playback_repo = mock_repositories["playback_repo"]
    service.manifest_repo = mock_repositories["manifest_repo"]
    service.transcoding_repo = mock_repositories["transcoding_repo"]
    service.quality_repo = mock_repositories["quality_repo"]
    service.cdn_repo = mock_repositories["cdn_repo"]
    service.download_repo = mock_repositories["download_repo"]
    return service


class TestPlaybackSessions:
    """Test playback session operations."""

    async def test_start_playback_session(
        self, streaming_service, mock_repositories, user_id, content_id
    ):
        """Test starting a playback session."""
        from app.schemas import PlaybackSessionCreateRequest

        mock_repositories["playback_repo"].create.return_value = MagicMock()

        request = PlaybackSessionCreateRequest(
            user_id=user_id, content_id=content_id, device_id="dev-1"
        )
        result = await streaming_service.start_playback_session(request)

        assert result is not None
        mock_repositories["playback_repo"].create.assert_called_once()

    async def test_get_playback_session(self, streaming_service, mock_repositories):
        """Test getting a playback session."""
        session_id = uuid4()
        mock_repositories["playback_repo"].get_by_id.return_value = MagicMock()

        result = await streaming_service.get_playback_session(session_id)

        assert result is not None
        mock_repositories["playback_repo"].get_by_id.assert_called_once_with(session_id)

    async def test_end_playback_session(self, streaming_service, mock_repositories):
        """Test ending a playback session."""
        session_id = uuid4()
        mock_repositories["playback_repo"].mark_completed.return_value = MagicMock()

        result = await streaming_service.end_playback_session(session_id)

        assert result is not None
        mock_repositories["playback_repo"].mark_completed.assert_called_once_with(session_id)


class TestManifests:
    """Test manifest operations."""

    async def test_generate_manifest(self, streaming_service, mock_repositories, episode_id):
        """Test generating a manifest."""
        from app.schemas import ManifestGenerationRequest

        mock_repositories["manifest_repo"].get_by_episode_and_protocol.return_value = None
        mock_repositories["manifest_repo"].create.return_value = MagicMock()

        request = ManifestGenerationRequest(episode_id=episode_id, content_id=uuid4())
        result = await streaming_service.generate_manifest(request)

        assert result is not None
        mock_repositories["manifest_repo"].create.assert_called_once()

    async def test_generate_manifest_existing(
        self, streaming_service, mock_repositories, episode_id
    ):
        """Test generating manifest that already exists."""
        from app.schemas import ManifestGenerationRequest

        existing = MagicMock()
        mock_repositories["manifest_repo"].get_by_episode_and_protocol.return_value = existing

        request = ManifestGenerationRequest(episode_id=episode_id, content_id=uuid4())
        result = await streaming_service.generate_manifest(request)

        assert result is existing
        mock_repositories["manifest_repo"].create.assert_not_called()

    async def test_get_manifest(self, streaming_service, mock_repositories):
        """Test getting a manifest."""
        manifest_id = uuid4()
        mock_repositories["manifest_repo"].get_by_id.return_value = MagicMock()

        result = await streaming_service.get_manifest(manifest_id)

        assert result is not None
        mock_repositories["manifest_repo"].get_by_id.assert_called_once_with(manifest_id)


class TestCDNRegions:
    """Test CDN region operations."""

    async def test_create_cdn_region(self, streaming_service, mock_repositories):
        """Test creating a CDN region."""
        from app.schemas import CDNRegionCreateRequest

        mock_repositories["cdn_repo"].create.return_value = MagicMock()

        request = CDNRegionCreateRequest(
            region_code="us-east",
            region_name="US East",
            country="US",
            cdn_provider="cloudfront",
            bandwidth_capacity_gbps=100.0,
        )
        result = await streaming_service.create_cdn_region(request)

        assert result is not None
        mock_repositories["cdn_repo"].create.assert_called_once()

    async def test_get_all_cdn_regions(self, streaming_service, mock_repositories):
        """Test listing CDN regions."""
        mock_repositories["cdn_repo"].get_all_active.return_value = [MagicMock(), MagicMock()]

        result = await streaming_service.get_all_cdn_regions()

        assert len(result) == 2
        mock_repositories["cdn_repo"].get_all_active.assert_called_once()


class TestQualityProfiles:
    """Test quality profile operations."""

    async def test_create_quality_profile(self, streaming_service, mock_repositories):
        """Test creating a quality profile."""
        from app.schemas import QualityProfileCreateRequest

        mock_repositories["quality_repo"].create.return_value = MagicMock()

        request = QualityProfileCreateRequest(
            name="1080p",
            resolution="1080p",
            bitrate_kbps=5000,
            min_bandwidth_kbps=5000,
            max_bandwidth_kbps=10000,
        )
        result = await streaming_service.create_quality_profile(request)

        assert result is not None
        mock_repositories["quality_repo"].create.assert_called_once()


class TestTranscoding:
    """Test transcoding job operations."""

    async def test_create_transcoding_job(self, streaming_service, mock_repositories, episode_id):
        """Test creating a transcoding job."""
        from app.schemas import TranscodingJobCreateRequest

        mock_repositories["transcoding_repo"].create.return_value = MagicMock()

        request = TranscodingJobCreateRequest(
            episode_id=episode_id, content_id=uuid4(), input_file_path="/tmp/video.mp4"
        )
        result = await streaming_service.create_transcoding_job(request)

        assert result is not None
        mock_repositories["transcoding_repo"].create.assert_called_once()

    async def test_get_pending_jobs(self, streaming_service, mock_repositories):
        """Test getting pending transcoding jobs."""
        mock_repositories["transcoding_repo"].get_pending_jobs.return_value = [MagicMock()]

        result = await streaming_service.get_pending_jobs()

        assert len(result) == 1
        mock_repositories["transcoding_repo"].get_pending_jobs.assert_called_once()
