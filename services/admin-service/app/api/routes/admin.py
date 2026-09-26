import asyncio
from datetime import UTC, datetime

from typing import Annotated

import redis.asyncio as redis
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from wildframe_auth.verifier import get_cached_jwks, verify_token as verify_jwt_token

from app.core.database import get_db
from app.core.settings import DEV_ENVIRONMENTS, settings
from app.schemas.admin import (
    AdminAuditLogResponse,
    ContentModerationRequest,
    ContentModerationResponse,
    SystemAlertRequest,
    SystemAlertResponse,
    SystemConfigRequest,
    SystemConfigResponse,
    SystemStatsResponse,
    UserModerationRequest,
    UserModerationResponse,
)
from app.services.admin import AdminService

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


async def _decode_token(token: str, expected_type: str = "access") -> dict:
    try:
        jwks = await get_cached_jwks(settings.JWT_JWKS_URL)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token verification is unavailable",
        ) from exc
    try:
        return verify_jwt_token(
            token,
            jwks,
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            expected_type=expected_type,
        )
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


async def _enforce_auth_version(authorization: str, payload: dict) -> None:
    token_version = payload.get("av")
    if type(token_version) is not int:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(
                f"{settings.AUTH_SERVICE_URL}/api/v1/auth/me",
                headers={"Authorization": authorization},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service unavailable",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc
    if response.status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service unavailable",
        )
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    try:
        current_version = response.json()["auth_version"]
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token payload") from exc
    if type(current_version) is not int:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    if current_version != token_version:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def get_current_admin_id(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.replace("Bearer ", "")
    payload = await _decode_token(token)
    await _enforce_auth_version(authorization, payload)
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    try:
        role_version = int(payload.get("arv") or 0)
    except (TypeError, ValueError):
        role_version = -1
    if role_version != settings.ADMIN_ROLE_VERSION:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    subject = payload.get("sub") or payload.get("user_id")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(status_code=401, detail="Invalid token subject")
    return subject


_stepup_jti_seen: set[str] = set()
_stepup_jti_lock = asyncio.Lock()


async def _consume_stepup_jti(jti: str, exp: int | float | None) -> bool:
    if not settings.REDIS_URL:
        if settings.ENVIRONMENT not in DEV_ENVIRONMENTS:
            raise RuntimeError("Shared replay protection is not configured")
        async with _stepup_jti_lock:
            if jti in _stepup_jti_seen:
                return True
            _stepup_jti_seen.add(jti)
            return False
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    ttl = 300
    if isinstance(exp, (int, float)):
        ttl = max(1, int(exp - datetime.now(UTC).timestamp()))
    try:
        ok = await client.set(f"stepup:jti:{jti}", "1", nx=True, ex=ttl)
        return ok is None or ok is False
    finally:
        await client.aclose()


async def verify_admin_reauth(
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    x_admin_reauth: Annotated[str | None, Header(alias="X-Admin-Reauth")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> str:
    if not x_admin_reauth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Step-up authentication required: X-Admin-Reauth header missing",
        )
    payload = await _decode_token(x_admin_reauth, expected_type="admin_step_up")
    if authorization:
        access_payload = await _decode_token(authorization.removeprefix("Bearer "))
        if payload.get("av") != access_payload.get("av"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Reauth token has been revoked",
            )
    if payload.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )
    if int(payload.get("arv") or 0) != settings.ADMIN_ROLE_VERSION:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )
    if str(payload.get("sub") or payload.get("user_id")) != admin_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Reauth token admin mismatch"
        )
    if payload.get("scope") != "admin:destructive":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient scope")
    amr = payload.get("amr") or []
    if "pwd" not in amr:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Insufficient authentication strength"
        )
    jti = payload.get("jti")
    if not isinstance(jti, str) or not jti:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Reauth token missing jti"
        )
    try:
        was_used = await _consume_stepup_jti(jti, payload.get("exp"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Step-up replay protection is unavailable",
        ) from exc
    if was_used:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Reauth token already used"
        )
    return admin_id


def _client_ip(request: Request) -> str:
    """Best-effort client address for audit records (never hard-coded).

    X-Forwarded-For is only honored when the service is explicitly deployed
    behind a trusted proxy; otherwise the direct peer address is recorded.
    """
    if settings.TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


# --- Authorization helpers -------------------------------------------------
# Issue #618 / #622: lookup-then-authorize-then-404. Never return 403 for
# resources the caller cannot see — return 404 so existence is not leaked.
AUDIT_RESOURCE_VISIBILITY = frozenset({"system", "alert", "config", "user", "content"})


def _ensure_admin_can_view_target_admin(viewer_id: str, target_admin_id: str) -> None:
    """Admins may only view their own audit log; everyone else 404s."""
    if viewer_id != target_admin_id:
        raise HTTPException(status_code=404, detail="Not found")


def _ensure_admin_can_view_resource(resource_type: str, viewer_id: str) -> None:
    """Non-super admins may only view audit resources visible to all admins."""
    if resource_type not in AUDIT_RESOURCE_VISIBILITY:
        raise HTTPException(status_code=404, detail="Not found")


# User Moderation Endpoints
@router.post("/users/moderate", response_model=UserModerationResponse)
async def moderate_user(
    request: UserModerationRequest,
    request_meta: Request,
    admin_id: Annotated[str, Depends(verify_admin_reauth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Moderate a user (suspend/ban/activate). Step-up reauth required."""
    service = AdminService(db)
    return await service.moderate_user(
        request.user_id, request.status, request.reason, admin_id, _client_ip(request_meta)
    )


@router.get("/users/moderation/{user_id}", response_model=UserModerationResponse)
async def get_user_moderation(
    user_id: str,
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get user moderation history. Returns 404 if no moderation record."""
    service = AdminService(db)
    result = await service.get_user_moderation_history(user_id)
    if not result:
        raise HTTPException(status_code=404, detail="Not found")
    return result


@router.get("/users/moderated", response_model=list[UserModerationResponse])
async def list_moderated_users(
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(le=100)] = 50,
    offset: Annotated[int, Query()] = 0,
):
    """List moderated users"""
    service = AdminService(db)
    return await service.list_moderated_users(status, limit, offset)


# Content Moderation Endpoints
@router.post("/content/flag", response_model=ContentModerationResponse)
async def flag_content(
    request: ContentModerationRequest,
    request_meta: Request,
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Flag inappropriate content"""
    service = AdminService(db)
    return await service.flag_content(
        request.content_id, request.content_type, request.reason, admin_id, _client_ip(request_meta)
    )


@router.post("/content/resolve", response_model=ContentModerationResponse)
async def resolve_content_flag(
    content_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[str, Depends(verify_admin_reauth)],
    request_meta: Request,
    status: Annotated[str, Query(pattern="^(active|removed)$")],
):
    """Resolve flagged content (removed deletes it). Step-up reauth required."""
    service = AdminService(db)
    result = await service.resolve_content_flag(
        content_id, status, admin_id, _client_ip(request_meta)
    )
    if not result:
        raise HTTPException(status_code=404, detail="Not found")
    return result


@router.get("/content/flagged", response_model=list[ContentModerationResponse])
async def list_flagged_content(
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(le=100)] = 50,
    offset: Annotated[int, Query()] = 0,
):
    """List flagged content"""
    service = AdminService(db)
    return await service.list_flagged_content(limit, offset)


# System Alert Endpoints
@router.post("/alerts", response_model=SystemAlertResponse)
async def create_alert(
    request: SystemAlertRequest,
    request_meta: Request,
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create system alert"""
    service = AdminService(db)
    return await service.create_alert(
        request.alert_type,
        request.severity,
        request.message,
        request.service,
        admin_id,
        _client_ip(request_meta),
    )


@router.get("/alerts", response_model=list[SystemAlertResponse])
async def get_alerts(
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(le=100)] = 50,
):
    """Get unacknowledged system alerts"""
    service = AdminService(db)
    return await service.get_system_alerts(limit)


@router.get("/alerts/critical", response_model=list[SystemAlertResponse])
async def get_critical_alerts(
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get critical system alerts"""
    service = AdminService(db)
    return await service.get_critical_alerts()


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: int,
    request_meta: Request,
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Acknowledge system alert. Returns 404 if alert does not exist."""
    service = AdminService(db)
    result = await service.acknowledge_alert(alert_id, admin_id, _client_ip(request_meta))
    if not result:
        raise HTTPException(status_code=404, detail="Not found")
    return result


# System Configuration Endpoints
@router.post("/config", response_model=SystemConfigResponse)
async def set_config(
    request: SystemConfigRequest,
    request_meta: Request,
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Set system configuration"""
    service = AdminService(db)
    return await service.set_config(
        request.key,
        request.value,
        request.config_type,
        request.description,
        admin_id,
        _client_ip(request_meta),
    )


@router.get("/config/{key}", response_model=SystemConfigResponse)
async def get_config(
    key: str,
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get system configuration. Returns 404 if key is unknown."""
    service = AdminService(db)
    result = await service.get_config(key)
    if not result:
        raise HTTPException(status_code=404, detail="Not found")
    return result


@router.get("/config", response_model=list[SystemConfigResponse])
async def list_configs(
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(le=500)] = 100,
):
    """List all system configurations"""
    service = AdminService(db)
    return await service.list_configs(limit)


# Audit Log Endpoints
@router.get("/audit/admin/{admin_id}", response_model=list[AdminAuditLogResponse])
async def get_audit_by_admin(
    admin_id: str,
    viewer_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(le=100)] = 50,
):
    """Get audit logs by admin. Authz: admins see only their own; others 404."""
    _ensure_admin_can_view_target_admin(viewer_id, admin_id)
    service = AdminService(db)
    return await service.get_audit_logs_by_admin(admin_id, limit)


@router.get(
    "/audit/resource/{resource_type}/{resource_id}", response_model=list[AdminAuditLogResponse]
)
async def get_audit_by_resource(
    resource_type: str,
    resource_id: str,
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(le=100)] = 50,
):
    """Get audit logs by resource. Authz: restricted to visible resource types."""
    _ensure_admin_can_view_resource(resource_type, admin_id)
    service = AdminService(db)
    return await service.get_audit_logs_by_resource(resource_type, resource_id, limit)


# System Stats Endpoint
@router.get("/stats", response_model=SystemStatsResponse)
async def get_system_stats(
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get system statistics"""
    service = AdminService(db)
    return await service.get_system_stats()
