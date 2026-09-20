import base64
import json

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from app.core.settings import settings

_cached_private_pem: str | None = None
_cached_public_pem: str | None = None
_cached_jwk: dict | None = None


def _b64url_uint(n: int) -> str:
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _load_private_key(pem: str):
    return serialization.load_pem_private_key(pem.encode(), password=None)


def _rsa_public_to_jwk(public_key, kid: str, alg: str = "RS256") -> dict:
    nums = public_key.public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": alg,
        "n": _b64url_uint(nums.n),
        "e": _b64url_uint(nums.e),
    }


def _generate_keypair() -> tuple[str, str]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub_pem = (
        priv.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return priv_pem, pub_pem


def _get_private_pem() -> str:
    global _cached_private_pem, _cached_public_pem
    if _cached_private_pem is not None:
        return _cached_private_pem
    pem = getattr(settings, "JWT_PRIVATE_KEY", None)
    if pem:
        pem = pem.replace("\\n", "\n")
        _cached_private_pem = pem
        try:
            priv = _load_private_key(pem)
            pub = priv.public_key()
            _cached_public_pem = pub.public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode()
        except Exception:
            pass
        return _cached_private_pem
    if settings.ENVIRONMENT in ("", "development", "test"):
        priv_pem, pub_pem = _generate_keypair()
        _cached_private_pem = priv_pem
        _cached_public_pem = pub_pem
        return priv_pem
    raise ValueError("JWT_PRIVATE_KEY must be set in production")


def get_private_key_pem() -> str:
    return _get_private_pem()


def get_public_key_pem() -> str:
    global _cached_public_pem
    if _cached_public_pem is not None:
        return _cached_public_pem
    pem = getattr(settings, "JWT_PUBLIC_KEY", None)
    if pem:
        _cached_public_pem = pem.replace("\\n", "\n")
        return _cached_public_pem
    priv_pem = _get_private_pem()
    priv = _load_private_key(priv_pem)
    pub = priv.public_key()
    pub_pem = pub.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    _cached_public_pem = pub_pem
    return pub_pem


def get_current_jwk() -> dict:
    global _cached_jwk
    if _cached_jwk is not None:
        return _cached_jwk
    pub_pem = get_public_key_pem()
    pub = serialization.load_pem_public_key(pub_pem.encode())
    jwk = _rsa_public_to_jwk(pub, settings.JWT_KEY_ID, settings.JWT_ALGORITHM)
    _cached_jwk = jwk
    return jwk


def get_jwks() -> dict:
    jwks: list[dict] = []
    current = get_current_jwk()
    jwks.append(current)
    raw = getattr(settings, "JWT_PREVIOUS_JWKS", "") or ""
    if raw:
        try:
            prev = json.loads(raw)
            if isinstance(prev, list):
                for item in prev:
                    if isinstance(item, dict) and item.get("kid") != current.get("kid"):
                        jwks.append(item)
            elif isinstance(prev, dict) and "keys" in prev:
                for item in prev["keys"]:
                    if item.get("kid") != current.get("kid"):
                        jwks.append(item)
        except Exception:
            pass
    return {"keys": jwks}


def get_jwk_for_kid(kid: str) -> dict | None:
    jwks = get_jwks()
    for k in jwks.get("keys", []):
        if k.get("kid") == kid:
            return k
    return None


def reset_cache() -> None:
    global _cached_private_pem, _cached_public_pem, _cached_jwk
    _cached_private_pem = None
    _cached_public_pem = None
    _cached_jwk = None
