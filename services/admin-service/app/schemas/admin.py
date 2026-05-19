from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class UserModerationRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    status: str = Field(..., pattern="^(active|suspended|banned)$")
    reason: Optional[str] = None


class UserModerationResponse(BaseModel):
    id: int
    user_id: str
    status: str
    reason: Optional[str]
    moderated_by: str
    moderated_at: datetime
    created_at: datetime
    updated_at: datetime


class ContentModerationRequest(BaseModel):
    content_id: str = Field(..., min_length=1)
    content_type: str = Field(..., pattern="^(movie|show|episode)$")
    status: str = Field(..., pattern="^(active|flagged|removed)$")
    reason: Optional[str] = None


class ContentModerationResponse(BaseModel):
    id: int
    content_id: str
    content_type: str
    status: str
    reason: Optional[str]
    flagged_at: datetime
    resolved_at: Optional[datetime]
    created_at: datetime


class SystemAlertRequest(BaseModel):
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
    acknowledged_by: Optional[str]
    created_at: datetime


class SystemConfigRequest(BaseModel):
    key: str = Field(..., min_length=1)
    value: str = Field(..., min_length=1)
    config_type: str = Field(..., pattern="^(string|integer|boolean|json)$")
    description: Optional[str] = None


class SystemConfigResponse(BaseModel):
    id: int
    key: str
    value: str
    config_type: str
    description: Optional[str]
    updated_by: str
    created_at: datetime
    updated_at: datetime


class AdminAuditLogResponse(BaseModel):
    id: int
    admin_id: str
    action: str
    resource_type: str
    resource_id: str
    changes: Optional[str]
    ip_address: str
    created_at: datetime


class SystemStatsResponse(BaseModel):
    total_users: int
    active_users: int
    suspended_users: int
    flagged_content: int
    active_alerts: int
    system_uptime_hours: float
