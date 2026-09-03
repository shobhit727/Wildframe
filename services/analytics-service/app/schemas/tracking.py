from pydantic import BaseModel
from uuid import UUID


class TrackingCreate(BaseModel):
    user_id: UUID
    cookie_consent: str = "essential"
    sdk_governed: bool = True
    consent_mode: str = "denied"
