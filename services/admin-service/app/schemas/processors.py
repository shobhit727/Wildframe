from pydantic import BaseModel


class ProcessorCreate(BaseModel):
    name: str
    dpa_url: str | None = None
    vendor_change_status: str = "pending"
