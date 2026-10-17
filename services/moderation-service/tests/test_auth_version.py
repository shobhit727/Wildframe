from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from jose import jwt

from app.api.moderation_routes import _verify_token, get_current_admin_id, get_current_user_id
from app.core.settings import settings
from tests._test_jwks import JWKS
from tests._test_jwks import PRIVATE_PEM as PRIVATE_PEM
from wildframe_auth.verifier import clear_jwks_cache


@pytest.fixture(autouse=True)
def _stub_jwks(monkeypatch):
    """Replace the JWKS *fetch* seam only; verification itself stays real."""

    async def fetch(_url):
        return JWKS

    monkeypatch.setattr("wildframe_auth.verifier.fetch_jwks", fetch)
    clear_jwks_cache()
    yield
    clear_jwks_cache()


def _token(av=2, arv=0, role="user", token_type="access", sub=None):
    sub = sub or str(uuid4())
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "role": role,
        "type": token_type,
        "av": av,
        "arv": arv,
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": now + timedelta(minutes=15),
        "iat": now,
    }
    return (
        jwt.encode(payload, PRIVATE_PEM, algorithm="RS256", headers={"kid": "k1"}),
        sub,
    )


def _mock_client(resp=None, exc=None):
    m = MagicMock()
    m.__aenter__ = AsyncMock(return_value=m)
    m.__aexit__ = AsyncMock(return_value=None)
    if exc:
        m.get = AsyncMock(side_effect=exc)
    else:
        m.get = AsyncMock(return_value=resp)
    return m


@pytest.mark.asyncio
async def test_valid_av_passes_user():
    token, sub = _token(av=2)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"auth_version": 2}
    client = _mock_client(resp=resp)
    with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
        uid = await get_current_user_id(authorization=f"Bearer {token}")
        assert uid == sub


@pytest.mark.asyncio
async def test_valid_av_passes_admin():
    token, sub = _token(av=2, role="admin")
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"av": 2}
    client = _mock_client(resp=resp)
    with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
        uid = await get_current_admin_id(authorization=f"Bearer {token}")
        assert uid == sub


@pytest.mark.asyncio
async def test_stale_av_rejected_via_json():
    token, _ = _token(av=1)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"auth_version": 2}
    client = _mock_client(resp=resp)
    with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await _verify_token(f"Bearer {token}", require_admin=False)
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_stale_av_rejected_via_401():
    token, _ = _token(av=1)
    resp = MagicMock()
    resp.status_code = 401
    resp.json.return_value = {"detail": "revoked"}
    client = _mock_client(resp=resp)
    with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await _verify_token(f"Bearer {token}", require_admin=False)
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_revoked_user_rejected():
    token, _ = _token(av=2)
    resp = MagicMock()
    resp.status_code = 404
    resp.json.return_value = {"detail": "not found"}
    client = _mock_client(resp=resp)
    with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await _verify_token(f"Bearer {token}", require_admin=False)
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_introspection_failure_rejected():
    token, _ = _token(av=2)
    client = _mock_client(exc=httpx.RequestError("fail"))
    with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await _verify_token(f"Bearer {token}", require_admin=False)
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_timeout_rejected():
    token, _ = _token(av=2)
    client = _mock_client(exc=httpx.ConnectTimeout("timeout"))
    with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(authorization=f"Bearer {token}")
        assert exc.value.status_code == 401
