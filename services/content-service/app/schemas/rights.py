"""Content rights schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class RightsHolderCreate(BaseModel):
    name: str
    type: str
    contact: str | None = None


class TerritorialLicenseCreate(BaseModel):
    content_id: UUID
    rights_holder_id: UUID
    territory: str
    exclusive: bool = True
    avail_start: datetime
    avail_end: datetime
    royalty_rate: str = "0.30"
