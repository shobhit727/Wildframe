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
