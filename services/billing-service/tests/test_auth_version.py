from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException
from jose import jwt
from tests._test_jwks import PRIVATE_PEM

from app.api.billing_routes import (
    _verify_creator,
    get_current_user_id,
    get_current_user_payload,
    require_admin,
    require_self,
)
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


# ---------------------------------------------------------------------------
# _verify_creator — creator-profile introspection (billing_routes.py:47-72)
# ---------------------------------------------------------------------------


def _creators_client(status_code: int = 200, exc=None) -> MagicMock:
    """httpx double whose GET returns the given status (or raises)."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    resp = MagicMock(status_code=status_code)
    client.get = AsyncMock(side_effect=exc) if exc else AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_verify_creator_fails_closed_when_the_service_url_is_unconfigured():
    with patch.object(settings, "CREATORS_SERVICE_URL", ""):
        with pytest.raises(HTTPException) as exc:
            await _verify_creator(None)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_verify_creator_requires_an_authorization_header():
    with pytest.raises(HTTPException) as exc:
        await _verify_creator(None)
    assert exc.value.status_code == 401
    assert "authorization header" in exc.value.detail


@pytest.mark.asyncio
async def test_verify_creator_accepts_a_200_response():
    client = _creators_client(200)
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        assert await _verify_creator("Bearer tok") is None
    assert client.get.await_args.args[0].endswith("/api/v1/creators/me")
    assert client.get.await_args.kwargs["headers"] == {"Authorization": "Bearer tok"}


@pytest.mark.asyncio
async def test_verify_creator_rejects_a_missing_creator_profile():
    client = _creators_client(404)
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await _verify_creator("Bearer tok")
    assert exc.value.status_code == 403
    assert exc.value.detail == "Creator profile not found"


@pytest.mark.parametrize("status", [400, 401, 403, 500, 503])
@pytest.mark.asyncio
async def test_verify_creator_rejects_other_failure_statuses(status):
    client = _creators_client(status)
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await _verify_creator("Bearer tok")
    assert exc.value.status_code == 403
    assert exc.value.detail == "Creator verification failed"


@pytest.mark.asyncio
async def test_verify_creator_fails_closed_on_a_transport_error():
    # A creators-service outage must deny, never allow-by-default.
    client = _creators_client(exc=httpx.RequestError("connection refused"))
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await _verify_creator("Bearer tok")
    assert exc.value.status_code == 403
    assert exc.value.detail == "Creator verification failed"


@pytest.mark.asyncio
async def test_verify_creator_uses_a_bounded_timeout():
    client = _creators_client(200)
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client) as ctor:
        await _verify_creator("Bearer tok")
    assert ctor.call_args.kwargs["timeout"] == 5.0


# ---------------------------------------------------------------------------
# auth_version payload shapes (billing_routes.py:88-108)
# ---------------------------------------------------------------------------


def _introspect(token: str, json_value):
    resp = MagicMock()
    resp.status_code = 200
    if isinstance(json_value, Exception):
        resp.json = MagicMock(side_effect=json_value)
    else:
        resp.json = MagicMock(return_value=json_value)
    return _mock_client(resp=resp)


@pytest.mark.asyncio
async def test_unparseable_introspection_body_is_rejected():
    token = _token(av=7)
    client = _introspect(token, ValueError("not json"))
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_payload(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_auth_version_is_read_from_a_nested_user_object():
    token = _token(av=3)
    client = _introspect(token, {"user": {"auth_version": 3}})
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        payload = await get_current_user_payload(authorization=f"Bearer {token}")
    assert payload["av"] == 3


@pytest.mark.asyncio
async def test_auth_version_alias_av_is_read_from_a_nested_user_object():
    token = _token(av=4)
    client = _introspect(token, {"user": {"av": 4}})
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        payload = await get_current_user_payload(authorization=f"Bearer {token}")
    assert payload["av"] == 4


@pytest.mark.asyncio
async def test_top_level_av_alias_is_honoured():
    token = _token(av=6)
    client = _introspect(token, {"av": 6})
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        payload = await get_current_user_payload(authorization=f"Bearer {token}")
    assert payload["av"] == 6


@pytest.mark.asyncio
async def test_nested_stale_auth_version_is_rejected():
    token = _token(av=1)
    client = _introspect(token, {"user": {"auth_version": 9}})
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_payload(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_non_numeric_auth_version_is_rejected_as_an_invalid_token():
    # A malformed version cannot be compared, so the token is rejected outright
    # rather than silently treated as matching.
    token = _token(av=2)
    client = _introspect(token, {"auth_version": "not-a-number"})
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_payload(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token payload"


@pytest.mark.asyncio
async def test_non_dict_body_is_rejected():
    token = _token(av=2)
    client = _introspect(token, ["unexpected", "list"])
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_payload(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# require_admin (billing_routes.py:128-139)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_admin_returns_the_subject_uuid():
    sub = str(uuid4())
    assert await require_admin({"role": "admin", "sub": sub}) == UUID(sub)


@pytest.mark.asyncio
async def test_require_admin_falls_back_to_user_id_claim():
    sub = str(uuid4())
    assert await require_admin({"role": "admin", "user_id": sub}) == UUID(sub)


@pytest.mark.asyncio
async def test_require_admin_rejects_a_non_admin_role():
    with pytest.raises(HTTPException) as exc:
        await require_admin({"role": "user", "sub": str(uuid4())})
    assert exc.value.status_code == 403
    assert exc.value.detail == "Admin privileges required"


@pytest.mark.asyncio
async def test_require_admin_rejects_a_missing_role():
    with pytest.raises(HTTPException) as exc:
        await require_admin({"sub": str(uuid4())})
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_admin_rejects_a_token_with_no_subject():
    with pytest.raises(HTTPException) as exc:
        await require_admin({"role": "admin"})
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token subject"


@pytest.mark.asyncio
async def test_require_admin_rejects_a_non_uuid_subject():
    with pytest.raises(HTTPException) as exc:
        await require_admin({"role": "admin", "sub": "admin@example.com"})
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token subject"


# ---------------------------------------------------------------------------
# get_current_user_id guards (billing_routes.py:142-162)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [None, "", "Token abc", "abc"])
async def test_current_user_id_requires_a_bearer_header(header):
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(authorization=header)
    assert exc.value.status_code == 401
    assert "authorization header" in exc.value.detail


@pytest.mark.asyncio
async def test_current_user_id_rejects_a_token_that_fails_verification():
    from jose import JWTError

    with patch(
        "app.api.billing_routes.verify_jwt_token", new=AsyncMock(side_effect=JWTError("bad"))
    ):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(authorization="Bearer garbage")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


@pytest.mark.asyncio
async def test_current_user_id_falls_back_to_the_user_id_claim():
    sub = str(uuid4())
    client = _introspect(sub, {"auth_version": 2})
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        with patch(
            "app.api.billing_routes.verify_jwt_token",
            new=AsyncMock(return_value={"user_id": sub, "av": 2}),
        ):
            assert await get_current_user_id(authorization="Bearer tok") == UUID(sub)


@pytest.mark.asyncio
async def test_current_user_id_rejects_a_payload_with_no_subject():
    client = _introspect("x", {"auth_version": 2})
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        with patch(
            "app.api.billing_routes.verify_jwt_token", new=AsyncMock(return_value={"av": 2})
        ):
            with pytest.raises(HTTPException) as exc:
                await get_current_user_id(authorization="Bearer tok")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token subject"


@pytest.mark.asyncio
async def test_current_user_id_rejects_a_non_uuid_subject():
    client = _introspect("x", {"auth_version": 2})
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        with patch(
            "app.api.billing_routes.verify_jwt_token",
            new=AsyncMock(return_value={"sub": "not-a-uuid", "av": 2}),
        ):
            with pytest.raises(HTTPException) as exc:
                await get_current_user_id(authorization="Bearer tok")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token subject"


# ---------------------------------------------------------------------------
# require_self (billing_routes.py:165-176)
# ---------------------------------------------------------------------------


def _request_with_path(user_id) -> MagicMock:
    request = MagicMock()
    request.path_params = {} if user_id is None else {"user_id": str(user_id)}
    return request


@pytest.mark.asyncio
async def test_require_self_allows_a_matching_path_user():
    user = uuid4()
    assert await require_self(user, _request_with_path(user)) is user


@pytest.mark.asyncio
async def test_require_self_allows_an_absent_path_user():
    # Routes that do not put user_id in the path (e.g. /purchase) still need
    # the dependency to resolve.
    user = uuid4()
    assert await require_self(user, _request_with_path(None)) is user


@pytest.mark.asyncio
async def test_require_self_blocks_a_cross_account_path():
    user, other = uuid4(), uuid4()
    with pytest.raises(HTTPException) as exc:
        await require_self(user, _request_with_path(other))
    assert exc.value.status_code == 403
    assert exc.value.detail == "You can only access your own data"


# ---------------------------------------------------------------------------
# get_current_user_id subject guard (billing_routes.py:156-162)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_current_user_id_rejects_a_blank_user_id_claim():
    """`sub` absent but `user_id` blank: the explicit empty-subject guard fires.

    ``sub = str(payload.get("sub") or payload.get("user_id"))`` yields ``""``
    when sub is missing and user_id is the empty string, so the guard on the
    next line is reachable. Note this only works because there is no trailing
    ``or ""`` here — ``require_admin`` (line 133) appends one, so it can never
    reach its equivalent guard and relies on the UUID parse instead.
    """
    client = _introspect("x", {"auth_version": 2})
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        with patch(
            "app.api.billing_routes.verify_jwt_token",
            new=AsyncMock(return_value={"sub": None, "user_id": "", "av": 2}),
        ):
            with pytest.raises(HTTPException) as exc:
                await get_current_user_id(authorization="Bearer tok")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token subject"


def test_the_subject_claim_resolution_cannot_produce_a_none_uuid():
    # ``str(...)`` is applied *after* the ``or``, so a missing sub with a
    # missing user_id becomes the literal string "None", not None. The UUID
    # parse then rejects it — which is why the 401 message is the same for both
    # the empty and the non-UUID subject cases.
    payload = {"sub": None, "user_id": None}
    assert str(payload.get("sub") or payload.get("user_id")) == "None"
