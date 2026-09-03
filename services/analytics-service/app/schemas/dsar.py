"""Analytics-service DSAR schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AnalyticsExportResponse(BaseModel):
    id: UUID
    user_id: UUID
    dsar_id: UUID
    export_format: str
    retention_days: int
    sla_compliant: bool
    data: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
