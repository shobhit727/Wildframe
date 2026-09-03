from pydantic import BaseModel
class IndiaCreate(BaseModel):
    ott_registered: bool = True
    grievance_officer: str
    tier: str = "tier1"
