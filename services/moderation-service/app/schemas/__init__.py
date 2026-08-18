"""Moderation service Pydantic request/response schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import DecisionType, FlagReason, FlagStatus, StrikeReason

# ---------------------------------------------------------------------------
# Enums: API wire format mirrors the SQLAlchemy enums in app.models. We
# re-export them under the "...Enum" names the rest of this module uses so
# the schema layer keeps a consistent naming convention without duplicating
# any class (a duplicate between FlagReason and FlagReasonEnum was the source
# of mypy [arg-type] mismatches at the service boundary).
# ---------------------------------------------------------------------------

FlagReasonEnum = FlagReason
FlagStatusEnum = FlagStatus
DecisionTypeEnum = DecisionType
StrikeReasonEnum = StrikeReason

# ---------------------------------------------------------------------------
# Request schemas.
# ---------------------------------------------------------------------------


class FlagContentRequest(BaseModel):
    """Request body for POST /moderation/flags.

    The reporter is the authenticated caller (token ``sub``), never a
    caller-supplied body field.
    """

    content_id: UUID
    content_creator_id: UUID | None = None
    flag_reason: FlagReasonEnum


class MakeDecisionRequest(BaseModel):
    """Request body for POST /moderation/decisions.

    The moderator is the authenticated caller (token ``sub`` + admin role),
    never a caller-supplied body field.
    """

    flag_id: UUID
    decision: DecisionTypeEnum
    notes: str | None = Field(None, max_length=2000)


# ---------------------------------------------------------------------------
# Response schemas.
# ---------------------------------------------------------------------------


class FlagResponse(BaseModel):
    id: UUID
    content_id: UUID
    content_creator_id: UUID | None = None
    flag_reason: FlagReasonEnum
    reported_by: UUID
    status: FlagStatusEnum
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    resolution_notes: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DecisionResponse(BaseModel):
    """Response for a single moderation decision."""

    id: UUID
    flag_id: UUID
    moderator_id: UUID
    decision: DecisionTypeEnum
    notes: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class StrikeResponse(BaseModel):
    """Response for a single creator strike."""

    id: UUID
    creator_id: UUID
    strike_reason: StrikeReasonEnum
    related_flag_id: UUID | None = None
    is_active: bool
    expires_at: datetime | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class QueueResponse(BaseModel):
    """Response for GET /moderation/queue."""

    items: list[FlagResponse]
    total: int


class StrikesResponse(BaseModel):
    """Response for GET /moderation/strikes/{creator_id}."""

    creator_id: UUID
    strikes: list[StrikeResponse]
    active_count: int
