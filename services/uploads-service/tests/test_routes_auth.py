"""Route-level IDOR/security tests for the uploads service."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.uploads_routes import get_current_user_id as uploads_user_di
from app.api.uploads_routes import get_upload_service
from app.main import app


def test_create_session_requires_auth():
    app.dependency_overrides.clear()
    client = TestClient(app, base_url="http://localhost")
    response = client.post(
        "/api/v1/uploads/sessions",
        json={
            "creator_id": str(uuid4()),
            "filename": "clip.mp4",
            "mime": "video/mp4",
            "size_bytes": 1024,
        },
    )
    app.dependency_overrides.clear()
    assert response.status_code == 401


def test_create_session_other_creator_403():
    app.dependency_overrides.clear()
    session = MagicMock()
    session.id = uuid4()
    session.status = MagicMock(value="initiated")
    session.chunk_size = 512
    session.total_chunks = 2
    session.expires_at = __import__("datetime").datetime.now(__import__("datetime").UTC)

    svc = MagicMock()
    svc.create_session = AsyncMock(return_value=(session, []))
    app.dependency_overrides[get_upload_service] = lambda: svc
    app.dependency_overrides[uploads_user_di] = lambda: uuid4()

    client = TestClient(app, base_url="http://localhost")
    try:
        response = client.post(
            "/api/v1/uploads/sessions",
            json={
                "creator_id": str(uuid4()),
                "filename": "clip.mp4",
                "mime": "video/mp4",
                "size_bytes": 1024,
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403
    svc.create_session.assert_not_awaited()


def test_create_session_own_creator_ok():
    app.dependency_overrides.clear()
    me = uuid4()
    session = MagicMock()
    session.id = uuid4()
    session.status = MagicMock(value="initiated")
    session.expires_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
    session.chunk_size = 512
    session.total_chunks = 2

    svc = MagicMock()
    svc.create_session = AsyncMock(
        return_value=(
            session,
            [MagicMock(storage_key="k", upload_url="u", method="PUT", headers=None)],
        )
    )
    app.dependency_overrides[get_upload_service] = lambda: svc
    app.dependency_overrides[uploads_user_di] = lambda: me

    client = TestClient(app, base_url="http://localhost")
    try:
        response = client.post(
            "/api/v1/uploads/sessions",
            json={
                "creator_id": str(me),
                "filename": "clip.mp4",
                "mime": "video/mp4",
                "size_bytes": 1024,
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    svc.create_session.assert_awaited_once()
