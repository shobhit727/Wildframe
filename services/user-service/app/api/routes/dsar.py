"""User-service DSAR routes - Data Subject Rights workflow."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session

# from app.models.dsar import DSARRequest
from app.repositories.dsar import DSARRepository
from app.schemas.dsar import DSARCreateRequest, DSARResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dsar", tags=["dsar"])


async def get_dsar_repo(db: Annotated[AsyncSession, Depends(get_db_session)]) -> DSARRepository:
    return DSARRepository(db)


@router.post("", response_model=DSARResponse, status_code=status.HTTP_201_CREATED)
async def create_dsar(
    request: DSARCreateRequest,
    repo: Annotated[DSARRepository, Depends(get_dsar_repo)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> DSARResponse:
    dsar = await repo.create(
        request.user_id, request.request_type, request.data_categories, request.reason
    )
    await db.commit()
    await db.refresh(dsar)
    return DSARResponse.model_validate(dsar)


@router.get("/{dsar_id}", response_model=DSARResponse)
async def get_dsar(
    dsar_id: UUID,
    repo: Annotated[DSARRepository, Depends(get_dsar_repo)],
) -> DSARResponse:
    dsar = await repo.get_by_id(dsar_id)
    if not dsar:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DSAR not found")
    return DSARResponse.model_validate(dsar)
