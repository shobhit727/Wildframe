"""DRM schemas."""

from uuid import UUID

from pydantic import BaseModel


class DRMCreate(BaseModel):
    content_id: UUID
    fairplay_enabled: bool = True
    widevine_enabled: bool = True
    device_limit: int = 3
    expiry_hours: int = 48
    offline_allowed: bool = False
