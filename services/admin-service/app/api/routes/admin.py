from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.settings import settings
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


async def get_current_admin_id(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.replace("Bearer ", "")
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin privileges required")
        return payload.get("sub") or payload.get("user_id")
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


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
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Moderate a user (suspend/ban/activate)"""
    service = AdminService(db)
    return await service.moderate_user(
        request.user_id, request.status, request.reason, admin_id, "0.0.0.0"
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
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Flag inappropriate content"""
    service = AdminService(db)
    return await service.flag_content(
        request.content_id, request.content_type, request.reason, admin_id, "0.0.0.0"
    )


@router.post("/content/resolve", response_model=ContentModerationResponse)
async def resolve_content_flag(
    content_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    status: Annotated[str, Query(pattern="^(active|removed)$")],
):
    """Resolve flagged content. Returns 404 if the flag does not exist."""
    service = AdminService(db)
    result = await service.resolve_content_flag(content_id, status, admin_id, "0.0.0.0")
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
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create system alert"""
    service = AdminService(db)
    return await service.create_alert(
        request.alert_type, request.severity, request.message, request.service
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
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Acknowledge system alert. Returns 404 if alert does not exist."""
    service = AdminService(db)
    result = await service.acknowledge_alert(alert_id, admin_id)
    if not result:
        raise HTTPException(status_code=404, detail="Not found")
    return result


# System Configuration Endpoints
@router.post("/config", response_model=SystemConfigResponse)
async def set_config(
    request: SystemConfigRequest,
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Set system configuration"""
    service = AdminService(db)
    return await service.set_config(
        request.key, request.value, request.config_type, request.description, admin_id, "0.0.0.0"
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
