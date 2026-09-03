"""Payout routes - schedule, multi-currency."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.payout import CreatorPayout
from app.schemas.payout import PayoutCreate

router = APIRouter(prefix="/payouts", tags=["payouts"])


@router.post("", status_code=201)
async def create_payout(
    request: PayoutCreate, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict:
    payout = CreatorPayout(
        creator_id=request.creator_id,
        amount_cents=request.amount_cents,
        currency=request.currency,
        schedule=request.schedule,
    )
    db.add(payout)
    await db.flush()
    await db.commit()
    return {"id": str(payout.id), "status": "pending"}
