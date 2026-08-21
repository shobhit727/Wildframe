"""Identity verification (JWT) and integrity-protected pagination cursors."""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, Request
from jose import jwt
from wildframe_observability.logging import correlation_id_var

from app.core.settings import settings


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


def verify_token(request: Request) -> Identity | None:
    """Verify a bearer JWT (HS256, exp required). Returns None for anonymous."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
    try:
        scheme, token = auth_header.split(None, 1)
        if scheme.lower() != "bearer":
            return None
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            options={"require": ["exp"]},
        )
        # Token-type separation (#221): refresh tokens are not access tokens.
        if payload.get("type") != "access":
            return None
        raw_user_id = payload.get("user_id") or payload.get("sub")
        if not raw_user_id:
            return None
        return Identity(
            user_id=UUID(str(raw_user_id)),
            role=str(payload.get("role") or "user"),
            arv=int(payload.get("arv") or 0),
        )
    except Exception:  # noqa: BLE001 - invalid/expired/malformed tokens are anonymous
        return None


async def get_optional_identity(request: Request) -> Identity | None:
    """Optional auth dependency for public search endpoints."""
    return verify_token(request)


async def get_required_identity(request: Request) -> Identity:
    """Auth dependency: 401 when no valid bearer token is present."""
    identity = verify_token(request)
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


def encode_cursor(query: str, content_type: str | None, limit: int, sort_values: list) -> str:
    """HMAC-sign the search_after sort values bound to the exact query scope.

    A cursor from one query, user, or result size cannot be replayed against
    another: the scope digest is signed together with the sort values.
    """
    raw = json.dumps(
        {"scope": _scope_hash(query, content_type, limit), "sort": sort_values},
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(settings.JWT_SECRET_KEY.encode(), raw, hashlib.sha256).digest()
    return (
        base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        + "."
        + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    )


def decode_cursor(cursor: str, query: str, content_type: str | None, limit: int) -> list:
    """Verify a cursor's signature and scope; raises ValueError when tampered."""
    try:
        raw_b64, sig_b64 = cursor.rsplit(".", 1)
        raw = base64.urlsafe_b64decode(raw_b64 + "=" * (-len(raw_b64) % 4))
        sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
        expected = hmac.new(settings.JWT_SECRET_KEY.encode(), raw, hashlib.sha256).digest()
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
