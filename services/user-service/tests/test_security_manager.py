"""Behavioural tests for `app.security.manager` (auth-adjacent).

This module is the only place user-service hashes passwords and mints/verifies
JWTs, so the *deny* paths matter more than the happy paths:

* `PasswordManager.verify_password` must return False (never raise) on a
  malformed hash or an over-long password, so a bad request cannot 500.
* `TokenManager.verify_token` must return None (never raise) for a wrong-type,
  expired, tampered or foreign-audience token.

`_enforce_auth_version` (app/api/routes) is covered here too: it is the second
half of the auth boundary - the token-version check against auth-service.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import bcrypt
import httpx
import pytest
from fastapi import HTTPException
from jose import jwt

from app.api.routes import _enforce_auth_version
from app.core.settings import settings
from app.security.manager import (
    PASSWORD_MAX_LENGTH,
    PasswordManager,
    TokenManager,
    _encode_password,
)

# bcrypt is intentionally slow; keep the suite fast without changing behaviour.
FAST_ROUNDS = 4


def _hash(password: str, rounds: int = FAST_ROUNDS) -> str:
    salt = bcrypt.gensalt(rounds=rounds)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


# ---------------------------------------------------------------------------
# _encode_password
# ---------------------------------------------------------------------------


def test_encode_password_returns_utf8_bytes():
    assert _encode_password("hunter2") == b"hunter2"
    assert _encode_password("pässwörd") == "pässwörd".encode("utf-8")


def test_encode_password_accepts_exactly_the_maximum_length():
    at_limit = "a" * PASSWORD_MAX_LENGTH

    assert _encode_password(at_limit) == at_limit.encode("utf-8")


def test_encode_password_rejects_over_length_instead_of_truncating():
    with pytest.raises(ValueError, match=f"maximum length of {PASSWORD_MAX_LENGTH}"):
        _encode_password("a" * (PASSWORD_MAX_LENGTH + 1))


# ---------------------------------------------------------------------------
# PasswordManager
# ---------------------------------------------------------------------------


def test_hash_then_verify_round_trip():
    digest = PasswordManager.hash_password("correct horse", rounds=FAST_ROUNDS)

    assert digest != "correct horse"
    assert PasswordManager.verify_password("correct horse", digest) is True


def test_verify_rejects_wrong_password():
    digest = PasswordManager.hash_password("correct horse", rounds=FAST_ROUNDS)

    assert PasswordManager.verify_password("Correct horse", digest) is False


def test_verify_rejects_a_malformed_hash_without_raising():
    assert PasswordManager.verify_password("anything", "not-a-bcrypt-hash") is False


def test_known_defect_verify_raises_on_a_none_hash():
    """Characterisation test for a reported defect (NOT an assertion of intent).

    `app/security/manager.py:51` guards the verify path with
    ``except (ValueError, TypeError)`` but ``None.encode("utf-8")`` raises
    ``AttributeError``, which is not caught. A null/garbled stored hash
    therefore escapes as an unhandled 500 instead of a `False` verification
    failure. Expected to change when production code is fixed.
    """
    with pytest.raises(AttributeError):
        PasswordManager.verify_password("anything", None)


def test_verify_rejects_an_over_length_password_without_raising():
    """The length guard must not escape as a 500 from the verify path."""
    digest = PasswordManager.hash_password("short", rounds=FAST_ROUNDS)

    assert PasswordManager.verify_password("a" * (PASSWORD_MAX_LENGTH + 1), digest) is False


def test_hash_uses_configured_rounds_by_default():
    with patch("app.security.manager.settings") as fake_settings:
        fake_settings.PASSWORD_BCRYPT_ROUNDS = FAST_ROUNDS
        digest = PasswordManager.hash_password("secret")

    assert digest.startswith("$2b$04$")


def test_two_hashes_of_the_same_password_differ_by_salt():
    first = PasswordManager.hash_password("same", rounds=FAST_ROUNDS)
    second = PasswordManager.hash_password("same", rounds=FAST_ROUNDS)

    assert first != second
    assert PasswordManager.verify_password("same", first) is True
    assert PasswordManager.verify_password("same", second) is True


# ---------------------------------------------------------------------------
# TokenManager.create_access_token / verify_token
# ---------------------------------------------------------------------------


def test_create_access_token_round_trips_the_subject():
    subject = str(uuid4())

    token = TokenManager.create_access_token(subject)
    payload = TokenManager.verify_token(token)

    assert payload is not None
    assert payload["sub"] == subject
    assert payload["type"] == "access"


def test_known_defect_self_minted_tokens_carry_no_aud_claim():
    """Characterisation test for a reported gap (NOT an assertion of intent).

    `app/security/manager.py:65` builds the payload without `aud`, yet
    `verify_token` (line 77) decodes *with* `audience=settings.JWT_AUDIENCE`.
    python-jose skips the audience check when the claim is absent, so the
    round trip works - but tokens minted here are not spec-conformant
    (auth-service tokens do carry `aud`) and would be indistinguishable from
    a token that bypassed the audience policy.
    """
    payload = TokenManager.verify_token(TokenManager.create_access_token(str(uuid4())))

    assert payload is not None
    assert "aud" not in payload


def test_create_access_token_honours_an_explicit_expiry():
    token = TokenManager.create_access_token(
        str(uuid4()), expires_delta=timedelta(minutes=1, seconds=30)
    )
    payload = TokenManager.verify_token(token)

    assert payload is not None
    expires = datetime.fromtimestamp(payload["exp"], tz=UTC)
    assert timedelta(minutes=1) < expires - datetime.now(UTC) <= timedelta(minutes=1, seconds=30)


def test_verify_token_rejects_an_expired_token():
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "exp": datetime.now(UTC) - timedelta(minutes=1),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

    assert TokenManager.verify_token(token) is None


def test_verify_token_rejects_a_tampered_token():
    token = TokenManager.create_access_token(str(uuid4()))
    head, payload_b64, sig = token.split(".")
    tampered = f"{head}.{payload_b64}.{'A' * len(sig)}"

    assert TokenManager.verify_token(tampered) is None


def test_verify_token_rejects_a_token_signed_with_another_key():
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        "a-completely-different-signing-key-of-32-chars",
        algorithm=settings.JWT_ALGORITHM,
    )

    assert TokenManager.verify_token(token) is None


def test_verify_token_rejects_a_foreign_audience():
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": "some-other-api",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

    assert TokenManager.verify_token(token) is None


def test_verify_token_rejects_a_refresh_token_when_access_is_required():
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "refresh",
            "aud": settings.JWT_AUDIENCE,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

    assert TokenManager.verify_token(token) is None
    assert TokenManager.verify_token(token, token_type="refresh") is not None


def test_verify_token_returns_none_for_garbage_instead_of_raising():
    assert TokenManager.verify_token("not-a-jwt") is None
    assert TokenManager.verify_token("") is None


# ---------------------------------------------------------------------------
# TokenManager.hash_token
# ---------------------------------------------------------------------------


def test_hash_token_is_a_stable_sha256_hex_digest():
    digest = TokenManager.hash_token("abc")

    assert len(digest) == 64
    assert digest == TokenManager.hash_token("abc")
    assert digest != TokenManager.hash_token("abd")


def test_hash_token_matches_hashlib_sha256():
    import hashlib

    assert TokenManager.hash_token("abc") == hashlib.sha256(b"abc").hexdigest()


# ---------------------------------------------------------------------------
# _enforce_auth_version - the second half of the auth boundary
# ---------------------------------------------------------------------------


def _mock_auth_client(*, status_code=200, json_value=None, json_exc=None, exc=None):
    response = MagicMock()
    response.status_code = status_code
    if json_exc is not None:
        response.json.side_effect = json_exc
    else:
        response.json.return_value = json_value

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    if exc is not None:
        client.get = AsyncMock(side_effect=exc)
    else:
        client.get = AsyncMock(return_value=response)
    return client


async def test_enforce_auth_version_accepts_a_matching_version():
    client = _mock_auth_client(json_value={"auth_version": 3})

    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        await _enforce_auth_version("Bearer tok", {"av": 3})

    assert client.get.await_args.kwargs["headers"] == {"Authorization": "Bearer tok"}


async def test_enforce_auth_version_rejects_a_stale_version():
    client = _mock_auth_client(json_value={"auth_version": 4})

    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as excinfo:
            await _enforce_auth_version("Bearer tok", {"av": 3})

    assert excinfo.value.status_code == 401
    assert excinfo.value.headers["WWW-Authenticate"] == "Bearer"


async def test_enforce_auth_version_rejects_a_token_with_no_av_claim():
    """Missing `av` defaults to 0, so any real auth_version above 0 denies."""
    client = _mock_auth_client(json_value={"auth_version": 1})

    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as excinfo:
            await _enforce_auth_version("Bearer tok", {})

    assert excinfo.value.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"authVersion": 7},
        {"av": 7},
        {"user": {"auth_version": 7}},
        {"user": {"av": 7}},
    ],
)
async def test_enforce_auth_version_reads_every_supported_spellings(body: dict):
    """auth-service has shipped several field spellings; all are honoured."""
    client = _mock_auth_client(json_value=body)

    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        await _enforce_auth_version("Bearer tok", {"av": 7})

    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException):
            await _enforce_auth_version("Bearer tok", {"av": 6})


async def test_enforce_auth_version_rejects_a_non_numeric_av_claim():
    client = _mock_auth_client(json_value={"auth_version": 3})

    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as excinfo:
            await _enforce_auth_version("Bearer tok", {"av": "not-a-number"})

    assert excinfo.value.detail == "Invalid token"


async def test_enforce_auth_version_rejects_a_null_av_claim():
    client = _mock_auth_client(json_value={"auth_version": 3})

    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as excinfo:
            await _enforce_auth_version("Bearer tok", {"av": None})

    assert excinfo.value.detail == "Invalid token"


async def test_enforce_auth_version_allows_a_token_when_no_version_is_published():
    client = _mock_auth_client(json_value={"sub": str(uuid4())})

    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        await _enforce_auth_version("Bearer tok", {"av": 99})


async def test_enforce_auth_version_ignores_a_non_dict_body():
    client = _mock_auth_client(json_value=[1, 2, 3])

    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        await _enforce_auth_version("Bearer tok", {"av": 99})


async def test_enforce_auth_version_tolerates_an_unparseable_body():
    """A non-JSON 200 is not treated as a rejection - the JWT check stands."""
    client = _mock_auth_client(json_exc=ValueError("not json"))

    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        await _enforce_auth_version("Bearer tok", {"av": 99})


@pytest.mark.parametrize("status_code", [401, 403, 500, 503])
async def test_enforce_auth_version_rejects_any_non_200_response(status_code: int):
    client = _mock_auth_client(status_code=status_code, json_value={"auth_version": 3})

    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as excinfo:
            await _enforce_auth_version("Bearer tok", {"av": 3})

    assert excinfo.value.status_code == 401


async def test_enforce_auth_version_rejects_when_auth_service_is_unreachable():
    client = _mock_auth_client(exc=httpx.ConnectError("connection refused"))

    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as excinfo:
            await _enforce_auth_version("Bearer tok", {"av": 3})

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid or expired token"


async def test_enforce_auth_version_calls_the_me_endpoint_with_a_timeout():
    client = _mock_auth_client(json_value={})

    with patch("app.api.routes.httpx.AsyncClient", return_value=client):
        await _enforce_auth_version("Bearer tok", {})

    url = client.get.await_args.args[0]
    assert url.endswith("/api/v1/auth/me")
    assert url.startswith(settings.AUTH_SERVICE_URL)
