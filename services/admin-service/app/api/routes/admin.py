from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.admin import (
    UserModerationRequest, UserModerationResponse,
    ContentModerationRequest, ContentModerationResponse,
    SystemAlertRequest, SystemAlertResponse,
    SystemConfigRequest, SystemConfigResponse,
    AdminAuditLogResponse, SystemStatsResponse
)
from app.services.admin import AdminService

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def get_db() -> AsyncSession:
    # Placeholder: dependency injection for DB session
    pass


async def get_current_admin_id() -> str:
    # Placeholder: JWT token extraction for admin context
    return "admin_user_123"


# User Moderation Endpoints
@router.post("/users/moderate", response_model=UserModerationResponse)
async def moderate_user(
    request: UserModerationRequest,
    admin_id: str = Depends(get_current_admin_id),
    db: AsyncSession = Depends(get_db)
):
    """Moderate a user (suspend/ban/activate)"""
    service = AdminService(db)
    return await service.moderate_user(request.user_id, request.status, request.reason, admin_id, "0.0.0.0")


@router.get("/users/moderation/{user_id}", response_model=UserModerationResponse)
async def get_user_moderation(
    user_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get user moderation history"""
    service = AdminService(db)
    result = await service.get_user_moderation_history(user_id)
    if not result:
        raise HTTPException(status_code=404, detail="User moderation not found")
    return result


@router.get("/users/moderated", response_model=list[UserModerationResponse])
async def list_moderated_users(
    status: str = Query(None),
    limit: int = Query(50, le=100),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db)
):
    """List moderated users"""
    service = AdminService(db)
    return await service.list_moderated_users(status, limit, offset)


# Content Moderation Endpoints
@router.post("/content/flag", response_model=ContentModerationResponse)
async def flag_content(
    request: ContentModerationRequest,
    admin_id: str = Depends(get_current_admin_id),
    db: AsyncSession = Depends(get_db)
):
    """Flag inappropriate content"""
    service = AdminService(db)
    return await service.flag_content(request.content_id, request.content_type, request.reason, admin_id, "0.0.0.0")


@router.post("/content/resolve", response_model=ContentModerationResponse)
async def resolve_content_flag(
    content_id: str,
    status: str = Query(..., pattern="^(active|removed)$"),
    admin_id: str = Depends(get_current_admin_id),
    db: AsyncSession = Depends(get_db)
):
    """Resolve flagged content"""
    service = AdminService(db)
    result = await service.resolve_content_flag(content_id, status, admin_id, "0.0.0.0")
    if not result:
        raise HTTPException(status_code=404, detail="Content not found")
    return result


@router.get("/content/flagged", response_model=list[ContentModerationResponse])
async def list_flagged_content(
    limit: int = Query(50, le=100),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db)
):
    """List flagged content"""
    service = AdminService(db)
    return await service.list_flagged_content(limit, offset)


# System Alert Endpoints
@router.post("/alerts", response_model=SystemAlertResponse)
async def create_alert(
    request: SystemAlertRequest,
    db: AsyncSession = Depends(get_db)
):
    """Create system alert"""
    service = AdminService(db)
    return await service.create_alert(request.alert_type, request.severity, request.message, request.service)


@router.get("/alerts", response_model=list[SystemAlertResponse])
async def get_alerts(
    limit: int = Query(50, le=100),
    db: AsyncSession = Depends(get_db)
):
    """Get unacknowledged system alerts"""
    service = AdminService(db)
    return await service.get_system_alerts(limit)


@router.get("/alerts/critical", response_model=list[SystemAlertResponse])
async def get_critical_alerts(db: AsyncSession = Depends(get_db)):
    """Get critical system alerts"""
    service = AdminService(db)
    return await service.get_critical_alerts()


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: int,
    admin_id: str = Depends(get_current_admin_id),
    db: AsyncSession = Depends(get_db)
):
    """Acknowledge system alert"""
    service = AdminService(db)
    result = await service.acknowledge_alert(alert_id, admin_id)
    if not result:
        raise HTTPException(status_code=404, detail="Alert not found")
    return result


# System Configuration Endpoints
@router.post("/config", response_model=SystemConfigResponse)
async def set_config(
    request: SystemConfigRequest,
    admin_id: str = Depends(get_current_admin_id),
    db: AsyncSession = Depends(get_db)
):
    """Set system configuration"""
    service = AdminService(db)
    return await service.set_config(request.key, request.value, request.config_type, request.description, admin_id, "0.0.0.0")


@router.get("/config/{key}", response_model=SystemConfigResponse)
async def get_config(
    key: str,
    db: AsyncSession = Depends(get_db)
):
    """Get system configuration"""
    service = AdminService(db)
    result = await service.get_config(key)
    if not result:
        raise HTTPException(status_code=404, detail="Config not found")
    return result


@router.get("/config", response_model=list[SystemConfigResponse])
async def list_configs(
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db)
):
    """List all system configurations"""
    service = AdminService(db)
    return await service.list_configs(limit)


# Audit Log Endpoints
@router.get("/audit/admin/{admin_id}", response_model=list[AdminAuditLogResponse])
async def get_audit_by_admin(
    admin_id: str,
    limit: int = Query(50, le=100),
    db: AsyncSession = Depends(get_db)
):
    """Get audit logs by admin"""
    service = AdminService(db)
    return await service.get_audit_logs_by_admin(admin_id, limit)


@router.get("/audit/resource/{resource_type}/{resource_id}", response_model=list[AdminAuditLogResponse])
async def get_audit_by_resource(
    resource_type: str,
    resource_id: str,
    limit: int = Query(50, le=100),
    db: AsyncSession = Depends(get_db)
):
    """Get audit logs by resource"""
    service = AdminService(db)
    return await service.get_audit_logs_by_resource(resource_type, resource_id, limit)


# System Stats Endpoint
@router.get("/stats", response_model=SystemStatsResponse)
async def get_system_stats(db: AsyncSession = Depends(get_db)):
    """Get system statistics"""
    service = AdminService(db)
    return await service.get_system_stats()
