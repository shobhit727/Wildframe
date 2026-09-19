"""Moderation service API routes.

All routes are prefixed with ``/moderation`` (see ``main.py``).

Endpoints:
    POST /moderation/flags          — flag content for review
    GET  /moderation/queue          — list pending review items
    POST /moderation/decisions      — make a moderation decision
    GET  /moderation/strikes/{creator_id} — get strike history for a creator
    GET  /health                    — health check
"""

from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.settings import settings
from app.repositories import (
    ContentFlagRepository,
    CreatorStrikeRepository,
    ModerationDecisionRepository,
)
from app.schemas import (
    DecisionResponse,
    FlagContentRequest,
    FlagResponse,
    MakeDecisionRequest,
    QueueResponse,
    StrikeResponse,
    StrikesResponse,
)
from app.services import ModerationError, ModerationService

router = APIRouter(prefix="/api/v1/moderation", tags=["moderation"])


async def _enforce_auth_version(authorization: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(
                f"{settings.AUTH_SERVICE_URL}/api/v1/auth/me",
                headers={"Authorization": authorization},
            )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    try:
        data = resp.json()
    except Exception:
        return
    current_av = None
    if isinstance(data, dict):
        current_av = data.get("auth_version")
        if current_av is None:
            current_av = data.get("authVersion")
        if current_av is None:
            current_av = data.get("av")
        if current_av is None and "user" in data and isinstance(data["user"], dict):
            current_av = data["user"].get("auth_version")
        if current_av is None and "user" in data and isinstance(data["user"], dict):
            current_av = data["user"].get("av")
    if current_av is not None:
        try:
            if int(payload.get("av", 0)) != int(current_av):
                raise HTTPException(status_code=401, detail="Invalid or expired token")
        except (ValueError, TypeError):
            raise HTTPException(status_code=401, detail="Invalid token")


async def _verify_token(
    authorization: str | None,
    *,
    require_admin: bool,
) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.replace("Bearer ", "")
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    await _enforce_auth_version(authorization, payload)
    if require_admin and payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    if require_admin and int(payload.get("arv") or 0) != settings.ADMIN_ROLE_VERSION:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return str(payload.get("sub") or payload.get("user_id"))


async def get_current_user_id(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> str:
    return await _verify_token(authorization, require_admin=False)


async def get_current_admin_id(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> str:
    return await _verify_token(authorization, require_admin=True)


async def get_moderation_service(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ModerationService:
    """Build a ModerationService wired to the request's DB session."""
    return ModerationService(
        flag_repo=ContentFlagRepository(db),
        decision_repo=ModerationDecisionRepository(db),
        strike_repo=CreatorStrikeRepository(db),
    )


# ---------------------------------------------------------------------------
# Routes.
# ---------------------------------------------------------------------------


@router.post("/flags", response_model=FlagResponse, status_code=201)
async def flag_content(
    request: FlagContentRequest,
    reporter_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ModerationService, Depends(get_moderation_service)],
):
    """Flag a piece of content for moderator review (any authenticated user)."""
    try:
        flag = await service.flag_content(
            content_id=request.content_id,
            content_creator_id=request.content_creator_id,
            flag_reason=request.flag_reason,
            reporter_id=UUID(reporter_id),
        )
    except ModerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _flag_to_response(flag)


@router.get("/queue", response_model=QueueResponse)
async def get_queue(
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    service: Annotated[ModerationService, Depends(get_moderation_service)],
    limit: int = 50,
):
    """List pending review items, oldest first (admin only)."""
    flags = await service.get_queue(limit=limit)
    return QueueResponse(
        items=[_flag_to_response(f) for f in flags],
        total=len(flags),
    )


@router.post("/decisions", response_model=DecisionResponse, status_code=201)
async def make_decision(
    request: MakeDecisionRequest,
    moderator_id: Annotated[str, Depends(get_current_admin_id)],
    service: Annotated[ModerationService, Depends(get_moderation_service)],
):
    """Make a moderation decision (approve / reject / escalate) on a flag.

    Admin only; the moderator identity is the verified token subject, never
    a caller-supplied body field.
    """
    try:
        decision = await service.make_decision(
            flag_id=request.flag_id,
            decision=request.decision,
            moderator_id=UUID(moderator_id),
            notes=request.notes,
        )
    except ModerationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _decision_to_response(decision)


@router.get("/strikes/{creator_id}", response_model=StrikesResponse)
async def get_strikes(
    creator_id: UUID,
    admin_id: Annotated[str, Depends(get_current_admin_id)],
    service: Annotated[ModerationService, Depends(get_moderation_service)],
):
    """Get the full strike history for a creator (admin only)."""
    strikes = await service.get_strikes(creator_id)
    active_count = await service.strike_repo.count_active(creator_id)
    return StrikesResponse(
        creator_id=creator_id,
        strikes=[_strike_to_response(s) for s in strikes],
        active_count=active_count,
    )


@router.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "moderation",
    }


# ---------------------------------------------------------------------------
# Response mappers.
# ---------------------------------------------------------------------------


def _flag_to_response(flag) -> FlagResponse:
    return FlagResponse(
        id=flag.id,
        content_id=flag.content_id,
        content_creator_id=flag.content_creator_id,
        flag_reason=flag.flag_reason,
        reported_by=flag.reported_by,
        status=flag.status,
        reviewed_by=flag.reviewed_by,
        reviewed_at=flag.reviewed_at,
        resolution_notes=flag.resolution_notes,
        created_at=flag.created_at,
        updated_at=flag.updated_at,
    )


def _decision_to_response(decision) -> DecisionResponse:
    return DecisionResponse(
        id=decision.id,
        flag_id=decision.flag_id,
        moderator_id=decision.moderator_id,
        decision=decision.decision,
        notes=decision.notes,
        created_at=decision.created_at,
    )


def _strike_to_response(strike) -> StrikeResponse:
    return StrikeResponse(
        id=strike.id,
        creator_id=strike.creator_id,
        strike_reason=strike.strike_reason,
        related_flag_id=strike.related_flag_id,
        is_active=strike.is_active,
        expires_at=strike.expires_at,
        created_at=strike.created_at,
    )
