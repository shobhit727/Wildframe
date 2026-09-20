from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from jose import jwt
from tests._test_jwks import PRIVATE_PEM

from app.api.billing_routes import get_current_user_id, get_current_user_payload
from app.core.settings import settings


def _token(av=2, token_type="access", sub=None):
    sub = sub or str(uuid4())
    payload = {
        "sub": sub,
        "user_id": sub,
        "type": token_type,
        "av": av,
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=15),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, PRIVATE_PEM, algorithm="RS256", headers={"kid": "k1"})


def _mock_client(resp=None, exc=None):
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    if exc:
        mock_client.get = AsyncMock(side_effect=exc)
    else:
        mock_client.get = AsyncMock(return_value=resp)
    return mock_client


@pytest.mark.asyncio
async def test_valid_av_passes():
    token = _token(av=2)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"auth_version": 2}
    client = _mock_client(resp=resp)
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        payload = await get_current_user_payload(authorization=f"Bearer {token}")
        assert payload["av"] == 2
        client.get.assert_awaited_once()
        assert client.get.await_args.kwargs["headers"]["Authorization"] == f"Bearer {token}"
        url = client.get.await_args.args[0]
        assert "/api/v1/auth/me" in url


@pytest.mark.asyncio
async def test_valid_av_passes_user_id():
    sub = str(uuid4())
    token = _token(av=5, sub=sub)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"auth_version": 5}
    client = _mock_client(resp=resp)
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        uid = await get_current_user_id(authorization=f"Bearer {token}")
        assert str(uid) == sub


@pytest.mark.asyncio
async def test_stale_av_rejected_via_json():
    token = _token(av=1)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"auth_version": 2}
    client = _mock_client(resp=resp)
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_payload(authorization=f"Bearer {token}")
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_stale_av_rejected_via_401():
    token = _token(av=1)
    resp = MagicMock()
    resp.status_code = 401
    resp.json.return_value = {"detail": "Invalid or expired token"}
    client = _mock_client(resp=resp)
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_payload(authorization=f"Bearer {token}")
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_revoked_user_rejected():
    token = _token(av=2)
    resp = MagicMock()
    resp.status_code = 404
    resp.json.return_value = {"detail": "User not found"}
    client = _mock_client(resp=resp)
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_payload(authorization=f"Bearer {token}")
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_introspection_failure_rejected():
    token = _token(av=2)
    client = _mock_client(exc=httpx.RequestError("network failure"))
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_payload(authorization=f"Bearer {token}")
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_introspection_timeout_rejected():
    token = _token(av=2)
    client = _mock_client(exc=httpx.ConnectTimeout("timeout"))
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(authorization=f"Bearer {token}")
        assert exc.value.status_code == 401
