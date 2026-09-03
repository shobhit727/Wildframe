"""User-service DSAR schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DSARCreateRequest(BaseModel):
    user_id: UUID
    request_type: str = Field(..., pattern="^(access|portability|correction|deletion|restriction|objection|automated_decision)$")
    data_categories: list[str] = Field(default_factory=list)
    reason: str | None = None


class DSARResponse(BaseModel):
    id: UUID
    user_id: UUID
    request_type: str
    status: str
    data_categories: str
    sla_deadline: datetime
    verified_at: datetime | None
    completed_at: datetime | None
    result_location: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
