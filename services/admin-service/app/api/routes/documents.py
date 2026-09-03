from fastapi import APIRouter
router = APIRouter(prefix="/documents", tags=["documents"])
@router.post("", status_code=201)
async def create_document(body: dict) -> dict:
    return {"id": "test", "version": body.get("version", "1.0.0")}
@router.post("/{doc_id}/accept", status_code=201)
async def accept_document(doc_id: str, user_id: str) -> dict:
    return {"doc_id": doc_id, "user_id": user_id, "accepted": True}
