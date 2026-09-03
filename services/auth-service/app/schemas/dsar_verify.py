"""Auth-service DSAR verification schemas - identity proofing."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DSARVerifyRequest(BaseModel):
    """Verify identity for DSAR initiation."""

    user_id: UUID
    email: str = Field(..., max_length=255)
    verification_method: str = Field(..., pattern="^(email_otp|id_document|knowledge)$")
    token: str | None = None  # OTP or document verification token


class DSARVerifyResponse(BaseModel):
    """Verification result."""

    user_id: UUID
    verified: bool
    verified_at: datetime | None
    method: str
    expires_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
