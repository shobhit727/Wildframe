"""Privacy schemas for User Service - consent collection and preference center."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConsentRecordCreate(BaseModel):
    """Consent creation request."""

    user_id: UUID
    consent_type: str = Field(..., min_length=1, max_length=100)
    jurisdiction: str = Field(..., min_length=2, max_length=100)
    granted: bool
    version: str = Field(..., min_length=1, max_length=50)
    ip_address: str | None = Field(None, max_length=45)
    user_agent: str | None = None
    consent_metadata: str | None = None


class ConsentRecordResponse(BaseModel):
    """Consent record response."""

    id: UUID
    user_id: UUID
    consent_type: str
    jurisdiction: str
    granted: bool
    granted_at: datetime | None
    withdrawn_at: datetime | None
    withdrawal_reason: str | None
    version: str
    ip_address: str | None
    user_agent: str | None
    consent_metadata: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PreferenceCenterResponse(BaseModel):
    """User-facing preference center."""

    user_id: UUID
    consent_records: list[ConsentRecordResponse]
    available_consent_types: dict[str, str]
