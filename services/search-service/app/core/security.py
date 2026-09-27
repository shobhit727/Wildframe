"""Identity verification (JWT) and integrity-protected pagination cursors."""

import base64
import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, Request
from jose import JWTError
from wildframe_auth import JWKSUnavailableError, verify_token_with_jwks
from wildframe_observability.logging import correlation_id_var

from app.core.settings import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Identity:
    """Authenticated caller derived from the bearer token, never from query params."""

    user_id: UUID
    role: str = "user"
    arv: int = 0

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def role_current(self) -> bool:
        """True when the token's admin role version matches the live config.

        #81/#101: admin role revocation is immediate — tokens minted before
        ADMIN_ROLE_VERSION was bumped carry an older "arv" and must not
        grant privileged access.
        """
        return self.arv == settings.ADMIN_ROLE_VERSION


async def verify_token(request: Request) -> Identity | None:
    """Verify a bearer JWT (RS256 via the auth-service JWKS). None for anonymous.

    This used to be a *synchronous* shared-secret HMAC decode. Two things
    changed with #941, and both are load-bearing:

    * **It is now async.** The replacement, ``verify_token_with_jwks``, fetches
      the JWKS over the network, so calling it from sync code would mean
      blocking the event loop on I/O -- a denial-of-service surface, since
      every search request passes through here. Both in-module callers
      (``get_optional_identity`` and ``get_required_identity``) were already
      coroutines and now await it, as does every route that uses them.

    * **The shared-secret path is deleted rather than rotated.** The shared HMAC
      secret is a committed development value and ``DEV_ENVIRONMENTS`` exempts
      it from the production validator, so a forged HS256 token carrying any
      ``user_id``/``sub`` -- and therefore any ``role: "admin"`` -- was
      accepted. ``is_admin`` is checked by ``get_admin_identity`` on the
      index-mutating routes, so that was a privilege-escalation bypass, not
      merely an identity spoof.

    Token-type separation (#221) is no longer hand-checked: the shared verifier
    enforces ``expected_type="access"``, and ``exp`` is one of its required
    claims, replacing the old ``options={"require_exp": True}``.

    Return contract is unchanged -- ``None`` means "no usable identity" (no
    header, not a bearer scheme, or a token that is simply not valid) -- with one
    deliberate exception: a JWKS outage raises **503** rather than returning
    ``None``. Silently degrading to anonymous would turn an availability problem
    into an authorization answer, and for ``get_admin_identity`` it would read
    as a 403 against a caller whose token is perfectly fine.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    # JWKSUnavailableError must be caught *before* JWTError: it is a JWTError
    # subclass, and it is the branch that keeps a JWKS outage (fetch failure, or
    # a body that is not a JWKS) a 503 instead of a 401/anonymous. Everything
    # else the verifier rejects -- bad signature, expired, wrong audience,
    # unknown kid -- stays anonymous, as it always was.
    try:
        payload = await verify_token_with_jwks(
            token,
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            url=settings.JWT_JWKS_URL,
            expected_type="access",
        )
    except JWKSUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Token verification is unavailable",
                "correlation_id": correlation_id_var.get(),
            },
        ) from exc
    except JWTError:
        return None
    try:
        # ``user_id`` is still consulted first: that ordering predates this
        # migration and is part of the established identity contract here.
        raw_user_id = payload.get("user_id") or payload.get("sub")
        if not raw_user_id:
            return None
        return Identity(
            user_id=UUID(str(raw_user_id)),
            role=str(payload.get("role") or "user"),
            arv=int(payload.get("arv") or 0),
        )
    except (TypeError, ValueError):
        # A ``sub``/``user_id``/``arv`` that is present but not usable is still
        # just an unusable identity, exactly as before this migration.
        return None


async def get_optional_identity(request: Request) -> Identity | None:
    """Optional auth dependency for public search endpoints."""
    return await verify_token(request)


async def get_required_identity(request: Request) -> Identity:
    """Auth dependency: 401 when no valid bearer token is present."""
    identity = await verify_token(request)
    if identity is None:
        raise HTTPException(
            status_code=401,
            detail={
                "message": "Authentication required",
                "correlation_id": correlation_id_var.get(),
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    return identity


async def get_admin_identity(request: Request) -> Identity:
    """Admin-only dependency for expensive/destructive operations."""
    identity = await get_required_identity(request)
    if not identity.is_admin:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Administrator privileges required",
                "correlation_id": correlation_id_var.get(),
            },
        )
    if not identity.role_current:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Administrator privileges required (role version changed)",
                "correlation_id": correlation_id_var.get(),
            },
        )
    return identity


def _scope_hash(query: str, content_type: str | None, limit: int) -> str:
    return hashlib.sha256(f"{query}|{content_type}|{limit}".encode()).hexdigest()[:16]


# Used only when SEARCH_CURSOR_SECRET is empty. Generated once per process and
# never persisted, so a misconfigured deployment signs cursors with a key nobody
# else -- including an attacker -- can know. See _cursor_secret for why the
# fallback is random rather than the dev default.
_EPHEMERAL_CURSOR_SECRET = secrets.token_bytes(32)
_ephemeral_cursor_secret_warned = False


def _cursor_secret() -> bytes:
    """The HMAC key for pagination cursors, as raw bytes.

    The key is ``SEARCH_CURSOR_SECRET``, not ``JWT_SECRET_KEY``. Token signing
    and cursor integrity are separate concerns with separate lifecycles: they
    used to share one key, which meant (a) cursors were defended by the same
    committed development secret the rest of the platform is served, and
    (b) rotating the token key silently invalidated every in-flight cursor, and
    vice versa.

    **Unset key.** Previously an unset key raised ``RuntimeError`` out of both
    functions, which surfaced as a 500 on a read-only search endpoint -- and the
    operator's remedy was a setting they had no reason to know existed.
    Pagination signing is not a misconfiguration worth failing a search over, so
    the fallback is a per-process random key rather than an error *or* the
    committed dev default:

    * Falling back to the dev default would restore exactly the defect this
      key split fixes. That value is in the repository, so a client could mint
      cursors for any scope -- and re-sign a leaked cursor for a different
      query -- and the protection would be silently, invisibly off.
    * A random key keeps the security property intact and degrades only
      availability: cursors stop verifying across a restart or across
      horizontally-scaled replicas. For search pagination that means the client
      gets an ordinary "invalid cursor" and restarts from page one. A 500 is
      never the outcome, and a forged cursor is never accepted.

    The production validator in ``settings.py`` makes this path unreachable in
    any non-development environment (it rejects a missing, known-default, or
    under-32-character value at startup), so the fallback is confined to
    development and test.
    """
    secret = settings.SEARCH_CURSOR_SECRET
    if secret:
        return secret.encode()
    global _ephemeral_cursor_secret_warned
    if not _ephemeral_cursor_secret_warned:
        _ephemeral_cursor_secret_warned = True
        # Never the value -- only the fact that the fallback is in play, so an
        # operator can tell why cursors do not survive a restart.
        logger.warning(
            "SEARCH_CURSOR_SECRET is not configured; using a per-process random "
            "cursor key. Pagination cursors will not survive a restart or span "
            "replicas. Set SEARCH_CURSOR_SECRET to a strong random value of at "
            "least 32 characters."
        )
    return _EPHEMERAL_CURSOR_SECRET


def encode_cursor(query: str, content_type: str | None, limit: int, sort_values: list) -> str:
    """HMAC-sign the search_after sort values bound to the exact query scope.

    A cursor from one query, user, or result size cannot be replayed against
    another: the scope digest is signed together with the sort values.
    """
    raw = json.dumps(
        {"scope": _scope_hash(query, content_type, limit), "sort": sort_values},
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(_cursor_secret(), raw, hashlib.sha256).digest()
    return (
        base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        + "."
        + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    )


def decode_cursor(cursor: str, query: str, content_type: str | None, limit: int) -> list:
    """Verify a cursor's signature and scope; raises ValueError when tampered.

    The key is resolved *before* the try: an unusable key and an unusable
    cursor are different diagnoses, and only the latter is the client's problem.
    """
    secret = _cursor_secret()
    try:
        raw_b64, sig_b64 = cursor.rsplit(".", 1)
        raw = base64.urlsafe_b64decode(raw_b64 + "=" * (-len(raw_b64) % 4))
        sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
        expected = hmac.new(secret, raw, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, sig):
            raise ValueError("tampered cursor")
        payload = json.loads(raw)
        if payload.get("scope") != _scope_hash(query, content_type, limit):
            raise ValueError("cursor does not match query scope")
        sort_values = payload.get("sort")
        if not isinstance(sort_values, list):
            raise ValueError("invalid cursor payload")
        return sort_values
    except Exception as e:  # noqa: BLE001 - any failure means the cursor is unusable
        raise ValueError("invalid cursor") from e
