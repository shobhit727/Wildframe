"""Streaming service tests."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.services import StreamingService


@pytest.fixture
def user_id():
    """Generate test user ID."""
    return uuid4()


@pytest.fixture
def content_id():
    """Generate test content ID."""
    return uuid4()


@pytest.fixture
def session_id():
    """Generate test session ID."""
    return uuid4()


@pytest.fixture
def mock_db():
    """Create mock database session."""
    return AsyncMock()


@pytest.fixture
def mock_repositories():
    """Create mock repositories."""
    return {
        "session_repo": AsyncMock(),
        "manifest_repo": AsyncMock(),
        "metrics_repo": AsyncMock(),
        "subtitle_repo": AsyncMock(),
        "audio_repo": AsyncMock(),
        "cdn_repo": AsyncMock(),
    }


@pytest.fixture
def streaming_service(mock_db, mock_repositories):
    """Create StreamingService instance with mocks."""
    service = StreamingService(mock_db)
    service.session_repo = mock_repositories["session_repo"]
    service.manifest_repo = mock_repositories["manifest_repo"]
    service.metrics_repo = mock_repositories["metrics_repo"]
    service.subtitle_repo = mock_repositories["subtitle_repo"]
    service.audio_repo = mock_repositories["audio_repo"]
    service.cdn_repo = mock_repositories["cdn_repo"]
    return service


class TestSessionManagement:
    """Test session management."""

    @pytest.mark.asyncio
    async def test_start_streaming(self, streaming_service, user_id, content_id, mock_repositories):
        """Test starting streaming."""
        mock_manifest = MagicMock()
        mock_manifest.duration_seconds = 7200
        mock_repositories["manifest_repo"].get_by_media_key.return_value = mock_manifest
        mock_repositories["manifest_repo"].is_valid.return_value = True

        mock_session = MagicMock()
        mock_session.id = uuid4()
        mock_repositories["session_repo"].create.return_value = mock_session

        session = await streaming_service.start_streaming(
            user_id=user_id,
            content_id=content_id,
            media_key="test_media",
            content_type="movie",
            device_type="web",
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
        )

        assert session.id is not None

    @pytest.mark.asyncio
    async def test_get_streaming_session(self, streaming_service, mock_repositories):
        """Test getting streaming session."""
        mock_session = MagicMock()
        mock_session.id = uuid4()
        mock_repositories["session_repo"].get_by_token.return_value = mock_session

        session = await streaming_service.get_streaming_session("token123")

        assert session.id is not None

    @pytest.mark.asyncio
    async def test_heartbeat(self, streaming_service, mock_repositories):
        """Test heartbeat."""
        mock_session = MagicMock()
        mock_session.id = uuid4()
        mock_repositories["session_repo"].get_by_token.return_value = mock_session
        mock_repositories["session_repo"].update_heartbeat.return_value = mock_session

        session = await streaming_service.heartbeat("token123", 3600, 5.0, 2500)

        assert session.id is not None

    @pytest.mark.asyncio
    async def test_end_streaming(self, streaming_service, mock_repositories):
        """Test ending streaming."""
        mock_session = MagicMock()
        mock_session.id = uuid4()
        mock_repositories["session_repo"].get_by_token.return_value = mock_session
        mock_repositories["session_repo"].end_session.return_value = mock_session

        session = await streaming_service.end_streaming("token123", 3600)

        assert session.id is not None


class TestMetrics:
    """Test metrics recording."""

    @pytest.mark.asyncio
    async def test_record_metrics(self, streaming_service, mock_repositories):
        """Test recording metrics."""
        mock_session = MagicMock()
        mock_session.id = uuid4()
        mock_session.user_id = uuid4()
        mock_session.content_id = uuid4()
        mock_repositories["session_repo"].get_by_token.return_value = mock_session

        mock_metrics = MagicMock()
        mock_metrics.id = uuid4()
        mock_repositories["metrics_repo"].record.return_value = mock_metrics

        metrics = await streaming_service.record_metrics(
            "token123",
            bandwidth_mbps=5.0,
            bitrate_kbps=2500,
            quality="1080p",
            rebuffering_seconds=2.0,
            packets_lost=5,
            latency_ms=50,
        )

        assert metrics.id is not None

    @pytest.mark.asyncio
    async def test_record_buffering(self, streaming_service, mock_repositories):
        """Test recording buffering."""
        mock_session = MagicMock()
        mock_session.id = uuid4()
        mock_repositories["session_repo"].get_by_token.return_value = mock_session

        await streaming_service.record_buffering("token123", 2.5)

        mock_repositories["session_repo"].record_buffering.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_session_stats(self, streaming_service, mock_repositories):
        """Test getting session stats."""
        mock_session = MagicMock()
        mock_session.id = uuid4()
        mock_session.played_until_seconds = 3600
        mock_session.duration_seconds = 7200
        mock_session.buffering_count = 2
        mock_session.total_buffer_seconds = 5
        mock_session.stream_quality = "1080p"
        mock_session.started_at = datetime.now(UTC)
        mock_session.last_heartbeat = datetime.now(UTC)
        mock_repositories["session_repo"].get_by_token.return_value = mock_session
        mock_repositories["metrics_repo"].get_average_metrics.return_value = {
            "avg_bandwidth_mbps": 5.0,
            "avg_bitrate_kbps": 2500,
            "total_rebuffer_seconds": 5,
            "packet_loss_total": 10,
            "avg_latency_ms": 50,
        }

        stats = await streaming_service.get_session_stats("token123")

        assert stats["completion_percentage"] == 50.0
        assert stats["buffer_events"] == 2


class TestSubtitles:
    """Test subtitle management."""

    @pytest.mark.asyncio
    async def test_add_subtitle(self, streaming_service, mock_repositories):
        """Test adding subtitle."""
        mock_subtitle = MagicMock()
        mock_subtitle.id = uuid4()
        mock_repositories["subtitle_repo"].create.return_value = mock_subtitle

        subtitle = await streaming_service.add_subtitle(
            media_key="test_media",
            language="en",
            language_name="English",
            subtitle_url="https://example.com/subtitle.vtt",
            format="vtt",
        )

        assert subtitle.id is not None

    @pytest.mark.asyncio
    async def test_list_subtitles(self, streaming_service, mock_repositories):
        """Test listing subtitles."""
        mock_subtitle1 = MagicMock()
        mock_subtitle2 = MagicMock()
        mock_repositories["subtitle_repo"].list_by_media_key.return_value = [
            mock_subtitle1,
            mock_subtitle2,
        ]

        subtitles = await streaming_service.list_subtitles("test_media")

        assert len(subtitles) == 2


class TestAudioTracks:
    """Test audio track management."""

    @pytest.mark.asyncio
    async def test_add_audio_track(self, streaming_service, mock_repositories):
        """Test adding audio track."""
        mock_track = MagicMock()
        mock_track.id = uuid4()
        mock_repositories["audio_repo"].create.return_value = mock_track

        track = await streaming_service.add_audio_track(
            media_key="test_media",
            language="en",
            language_name="English",
            codec="aac",
            bitrate_kbps=128,
            channels=2,
        )

        assert track.id is not None

    @pytest.mark.asyncio
    async def test_list_audio_tracks(self, streaming_service, mock_repositories):
        """Test listing audio tracks."""
        mock_track1 = MagicMock()
        mock_track2 = MagicMock()
        mock_repositories["audio_repo"].list_by_media_key.return_value = [mock_track1, mock_track2]

        tracks = await streaming_service.list_audio_tracks("test_media")

        assert len(tracks) == 2


class TestManifests:
    """Test manifest management."""

    @pytest.mark.asyncio
    async def test_create_manifest(self, streaming_service, mock_repositories):
        """Test creating manifest."""
        mock_manifest = MagicMock()
        mock_manifest.id = uuid4()
        mock_repositories["manifest_repo"].create.return_value = mock_manifest

        manifest = await streaming_service.create_manifest(
            media_key="test_media",
            content_type="movie",
            hls_url="https://example.com/playlist.m3u8",
            dash_url="https://example.com/manifest.mpd",
            bitrates=[500, 1000, 2500, 5000],
            duration_seconds=7200,
        )

        assert manifest.id is not None

    @pytest.mark.asyncio
    async def test_get_manifest(self, streaming_service, mock_repositories):
        """Test getting manifest."""
        mock_manifest = MagicMock()
        mock_manifest.id = uuid4()
        mock_repositories["manifest_repo"].get_by_media_key.return_value = mock_manifest

        manifest = await streaming_service.get_manifest("test_media")

        assert manifest.id is not None
