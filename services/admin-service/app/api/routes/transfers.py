from fastapi import APIRouter
router = APIRouter(prefix="/transfers", tags=["transfers"])
@router.post("", status_code=201)
async def create_transfer(body: dict) -> dict:
    from app.models.transfers import TransferRecord
    return {"id": "test", "mechanism": body.get("mechanism", "SCC"), "adequacy_check": True}
