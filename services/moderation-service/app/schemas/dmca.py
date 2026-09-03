"""DMCA schemas."""

from uuid import UUID

from pydantic import BaseModel, Field


class TakedownCreate(BaseModel):
    content_id: UUID
    reporter_email: str
    reason: str


class CounterNoticeCreate(BaseModel):
    takedown_id: UUID
    counter_reason: str
