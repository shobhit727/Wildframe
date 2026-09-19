from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from jose import jwt

from app.api.routes import get_current_user_id
from app.core.settings import settings


def _token(av=3, sub=None):
    sub = sub or str(uuid4())
    payload = {
        "sub": sub,
        "type": "access",
        "av": av,
        "aud": settings.JWT_AUDIENCE,
        "exp": datetime.now(UTC) + timedelta(minutes=15),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM), sub


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
    token, sub = _token(av=2)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"auth_version": 2}
    client = _mock_client(resp=resp)
    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        uid = await get_current_user_id(authorization=f"Bearer {token}")
        assert str(uid) == sub
        assert client.get.await_args.args[0].endswith("/api/v1/auth/me")


@pytest.mark.asyncio
async def test_stale_av_rejected_via_json():
    token, _ = _token(av=1)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"auth_version": 2}
    client = _mock_client(resp=resp)
    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(authorization=f"Bearer {token}")
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_stale_av_rejected_via_401():
    token, _ = _token(av=1)
    resp = MagicMock()
    resp.status_code = 401
    resp.json.return_value = {"detail": "Invalid or expired token"}
    client = _mock_client(resp=resp)
    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(authorization=f"Bearer {token}")
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_revoked_user_rejected():
    token, _ = _token(av=2)
    resp = MagicMock()
    resp.status_code = 401
    resp.json.return_value = {"detail": "Token has been revoked"}
    client = _mock_client(resp=resp)
    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(authorization=f"Bearer {token}")
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_introspection_failure_rejected():
    token, _ = _token(av=2)
    client = _mock_client(exc=httpx.RequestError("fail"))
    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(authorization=f"Bearer {token}")
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_introspection_timeout_rejected():
    token, _ = _token(av=2)
    client = _mock_client(exc=httpx.ConnectTimeout("timeout"))
    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(authorization=f"Bearer {token}")
        assert exc.value.status_code == 401
