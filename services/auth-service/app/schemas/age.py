"""Auth-service age verification schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgeVerifyRequest(BaseModel):
    user_id: UUID
    declared_age: int = Field(..., ge=0, le=120)
    jurisdiction: str = Field(..., min_length=2, max_length=10)
    verification_method: str = Field(default="self_declare", pattern="^(self_declare|document|id_check)$")
    document_type: str | None = None


class AgeVerifyResponse(BaseModel):
    user_id: UUID
    verified_age: int | None
    is_minor: bool
    jurisdiction: str
    consent_minor_age: int
    verified: bool
    verified_at: datetime | None
    jwt_claim: dict  # age_verified, is_minor, minor_flag

    model_config = ConfigDict(from_attributes=True)
