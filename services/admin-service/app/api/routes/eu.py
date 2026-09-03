from fastapi import APIRouter
router = APIRouter(prefix="/eu", tags=["eu"])
@router.post("", status_code=201)
async def create_eu(body: dict) -> dict:
    return {"id": "test", "avms": body.get("avms_enabled"), "dsa": body.get("dsa_enabled"), "compliance": "AVMS+DSA+DMA"}
