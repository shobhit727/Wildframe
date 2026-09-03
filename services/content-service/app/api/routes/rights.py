"""Content rights routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.schemas.rights import RightsHolderCreate, TerritorialLicenseCreate

router = APIRouter(prefix="/rights", tags=["rights"])


@router.post("/holders", status_code=201)
async def create_holder(request: RightsHolderCreate, db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    from app.models.rights import RightsHolder
    holder = RightsHolder(name=request.name, type=request.type, contact=request.contact)
    db.add(holder)
    await db.flush()
    await db.commit()
    await db.refresh(holder)
    return {"id": str(holder.id), **request.model_dump()}


@router.post("/licenses", status_code=201)
async def create_license(request: TerritorialLicenseCreate, db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    from app.models.rights import TerritorialLicense
    lic = TerritorialLicense(content_id=request.content_id, rights_holder_id=request.rights_holder_id, territory=request.territory, exclusive=request.exclusive, avail_start=request.avail_start, avail_end=request.avail_end, royalty_rate=request.royalty_rate)
    db.add(lic)
    await db.flush()
    await db.commit()
    await db.refresh(lic)
    return {"id": str(lic.id), **request.model_dump()}
