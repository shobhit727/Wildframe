"""DMCA routes - takedown, counter-notice, repeat infringer."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.dmca import DMCATakedown
from app.schemas.dmca import CounterNoticeCreate, TakedownCreate

router = APIRouter(prefix="/dmca", tags=["dmca"])


@router.post("/takedown", status_code=201)
async def create_takedown(request: TakedownCreate, db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    rec = DMCATakedown(content_id=request.content_id, reporter_email=request.reporter_email, reason=request.reason)
    db.add(rec)
    await db.flush()
    await db.commit()
    return {"id": str(rec.id), "status": "pending"}


@router.post("/counter", status_code=201)
async def counter_notice(request: CounterNoticeCreate, db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    from sqlalchemy import select

    stmt = select(DMCATakedown).where(DMCATakedown.id == request.takedown_id)
    result = await db.execute(stmt)
    rec = result.scalar_one_or_none()
    if not rec:
        return {"error": "not found"}
    rec.counter_notice = request.counter_reason
    rec.status = "countered"
    await db.commit()
    return {"id": str(rec.id), "status": "countered"}
