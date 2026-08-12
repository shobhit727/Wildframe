from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _StrictRequest(BaseModel):
    """Request base: unknown fields (e.g. caller-supplied actor IDs) are rejected."""

    model_config = ConfigDict(extra="forbid")


class UserModerationRequest(_StrictRequest):
    user_id: str = Field(..., min_length=1)
    status: str = Field(..., pattern="^(active|suspended|banned)$")
    reason: str | None = None


class UserModerationResponse(BaseModel):
    id: int
    user_id: str
    status: str
    reason: str | None
    moderated_by: str
    moderated_at: datetime
    created_at: datetime
    updated_at: datetime


class ContentModerationRequest(_StrictRequest):
    content_id: str = Field(..., min_length=1)
    content_type: str = Field(..., pattern="^(movie|show|episode)$")
    status: str = Field(..., pattern="^(active|flagged|removed)$")
    reason: str | None = None


class ContentModerationResponse(BaseModel):
    id: int
    content_id: str
    content_type: str
    status: str
    reason: str | None
    flagged_at: datetime
    resolved_at: datetime | None
    created_at: datetime


class SystemAlertRequest(_StrictRequest):
    alert_type: str = Field(..., min_length=1)
    severity: str = Field(..., pattern="^(info|warning|critical)$")
    message: str = Field(..., min_length=1)
    service: str = Field(..., min_length=1)


class SystemAlertResponse(BaseModel):
    id: int
    alert_type: str
    severity: str
    message: str
    service: str
    acknowledged: bool
    acknowledged_by: str | None
    created_at: datetime


class SystemConfigRequest(_StrictRequest):
    key: str = Field(..., min_length=1)
    value: str = Field(..., min_length=1)
    config_type: str = Field(..., pattern="^(string|integer|boolean|json)$")
    description: str | None = None


class SystemConfigResponse(BaseModel):
    id: int
    key: str
    value: str
    config_type: str
    description: str | None
    updated_by: str
    created_at: datetime
    updated_at: datetime


class AdminAuditLogResponse(BaseModel):
    id: int
    admin_id: str
    action: str
    resource_type: str
    resource_id: str
    changes: str | None
    ip_address: str
    created_at: datetime


class SystemStatsResponse(BaseModel):
    total_users: int
    active_users: int
    suspended_users: int
    flagged_content: int
    active_alerts: int
    system_uptime_hours: float
