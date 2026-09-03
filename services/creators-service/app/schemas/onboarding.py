"""Creators onboarding schemas."""

from uuid import UUID

from pydantic import BaseModel, Field


class OnboardingCreate(BaseModel):
    user_id: UUID
    kyc_type: str = Field(..., pattern="^(individual|entity)$")
    stripe_account_id: str | None = None
    tax_form_type: str | None = Field(None, pattern="^(W-8BEN|W-9|GST)$")


class OnboardingResponse(BaseModel):
    id: UUID
    user_id: UUID
    kyc_status: str
    stripe_account_id: str | None

    class Config:
        from_attributes = True
