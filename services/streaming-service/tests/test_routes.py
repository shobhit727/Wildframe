"""Route-level tests for the Streaming Service HTTP API.

Exercises the real FastAPI router -> service call chain via TestClient with
``get_streaming_service`` dependency-overridden to inject a fake service.
Covers playback sessions, manifests, transcoding jobs, quality profiles, CDN
regions and download sessions: status codes, response models, 404 behaviour
and the argument contract between routes and the service layer.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app.api.routes import (
    get_current_user_id as streaming_user_di,
    get_streaming_service,
    require_self as streaming_require_self,
)
from app.main import app


@pytest.fixture
def auth_user_id():
    return uuid4()


@pytest.fixture
def fake_service():
    return MagicMock()


async def _echo_path_self(request: Request) -> UUID:
    return UUID(request.path_params["user_id"])


@pytest.fixture(autouse=True)
def override_deps(fake_service, auth_user_id):
    app.dependency_overrides[get_streaming_service] = lambda: fake_service
    app.dependency_overrides[streaming_require_self] = _echo_path_self
    app.dependency_overrides[streaming_user_di] = lambda: auth_user_id
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def make_playback_session(id_value=None, user_id=None):
    s = MagicMock()
    s.id = id_value or uuid4()
    s.user_id = user_id or uuid4()
    s.content_id = uuid4()
    s.episode_id = None
    s.device_id = "device-1"
    s.status = "active"
    s.current_position_seconds = 0
    s.total_duration_seconds = 3600
    s.protocol = "hls"
    s.resolution = "720p"
    s.bitrate_kbps = 2500
    s.buffer_health_seconds = 5.0
    s.stalls_count = 0
    s.dropped_frames = 0
    s.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    s.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    s.last_active_at = datetime(2026, 1, 1, tzinfo=UTC)
    return s


def make_manifest(id_value=None):
    m = MagicMock()
    m.id = id_value or uuid4()
    m.episode_id = uuid4()
    m.content_id = uuid4()
    m.protocol = "hls"
    m.manifest_url = "https://cdn.example.com/manifest.m3u8"
    m.variants = ["720p"]
    m.available_bitrates = [2500]
    m.generated_at = datetime(2026, 1, 1, tzinfo=UTC)
    m.expires_at = None
    return m


def make_transcoding_job(id_value=None):
    j = MagicMock()
    j.id = id_value or uuid4()
    j.episode_id = uuid4()
    j.content_id = uuid4()
    j.status = "pending"
    j.priority = 5
    j.input_file_path = "/media/episode.mp4"
    j.target_resolutions = ["1080p", "720p"]
    j.target_bitrates = [5000, 2500]
    j.progress_percent = 0
    j.started_at = None
    j.completed_at = None
    j.error_message = None
    return j


def make_quality_profile(id_value=None):
    p = MagicMock()
    p.id = id_value or uuid4()
    p.name = "HD 720p"
    p.description = None
    p.resolution = "720p"
    p.bitrate_kbps = 2500
    p.fps = 24
    p.video_codec = "h264"
    p.audio_codec = "aac"
    p.audio_bitrate_kbps = 128
    p.supported_devices = ["web", "ios", "android"]
    p.min_bandwidth_kbps = 1500
    p.max_bandwidth_kbps = 4000
    return p


def make_cdn_region(id_value=None):
    r = MagicMock()
    r.id = id_value or uuid4()
    r.region_code = "us-east-1"
    r.region_name = "US East"
    r.country = "US"
    r.cdn_provider = "cloudfront"
    r.max_concurrent_streams = 10000
    r.current_active_streams = 0
    r.bandwidth_capacity_gbps = 50.0
    r.is_active = True
    return r


def make_download(id_value=None, user_id=None):
    d = MagicMock()
    d.id = id_value or uuid4()
    d.user_id = user_id or uuid4()
    d.episode_id = uuid4()
    d.device_id = "device-1"
    d.status = "downloading"
    d.resolution = "720p"
    d.progress_percent = 0
    d.bytes_downloaded = 0
    d.total_bytes = 1000
    d.started_at = None
    d.completed_at = None
    d.expires_at = None
    return d


class TestPlaybackRoutes:
    def test_start_playback_returns_201(self, client, fake_service, auth_user_id):
        session = make_playback_session()
        session.user_id = auth_user_id
        fake_service.start_playback_session = AsyncMock(return_value=session)

        response = client.post(
            "/api/v1/playback-sessions",
            json={
                "user_id": str(auth_user_id),
                "content_id": str(session.content_id),
                "device_id": "device-1",
            },
        )

        assert response.status_code == 201
        assert response.json()["id"] == str(session.id)

    def test_start_playback_rejects_bad_protocol(self, client):
        response = client.post(
            "/api/v1/playback-sessions",
            json={
                "user_id": str(uuid4()),
                "content_id": str(uuid4()),
                "device_id": "d",
                "protocol": "rtsp",
            },
        )

        assert response.status_code == 422

    def test_get_playback_session(self, client, fake_service, auth_user_id):
        session = make_playback_session(user_id=auth_user_id)
        fake_service.get_playback_session = AsyncMock(return_value=session)

        response = client.get(f"/api/v1/playback-sessions/{session.id}")

        assert response.status_code == 200
        assert response.json()["status"] == "active"

    def test_get_playback_session_requires_auth(self, client, fake_service):
        async def _deny() -> UUID:
            raise HTTPException(status_code=401, detail="Missing or invalid authorization header")

        app.dependency_overrides[streaming_user_di] = _deny
        fake_service.get_playback_session = AsyncMock(return_value=make_playback_session())

        response = client.get(f"/api/v1/playback-sessions/{uuid4()}")

        assert response.status_code == 401

    def test_get_playback_session_other_user_returns_403(self, client, fake_service):
        session = make_playback_session()  # owned by someone else
        fake_service.get_playback_session = AsyncMock(return_value=session)

        response = client.get(f"/api/v1/playback-sessions/{session.id}")

        assert response.status_code == 403

    def test_get_playback_session_missing_returns_404(self, client, fake_service):
        fake_service.get_playback_session = AsyncMock(return_value=None)

        response = client.get(f"/api/v1/playback-sessions/{uuid4()}")

        assert response.status_code == 404

    def test_get_user_playback_sessions(self, client, fake_service):
        user_id = uuid4()
        fake_service.get_active_sessions = AsyncMock(return_value=[make_playback_session()])

        response = client.get(f"/api/v1/users/{user_id}/playback-sessions")

        assert response.status_code == 200
        assert len(response.json()) == 1
        fake_service.get_active_sessions.assert_awaited_once_with(user_id)

    def test_update_playback_session(self, client, fake_service, auth_user_id):
        session = make_playback_session(user_id=auth_user_id)
        fake_service.get_playback_session = AsyncMock(return_value=session)
        fake_service.update_playback_session = AsyncMock(return_value=session)

        response = client.patch(
            f"/api/v1/playback-sessions/{session.id}", json={"current_position_seconds": 120}
        )

        assert response.status_code == 200
        assert response.json()["current_position_seconds"] == 0

    def test_update_playback_session_other_user_returns_403(self, client, fake_service):
        session = make_playback_session()  # owned by someone else
        fake_service.get_playback_session = AsyncMock(return_value=session)

        response = client.patch(
            f"/api/v1/playback-sessions/{session.id}", json={"current_position_seconds": 120}
        )

        assert response.status_code == 403

    def test_end_playback_session_returns_204(self, client, fake_service, auth_user_id):
        session = make_playback_session(user_id=auth_user_id)
        fake_service.get_playback_session = AsyncMock(return_value=session)
        fake_service.end_playback_session = AsyncMock(return_value=session)

        response = client.post(f"/api/v1/playback-sessions/{session.id}/end")

        assert response.status_code == 204

    def test_end_playback_session_other_user_returns_403(self, client, fake_service):
        session = make_playback_session()  # owned by someone else
        fake_service.get_playback_session = AsyncMock(return_value=session)

        response = client.post(f"/api/v1/playback-sessions/{session.id}/end")

        assert response.status_code == 403

    def test_end_playback_session_missing_returns_404(self, client, fake_service):
        fake_service.get_playback_session = AsyncMock(return_value=None)
        fake_service.end_playback_session = AsyncMock(return_value=None)

        response = client.post(f"/api/v1/playback-sessions/{uuid4()}/end")

        assert response.status_code == 404


class TestManifestRoutes:
    def test_generate_manifest_returns_201(self, client, fake_service):
        manifest = make_manifest()
        fake_service.generate_manifest = AsyncMock(return_value=manifest)

        response = client.post(
            "/api/v1/manifests",
            json={
                "episode_id": str(manifest.episode_id),
                "content_id": str(manifest.content_id),
                "protocol": "hls",
            },
        )

        assert response.status_code == 201
        assert response.json()["protocol"] == "hls"

    def test_get_manifest(self, client, fake_service):
        manifest = make_manifest()
        fake_service.get_manifest = AsyncMock(return_value=manifest)

        response = client.get(f"/api/v1/manifests/{manifest.id}")

        assert response.status_code == 200
        assert response.json()["id"] == str(manifest.id)

    def test_get_manifest_missing_returns_404(self, client, fake_service):
        fake_service.get_manifest = AsyncMock(return_value=None)

        response = client.get(f"/api/v1/manifests/{uuid4()}")

        assert response.status_code == 404

    def test_get_episode_manifest(self, client, fake_service):
        episode_id = uuid4()
        manifest = make_manifest()
        fake_service.get_manifest_for_episode = AsyncMock(return_value=manifest)

        response = client.get(f"/api/v1/episodes/{episode_id}/manifest")

        assert response.status_code == 200
        fake_service.get_manifest_for_episode.assert_awaited_once_with(episode_id, "hls")

    def test_get_episode_manifest_rejects_bad_protocol(self, client):
        response = client.get(f"/api/v1/episodes/{uuid4()}/manifest?protocol=flv")

        assert response.status_code == 422


class TestTranscodingRoutes:
    def test_create_transcoding_job_returns_201(self, client, fake_service):
        job = make_transcoding_job()
        fake_service.create_transcoding_job = AsyncMock(return_value=job)

        response = client.post(
            "/api/v1/transcoding-jobs",
            json={
                "episode_id": str(job.episode_id),
                "content_id": str(job.content_id),
                "input_file_path": "/media/episode.mp4",
            },
        )

        assert response.status_code == 201
        assert response.json()["status"] == "pending"

    def test_get_transcoding_job(self, client, fake_service):
        job = make_transcoding_job()
        fake_service.get_transcoding_job = AsyncMock(return_value=job)

        response = client.get(f"/api/v1/transcoding-jobs/{job.id}")

        assert response.status_code == 200

    def test_get_transcoding_job_missing_returns_404(self, client, fake_service):
        fake_service.get_transcoding_job = AsyncMock(return_value=None)

        response = client.get(f"/api/v1/transcoding-jobs/{uuid4()}")

        assert response.status_code == 404

    def test_get_pending_jobs(self, client, fake_service):
        fake_service.get_pending_jobs = AsyncMock(return_value=[make_transcoding_job()])

        response = client.get("/api/v1/transcoding-jobs/pending")

        assert response.status_code == 200
        assert len(response.json()) == 1
        fake_service.get_pending_jobs.assert_awaited_once_with(10)

    def test_update_transcoding_progress(self, client, fake_service):
        job = make_transcoding_job()
        job.progress_percent = 50
        fake_service.update_transcoding_progress = AsyncMock(return_value=job)

        response = client.patch(f"/api/v1/transcoding-jobs/{job.id}/progress?progress_percent=50")

        assert response.status_code == 200
        fake_service.update_transcoding_progress.assert_awaited_once_with(job.id, 50, None)

    def test_update_transcoding_progress_rejects_out_of_range(self, client):
        response = client.patch(f"/api/v1/transcoding-jobs/{uuid4()}/progress?progress_percent=150")

        assert response.status_code == 422


class TestQualityProfileRoutes:
    def test_create_quality_profile_returns_201(self, client, fake_service):
        profile = make_quality_profile()
        fake_service.create_quality_profile = AsyncMock(return_value=profile)

        response = client.post(
            "/api/v1/quality-profiles",
            json={
                "name": "HD 720p",
                "resolution": "720p",
                "bitrate_kbps": 2500,
                "min_bandwidth_kbps": 1500,
                "max_bandwidth_kbps": 4000,
            },
        )

        assert response.status_code == 201
        assert response.json()["name"] == "HD 720p"

    def test_get_quality_profile(self, client, fake_service):
        profile = make_quality_profile()
        fake_service.get_quality_profile = AsyncMock(return_value=profile)

        response = client.get(f"/api/v1/quality-profiles/{profile.id}")

        assert response.status_code == 200

    def test_get_quality_profile_missing_returns_404(self, client, fake_service):
        fake_service.get_quality_profile = AsyncMock(return_value=None)

        response = client.get(f"/api/v1/quality-profiles/{uuid4()}")

        assert response.status_code == 404

    def test_list_quality_profiles_for_bandwidth(self, client, fake_service):
        fake_service.get_quality_profiles_for_bandwidth = AsyncMock(
            return_value=[make_quality_profile()]
        )

        response = client.get("/api/v1/quality-profiles?bandwidth_kbps=3000")

        assert response.status_code == 200
        fake_service.get_quality_profiles_for_bandwidth.assert_awaited_once_with(3000)

    def test_list_quality_profiles_rejects_low_bandwidth(self, client):
        response = client.get("/api/v1/quality-profiles?bandwidth_kbps=50")

        assert response.status_code == 422


class TestCDNRegionRoutes:
    def test_create_cdn_region_returns_201(self, client, fake_service):
        region = make_cdn_region()
        fake_service.create_cdn_region = AsyncMock(return_value=region)

        response = client.post(
            "/api/v1/cdn-regions",
            json={
                "region_code": "us-east-1",
                "region_name": "US East",
                "country": "US",
                "cdn_provider": "cloudfront",
                "bandwidth_capacity_gbps": 50.0,
            },
        )

        assert response.status_code == 201
        assert response.json()["region_code"] == "us-east-1"

    def test_get_cdn_region(self, client, fake_service):
        region = make_cdn_region()
        fake_service.get_cdn_region = AsyncMock(return_value=region)

        response = client.get(f"/api/v1/cdn-regions/{region.id}")

        assert response.status_code == 200

    def test_get_cdn_region_missing_returns_404(self, client, fake_service):
        fake_service.get_cdn_region = AsyncMock(return_value=None)

        response = client.get(f"/api/v1/cdn-regions/{uuid4()}")

        assert response.status_code == 404

    def test_list_cdn_regions(self, client, fake_service):
        fake_service.get_all_cdn_regions = AsyncMock(return_value=[make_cdn_region()])

        response = client.get("/api/v1/cdn-regions")

        assert response.status_code == 200
        assert len(response.json()) == 1


class TestDownloadRoutes:
    def test_create_download_returns_201(self, client, fake_service, auth_user_id):
        download = make_download()
        download.user_id = auth_user_id
        fake_service.create_download_session = AsyncMock(return_value=download)

        response = client.post(
            "/api/v1/download-sessions",
            json={
                "user_id": str(auth_user_id),
                "episode_id": str(download.episode_id),
                "device_id": "device-1",
            },
        )

        assert response.status_code == 201
        assert response.json()["id"] == str(download.id)

    def test_get_download(self, client, fake_service, auth_user_id):
        download = make_download(user_id=auth_user_id)
        fake_service.get_download_session = AsyncMock(return_value=download)

        response = client.get(f"/api/v1/download-sessions/{download.id}")

        assert response.status_code == 200

    def test_get_download_other_user_returns_403(self, client, fake_service):
        download = make_download()  # owned by someone else
        fake_service.get_download_session = AsyncMock(return_value=download)

        response = client.get(f"/api/v1/download-sessions/{download.id}")

        assert response.status_code == 403

    def test_get_download_missing_returns_404(self, client, fake_service):
        fake_service.get_download_session = AsyncMock(return_value=None)

        response = client.get(f"/api/v1/download-sessions/{uuid4()}")

        assert response.status_code == 404

    def test_get_user_downloads(self, client, fake_service):
        user_id = uuid4()
        fake_service.get_user_downloads = AsyncMock(return_value=[make_download()])

        response = client.get(f"/api/v1/users/{user_id}/downloads")

        assert response.status_code == 200
        fake_service.get_user_downloads.assert_awaited_once_with(user_id)

    def test_update_download_progress(self, client, fake_service, auth_user_id):
        download = make_download(user_id=auth_user_id)
        download.bytes_downloaded = 500
        fake_service.get_download_session = AsyncMock(return_value=download)
        fake_service.update_download_progress = AsyncMock(return_value=download)

        response = client.patch(
            f"/api/v1/download-sessions/{download.id}/progress?bytes_downloaded=500"
        )

        assert response.status_code == 200
        fake_service.update_download_progress.assert_awaited_once_with(download.id, 500)

    def test_update_download_progress_other_user_returns_403(self, client, fake_service):
        download = make_download()  # owned by someone else
        fake_service.get_download_session = AsyncMock(return_value=download)

        response = client.patch(
            f"/api/v1/download-sessions/{download.id}/progress?bytes_downloaded=500"
        )

        assert response.status_code == 403


class TestIdorProtection:
    def test_other_user_playback_sessions_403(self, client, auth_user_id):
        app.dependency_overrides.pop(streaming_require_self, None)
        app.dependency_overrides[streaming_user_di] = lambda: auth_user_id
        try:
            response = client.get(f"/api/v1/users/{uuid4()}/playback-sessions")
        finally:
            app.dependency_overrides[streaming_require_self] = _echo_path_self
        assert response.status_code == 403

    def test_no_token_rejected_401(self, client):
        app.dependency_overrides.clear()
        response = client.get(f"/api/v1/users/{uuid4()}/playback-sessions")
        assert response.status_code == 401

    def test_single_session_endpoints_require_auth(self, client, fake_service):
        app.dependency_overrides.clear()
        fake_service.get_playback_session = AsyncMock(return_value=make_playback_session())
        session_id = str(uuid4())
        assert client.get(f"/api/v1/playback-sessions/{session_id}").status_code == 401
        assert (
            client.patch(
                f"/api/v1/playback-sessions/{session_id}",
                json={"current_position_seconds": 1},
            ).status_code
            == 401
        )
        assert client.post(f"/api/v1/playback-sessions/{session_id}/end").status_code == 401
        assert client.get(f"/api/v1/download-sessions/{session_id}").status_code == 401

    def test_start_playback_other_user_403(self, client):
        response = client.post(
            "/api/v1/playback-sessions",
            json={
                "user_id": str(uuid4()),
                "content_id": str(uuid4()),
                "device_id": "d",
            },
        )
        assert response.status_code == 403


class TestManifestAuth:
    """Tests for manifest authentication and entitlement checks (#76, #147, #194, #219, #251, #587)."""

    def test_generate_manifest_requires_auth(self, client):
        app.dependency_overrides.clear()
        response = client.post(
            "/api/v1/manifests",
            json={"episode_id": str(uuid4()), "content_id": str(uuid4()), "protocol": "hls"},
        )
        assert response.status_code == 401

    def test_get_manifest_requires_auth(self, client, fake_service):
        app.dependency_overrides.clear()
        fake_service.get_manifest = AsyncMock(return_value=make_manifest())
        response = client.get(f"/api/v1/manifests/{uuid4()}")
        assert response.status_code == 401

    def test_get_episode_manifest_requires_auth(self, client, fake_service):
        app.dependency_overrides.clear()
        fake_service.get_manifest_for_episode = AsyncMock(return_value=make_manifest())
        response = client.get(f"/api/v1/episodes/{uuid4()}/manifest")
        assert response.status_code == 401

    def test_get_episode_manifest_with_valid_signed_url(self, client, fake_service):
        """Signed URL bypasses auth but validates signature (#489, #491)."""
        app.dependency_overrides.pop(streaming_user_di, None)
        app.dependency_overrides.pop(streaming_require_self, None)
        episode_id = uuid4()
        session_id = uuid4()
        content_id = episode_id  # episode_id used as content_id in URL
        expires = int((datetime.now(UTC) + timedelta(seconds=3600)).timestamp())
        # Generate valid signature
        import hmac
        import hashlib

        secret = "dev-playback-signing-secret-change-in-production".encode()
        message = f"{session_id}|{content_id}|{expires}".encode()
        signature = hmac.new(secret, message, hashlib.sha256).hexdigest()

        fake_service.verify_signed_url = lambda s, c, sig, exp: True
        fake_service.check_session_valid_for_playback = AsyncMock(return_value=True)
        fake_service.get_manifest_for_episode = AsyncMock(
            return_value=make_manifest(id_value=uuid4())
        )

        response = client.get(
            f"/api/v1/episodes/{episode_id}/manifest",
            params={"session_id": str(session_id), "signature": signature, "expires": expires},
        )
        assert response.status_code == 200

    def test_get_episode_manifest_rejects_invalid_signature(self, client, fake_service):
        app.dependency_overrides.pop(streaming_user_di, None)
        app.dependency_overrides.pop(streaming_require_self, None)
        fake_service.verify_signed_url = lambda s, c, sig, exp: False
        response = client.get(
            f"/api/v1/episodes/{uuid4()}/manifest",
            params={
                "session_id": str(uuid4()),
                "signature": "bad",
                "expires": int((datetime.now(UTC) + timedelta(seconds=3600)).timestamp()),
            },
        )
        assert response.status_code == 403

    def test_get_episode_manifest_rejects_expired_url(self, client, fake_service):
        app.dependency_overrides.pop(streaming_user_di, None)
        app.dependency_overrides.pop(streaming_require_self, None)
        expires = int((datetime.now(UTC) - timedelta(seconds=10)).timestamp())
        fake_service.verify_signed_url = lambda s, c, sig, exp: exp > datetime.now(UTC).timestamp()
        response = client.get(
            f"/api/v1/episodes/{uuid4()}/manifest",
            params={"session_id": str(uuid4()), "signature": "sig", "expires": expires},
        )
        assert response.status_code == 403

    def test_get_episode_manifest_rejects_revoked_session(self, client, fake_service):
        app.dependency_overrides.pop(streaming_user_di, None)
        app.dependency_overrides.pop(streaming_require_self, None)
        fake_service.verify_signed_url = lambda s, c, sig, exp: True
        fake_service.check_session_valid_for_playback = AsyncMock(return_value=False)
        response = client.get(
            f"/api/v1/episodes/{uuid4()}/manifest",
            params={
                "session_id": str(uuid4()),
                "signature": "sig",
                "expires": int((datetime.now(UTC) + timedelta(seconds=3600)).timestamp()),
            },
        )
        assert response.status_code == 403

    def test_manifest_response_is_private_no_store(self, client, fake_service):
        """#528/#526: authorized manifests must not be cached by intermediaries."""
        app.dependency_overrides.pop(streaming_user_di, None)
        app.dependency_overrides.pop(streaming_require_self, None)
        fake_service.verify_signed_url = lambda s, c, sig, exp: True
        fake_service.check_session_valid_for_playback = AsyncMock(return_value=True)
        fake_service.get_manifest_for_episode = AsyncMock(return_value=make_manifest())
        response = client.get(
            f"/api/v1/episodes/{uuid4()}/manifest",
            params={
                "session_id": str(uuid4()),
                "signature": "sig",
                "expires": int((datetime.now(UTC) + timedelta(seconds=3600)).timestamp()),
            },
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"

    def test_range_header_does_not_bypass_signature_check(self, client, fake_service):
        """#530: a Range header must not skip signed-URL/entitlement checks."""
        app.dependency_overrides.pop(streaming_user_di, None)
        app.dependency_overrides.pop(streaming_require_self, None)
        fake_service.verify_signed_url = lambda s, c, sig, exp: False
        response = client.get(
            f"/api/v1/episodes/{uuid4()}/manifest",
            headers={"Range": "bytes=0-1023"},
            params={
                "session_id": str(uuid4()),
                "signature": "forged",
                "expires": int((datetime.now(UTC) + timedelta(seconds=3600)).timestamp()),
            },
        )
        assert response.status_code == 403

    def test_signature_is_bound_to_requested_asset(self, client, fake_service):
        """#531: a signature minted for content A fails for content B."""
        import hashlib
        import hmac

        app.dependency_overrides.pop(streaming_user_di, None)
        app.dependency_overrides.pop(streaming_require_self, None)

        secret = "dev-playback-signing-secret-change-in-production".encode()

        def realish_verify(s, c, sig, exp):
            msg = f"{s}|{c}|{exp}".encode()
            expected = hmac.new(secret, msg, hashlib.sha256).hexdigest()
            return exp > datetime.now(UTC).timestamp() and hmac.compare_digest(sig, expected)

        fake_service.verify_signed_url = realish_verify
        fake_service.check_session_valid_for_playback = AsyncMock(return_value=True)

        session_id = uuid4()
        signed_for = uuid4()
        expires = int((datetime.now(UTC) + timedelta(seconds=3600)).timestamp())
        good_sig = hmac.new(
            secret, f"{session_id}|{signed_for}|{expires}".encode(), hashlib.sha256
        ).hexdigest()

        # Same signature replayed against a different episode -> 403.
        other_episode = uuid4()
        assert other_episode != signed_for
        response = client.get(
            f"/api/v1/episodes/{other_episode}/manifest",
            params={
                "session_id": str(session_id),
                "signature": good_sig,
                "expires": expires,
            },
        )
        assert response.status_code == 403


class TestConcurrencyLimit:
    """Tests for max concurrent sessions enforcement (#281, #490)."""

    def test_start_playback_respects_max_sessions(self, client, fake_service, auth_user_id):
        fake_service.start_playback_session = AsyncMock(
            side_effect=HTTPException(
                status_code=409, detail="Maximum concurrent sessions (5) reached"
            )
        )
        response = client.post(
            "/api/v1/playback-sessions",
            json={"user_id": str(auth_user_id), "content_id": str(uuid4()), "device_id": "d"},
        )
        assert response.status_code == 409
        assert "Maximum concurrent sessions" in response.json()["detail"]


class TestSignedPlaybackUrl:
    """Tests for HMAC-signed playback URLs (#489, #491)."""

    def test_create_signed_url_requires_auth(self, client):
        app.dependency_overrides.clear()
        response = client.post(
            "/api/v1/playback-sessions/signed-url",
            json={"session_id": str(uuid4()), "content_id": str(uuid4())},
        )
        assert response.status_code == 401

    def test_create_signed_url_other_user_session_returns_403(
        self, client, fake_service, auth_user_id
    ):
        session = make_playback_session()
        fake_service.get_playback_session = AsyncMock(return_value=session)
        response = client.post(
            "/api/v1/playback-sessions/signed-url",
            json={"session_id": str(session.id), "content_id": str(uuid4())},
        )
        assert response.status_code == 403

    def test_create_signed_url_inactive_session_returns_409(
        self, client, fake_service, auth_user_id
    ):
        session = make_playback_session(user_id=auth_user_id)
        session.status = "completed"
        fake_service.get_playback_session = AsyncMock(return_value=session)
        response = client.post(
            "/api/v1/playback-sessions/signed-url",
            json={"session_id": str(session.id), "content_id": str(uuid4())},
        )
        assert response.status_code == 409

    def test_create_signed_url_returns_signed_url(self, client, fake_service, auth_user_id):
        session = make_playback_session(user_id=auth_user_id)
        fake_service.get_playback_session = AsyncMock(return_value=session)
        fake_service.generate_signed_url = lambda req: (
            "/api/v1/episodes/test?sig=abc",
            datetime.now(UTC) + timedelta(seconds=3600),
        )

        response = client.post(
            "/api/v1/playback-sessions/signed-url",
            json={"session_id": str(session.id), "content_id": str(uuid4())},
        )
        assert response.status_code == 201
        assert "signed_url" in response.json()
        assert "expires_at" in response.json()


class TestSessionExpiry:
    """Tests for session expiry and revocation checks (#76, #147, #194, #219, #251)."""

    def _real_service(self, fake_service):
        from app.services import StreamingService

        svc = StreamingService(MagicMock())
        svc.playback_repo = fake_service.playback_repo
        return svc

    def test_check_session_valid_for_playback_expired_returns_false(self, fake_service):
        import asyncio

        session = make_playback_session()
        session.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=10)
        fake_service.playback_repo.get_by_id = AsyncMock(return_value=session)

        result = asyncio.run(
            self._real_service(fake_service).check_session_valid_for_playback(
                session.id, session.user_id
            )
        )
        assert result is False

    def test_check_session_valid_for_playback_revoked_returns_false(self, fake_service):
        import asyncio

        session = make_playback_session()
        session.status = "completed"  # revoked/ended
        fake_service.playback_repo.get_by_id = AsyncMock(return_value=session)

        result = asyncio.run(
            self._real_service(fake_service).check_session_valid_for_playback(
                session.id, session.user_id
            )
        )
        assert result is False

    def test_check_session_valid_for_playback_active_returns_true(self, fake_service):
        import asyncio

        session = make_playback_session()
        session.expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=3600)
        fake_service.playback_repo.get_by_id = AsyncMock(return_value=session)

        result = asyncio.run(
            self._real_service(fake_service).check_session_valid_for_playback(
                session.id, session.user_id
            )
        )
        assert result is True
