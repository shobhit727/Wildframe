"""Edge-branch coverage for StreamingService — rollback paths, bandwidth filter,
progress math, manifest reuse."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.services import StreamingService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def service():
    svc = StreamingService(AsyncMock())
    svc.playback_repo = AsyncMock()
    svc.manifest_repo = AsyncMock()
    svc.transcoding_repo = AsyncMock()
    svc.quality_repo = AsyncMock()
    svc.cdn_repo = AsyncMock()
    svc.download_repo = AsyncMock()
    return svc


class TestPlaybackBranches:
    async def test_start_playback_rollback_on_error(self, service):
        from app.schemas import PlaybackSessionCreateRequest

        service.playback_repo.create.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await service.start_playback_session(
                PlaybackSessionCreateRequest(user_id=uuid4(), content_id=uuid4(), device_id="d")
            )

        service.playback_repo.rollback.assert_awaited_once()

    async def test_get_active_sessions(self, service, mocker):
        service.playback_repo.get_active_sessions.return_value = [MagicMock()]

        assert len(await service.get_active_sessions(uuid4())) == 1
        service.playback_repo.get_active_sessions.assert_awaited_once()

    async def test_update_playback_session_rollback(self, service):
        from app.schemas import PlaybackSessionUpdateRequest

        service.playback_repo.update.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await service.update_playback_session(
                uuid4(), PlaybackSessionUpdateRequest(current_position_seconds=30)
            )

        service.playback_repo.rollback.assert_awaited_once()

    async def test_end_playback_session(self, service):
        mock_session = MagicMock()
        service.playback_repo.mark_completed.return_value = mock_session

        result = await service.end_playback_session(uuid4())

        assert result is mock_session
        service.playback_repo.commit.assert_awaited_once()


class TestManifestBranches:
    async def test_manifest_reuses_existing(self, service):
        from app.schemas import ManifestGenerationRequest

        existing = MagicMock()
        service.manifest_repo.get_by_episode_and_protocol.return_value = existing

        result = await service.generate_manifest(
            ManifestGenerationRequest(episode_id=uuid4(), content_id=uuid4())
        )

        assert result is existing
        service.manifest_repo.create.assert_not_awaited()

    async def test_manifest_hls_content(self, service):
        from app.schemas import ManifestGenerationRequest

        request = ManifestGenerationRequest(episode_id=uuid4(), content_id=uuid4())
        content = service._generate_manifest_content(request)

        assert "#EXTM3U" in content
        assert "segment-001.ts" in content

    async def test_manifest_dash_content_empty(self, service):
        from app.schemas import ManifestGenerationRequest

        request = ManifestGenerationRequest(episode_id=uuid4(), content_id=uuid4(), protocol="dash")

        assert service._generate_manifest_content(request) == ""

    async def test_get_manifest_for_episode(self, service):
        service.manifest_repo.get_by_episode_and_protocol.return_value = MagicMock()

        assert await service.get_manifest_for_episode(uuid4(), "hls") is not None


class TestTranscodingBranches:
    async def test_create_job_delegates(self, service):
        from app.schemas import TranscodingJobCreateRequest

        job = MagicMock()
        service.transcoding_repo.create.return_value = job

        result = await service.create_transcoding_job(
            TranscodingJobCreateRequest(
                episode_id=uuid4(),
                content_id=uuid4(),
                input_file_path="/tmp/a.mp4",
                target_resolutions=["1080p"],
                target_bitrates=[5000],
            )
        )

        assert result is job
        service.transcoding_repo.commit.assert_awaited_once()

    async def test_get_transcoding_job(self, service):
        service.transcoding_repo.get_by_id.return_value = MagicMock()

        assert await service.get_transcoding_job(uuid4()) is not None

    async def test_update_progress(self, service):
        job = MagicMock()
        service.transcoding_repo.update.return_value = job

        result = await service.update_transcoding_progress(uuid4(), 50)

        assert result is job
        args, kwargs = service.transcoding_repo.update.await_args
        assert kwargs["progress_percent"] == 50

    async def test_complete_transcoding_job(self, service):
        service.transcoding_repo.update.return_value = MagicMock()

        await service.complete_transcoding_job(uuid4(), {"1080p": "/out/a_1080.m3u8"})

        _, kwargs = service.transcoding_repo.update.await_args
        assert kwargs["status"] == "completed"
        assert kwargs["progress_percent"] == 100
        service.transcoding_repo.commit.assert_awaited_once()


class TestQualityBranches:
    async def test_bandwidth_filter_selects_matching(self, service):
        profiles = [
            MagicMock(min_bandwidth_kbps=500, max_bandwidth_kbps=1500),
            MagicMock(min_bandwidth_kbps=2500, max_bandwidth_kbps=6000),
            MagicMock(min_bandwidth_kbps=100, max_bandwidth_kbps=300),
        ]
        service.quality_repo.get_all_active.return_value = profiles

        result = await service.get_quality_profiles_for_bandwidth(1000)

        assert len(result) == 1
        assert result[0].max_bandwidth_kbps == 1500

    async def test_get_quality_profile(self, service):
        service.quality_repo.get_by_id.return_value = MagicMock()

        assert await service.get_quality_profile(uuid4()) is not None


class TestCdnBranches:
    async def test_create_cdn_region_rollback(self, service):
        from app.schemas import CDNRegionCreateRequest

        service.cdn_repo.create.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await service.create_cdn_region(
                CDNRegionCreateRequest(
                    region_code="us-east-1",
                    region_name="US East",
                    country="US",
                    cdn_provider="CloudFront",
                    bandwidth_capacity_gbps=50.0,
                )
            )

        service.cdn_repo.rollback.assert_awaited_once()

    async def test_get_cdn_region(self, service):
        service.cdn_repo.get_by_id.return_value = MagicMock()

        assert await service.get_cdn_region(uuid4()) is not None


class TestDownloadBranches:
    async def test_create_download_session(self, service):
        from app.schemas import DownloadSessionCreateRequest

        dl = MagicMock()
        service.download_repo.create.return_value = dl

        result = await service.create_download_session(
            DownloadSessionCreateRequest(user_id=uuid4(), episode_id=uuid4(), device_id="dev-1")
        )

        assert result is dl
        service.download_repo.commit.assert_awaited_once()

    async def test_get_user_downloads(self, service):
        service.download_repo.get_user_downloads.return_value = [MagicMock()]

        assert len(await service.get_user_downloads(uuid4())) == 1

    async def test_update_download_progress_missing_returns_none(self, service):
        service.download_repo.get_by_id.return_value = None

        assert await service.update_download_progress(uuid4(), 100) is None

    async def test_update_download_progress_zero_total_bytes(self, service):
        dl = MagicMock(total_bytes=0)
        service.download_repo.get_by_id.return_value = dl
        service.download_repo.update.return_value = dl

        await service.update_download_progress(uuid4(), 100)

        _, kwargs = service.download_repo.update.await_args
        assert kwargs["progress_percent"] == 0

    async def test_update_download_progress_pct(self, service):
        dl = MagicMock(total_bytes=1000)
        service.download_repo.get_by_id.return_value = dl
        service.download_repo.update.return_value = dl

        await service.update_download_progress(uuid4(), 250)

        _, kwargs = service.download_repo.update.await_args
        assert kwargs["progress_percent"] == 25
