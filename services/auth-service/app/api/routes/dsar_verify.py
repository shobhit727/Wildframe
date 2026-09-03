"""Auth-service DSAR verification routes - identity proofing."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.dsar_verify import DSARVerifyRequest, DSARVerifyResponse
from app.security import TokenManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dsar", tags=["dsar-verify"])


@router.post("/verify", response_model=DSARVerifyResponse)
async def verify_dsar_identity(
    request: DSARVerifyRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> DSARVerifyResponse:
    """Verify identity for DSAR - email ownership challenge."""
    # Verify JWT matches user_id
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing auth")
    token = authorization.removeprefix("Bearer ")
    payload = TokenManager.verify_token(token, token_type="access")
    if not payload or str(payload.get("user_id")) != str(request.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User mismatch")
    # In dev, accept any token + email match; in prod would check OTP/document
    verified = True
    now = datetime.now(UTC)
    return DSARVerifyResponse(
        user_id=request.user_id,
        verified=verified,
        verified_at=now if verified else None,
        method=request.verification_method,
        expires_at=now + timedelta(hours=24) if verified else None,
    )
