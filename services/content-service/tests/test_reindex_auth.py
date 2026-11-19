"""`POST /api/v1/reindex` is admin-only and never reports work that did not happen (#1138).

The route used to be a TODO stub with no auth dependency: any caller got a
200 and a ``job_id``, the job was parked in a module-level dict that was never
pruned (unbounded memory growth), and ``GET /reindex/{job_id}`` reported
``accepted`` forever although no reindex was ever started.

These tests drive the *real* router with real RS256 tokens (the
``tests/_test_jwks.py`` key pair); only the outbound JWKS fetch and the
auth-version introspection call are replaced. ``get_admin_identity`` is NOT
overridden, so the guard genuinely runs.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import wildframe_auth
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

import app.api.routes as routes
from app.api.routes import router
from app.core.settings import settings
from tests._test_jwks import JWKS, PRIVATE_PEM
from wildframe_auth.verifier import clear_jwks_cache

REINDEX = "/api/v1/reindex"


def _token(role: str) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid.uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "role": role,
        "av": 1,
        "arv": settings.ADMIN_ROLE_VERSION,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
    }
    return jwt.encode(claims, PRIVATE_PEM, algorithm="RS256", headers={"kid": "k1"})


def _bearer(role: str) -> dict:
    return {"Authorization": f"Bearer {_token(role)}"}


@pytest.fixture
def client(monkeypatch):
    async def _fetch(url: str):
        return JWKS

    monkeypatch.setattr(wildframe_auth.verifier, "fetch_jwks", _fetch)
    clear_jwks_cache()

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {}
    http = MagicMock()
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=None)
    http.get = AsyncMock(return_value=response)

    app = FastAPI()
    app.include_router(router)
    with patch("app.api.routes.httpx.AsyncClient", lambda *a, **k: http):
        with TestClient(app, base_url="http://test") as test_client:
            yield test_client
    clear_jwks_cache()


def _retained_jobs() -> int:
    """Jobs held in module state; 0 once the registry is gone."""
    return len(getattr(routes, "_reindex_jobs", {}))


def test_unauthenticated_reindex_is_401_and_has_no_side_effect(client):
    before = _retained_jobs()

    response = client.post(REINDEX)

    assert response.status_code == 401
    assert _retained_jobs() == before


def test_non_admin_reindex_is_403_and_has_no_side_effect(client):
    before = _retained_jobs()

    response = client.post(REINDEX, headers=_bearer("user"))

    assert response.status_code == 403
    assert _retained_jobs() == before


def test_admin_reindex_is_an_honest_501_not_a_job_id(client):
    response = client.post(REINDEX, headers=_bearer("admin"))

    assert response.status_code == 501
    assert "job_id" not in response.json()
    assert "not implemented" in response.json()["detail"].lower()


def test_repeated_admin_calls_retain_nothing(client):
    for _ in range(50):
        client.post(REINDEX, headers=_bearer("admin"))

    assert _retained_jobs() == 0


def test_no_false_success_status_route_remains(client):
    """There is no job, so there is no job status to report as ``accepted``."""
    paths = {route.path for route in router.routes}

    assert f"{REINDEX}/{{job_id}}" not in paths
    assert not hasattr(routes, "_reindex_jobs")
    response = client.get(f"{REINDEX}/{uuid.uuid4()}", headers=_bearer("admin"))
    assert response.status_code in (404, 405)
