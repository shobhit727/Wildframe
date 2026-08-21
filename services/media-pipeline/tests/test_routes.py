"""Route coverage for the media pipeline API — start, status, legacy endpoints."""

from datetime import UTC, datetime, timedelta, timezone

from jose import jwt as jose_jwt
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.media_pipeline_routes import get_current_user_id, get_pipeline_service
from app.core.settings import settings
from app.main import app
from app.services import PipelineError

pytestmark = pytest.mark.asyncio


def make_job(**overrides):
    job = MagicMock()
    job.id = uuid4()
    job.content_id = uuid4()
    job.upload_session_id = uuid4()
    job.status = MagicMock(value="running")
    job.current_stage = "virus_scan"
    job.retries = 1
    job.error = None
    job.stage_versions = {"virus_scan": 1}
    job.pipeline_stage_logs = []
    for k, v in overrides.items():
        setattr(job, k, v)
    return job


def make_log():
    log = MagicMock()
    log.stage = "virus_scan"
    log.status = MagicMock(value="success")
    log.duration_ms = 12
    log.message = "clean"
    log.created_at = datetime.now(UTC)
    return log


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    app.dependency_overrides[get_current_user_id] = lambda: uuid4()
    yield TestClient(app, base_url="http://localhost")
    app.dependency_overrides.clear()


@pytest.fixture
def service():
    mock = MagicMock()
    mock.start_job = AsyncMock()
    mock.advance = AsyncMock()
    mock.job_repo = MagicMock(get=AsyncMock())
    mock.log_repo = MagicMock(list_for_job=AsyncMock(return_value=[]))
    mock.start_transcoding = AsyncMock()
    mock.get_job_status = AsyncMock()
    return mock


def override(service_mock):
    def _dep():
        return service_mock

    return _dep


class TestPipelineAuth:
    """Pipeline job creation/reads must require a verified JWT."""

    def _start_payload(self):
        return {
            "content_id": str(uuid4()),
            "upload_session_id": str(uuid4()),
            "storage_key": "uploads/x/v.mp4",
            "idempotency_key": f"test-{uuid4()}",
        }

    def test_start_requires_token(self, client):
        app.dependency_overrides.pop(get_current_user_id, None)
        response = client.post(f"/api/v1/pipeline/jobs/{uuid4()}/start", json=self._start_payload())
        assert response.status_code == 401

    def test_start_rejects_garbage_token(self, client):
        app.dependency_overrides.pop(get_current_user_id, None)
        response = client.post(
            f"/api/v1/pipeline/jobs/{uuid4()}/start",
            json=self._start_payload(),
            headers={"Authorization": "Bearer garbage"},
        )
        assert response.status_code == 401

    def test_start_rejects_expired_token(self, client):
        app.dependency_overrides.pop(get_current_user_id, None)
        token = jose_jwt.encode(
            {
                "sub": str(uuid4()),
                "iss": settings.JWT_ISSUER,
                "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        response = client.post(
            f"/api/v1/pipeline/jobs/{uuid4()}/start",
            json=self._start_payload(),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401

    def test_get_job_requires_token(self, client):
        app.dependency_overrides.pop(get_current_user_id, None)
        response = client.get(f"/api/v1/pipeline/jobs/{uuid4()}")
        assert response.status_code == 401


class TestStartJob:
    def test_start_success(self, client, service):
        job = make_job()
        service.start_job.return_value = job
        service.advance.return_value = job
        app.dependency_overrides[get_pipeline_service] = override(service)
        upload_id = uuid4()

        response = client.post(
            f"/api/v1/pipeline/jobs/{upload_id}/start",
            json={
                "content_id": str(job.content_id),
                "upload_session_id": str(upload_id),
                "storage_key": "uploads/abc/video.mp4",
                "idempotency_key": f"test-{upload_id}",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["job_id"] == str(job.id)
        assert body["status"] == "running"
        service.start_job.assert_awaited_once()

    def test_start_pipeline_error_returns_400(self, client, service, mocker):
        service.start_job.side_effect = PipelineError("upload not found")
        app.dependency_overrides[get_pipeline_service] = override(service)
        upload_id = uuid4()
        response = client.post(
            f"/api/v1/pipeline/jobs/{upload_id}/start",
            json={
                "content_id": str(uuid4()),
                "upload_session_id": str(upload_id),
                "storage_key": "uploads/x/v.mp4",
                "idempotency_key": f"test-{upload_id}",
            },
        )

        assert response.status_code == 400
        assert "upload not found" in response.json()["detail"]


class TestGetJob:
    def test_get_job_success(self, client, service):
        job = make_job()
        service.job_repo.get.return_value = job
        service.log_repo.list_for_job.return_value = [make_log()]
        app.dependency_overrides[get_pipeline_service] = override(service)

        response = client.get(f"/api/v1/pipeline/jobs/{job.id}")

        assert response.status_code == 200
        body = response.json()
        assert body["job"]["job_id"] == str(job.id)
        assert len(body["stage_log"]) == 1
        assert body["stage_log"][0]["stage"] == "virus_scan"

    def test_get_job_not_found_returns_404(self, client, service):
        service.job_repo.get.return_value = None
        app.dependency_overrides[get_pipeline_service] = override(service)

        response = client.get(f"/api/v1/pipeline/jobs/{uuid4()}")

        assert response.status_code == 404


class TestLegacyRoutes:
    def test_start_transcoding(self, client):
        from app.core.database import get_db

        session = AsyncMock()
        app.dependency_overrides[get_db] = lambda: session

        response = client.post(
            "/media/transcode",
            json={"content_id": str(uuid4()), "source_url": "https://cdn/x/v.m3u8"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "pending"

    def test_get_transcoding_status_success(self, client):
        from app.core.database import get_db

        session = AsyncMock()
        session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(
                return_value=MagicMock(status="running", progress_percentage=42)
            )
        )
        app.dependency_overrides[get_db] = lambda: session

        response = client.get(f"/media/job-status/{uuid4()}")

        assert response.status_code == 200
        assert response.json()["status"] == "running"
        assert response.json()["progress"] == 42

    def test_get_transcoding_status_not_found(self, client):
        from app.core.database import get_db

        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute.return_value = result
        app.dependency_overrides[get_db] = lambda: session

        response = client.get(f"/media/job-status/{uuid4()}")

        assert response.status_code == 404
