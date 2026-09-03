"""Streaming-service maturity schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MaturityCreate(BaseModel):
    content_id: UUID
    maturity_rating: str = Field(..., pattern="^(G|PG|PG-13|R|NC-17|18\\+)$")
    min_age: int = Field(..., ge=0, le=21)
    requires_parental_consent: bool = False
    purchase_restricted: bool = False
    spending_limit_cents: int | None = Field(None, ge=0)
    screen_time_limit_minutes: int | None = Field(None, ge=0)
    bedtime_start: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    bedtime_end: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")


class MaturityResponse(BaseModel):
    id: UUID
    content_id: UUID
    maturity_rating: str
    min_age: int
    requires_parental_consent: bool
    purchase_restricted: bool
    spending_limit_cents: int | None
    screen_time_limit_minutes: int | None
    bedtime_start: str | None
    bedtime_end: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MaturityCheckRequest(BaseModel):
    user_id: UUID
    content_id: UUID
    user_age: int
    parental_consent: bool = False


class MaturityCheckResponse(BaseModel):
    allowed: bool
    reason: str | None
    requires_consent: bool
    min_age: int
