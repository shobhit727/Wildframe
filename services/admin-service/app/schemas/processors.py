from pydantic import BaseModel
from uuid import UUID
class ProcessorCreate(BaseModel):
    name: str
    dpa_url: str | None = None
    vendor_change_status: str = "pending"
