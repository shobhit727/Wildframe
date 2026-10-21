"""Suite-wide doubles for the two outbound dependencies the billing service has.

JWKS
    ``app.core.jwt_verifier.get_cached_jwks`` is pinned to the shared test key
    from ``tests/_test_jwks`` so RS256 tokens minted by ``tests/_auth_tokens``
    verify for real, signature and claims included.

auth-service ``GET /api/v1/auth/me``
    ``app.api.billing_routes._enforce_auth_version`` introspects the presented
    token against the live auth-service on *every* authenticated request and
    fails closed — 401 — when the round-trip raises, when the body carries no
    integer auth version, or when that version disagrees with the token's ``av``
    claim. Unit tests have no auth-service to talk to, so this double answers
    that one call with the body ``UserResponse`` really returns, taking
    ``auth_version`` from the token's own ``av``.

    Echoing the token's ``av`` rather than hardcoding a number keeps the real
    comparison in ``_enforce_auth_version`` running: an expired/revoked token
    (a stale ``av``, or one that is not an ``int``) still produces a 401, which
    is the behaviour under test. A test that wants a specific introspection
    outcome patches ``app.api.billing_routes.httpx.AsyncClient`` itself — see
    ``tests/test_auth_version.py``.

    Outbound calls to any *other* URL keep the previous permissive default
    (200 + ``{}``) so unrelated doubles elsewhere in the suite are unaffected.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jose import JWTError

from app.core.jwt_verifier import verify_with_jwks
from tests._test_jwks import JWKS

_AUTH_ME_PATH = "/api/v1/auth/me"


def _response(status_code: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


def _auth_me_body(authorization: str) -> MagicMock:
    """Answer ``/auth/me`` the way auth-service would for this token."""
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        return _response(401, {"detail": "Missing token"})
    try:
        claims = verify_with_jwks(token, JWKS, expected_type="access")
    except JWTError:
        return _response(401, {"detail": "Invalid or expired token"})

    auth_version = claims.get("av")
    if not isinstance(auth_version, int) or isinstance(auth_version, bool):
        return _response(401, {"detail": "Invalid or expired token"})
    return _response(
        200,
        {
            "id": claims.get("sub"),
            "email": claims.get("email", "user@example.com"),
            "first_name": None,
            "last_name": None,
            "email_verified": True,
            "last_login_at": None,
            "created_at": "2026-01-01T00:00:00",
            "auth_version": auth_version,
            "role": claims.get("role", "user"),
        },
    )


@pytest.fixture(autouse=True)
def _fake_outbound_dependencies():
    async def _get(url, *args, headers=None, **kwargs):
        if str(url).endswith(_AUTH_ME_PATH):
            authorization = (headers or {}).get("Authorization", "")
            return _auth_me_body(authorization)
        return _response(200, {})

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=_get)
    with patch("httpx.AsyncClient", return_value=mock_client):
        with patch("app.api.billing_routes.httpx.AsyncClient", return_value=mock_client):
            with patch("app.core.jwt_verifier.get_cached_jwks", new=AsyncMock(return_value=JWKS)):
                yield
