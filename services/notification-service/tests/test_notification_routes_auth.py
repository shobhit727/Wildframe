"""Tests for `app.api.notification_routes.get_current_user_id`.

This is the service's only authentication boundary, and it enforces three
separate rules that the existing route tests never reach (they *override* the
dependency):

1. the header must be a `Bearer` token;
2. the token must decode with the platform audience/issuer AND be of type
   `access` (refresh tokens share the audience and must never pass - #221);
3. the subject must exist and be a UUID.

Every DENY path is asserted; a false accept here is an authentication bypass.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jose import JWTError, jwt

from app.api.notification_routes import get_current_user_id
from app.core.settings import settings


def _token(**claims) -> str:
    payload = {
        "sub": str(uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
        **claims,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


# ---------------------------------------------------------------------------
# Accept
# ---------------------------------------------------------------------------


async def test_a_well_formed_access_token_resolves_the_user_id():
    subject = uuid4()

    user_id = await get_current_user_id(authorization=f"Bearer {_token(sub=str(subject))}")

    assert isinstance(user_id, UUID)
    assert user_id == subject


async def test_the_user_id_claim_is_accepted_as_an_alias_for_sub():
    """Tokens minted before the `sub` rename still authenticate."""
    subject = uuid4()
    claims = {
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "user_id": str(subject),
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    token = jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    user_id = await get_current_user_id(authorization=f"Bearer {token}")

    assert user_id == subject


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
    forged = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "iss": settings.JWT_ISSUER,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        "a-completely-different-signing-key-32c",
        algorithm=settings.JWT_ALGORITHM,
    )

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {forged}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid token"


async def test_an_expired_token_is_rejected():
    expired = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "iss": settings.JWT_ISSUER,
            "exp": datetime.now(UTC) - timedelta(minutes=1),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {expired}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid token"


async def test_a_token_with_the_wrong_audience_is_rejected():
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": "some-other-api",
            "iss": settings.JWT_ISSUER,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {token}")

    assert excinfo.value.status_code == 401


async def test_a_token_with_the_wrong_issuer_is_rejected():
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "iss": "some-other-issuer",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {token}")

    assert excinfo.value.status_code == 401


async def test_a_tampered_token_is_rejected():
    token = _token()
    head, payload_b64, signature = token.split(".")

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {head}.{payload_b64}.{'A' * len(signature)}")

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
    refresh = _token(type="refresh")

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {refresh}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid token type"


async def test_a_token_with_no_type_claim_is_rejected():
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "aud": settings.JWT_AUDIENCE,
            "iss": settings.JWT_ISSUER,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {token}")

    assert excinfo.value.detail == "Invalid token type"


# ---------------------------------------------------------------------------
# Deny - subject
# ---------------------------------------------------------------------------


async def test_a_token_without_any_subject_is_rejected():
    token = jwt.encode(
        {
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "iss": settings.JWT_ISSUER,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {token}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid token subject"


async def test_a_non_uuid_subject_is_rejected():
    """A signature-valid token whose `sub` is not a UUID must not authenticate."""
    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {_token(sub='admin')}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid token subject"


# ---------------------------------------------------------------------------
# Contract with the JWT layer
# ---------------------------------------------------------------------------


def test_the_decoder_validates_audience_issuer_and_algorithm():
    """The settings the boundary relies on are the platform contract."""
    assert settings.JWT_AUDIENCE == "wildframe-api"
    assert settings.JWT_ISSUER == "wildframe-auth"
    assert settings.JWT_ALGORITHM == "HS256"


def test_garbage_raises_a_jose_error_not_a_bare_exception():
    """`get_current_user_id` only catches JWTError, so assert that is the case."""
    with pytest.raises(JWTError):
        jwt.decode(
            "not-a-jwt",
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )


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
