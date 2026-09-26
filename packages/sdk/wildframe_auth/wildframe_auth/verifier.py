import time
import logging
from typing import Any

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

logger = logging.getLogger(__name__)

ALLOWED_ALGORITHMS = {"RS256"}
REQUIRED_CLAIMS = {"exp", "iat", "iss", "aud", "sub", "type"}

_jwks_cache: dict | None = None
_jwks_cache_expiry: float = 0
_jwks_cache_url: str | None = None


def get_jwk_for_kid(jwks: dict, kid: str) -> dict | None:
    keys = jwks.get("keys") or []
    for k in keys:
        if k.get("kid") == kid:
            return k
    return None


def verify_token(
    token: str,
    jwks: dict,
    audience: str,
    issuer: str,
    leeway: int = 60,
    expected_type: str = "access",
) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise JWTError(f"invalid header: {e}") from e
    alg = header.get("alg")
    kid = header.get("kid")
    if alg not in ALLOWED_ALGORITHMS:
        raise JWTError(f"unsupported alg {alg}")
    if not kid:
        raise JWTError("missing kid")
    jwk = get_jwk_for_kid(jwks, kid)
    if jwk is None:
        raise JWTError(f"unknown kid {kid}")
    if jwk.get("alg") and jwk["alg"] not in ALLOWED_ALGORITHMS:
        raise JWTError(f"jwk alg not allowed {jwk['alg']}")
    try:
        payload = jwt.decode(
            token,
            jwk,
            algorithms=list(ALLOWED_ALGORITHMS),
            audience=audience,
            issuer=issuer,
            options={
                "leeway": leeway,
                # Explicitly require every security-critical claim declared by REQUIRED_CLAIMS.
                "require_exp": True,
                "require_iat": True,
                "require_iss": True,
                "require_aud": True,
                "require_sub": True,
                "require_type": True,
            },
        )
    except ExpiredSignatureError:
        raise
    except JWTError:
        raise
    # Enforce exact issuer/audience/type after signature verification; omission never fails open.
    if payload.get("aud") != audience:
        raise JWTError("invalid audience")
    if payload.get("iss") != issuer:
        raise JWTError("invalid issuer")
    if payload.get("type") != expected_type:
        raise JWTError(f"invalid type expected {expected_type}")
    return payload


def load_jwks_from_dict(data: dict) -> dict:
    return data


async def fetch_jwks(url: str, timeout: float = 5.0) -> dict:
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


def fetch_jwks_sync(url: str, timeout: float = 5.0) -> dict:
    import httpx

    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()


async def get_cached_jwks(url: str, ttl: int = 300) -> dict:
    global _jwks_cache, _jwks_cache_expiry, _jwks_cache_url
    now = time.time()
    if _jwks_cache is not None and _jwks_cache_url == url and now < _jwks_cache_expiry:
        return _jwks_cache
    data = await fetch_jwks(url)
    _jwks_cache = data
    _jwks_cache_url = url
    _jwks_cache_expiry = now + ttl
    return data


def clear_jwks_cache() -> None:
    global _jwks_cache, _jwks_cache_expiry, _jwks_cache_url
    _jwks_cache = None
    _jwks_cache_expiry = 0
    _jwks_cache_url = None
