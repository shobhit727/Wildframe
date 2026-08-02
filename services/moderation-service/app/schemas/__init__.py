"""Moderation service Pydantic request/response schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums (mirror the SQLAlchemy enums for API wire format).
# ---------------------------------------------------------------------------


class FlagReasonEnum(str, Enum):
    SPAM = "spam"
    INAPPROPRIATE = "inappropriate"
    COPYRIGHT = "copyright"
    OTHER = "other"


class FlagStatusEnum(str, Enum):
    PENDING = "pending"
    REVIEWING = "reviewing"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class DecisionTypeEnum(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"


class StrikeReasonEnum(str, Enum):
    CONTENT_VIOLATION = "content_violation"
    COPYRIGHT = "copyright"
    REPEATED_FLAGS = "repeated_flags"


# ---------------------------------------------------------------------------
# Request schemas.
# ---------------------------------------------------------------------------


class FlagContentRequest(BaseModel):
    """Request body for POST /moderation/flags."""

    content_id: UUID
    flag_reason: FlagReasonEnum
    reporter_id: UUID


class MakeDecisionRequest(BaseModel):
    """Request body for POST /moderation/decisions."""

    flag_id: UUID
    decision: DecisionTypeEnum
    moderator_id: UUID
    notes: str | None = Field(None, max_length=2000)


# ---------------------------------------------------------------------------
# Response schemas.
# ---------------------------------------------------------------------------


class FlagResponse(BaseModel):
    """Response for a single content flag."""

    id: UUID
    content_id: UUID
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
