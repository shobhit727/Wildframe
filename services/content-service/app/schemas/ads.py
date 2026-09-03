"""Ads schemas."""
from uuid import UUID
from pydantic import BaseModel
class AdCreate(BaseModel):
    content_id: UUID
    consent_gated: bool = True
    minor_safe: bool = True
    tcf_required: bool = True
