"""Realistic access-token fixtures for the billing-service test-suite.

The tokens minted here mirror what ``services/auth-service``'s
``TokenManager.create_access_token`` actually issues: RS256 over the shared
test key, a ``kid`` header, and the full claim set — ``sub``, ``user_id``,
``email``, ``role``, ``type``, ``av``, ``arv``, ``iat``, ``exp``, ``iss``,
``aud``, ``jti``.

Fidelity matters because the service under test enforces that claim set:

* ``wildframe_auth.verifier.verify_token`` rejects any access token missing
  ``{exp, iat, iss, aud, sub, type}`` and requires ``av`` to be a real ``int``
  (python-jose does not enforce claim *presence* on its own, so this is the
  only thing standing between a claim-less token and the request).
* ``app.api.billing_routes._enforce_auth_version`` fails closed when the
  token's ``av`` is missing or is not an ``int``.

A hand-rolled token missing those claims is rejected as 401 — correct
behaviour, not a bug to work around. Every HTTP-level test in this service
must therefore present a token minted here (or by ``tests/test_auth_version``
and ``tests/test_jwks_verification``, which follow the same shape).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from jose import jwt

from app.core.settings import settings
from tests._test_jwks import PRIVATE_PEM

#: Access-token lifetime, read from the same setting the real issuer uses so
#: the fixture tracks configuration instead of hardcoding a value.
ACCESS_TOKEN_TTL = timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)

#: ``arv`` (admin role version) as auth-service mints it. Billing never reads
#: this claim; it is present only so the fixture matches the real issuer.
ADMIN_ROLE_VERSION = 0

#: ``av`` for a freshly created account — auth-service's default
#: ``auth_version`` on the user row.
DEFAULT_AUTH_VERSION = 0

_KEY_ID = "k1"
_ALGORITHM = "RS256"


def access_claims(
    user_id: Any,
    role: str = "user",
    *,
    auth_version: Any = DEFAULT_AUTH_VERSION,
    **overrides: Any,
) -> dict[str, Any]:
    """Build the claim set auth-service would issue for ``user_id``.

    ``auth_version`` is stored verbatim rather than coerced to ``int`` so a
    test can deliberately mint a malformed ``av`` (a string, a bool, ``None``)
    to exercise the fail-closed path. The default is a real ``int``, as the
    real issuer always produces.
    """
    now = datetime.now(UTC)
    subject = str(user_id)
    claims: dict[str, Any] = {
        "sub": subject,
        "user_id": subject,
        "email": f"{role}@example.com",
        "role": role,
        "type": "access",
        "av": auth_version,
        "arv": ADMIN_ROLE_VERSION,
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "jti": f"access_{subject}_{now.timestamp()}",
    }
    claims.update(overrides)
    return claims


def mint_access_token(
    user_id: Any,
    role: str = "user",
    *,
    auth_version: int = DEFAULT_AUTH_VERSION,
    **overrides: Any,
) -> str:
    """Sign a realistic RS256 access token for ``user_id``."""
    return jwt.encode(
        access_claims(user_id, role, auth_version=auth_version, **overrides),
        PRIVATE_PEM,
        algorithm=_ALGORITHM,
        headers={"kid": _KEY_ID},
    )


def bearer(
    user_id: Any,
    role: str = "user",
    *,
    auth_version: int = DEFAULT_AUTH_VERSION,
    **overrides: Any,
) -> dict[str, str]:
    """Ready-to-use ``Authorization`` header for ``user_id``."""
    token = mint_access_token(user_id, role, auth_version=auth_version, **overrides)
    return {"Authorization": f"Bearer {token}"}
