from pydantic import BaseModel
from uuid import UUID
class AccessibilityCreate(BaseModel):
    content_id: UUID
    captions_enabled: bool = True
    audio_description: bool = False
    keyboard_nav: bool = True
