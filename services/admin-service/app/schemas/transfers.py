from pydantic import BaseModel
class TransferCreate(BaseModel):
    source_region: str
    target_region: str
    mechanism: str = "SCC"
    adequacy: bool = False
