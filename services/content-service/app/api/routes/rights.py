"""Content rights routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.schemas.rights import RightsHolderCreate, TerritorialLicenseCreate

router = APIRouter(prefix="/rights", tags=["rights"])


@router.post("/holders", status_code=201)
async def create_holder(request: RightsHolderCreate) -> dict:
    return {"id": str(UUID(int=0)), **request.model_dump()}


@router.post("/licenses", status_code=201)
async def create_license(request: TerritorialLicenseCreate) -> dict:
    return {"id": str(UUID(int=0)), **request.model_dump()}
