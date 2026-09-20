import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from jose import JWTError, jwt

from app.core.settings import settings
from app.security import TokenManager
from app.security.jwks import get_jwks, get_private_key_pem, reset_cache


def _b64url_int(n: int) -> str:
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _generate_rsa_keypair():
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
        "kid": "test-k1",
        "use": "sig",
        "alg": "RS256",
        "n": _b64url_int(nums.n),
        "e": _b64url_int(nums.e),
    }
    return priv_pem, jwk


def test_access_token_is_rs256_with_kid():
    reset_cache()
    uid = uuid4()
    token = TokenManager.create_access_token(uid, "user@example.com", 0)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256"
    assert header["kid"] == settings.JWT_KEY_ID


def test_jwks_endpoint_structure():
    reset_cache()
    jwks = get_jwks()
    assert "keys" in jwks
    assert len(jwks["keys"]) >= 1
    k = jwks["keys"][0]
    assert k["kty"] == "RSA"
    assert k["kid"] == settings.JWT_KEY_ID
    assert k["alg"] == "RS256"
    assert "n" in k and "e" in k


def test_valid_rs256_verifies():
    reset_cache()
    uid = uuid4()
    token = TokenManager.create_access_token(uid, "user@example.com", 1)
    payload = TokenManager.verify_token(token, token_type="access")
    assert payload is not None
    assert payload["sub"] == str(uid)
    assert payload["type"] == "access"
    assert payload["aud"] == settings.JWT_AUDIENCE
    assert payload["iss"] == settings.JWT_ISSUER


def test_hs256_is_rejected():
    payload = {
        "sub": str(uuid4()),
        "user_id": str(uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
    }
    hs_token = jwt.encode(payload, "hs-secret-1234567890-1234567890", algorithm="HS256")
    result = TokenManager.verify_token(hs_token, token_type="access")
    assert result is None


def test_missing_kid_rejected():
    reset_cache()
    priv_pem = get_private_key_pem()
    payload = {
        "sub": str(uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, priv_pem, algorithm="RS256")
    result = TokenManager.verify_token(token, token_type="access")
    assert result is None


def test_unknown_kid_rejected():
    reset_cache()
    priv_pem = get_private_key_pem()
    payload = {
        "sub": str(uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, priv_pem, algorithm="RS256", headers={"kid": "unknown-kid"})
    result = TokenManager.verify_token(token, token_type="access")
    assert result is None


def test_wrong_audience_rejected():
    reset_cache()
    uid = uuid4()
    now = datetime.now(UTC)
    payload = {
        "sub": str(uid),
        "user_id": str(uid),
        "type": "access",
        "aud": "wrong-audience",
        "iss": settings.JWT_ISSUER,
        "exp": now + timedelta(minutes=5),
        "iat": now,
    }
    priv_pem = get_private_key_pem()
    token = jwt.encode(payload, priv_pem, algorithm="RS256", headers={"kid": settings.JWT_KEY_ID})
    result = TokenManager.verify_token(token, token_type="access")
    assert result is None


def test_wrong_issuer_rejected():
    reset_cache()
    uid = uuid4()
    now = datetime.now(UTC)
    payload = {
        "sub": str(uid),
        "user_id": str(uid),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": "wrong-issuer",
        "exp": now + timedelta(minutes=5),
        "iat": now,
    }
    priv_pem = get_private_key_pem()
    token = jwt.encode(payload, priv_pem, algorithm="RS256", headers={"kid": settings.JWT_KEY_ID})
    result = TokenManager.verify_token(token, token_type="access")
    assert result is None


def test_wrong_type_rejected():
    reset_cache()
    uid = uuid4()
    token = TokenManager.create_refresh_token(uid)
    with pytest.raises(JWTError):
        TokenManager.verify_token(token, token_type="access")


def test_rotation_overlap_verifies_previous_kid(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    reset_cache()
    prev_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    prev_priv_pem = prev_priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    prev_pub = prev_priv.public_key()
    nums = prev_pub.public_numbers()
    prev_jwk = {
        "kty": "RSA",
        "kid": "k0",
        "use": "sig",
        "alg": "RS256",
        "n": _b64url_int(nums.n),
        "e": _b64url_int(nums.e),
    }
    monkeypatch.setattr(settings, "JWT_PREVIOUS_JWKS", json.dumps([prev_jwk]))
    reset_cache()
    uid = uuid4()
    now = datetime.now(UTC)
    payload = {
        "sub": str(uid),
        "user_id": str(uid),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": now + timedelta(minutes=5),
        "iat": now,
    }
    old_token = jwt.encode(payload, prev_priv_pem, algorithm="RS256", headers={"kid": "k0"})
    result = TokenManager.verify_token(old_token, token_type="access")
    assert result is not None
    assert result["sub"] == str(uid)
    monkeypatch.setattr(settings, "JWT_PREVIOUS_JWKS", "")
    reset_cache()
    result2 = TokenManager.verify_token(old_token, token_type="access")
    assert result2 is None
