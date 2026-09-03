"""Content-service DSAR schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ContentDSARResponse(BaseModel):
    id: UUID
    user_id: UUID
    dsar_id: UUID
    content_type: str
    export_data: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
