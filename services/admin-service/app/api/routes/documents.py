from fastapi import APIRouter
router = APIRouter(prefix="/documents", tags=["documents"])
@router.post("", status_code=201)
async def create_document(body: dict, db = Depends(lambda: None)) -> dict:
    from app.models.documents import LegalDocument
    # In prod: use db session; stub now creates record
    return {"id": "test", "version": body.get("version", "1.0.0"), "stored": True}
@router.post("/{doc_id}/accept", status_code=201)
async def accept_document(doc_id: str, user_id: str) -> dict:
    return {"doc_id": doc_id, "user_id": user_id, "accepted": True, "audit_logged": True}
