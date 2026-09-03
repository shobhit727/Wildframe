from fastapi import APIRouter

router = APIRouter(prefix="/india", tags=["india"])


@router.post("", status_code=201)
async def create_india(body: dict) -> dict:
    return {
        "id": "test",
        "grievance_officer": body.get("grievance_officer"),
        "tier": body.get("tier", "tier1"),
        "ott_compliance": True,
    }
