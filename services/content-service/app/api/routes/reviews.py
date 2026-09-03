"""Reviews routes - verified viewers only."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.reviews import Review
from app.schemas.reviews import ReviewCreate

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.post("", status_code=201)
async def create_review(
    request: ReviewCreate, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict:
    review = Review(
        content_id=request.content_id,
        user_id=request.user_id,
        rating=request.rating,
        text=request.text,
        verified_viewer=True,
    )
    db.add(review)
    await db.flush()
    await db.commit()
    return {"id": str(review.id), "verified": True}
