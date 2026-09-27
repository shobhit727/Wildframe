"""Tests for `app.api.notification_routes.get_current_user_id`.

This is the service's only authentication boundary, and it enforces three
separate rules that the existing route tests never reach (they *override* the
dependency):

1. the header must be a `Bearer` token;
2. the token must verify against auth-service's JWKS with the platform
   audience/issuer AND be of type `access` (refresh tokens share the audience
   and must never pass - #221);
3. the subject must exist and be a UUID.

Every DENY path is asserted; a false accept here is an authentication bypass.

Tokens are RS256, signed with the key pair in `tests/_test_jwks.py`; only the
outbound JWKS HTTP call is replaced. The SDK verifier itself runs for real, so
these tests exercise the same code path as production.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jose import JWTError, jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import wildframe_auth
from app.api.notification_routes import get_current_user_id
from app.core.settings import settings
from tests._test_jwks import JWKS, PRIVATE_PEM
from wildframe_auth.verifier import clear_jwks_cache


def _token(**claims) -> str:
    """Mint an RS256 access token the way auth-service would.

    `av` is required: the SDK's `AUTH_VERSIONED_TYPES` covers `access`, so a
    token without a real int auth-version is rejected as 401 regardless of
    signature.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "role": "user",
        "av": 0,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        **claims,
    }
    return jwt.encode(payload, PRIVATE_PEM, algorithm="RS256", headers={"kid": "k1"})


def _token_without_sub(**claims) -> str:
    """Mint a token with no `sub` at all — the shape pre-rename tokens had."""
    now = datetime.now(UTC)
    payload = {
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "role": "user",
        "av": 0,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        **claims,
    }
    return jwt.encode(payload, PRIVATE_PEM, algorithm="RS256", headers={"kid": "k1"})


def _drop_claim(token: str, claim: str) -> str:
    """Re-sign ``token`` with ``claim`` removed from the payload."""
    _head, payload_b64, _sig = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    claims.pop(claim, None)
    return jwt.encode(claims, PRIVATE_PEM, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture(autouse=True)
def _jwks_endpoint(monkeypatch):
    """Replace the outbound JWKS fetch only; the SDK verifier still runs for real."""

    async def fetch(url: str):
        return JWKS

    monkeypatch.setattr(wildframe_auth.verifier, "fetch_jwks", fetch)
    clear_jwks_cache()
    yield
    clear_jwks_cache()


# ---------------------------------------------------------------------------
# Accept
# ---------------------------------------------------------------------------


async def test_a_well_formed_access_token_resolves_the_user_id():
    subject = uuid4()

    user_id = await get_current_user_id(authorization=f"Bearer {_token(sub=str(subject))}")

    assert isinstance(user_id, UUID)
    assert user_id == subject


async def test_a_token_without_a_sub_claim_is_rejected():
    """The SDK's REQUIRED_CLAIMS includes `sub`, so a `user_id`-only token fails.

    This used to authenticate via the route's `user_id` alias. It no longer
    can: the verifier rejects the token before the route reads any claim, so
    the alias is unreachable for RS256 tokens.
    """
    subject = uuid4()
    # A legacy-shaped token: `user_id` in place of `sub`.
    legacy = _token_without_sub(user_id=str(subject))

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {legacy}")

    assert excinfo.value.status_code == 401


# ---------------------------------------------------------------------------
# Deny - header shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Basic abc", "bearer abc", "Token abc"],
)
async def test_a_missing_or_non_bearer_header_is_rejected(header):
    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=header)

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Missing or invalid authorization header"


# ---------------------------------------------------------------------------
# Deny - token validity
# ---------------------------------------------------------------------------


async def test_a_token_signed_with_another_key_is_rejected():
    """A token whose signature does not match the published JWKS is a 401."""
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    now = datetime.now(UTC)
    forged = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "iss": settings.JWT_ISSUER,
            "av": 0,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        other_pem,
        algorithm="RS256",
        headers={"kid": "k1"},
    )

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {forged}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid token"


async def test_an_expired_token_is_rejected():
    expired = _token(exp=int((datetime.now(UTC) - timedelta(hours=1)).timestamp()))

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {expired}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid token"


async def test_a_token_with_the_wrong_audience_is_rejected():
    token = _token(aud="some-other-api")

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {token}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid token"


async def test_a_token_with_the_wrong_issuer_is_rejected():
    token = _token(iss="some-other-issuer")

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {token}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid token"


async def test_a_tampered_token_is_rejected():
    token = _token()
    head, payload_b64, signature = token.split(".")

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(
            authorization=f"Bearer {head}.{payload_b64}.{'A' * len(signature)}"
        )

    assert excinfo.value.status_code == 401


@pytest.mark.parametrize("garbage", ["", " ", "not-a-jwt", "a.b.c", "....", "null"])
async def test_garbage_tokens_are_rejected_without_raising(garbage):
    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {garbage}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid token"


# ---------------------------------------------------------------------------
# Deny - token type separation (#221)
# ---------------------------------------------------------------------------


async def test_a_refresh_token_is_rejected():
    """#221: refresh tokens share the audience but must never act as access tokens.

    The SDK enforces the type via `expected_type="access"` and reports it as a
    plain JWTError, so this is a 401 "Invalid token" rather than the bespoke
    "Invalid token type" detail the old HS256 branch produced.
    """
    refresh = _token(type="refresh")

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {refresh}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid token"


async def test_a_token_with_no_type_claim_is_rejected():
    token = _token_without_sub(type=None, sub=str(uuid4()))
    token = _drop_claim(token, "type")

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {token}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid token"


# ---------------------------------------------------------------------------
# Deny - subject
# ---------------------------------------------------------------------------


async def test_a_token_without_any_subject_is_rejected():
    token = _token_without_sub()

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {token}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid token"


async def test_a_non_uuid_subject_is_rejected():
    """A signature-valid token whose `sub` is not a UUID must not authenticate."""
    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {_token(sub='admin')}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid token subject"


# ---------------------------------------------------------------------------
# Contract with the JWT layer
# ---------------------------------------------------------------------------


def test_the_verifier_enforces_the_platform_audience_and_issuer():
    """The settings the boundary relies on are the platform contract."""
    assert settings.JWT_AUDIENCE == "wildframe-api"
    assert settings.JWT_ISSUER == "wildframe-auth"
    assert settings.JWT_JWKS_URL


def test_garbage_raises_a_jose_error_not_a_bare_exception():
    """`get_current_user_id` only catches JWTError, so assert that is the case."""
    with pytest.raises(JWTError):
        jwt.decode(
            "not-a-jwt",
            PRIVATE_PEM,
            algorithms=["RS256"],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )


async def test_the_sdk_refuses_hs256_entirely():
    """#941: the shared secret must not be a way in, even with a valid `kid`."""
    now = datetime.now(UTC)
    hs = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "iss": settings.JWT_ISSUER,
            "av": 0,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        "dev-secret-key-change-in-production-min-32-bytes",
        algorithm="HS256",
        headers={"kid": "k1"},
    )

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {hs}")

    assert excinfo.value.status_code == 401


# ---------------------------------------------------------------------------
# Route-level authorization: a token may only act on its own account
# ---------------------------------------------------------------------------


def _post_send(client, user_id, **overrides):
    payload = {
        "user_id": str(user_id),
        "title": "New episode",
        "message": "Season 5 is out",
    }
    payload.update(overrides)
    return client.post("/api/v1/notifications/send", json=payload)


async def test_send_for_another_account_is_forbidden():
    """A valid token must not be enough to notify a different user."""
    from app.api.notification_routes import get_current_user_id, get_notif_service
    from app.core.database import DatabaseManager
    from app.main import create_app

    mine, theirs = uuid4(), uuid4()
    app = create_app()
    service = MagicMock()
    service.send_notification = AsyncMock(return_value={"status": "sent"})
    app.dependency_overrides[get_current_user_id] = lambda: mine
    app.dependency_overrides[get_notif_service] = lambda: service

    with patch.object(DatabaseManager, "health_check", new=AsyncMock(return_value=True)):
        with patch.object(DatabaseManager, "close", new=AsyncMock(return_value=None)):
            with TestClient(app, base_url="http://localhost") as client:
                response = _post_send(client, theirs)

    assert response.status_code == 403
    assert response.json()["detail"] == "You can only act on your own account"
    # Nothing was dispatched on the victim's behalf.
    service.send_notification.assert_not_awaited()
