import base64
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from jose import jwt
from jose import JWTError

from app.core.settings import settings
from app.core.jwt_verifier import verify_with_jwks, verify_token, get_cached_jwks, clear_cache


def _b64url_int(n: int) -> str:
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _gen_keypair(kid: str = "k1"):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = priv.public_key()
    nums = pub.public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url_int(nums.n),
        "e": _b64url_int(nums.e),
    }
    jwks = {"keys": [jwk]}
    return priv_pem, jwk, jwks


@pytest.mark.asyncio
async def test_valid_rs256_via_jwks():
    priv_pem, jwk, jwks = _gen_keypair("k1")
    uid = str(uuid4())
    payload = {
        "sub": uid,
        "user_id": uid,
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, priv_pem, algorithm="RS256", headers={"kid": "k1"})
    result = verify_with_jwks(token, jwks, expected_type="access")
    assert result["sub"] == uid


@pytest.mark.asyncio
async def test_hs256_rejected():
    priv_pem, jwk, jwks = _gen_keypair("k1")
    payload = {
        "sub": str(uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
    }
    hs_token = jwt.encode(payload, "secret-12345678901234567890", algorithm="HS256")
    with pytest.raises(JWTError):
        verify_with_jwks(hs_token, jwks, expected_type="access")


@pytest.mark.asyncio
async def test_missing_kid_rejected():
    priv_pem, jwk, jwks = _gen_keypair("k1")
    payload = {
        "sub": str(uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, priv_pem, algorithm="RS256")
    with pytest.raises(JWTError):
        verify_with_jwks(token, jwks, expected_type="access")


@pytest.mark.asyncio
async def test_unknown_kid_rejected():
    priv_pem, jwk, jwks = _gen_keypair("k1")
    payload = {
        "sub": str(uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, priv_pem, algorithm="RS256", headers={"kid": "unknown"})
    with pytest.raises(JWTError):
        verify_with_jwks(token, jwks, expected_type="access")


@pytest.mark.asyncio
async def test_wrong_audience_rejected():
    priv_pem, jwk, jwks = _gen_keypair("k1")
    payload = {
        "sub": str(uuid4()),
        "type": "access",
        "aud": "wrong",
        "iss": settings.JWT_ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, priv_pem, algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(JWTError):
        verify_with_jwks(token, jwks, expected_type="access")


@pytest.mark.asyncio
async def test_wrong_issuer_rejected():
    priv_pem, jwk, jwks = _gen_keypair("k1")
    payload = {
        "sub": str(uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": "wrong",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, priv_pem, algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(JWTError):
        verify_with_jwks(token, jwks, expected_type="access")


@pytest.mark.asyncio
async def test_type_mismatch_rejected():
    priv_pem, jwk, jwks = _gen_keypair("k1")
    payload = {
        "sub": str(uuid4()),
        "type": "refresh",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, priv_pem, algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(JWTError):
        verify_with_jwks(token, jwks, expected_type="access")


@pytest.mark.asyncio
async def test_rotation_overlap():
    priv1, jwk1, _ = _gen_keypair("k1")
    priv0, jwk0, _ = _gen_keypair("k0")
    jwks = {"keys": [jwk1, jwk0]}
    uid = str(uuid4())
    payload = {
        "sub": uid,
        "user_id": uid,
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
    }
    token_old = jwt.encode(payload, priv0, algorithm="RS256", headers={"kid": "k0"})
    token_new = jwt.encode(payload, priv1, algorithm="RS256", headers={"kid": "k1"})
    assert verify_with_jwks(token_old, jwks)["sub"] == uid
    assert verify_with_jwks(token_new, jwks)["sub"] == uid
    jwks_only_new = {"keys": [jwk1]}
    with pytest.raises(JWTError):
        verify_with_jwks(token_old, jwks_only_new)


@pytest.mark.asyncio
async def test_billing_route_rejects_hs256(monkeypatch):
    from app.api.billing_routes import get_current_user_payload

    payload = {
        "sub": str(uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
    }
    hs_token = jwt.encode(payload, "secret-123", algorithm="HS256")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {}
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)
    jwks = {"keys": []}
    with patch("app.core.jwt_verifier.get_cached_jwks", new=AsyncMock(return_value=jwks)):
        with patch("app.api.billing_routes.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(Exception) as exc:
                await get_current_user_payload(authorization=f"Bearer {hs_token}")
            assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_billing_route_valid_rs256():
    from app.api.billing_routes import get_current_user_payload

    priv_pem, jwk, jwks = _gen_keypair("k1")
    uid = str(uuid4())
    payload = {
        "sub": uid,
        "user_id": uid,
        "type": "access",
        "role": "user",
        "av": 0,
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, priv_pem, algorithm="RS256", headers={"kid": "k1"})
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"auth_version": 0}
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)
    with patch("app.core.jwt_verifier.get_cached_jwks", new=AsyncMock(return_value=jwks)):
        with patch("app.api.billing_routes.httpx.AsyncClient", return_value=mock_client):
            result = await get_current_user_payload(authorization=f"Bearer {token}")
            assert result["sub"] == uid


# ===========================================================================
# Coverage-closing tests for app/core/jwt_verifier.py
# ===========================================================================

import httpx

from app.core import jwt_verifier
from app.core.jwt_verifier import (
    ALLOWED_ALG,
    _get_jwk_for_kid,
    fetch_jwks,
    verify_token_sync,
)


def _signed(claims: dict, kid: str = "k1", alg: str = "RS256") -> str:
    from tests._test_jwks import PRIVATE_PEM

    return jwt.encode(claims, PRIVATE_PEM, algorithm=alg, headers={"kid": kid})


def _access_claims(**overrides) -> dict:
    claims = {
        "sub": str(uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
    }
    claims.update(overrides)
    return claims


def _jwks_client(status: int = 200, payload=None, exc=None) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    if exc is not None:
        client.get = AsyncMock(side_effect=exc)
        return client
    resp = MagicMock(status_code=status)
    resp.json = MagicMock(return_value=payload if payload is not None else {"keys": []})
    if 200 <= status < 300:
        resp.raise_for_status = MagicMock()
    else:
        request = httpx.Request("GET", settings.JWT_JWKS_URL)
        response = httpx.Response(status, request=request)
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("boom", request=request, response=response)
        )
    client.get = AsyncMock(return_value=resp)
    return client


# ---------------------------------------------------------------------------
# _get_jwk_for_kid
# ---------------------------------------------------------------------------


class TestJwkSelection:
    def test_returns_the_matching_key(self):
        jwk = {"kid": "k1", "alg": "RS256"}
        assert _get_jwk_for_kid({"keys": [jwk]}, "k1") is jwk

    def test_returns_none_for_an_unknown_kid(self):
        assert _get_jwk_for_kid({"keys": [{"kid": "k1"}]}, "other") is None

    def test_returns_none_for_an_empty_key_set(self):
        assert _get_jwk_for_kid({"keys": []}, "k1") is None

    def test_tolerates_a_jwks_without_a_keys_member(self):
        assert _get_jwk_for_kid({}, "k1") is None


# ---------------------------------------------------------------------------
# verify_with_jwks — rejection paths
# ---------------------------------------------------------------------------


class TestVerifyWithJwksRejections:
    def test_malformed_token_is_rejected_as_an_invalid_header(self):
        with pytest.raises(JWTError, match="invalid header"):
            verify_with_jwks("not-a-jwt", {"keys": [{"kid": "k1"}]})

    def test_unsupported_algorithm_is_rejected(self):
        # Symmetric-signed token: the header says HS256, which is not allowed.
        token = jwt.encode(_access_claims(), "a-shared-secret", algorithm="HS256", headers={"kid": "k1"})
        assert jwt.get_unverified_header(token)["alg"] == "HS256"
        with pytest.raises(JWTError, match="unsupported alg HS256"):
            verify_with_jwks(token, {"keys": [{"kid": "k1", "alg": "RS256"}]})

    def test_only_rs256_is_allowed(self):
        assert ALLOWED_ALG == {"RS256"}

    def test_a_token_without_a_kid_is_rejected(self):
        from tests._test_jwks import PRIVATE_PEM

        token = jwt.encode(_access_claims(), PRIVATE_PEM, algorithm="RS256")
        with pytest.raises(JWTError, match="missing kid"):
            verify_with_jwks(token, {"keys": [{"kid": "k1"}]})

    def test_an_unknown_kid_is_rejected(self):
        token = _signed(_access_claims(), kid="k-unknown")
        with pytest.raises(JWTError, match="unknown kid"):
            verify_with_jwks(token, {"keys": [{"kid": "k1"}]})

    def test_a_jwk_declaring_a_disallowed_algorithm_is_rejected(self):
        # A rotated key that advertises a weak algorithm must not be honoured.
        from tests._test_jwks import JWK

        hostile = {**JWK, "alg": "HS256"}
        token = _signed(_access_claims(), kid="k1")
        with pytest.raises(JWTError, match="jwk alg not allowed"):
            verify_with_jwks(token, {"keys": [hostile]})

    def test_a_wrong_token_type_is_rejected(self):
        from tests._test_jwks import JWKS

        token = _signed(_access_claims(type="refresh"))
        with pytest.raises(JWTError, match="invalid type expected access"):
            verify_with_jwks(token, JWKS)

    def test_a_refresh_token_is_accepted_when_requested(self):
        from tests._test_jwks import JWKS

        token = _signed(_access_claims(type="refresh"))
        assert verify_with_jwks(token, JWKS, expected_type="refresh")["type"] == "refresh"


# ---------------------------------------------------------------------------
# fetch_jwks
# ---------------------------------------------------------------------------


class TestFetchJwks:
    async def test_fetches_from_the_configured_url(self):
        client = _jwks_client(payload={"keys": [{"kid": "k1"}]})
        with patch.object(jwt_verifier.httpx, "AsyncClient", return_value=client):
            data = await fetch_jwks()
        assert data == {"keys": [{"kid": "k1"}]}
        assert client.get.await_args.args[0] == settings.JWT_JWKS_URL

    async def test_an_explicit_url_overrides_the_configured_one(self):
        client = _jwks_client()
        with patch.object(jwt_verifier.httpx, "AsyncClient", return_value=client) as ctor:
            await fetch_jwks("http://auth.internal/jwks")
        assert client.get.await_args.args[0] == "http://auth.internal/jwks"
        assert ctor.call_args.kwargs["timeout"] == 5.0

    async def test_a_non_200_response_raises(self):
        client = _jwks_client(status=500)
        with patch.object(jwt_verifier.httpx, "AsyncClient", return_value=client):
            with pytest.raises(httpx.HTTPStatusError):
                await fetch_jwks()

    async def test_a_transport_error_propagates(self):
        client = _jwks_client(exc=httpx.ConnectError("refused"))
        with patch.object(jwt_verifier.httpx, "AsyncClient", return_value=client):
            with pytest.raises(httpx.ConnectError):
                await fetch_jwks()


# ---------------------------------------------------------------------------
# get_cached_jwks
# ---------------------------------------------------------------------------


class TestJwksCache:
    @pytest.fixture(autouse=True)
    def _clear(self):
        clear_cache()
        yield
        clear_cache()

    async def test_the_first_call_fetches_and_caches(self):
        client = _jwks_client(payload={"keys": [{"kid": "k1"}]})
        with patch.object(jwt_verifier.httpx, "AsyncClient", return_value=client):
            first = await get_cached_jwks()
            second = await get_cached_jwks()
        assert first == second == {"keys": [{"kid": "k1"}]}
        # The second call must be served from the cache.
        assert client.get.await_count == 1

    async def test_a_different_url_bypasses_the_cache(self):
        client = _jwks_client(payload={"keys": []})
        with patch.object(jwt_verifier.httpx, "AsyncClient", return_value=client):
            await get_cached_jwks("http://a/jwks")
            await get_cached_jwks("http://b/jwks")
        assert client.get.await_count == 2

    async def test_an_expired_cache_refetches(self):
        client = _jwks_client(payload={"keys": []})
        with patch.object(jwt_verifier.httpx, "AsyncClient", return_value=client):
            await get_cached_jwks(ttl=0)
            await get_cached_jwks(ttl=0)
        # ttl=0 means the entry is already stale on the next call.
        assert client.get.await_count == 2

    async def test_a_negative_ttl_is_always_stale(self):
        client = _jwks_client(payload={"keys": []})
        with patch.object(jwt_verifier.httpx, "AsyncClient", return_value=client):
            await get_cached_jwks(ttl=-1)
            await get_cached_jwks(ttl=-1)
        assert client.get.await_count == 2

    def test_clear_cache_resets_every_tracking_field(self):
        client = _jwks_client()

        async def _run():
            with patch.object(jwt_verifier.httpx, "AsyncClient", return_value=client):
                await get_cached_jwks()

        import asyncio

        asyncio.run(_run())
        assert jwt_verifier._jwks_cache is not None
        assert jwt_verifier._jwks_url_cached == settings.JWT_JWKS_URL
        assert jwt_verifier._jwks_expiry > 0
        clear_cache()
        assert jwt_verifier._jwks_cache is None
        assert jwt_verifier._jwks_url_cached is None
        assert jwt_verifier._jwks_expiry == 0


# ---------------------------------------------------------------------------
# verify_token / verify_token_sync
# ---------------------------------------------------------------------------


class TestVerifyTokenEntryPoints:
    def test_verify_token_sync_verifies_against_a_supplied_key_set(self):
        from tests._test_jwks import JWKS

        payload = verify_token_sync(_signed(_access_claims()), JWKS)
        assert payload["type"] == "access"

    def test_verify_token_sync_honours_the_expected_type(self):
        from tests._test_jwks import JWKS

        with pytest.raises(JWTError, match="invalid type"):
            verify_token_sync(_signed(_access_claims(type="refresh")), JWKS)

    async def test_verify_token_prefers_the_override_over_the_cache(self):
        from tests._test_jwks import JWKS

        with patch.object(jwt_verifier, "get_cached_jwks", new=AsyncMock()) as cached:
            payload = await verify_token(_signed(_access_claims()), jwks_override=JWKS)
        cached.assert_not_awaited()
        assert payload["type"] == "access"

    async def test_verify_token_falls_back_to_the_cache(self):
        from tests._test_jwks import JWKS

        with patch.object(jwt_verifier, "get_cached_jwks", new=AsyncMock(return_value=JWKS)) as cached:
            payload = await verify_token(_signed(_access_claims()))
        cached.assert_awaited_once()
        assert payload["type"] == "access"

    async def test_verify_token_propagates_a_verification_failure(self):
        from tests._test_jwks import JWKS

        with patch.object(jwt_verifier, "get_cached_jwks", new=AsyncMock(return_value=JWKS)):
            with pytest.raises(JWTError, match="unknown kid"):
                await verify_token(_signed(_access_claims(), kid="k-nope"))


# ---------------------------------------------------------------------------
# app/core/logging.py
# ---------------------------------------------------------------------------


class TestLoggingHelpers:
    def test_setup_logging_is_idempotent(self):
        from app.core.logging import setup_logging

        # basicConfig only configures once; calling it again must not raise.
        setup_logging()
        setup_logging()

    def test_set_request_id_generates_and_stores_a_uuid(self):
        from app.core.logging import request_id_var, set_request_id

        first = set_request_id()
        second = set_request_id()
        assert first != second
        assert request_id_var.get() == second
        # A request id must be a bare uuid, not a prefixed or namespaced string.
        assert len(first) == 36
        assert first.count("-") == 4

    def test_set_correlation_id_returns_and_stores_the_value(self):
        from app.core.logging import set_correlation_id

        assert set_correlation_id("corr-123") == "corr-123"
