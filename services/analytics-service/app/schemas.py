"""Analytics service Pydantic request schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services import MAX_EVENT_DATA_DEPTH


def _nesting_depth(value: Any, depth: int = 0) -> int:
    """Measure the maximum nesting depth of a JSON-ish structure."""
    if isinstance(value, dict):
        return max((_nesting_depth(v, depth + 1) for v in value.values()), default=depth)
    if isinstance(value, list):
        return max((_nesting_depth(v, depth + 1) for v in value), default=depth)
    return depth


class LogEventRequest(BaseModel):
    """Request schema for logging an analytics event."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    event_type: str = Field(min_length=1, max_length=100)
    event_data: dict | None = None
    content_id: UUID | None = None
    client_event_id: str | None = Field(default=None, max_length=200)

    @field_validator("event_data")
    @classmethod
    def _reject_deep_nesting(cls, value: dict | None) -> dict | None:
        if value is not None and _nesting_depth(value) > MAX_EVENT_DATA_DEPTH:
            raise ValueError(
                f"event_data nesting depth exceeds {MAX_EVENT_DATA_DEPTH}"
            )
        return value


class RecordViewEventRequest(BaseModel):
    """Request schema for recording a content view event."""

    model_config = ConfigDict(extra="forbid")

    content_id: UUID
    viewer_id: UUID
    watch_duration_seconds: int = Field(ge=0, le=86400)
    content_duration_seconds: int = Field(ge=0, le=86400)
    completion_pct: float = Field(ge=0, le=100)
    playback_quality: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    client_event_id: str | None = Field(default=None, max_length=200)
