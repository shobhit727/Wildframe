from fastapi import APIRouter
router = APIRouter(prefix="/commerce", tags=["commerce"])
@router.post("", status_code=201)
async def create_commerce(body: dict) -> dict:
    return {"id": "test", "invoice_id": body.get("invoice_id")}
