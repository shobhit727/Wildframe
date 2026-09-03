from fastapi import APIRouter
router = APIRouter(prefix="/tracking", tags=["tracking"])
@router.post("", status_code=201)
async def create_tracking(body: dict) -> dict:
    return {"id": "test", "consent_mode": body.get("consent_mode", "denied"), "sdk_governed": True, "retention": 365}
