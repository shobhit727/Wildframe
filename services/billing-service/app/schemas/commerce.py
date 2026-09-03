from pydantic import BaseModel
from uuid import UUID
class CommerceCreate(BaseModel):
    invoice_id: str
    amount_cents: int
    tax_cents: int
    currency: str = "USD"
