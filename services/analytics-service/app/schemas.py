"""Analytics service Pydantic request schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LogEventRequest(BaseModel):
    """Request schema for logging an analytics event."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    event_type: str = Field(min_length=1, max_length=100)
    event_data: dict | None = None
    content_id: UUID | None = None
    client_event_id: str | None = Field(default=None, max_length=200)


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
