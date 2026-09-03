"""User-service child account schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChildAccountCreate(BaseModel):
    child_user_id: UUID
    parent_user_id: UUID
    relationship: str = Field(default="parent", pattern="^(parent|guardian)$")
    verification_method: str = Field(
        default="email_otp", pattern="^(email_otp|document|credit_card)$"
    )


class ChildAccountResponse(BaseModel):
    id: UUID
    child_user_id: UUID
    parent_user_id: UUID
    relationship: str
    parental_consent_verified: bool
    verification_method: str
    verified_at: datetime | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
