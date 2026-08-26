"""Edge-branch coverage for StreamingService — rollback paths, bandwidth filter,
progress math, manifest reuse."""

from datetime import UTC, datetime, timedelta
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
        service.playback_repo.count_active_sessions_locked = AsyncMock(return_value=0)

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


class TestSignedUrlBranches:
    """Tests for HMAC signed URL generation and verification (#489, #491)."""

    async def test_generate_signed_url(self, service):
        from app.schemas import SignedPlaybackUrlRequest

        request = SignedPlaybackUrlRequest(session_id=uuid4(), content_id=uuid4(), ttl_seconds=3600)
        signed_url, expires_at = service.generate_signed_url(request)

        assert "session_id=" in signed_url
        assert "signature=" in signed_url
        assert "expires=" in signed_url
        assert expires_at is not None

    async def test_verify_signed_url_valid(self, service):
        session_id = uuid4()
        content_id = uuid4()
        expires = int((datetime.now(UTC) + timedelta(seconds=3600)).timestamp())

        import hmac
        import hashlib

        secret = "dev-playback-signing-secret-change-in-production".encode()
        message = f"{session_id}|{content_id}|{expires}".encode()
        signature = hmac.new(secret, message, hashlib.sha256).hexdigest()

        result = service.verify_signed_url(session_id, content_id, signature, expires)
        assert result is True

    async def test_verify_signed_url_invalid_signature(self, service):
        session_id = uuid4()
        content_id = uuid4()
        expires = int((datetime.now(UTC) + timedelta(seconds=3600)).timestamp())
        signature = "invalid_signature"

        result = service.verify_signed_url(session_id, content_id, signature, expires)
        assert result is False

    async def test_verify_signed_url_expired(self, service):
        session_id = uuid4()
        content_id = uuid4()
        expires = int((datetime.now(UTC) - timedelta(seconds=10)).timestamp())

        import hmac
        import hashlib

        secret = "dev-playback-signing-secret-change-in-production".encode()
        message = f"{session_id}|{content_id}|{expires}".encode()
        signature = hmac.new(secret, message, hashlib.sha256).hexdigest()

        result = service.verify_signed_url(session_id, content_id, signature, expires)
        assert result is False


class TestSessionValidityBranches:
    """Tests for session expiry/revocation checks at playback time (#76, #147, #194, #219, #251)."""

    async def test_check_session_valid_for_playback_active(self, service):
        session = MagicMock()
        session.user_id = uuid4()
        session.status = "active"
        session.expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=3600)
        service.playback_repo.get_by_id = AsyncMock(return_value=session)

        result = await service.check_session_valid_for_playback(session.id, session.user_id)
        assert result is True

    async def test_check_session_valid_for_playback_expired(self, service):
        session = MagicMock()
        session.user_id = uuid4()
        session.status = "active"
        session.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=10)
        service.playback_repo.get_by_id = AsyncMock(return_value=session)

        result = await service.check_session_valid_for_playback(session.id, session.user_id)
        assert result is False

    async def test_check_session_valid_for_playback_revoked(self, service):
        session = MagicMock()
        session.user_id = uuid4()
        session.status = "completed"
        session.expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=3600)
        service.playback_repo.get_by_id = AsyncMock(return_value=session)

        result = await service.check_session_valid_for_playback(session.id, session.user_id)
        assert result is False

    async def test_check_session_valid_for_playback_not_found(self, service):
        service.playback_repo.get_by_id = AsyncMock(return_value=None)

        result = await service.check_session_valid_for_playback(uuid4(), uuid4())
        assert result is False

    async def test_check_session_valid_for_playback_wrong_user(self, service):
        session = MagicMock()
        session.user_id = uuid4()
        session.status = "active"
        session.expires_at = None
        service.playback_repo.get_by_id = AsyncMock(return_value=session)

        result = await service.check_session_valid_for_playback(session.id, uuid4())
        assert result is False


class TestConcurrencyBranches:
    """Tests for atomic concurrency enforcement (#281, #490)."""

    async def test_start_playback_session_concurrency_check(self, service, mocker):
        from app.schemas import PlaybackSessionCreateRequest
        from fastapi import HTTPException, status

        # Mock the count to exceed limit AND no oldest session to retire ->
        # the defensive 409 branch (newest-device-wins normally replaces it).
        service.playback_repo.count_active_sessions_locked = AsyncMock(return_value=5)
        service.playback_repo.get_oldest_active = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await service.start_playback_session(
                PlaybackSessionCreateRequest(user_id=uuid4(), content_id=uuid4(), device_id="d")
            )

        assert exc_info.value.status_code == status.HTTP_409_CONFLICT
        assert "Maximum concurrent sessions" in exc_info.value.detail

    async def test_start_playback_session_reaps_idle_sessions(self, service, mocker):
        """ACTIVE sessions idle beyond the timeout are reaped before counting
        (#490): a crashed client must not hold a slot for the full window."""
        executed = []
        async def fake_execute(stmt, *params):
            executed.append(stmt)
            result = MagicMock()
            result.scalar_one.return_value = 0
            result.rowcount = 2
            return result

        mocker.patch.object(service.session, "execute", side_effect=fake_execute)

        from app.repositories import PlaybackSessionRepository

        repo = PlaybackSessionRepository(service.session)
        result = await repo.count_active_sessions_locked(uuid4())

        assert result == 0
        # First execute = advisory lock; second = the reap UPDATE.
        assert len(executed) == 3  # lock + reap UPDATE + count SELECT
        compiled = str(executed[1])
        assert "UPDATE playback_session" in compiled
        assert "last_activity_at" in compiled
        assert "ended_at" in compiled  # reaped sessions get an end timestamp

    async def test_start_playback_session_replaces_oldest(self, service):
        from app.schemas import PlaybackSessionCreateRequest

        service.playback_repo.count_active_sessions_locked = AsyncMock(return_value=5)
        oldest_id = uuid4()
        oldest = MagicMock(id=oldest_id)
        service.playback_repo.get_oldest_active = AsyncMock(return_value=oldest)
        service.playback_repo.mark_completed = AsyncMock(return_value=oldest)
        session = MagicMock()
        service.playback_repo.create = AsyncMock(return_value=session)

        result = await service.start_playback_session(
            PlaybackSessionCreateRequest(user_id=uuid4(), content_id=uuid4(), device_id="d")
        )

        assert result is session
        service.playback_repo.mark_completed.assert_awaited_once_with(oldest_id)

    async def test_start_playback_session_under_limit(self, service):
        from app.schemas import PlaybackSessionCreateRequest

        # Mock the count to be under limit
        service.playback_repo.count_active_sessions_locked = AsyncMock(return_value=2)
        session = MagicMock()
        service.playback_repo.create = AsyncMock(return_value=session)

        result = await service.start_playback_session(
            PlaybackSessionCreateRequest(user_id=uuid4(), content_id=uuid4(), device_id="d")
        )

        assert result is session
        service.playback_repo.commit.assert_awaited_once()
