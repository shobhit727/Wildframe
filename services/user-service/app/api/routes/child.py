"""User-service child account routes - creation and parent linking."""

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.models.child_account import ChildAccount
from app.schemas.child import ChildAccountCreate, ChildAccountResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/child-accounts", tags=["child-accounts"])


@router.post("", response_model=ChildAccountResponse, status_code=status.HTTP_201_CREATED)
async def create_child_account(
    request: ChildAccountCreate,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ChildAccountResponse:
    # Check not already linked
    stmt = select(ChildAccount).where(ChildAccount.child_user_id == request.child_user_id)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Child already linked")
    record = ChildAccount(
        child_user_id=request.child_user_id,
        parent_user_id=request.parent_user_id,
        relationship=request.relationship,
        verification_method=request.verification_method,
        parental_consent_verified=False,
    )
    db.add(record)
    await db.flush()
    await db.commit()
    await db.refresh(record)
    logger.info(
        f"Child account created: child={request.child_user_id} parent={request.parent_user_id}"
    )
    return ChildAccountResponse.model_validate(record)


@router.post("/{child_id}/verify", response_model=ChildAccountResponse)
async def verify_parental_consent(
    child_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ChildAccountResponse:
    stmt = select(ChildAccount).where(ChildAccount.id == child_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Child account not found")
    record.parental_consent_verified = True
    record.verified_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(record)
    return ChildAccountResponse.model_validate(record)


@router.get("/{parent_id}", response_model=list[ChildAccountResponse])
async def list_children(
    parent_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ChildAccountResponse]:
    stmt = select(ChildAccount).where(
        ChildAccount.parent_user_id == parent_id, ChildAccount.is_active.is_(True)
    )
    result = await db.execute(stmt)
    records = result.scalars().all()
    return [ChildAccountResponse.model_validate(r) for r in records]
