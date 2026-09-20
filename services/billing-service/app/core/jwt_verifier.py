import time
from typing import Any

import httpx
from jose import JWTError, jwt

from app.core.settings import settings

_jwks_cache: dict | None = None
_jwks_expiry: float = 0
_jwks_url_cached: str | None = None

ALLOWED_ALG = {"RS256"}


def _get_jwk_for_kid(jwks: dict, kid: str) -> dict | None:
    for k in jwks.get("keys", []):
        if k.get("kid") == kid:
            return k
    return None


def verify_with_jwks(
    token: str,
    jwks: dict,
    audience: str | None = None,
    issuer: str | None = None,
    leeway: int | None = None,
    expected_type: str = "access",
) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise JWTError(f"invalid header: {e}") from e
    alg = header.get("alg")
    kid = header.get("kid")
    if alg not in ALLOWED_ALG:
        raise JWTError(f"unsupported alg {alg}")
    if not kid:
        raise JWTError("missing kid")
    jwk = _get_jwk_for_kid(jwks, kid)
    if jwk is None:
        raise JWTError(f"unknown kid {kid}")
    if jwk.get("alg") and jwk["alg"] not in ALLOWED_ALG:
        raise JWTError("jwk alg not allowed")
    aud = audience or settings.JWT_AUDIENCE
    iss = issuer or settings.JWT_ISSUER
    lev = leeway if leeway is not None else settings.JWT_LEEWAY_SECONDS
    payload = jwt.decode(
        token,
        jwk,
        algorithms=list(ALLOWED_ALG),
        audience=aud,
        issuer=iss,
        options={"leeway": lev},
    )
    if payload.get("type") != expected_type:
        raise JWTError(f"invalid type expected {expected_type}")
    return payload


async def fetch_jwks(url: str | None = None) -> dict:
    target = url or settings.JWT_JWKS_URL
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(target)
        resp.raise_for_status()
        return resp.json()


async def get_cached_jwks(url: str | None = None, ttl: int = 300) -> dict:
    global _jwks_cache, _jwks_expiry, _jwks_url_cached
    target = url or settings.JWT_JWKS_URL
    now = time.time()
    if _jwks_cache is not None and _jwks_url_cached == target and now < _jwks_expiry:
        return _jwks_cache
    data = await fetch_jwks(target)
    _jwks_cache = data
    _jwks_url_cached = target
    _jwks_expiry = now + ttl
    return data


async def verify_token(
    token: str, expected_type: str = "access", jwks_override: dict | None = None
) -> dict[str, Any]:
    if jwks_override is not None:
        return verify_with_jwks(token, jwks_override, expected_type=expected_type)
    jwks = await get_cached_jwks()
    return verify_with_jwks(token, jwks, expected_type=expected_type)


def verify_token_sync(token: str, jwks: dict, expected_type: str = "access") -> dict[str, Any]:
    return verify_with_jwks(token, jwks, expected_type=expected_type)


def clear_cache() -> None:
    global _jwks_cache, _jwks_expiry, _jwks_url_cached
    _jwks_cache = None
    _jwks_expiry = 0
    _jwks_url_cached = None
