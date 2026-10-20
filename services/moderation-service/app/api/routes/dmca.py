"""DMCA routes - takedown, counter-notice, repeat infringer."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.dmca import DMCATakedown
from app.schemas.dmca import CounterNoticeCreate, TakedownCreate

router = APIRouter(prefix="/dmca", tags=["dmca"])


@router.post("/takedown", status_code=201)
async def create_takedown(
    request: TakedownCreate, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict:
    rec = DMCATakedown(
        content_id=request.content_id,
        reporter_email=request.reporter_email,
        reason=request.reason,
    )
    db.add(rec)
    await db.flush()
    await db.commit()
    return {"id": str(rec.id), "status": "pending"}


@router.post("/counter", status_code=201)
async def counter_notice(
    request: CounterNoticeCreate, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict:
    """Record a counter-notice against an existing takedown.

    Answers 404 (not a 201 with an error body) when the takedown does not
    exist: ``status_code=201`` is the *success* status for this route, so a
    failed lookup returned under it is read by clients and monitoring as a
    created counter-notice.
    """
    from sqlalchemy import select

    stmt = select(DMCATakedown).where(DMCATakedown.id == request.takedown_id)
    result = await db.execute(stmt)
    rec = result.scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Takedown not found")
    rec.counter_notice = request.counter_reason
    rec.status = "countered"
    await db.commit()
    return {"id": str(rec.id), "status": "countered"}
