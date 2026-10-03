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
import wildframe_auth
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
from tests._test_jwks import JWKS, PRIVATE_PEM
from wildframe_auth.verifier import clear_jwks_cache


class _Endpoint:
    """The auth service's JWKS endpoint, as the service sees it."""

    async def fetch(self, url: str):
        return JWKS


@pytest.fixture(autouse=True)
def _stub_jwks_endpoint(monkeypatch):
    """Serve the test JWKS and clear the SDK cache around every test."""
    monkeypatch.setattr(wildframe_auth.verifier, "fetch_jwks", _Endpoint().fetch)
    clear_jwks_cache()
    yield
    clear_jwks_cache()


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
# TokenManager.verify_token
# ---------------------------------------------------------------------------


def _mint(
    *,
    kid: str = "k1",
    typ: str = "access",
    role: str = "user",
    exp_offset: int = 300,
    aud: str | None = None,
    iss: str | None = None,
    sub: str | None = None,
    private_pem: str = PRIVATE_PEM,
    algorithm: str = "RS256",
    **extra: object,
) -> str:
    """A real RS256 token over the test key, or an HS256 forgery if asked.

    ``private_pem`` defaults to the in-memory test key because the service no
    longer accepts a shared-secret HS256 token at all.
    """
    now = datetime.now(UTC)
    claims = {
        "sub": sub or str(uuid4()),
        "type": typ,
        "aud": aud or settings.JWT_AUDIENCE,
        "iss": iss or settings.JWT_ISSUER,
        "role": role,
        "av": 0,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()) + exp_offset,
    }
    claims.update(extra)
    return jwt.encode(claims, private_pem, algorithm=algorithm, headers={"kid": kid})


async def test_verify_token_round_trips_the_subject():
    subject = str(uuid4())

    payload = await TokenManager.verify_token(_mint(sub=subject))

    assert payload is not None
    assert payload["sub"] == subject
    assert payload["type"] == "access"


async def test_verify_token_honours_an_explicit_expiry():
    payload = await TokenManager.verify_token(_mint(exp_offset=-90))

    assert payload is not None
    expires = datetime.fromtimestamp(payload["exp"], tz=UTC)
    assert timedelta(minutes=13) < expires - datetime.now(UTC) <= timedelta(minutes=15)


async def test_verify_token_rejects_an_expired_token():
    # 1h, not 1m: the shared verifier allows ~60s of clock skew, so a token that
    # expired "just now" is *meant* to pass.
    assert await TokenManager.verify_token(_mint(exp_offset=-3600)) is None


async def test_verify_token_rejects_a_tampered_token():
    token = _mint()
    head, payload_b64, sig = token.split(".")
    tampered = f"{head}.{payload_b64}.{'A' * len(sig)}"

    assert await TokenManager.verify_token(tampered) is None


async def test_verify_token_rejects_a_token_signed_with_another_key():
    """An RS256 token from an RSA key that is not in the JWKS is refused."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    intruder = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = intruder.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()

    assert await TokenManager.verify_token(_mint(private_pem=pem)) is None


async def test_verify_token_rejects_a_foreign_audience():
    assert await TokenManager.verify_token(_mint(aud="some-other-api")) is None


async def test_verify_token_rejects_a_foreign_issuer():
    assert await TokenManager.verify_token(_mint(iss="https://evil.test")) is None


async def test_verify_token_rejects_an_unknown_kid():
    """The rotation refresh must not turn "unknown" into "accepted"."""
    assert await TokenManager.verify_token(_mint(kid="k-never-published")) is None


async def test_verify_token_rejects_a_refresh_token_when_access_is_required():
    """Token-type separation (#221), now enforced by the shared verifier.

    The message is no longer this module's own -- the rejection happens inside
    the verifier, which raises ``JWTError`` and this method turns into ``None``.
    """
    token = _mint(typ="refresh")

    assert await TokenManager.verify_token(token) is None
    assert await TokenManager.verify_token(token, token_type="refresh") is not None


async def test_verify_token_rejects_a_token_without_a_sub():
    """``sub`` is a required claim for the shared verifier."""
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "iss": settings.JWT_ISSUER,
            "av": 0,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=15)).timestamp()),
        },
        PRIVATE_PEM,
        algorithm="RS256",
        headers={"kid": "k1"},
    )

    assert await TokenManager.verify_token(token) is None


async def test_verify_token_returns_none_for_garbage_instead_of_raising():
    assert await TokenManager.verify_token("not-a-jwt") is None
    assert await TokenManager.verify_token("") is None


@pytest.mark.parametrize(
    "secret", ["dev-secret-key", "dev-secret-key-change-in-production-min-32-bytes"]
)
async def test_verify_token_rejects_the_committed_dev_secret(secret):
    """The #941 bypass, at the level it was reported.

    Both committed development secrets are covered: the one
    ``docker-compose.dev.yml`` injects, and the pydantic default in
    ``app/core/settings.py`` that a service sees when the variable is unset.
    Neither is a valid key now, whatever the environment happens to hold.
    """
    forged = _mint(role="admin", private_pem=secret, algorithm="HS256")

    assert await TokenManager.verify_token(forged) is None


def test_create_access_token_no_longer_exists():
    """The shared-secret *minter* is gone, not just the verifier.

    It signed with the committed development secret, so it was a forge-token
    factory in the same module as the verifier. After the move to the JWKS no
    service would accept its output, which made it dead code as well as
    dangerous, and nothing in ``app/`` called it -- auth-service is the only
    issuer in this platform. Asserted by name so its return cannot creep back
    in unnoticed.
    """
    assert not hasattr(TokenManager, "create_access_token")
    assert "create_access_token" not in dir(TokenManager)


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
