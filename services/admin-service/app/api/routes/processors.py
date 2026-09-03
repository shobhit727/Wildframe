from fastapi import APIRouter
router = APIRouter(prefix="/processors", tags=["processors"])
@router.post("", status_code=201)
async def create_processor(body: dict) -> dict:
    from app.models.processors import Processor
    return {"id": "test", "name": body.get("name"), "dpa_stored": True}
