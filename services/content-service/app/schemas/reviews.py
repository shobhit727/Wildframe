"""Reviews schemas."""

from uuid import UUID

from pydantic import BaseModel, Field


class ReviewCreate(BaseModel):
    content_id: UUID
    user_id: UUID
    rating: int = Field(..., ge=1, le=5)
    text: str
