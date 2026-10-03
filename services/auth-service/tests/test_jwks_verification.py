import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from jose import JWTError, jwt

from app.core.settings import settings
from app.security import TokenManager
from app.security.jwks import (
    get_jwks,
    get_private_key_pem,
    get_public_key_pem,
    reset_cache,
)


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


# ==========================================================================
# Key sourcing and previous-key publication
# ==========================================================================


def _valid_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def test_configured_private_key_derives_the_public_key(monkeypatch):
    """A configured PEM is parsed once and its public half is cached too."""
    pem = _valid_pem()
    reset_cache()
    monkeypatch.setattr(settings, "JWT_PRIVATE_KEY", pem)
    monkeypatch.setattr(settings, "JWT_PUBLIC_KEY", None)
    try:
        assert get_private_key_pem() == pem
        public = get_public_key_pem()
        assert "BEGIN PUBLIC KEY" in public
        # Cached: a second call does not re-derive.
        assert get_public_key_pem() is public
    finally:
        reset_cache()


def test_escaped_newlines_in_the_configured_key_are_expanded(monkeypatch):
    pem = _valid_pem()
    reset_cache()
    monkeypatch.setattr(settings, "JWT_PRIVATE_KEY", pem.replace("\n", "\\n"))
    monkeypatch.setattr(settings, "JWT_PUBLIC_KEY", None)
    try:
        resolved = get_private_key_pem()
        assert "\\n" not in resolved
        assert resolved == pem
    finally:
        reset_cache()


def test_unparseable_configured_key_still_returns_the_pem(monkeypatch):
    """A bad PEM is returned verbatim; only public-key derivation is skipped."""
    reset_cache()
    monkeypatch.setattr(settings, "JWT_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----\nnope\n")
    monkeypatch.setattr(settings, "JWT_PUBLIC_KEY", None)
    try:
        assert "nope" in get_private_key_pem()
    finally:
        reset_cache()


def test_missing_private_key_outside_development_raises(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "JWT_PRIVATE_KEY", None)
    reset_cache()
    try:
        with pytest.raises(ValueError, match="JWT_PRIVATE_KEY must be set in production"):
            get_private_key_pem()
    finally:
        reset_cache()


def test_missing_private_key_is_generated_in_development(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "JWT_PRIVATE_KEY", None)
    reset_cache()
    try:
        assert "BEGIN PRIVATE KEY" in get_private_key_pem()
    finally:
        reset_cache()


def test_configured_public_key_short_circuits_derivation(monkeypatch):
    public = "-----BEGIN PUBLIC KEY-----\nZmFrZQ==\n-----END PUBLIC KEY-----"
    reset_cache()
    monkeypatch.setattr(settings, "JWT_PUBLIC_KEY", public)
    try:
        assert get_public_key_pem() == public
    finally:
        reset_cache()


def test_previous_jwks_as_a_list_are_published_alongside_the_current_key(monkeypatch):
    reset_cache()
    previous = {
        "kty": "RSA",
        "kid": "retired-k9",
        "use": "sig",
        "alg": "RS256",
        "n": "AQAB",
        "e": "AQAB",
    }
    monkeypatch.setattr(settings, "JWT_PREVIOUS_JWKS", json.dumps([previous]))
    try:
        keys = get_jwks()["keys"]
        assert [k["kid"] for k in keys] == [settings.JWT_KEY_ID, "retired-k9"]
    finally:
        reset_cache()


def test_previous_jwks_as_a_jwks_document_are_published(monkeypatch):
    reset_cache()
    previous = {
        "kty": "RSA",
        "kid": "retired-k9",
        "use": "sig",
        "alg": "RS256",
        "n": "AQAB",
        "e": "AQAB",
    }
    monkeypatch.setattr(settings, "JWT_PREVIOUS_JWKS", json.dumps({"keys": [previous]}))
    try:
        keys = get_jwks()["keys"]
        assert [k["kid"] for k in keys] == [settings.JWT_KEY_ID, "retired-k9"]
    finally:
        reset_cache()


def test_previous_jwks_with_the_current_kid_are_not_duplicated(monkeypatch):
    reset_cache()
    duplicate = {
        "kty": "RSA",
        "kid": settings.JWT_KEY_ID,
        "use": "sig",
        "alg": "RS256",
        "n": "AQAB",
        "e": "AQAB",
    }
    monkeypatch.setattr(settings, "JWT_PREVIOUS_JWKS", json.dumps([duplicate]))
    try:
        assert len(get_jwks()["keys"]) == 1
    finally:
        reset_cache()


def test_malformed_previous_jwks_is_ignored(monkeypatch):
    reset_cache()
    monkeypatch.setattr(settings, "JWT_PREVIOUS_JWKS", "{not json")
    try:
        keys = get_jwks()["keys"]
        assert [k["kid"] for k in keys] == [settings.JWT_KEY_ID]
    finally:
        reset_cache()


def test_empty_previous_jwks_is_ignored(monkeypatch):
    reset_cache()
    monkeypatch.setattr(settings, "JWT_PREVIOUS_JWKS", "")
    try:
        assert len(get_jwks()["keys"]) == 1
    finally:
        reset_cache()


def test_non_dict_entries_in_previous_jwks_are_skipped(monkeypatch):
    reset_cache()
    monkeypatch.setattr(settings, "JWT_PREVIOUS_JWKS", json.dumps(["not-a-jwk"]))
    try:
        assert len(get_jwks()["keys"]) == 1
    finally:
        reset_cache()


def test_get_jwk_for_kid_resolves_the_current_and_an_unknown_kid(monkeypatch):
    from app.security.jwks import get_jwk_for_kid

    reset_cache()
    monkeypatch.setattr(settings, "JWT_PREVIOUS_JWKS", "")
    try:
        assert get_jwk_for_kid(settings.JWT_KEY_ID)["kid"] == settings.JWT_KEY_ID
        assert get_jwk_for_kid("nope") is None
    finally:
        reset_cache()


def test_current_jwk_is_cached_across_calls(monkeypatch):
    from app.security.jwks import get_current_jwk

    reset_cache()
    monkeypatch.setattr(settings, "JWT_PREVIOUS_JWKS", "")
    try:
        assert get_current_jwk() is get_current_jwk()
    finally:
        reset_cache()


def test_reset_cache_forces_regeneration(monkeypatch):
    monkeypatch.setattr(settings, "JWT_PRIVATE_KEY", None)
    monkeypatch.setattr(settings, "JWT_PUBLIC_KEY", None)
    reset_cache()
    first = get_private_key_pem()
    reset_cache()
    second = get_private_key_pem()

    assert first != second


def test_private_pem_cache_short_circuits_the_settings_read(monkeypatch):
    reset_cache()
    monkeypatch.setattr(settings, "JWT_PRIVATE_KEY", _valid_pem())
    try:
        first = get_private_key_pem()
        # Removing the setting proves the cached value is used.
        monkeypatch.setattr(settings, "JWT_PRIVATE_KEY", None)
        assert get_private_key_pem() is first
    finally:
        reset_cache()


def test_public_pem_cache_short_circuits_derivation(monkeypatch):
    reset_cache()
    monkeypatch.setattr(settings, "JWT_PRIVATE_KEY", _valid_pem())
    monkeypatch.setattr(settings, "JWT_PUBLIC_KEY", None)
    try:
        first = get_public_key_pem()
        monkeypatch.setattr(settings, "JWT_PRIVATE_KEY", "garbage")
        assert get_public_key_pem() is first
    finally:
        reset_cache()


def test_b64url_uint_strips_padding():
    from app.security.jwks import _b64url_uint

    assert not _b64url_uint(65537).endswith("=")
    assert _b64url_uint(65537) == "AQAB"


def test_rsa_public_to_jwk_shape():
    from app.security.jwks import _rsa_public_to_jwk, _load_private_key

    jwk = _rsa_public_to_jwk(_load_private_key(_valid_pem()).public_key(), "kid-1")

    assert jwk["kty"] == "RSA"
    assert jwk["kid"] == "kid-1"
    assert jwk["use"] == "sig"
    assert jwk["alg"] == "RS256"
    assert "=" not in jwk["n"]
    assert jwk["e"] == "AQAB"
