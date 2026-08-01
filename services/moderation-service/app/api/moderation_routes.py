"""Moderation service API routes.

All routes are prefixed with ``/moderation`` (see ``main.py``).

Endpoints:
    POST /moderation/flags          — flag content for review
    GET  /moderation/queue          — list pending review items
    POST /moderation/decisions      — make a moderation decision
    GET  /moderation/strikes/{creator_id} — get strike history for a creator
    GET  /health                    — health check
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
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

router = APIRouter(prefix="/moderation", tags=["moderation"])


async def get_moderation_service(
    db: AsyncSession = Depends(get_db),
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
    service: ModerationService = Depends(get_moderation_service),
):
    """Flag a piece of content for moderator review."""
    try:
        flag = await service.flag_content(
            content_id=request.content_id,
            flag_reason=request.flag_reason,
            reporter_id=request.reporter_id,
        )
    except ModerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _flag_to_response(flag)


@router.get("/queue", response_model=QueueResponse)
async def get_queue(
    limit: int = 50,
    service: ModerationService = Depends(get_moderation_service),
):
    """List pending review items, oldest first."""
    flags = await service.get_queue(limit=limit)
    return QueueResponse(
        items=[_flag_to_response(f) for f in flags],
        total=len(flags),
    )


@router.post("/decisions", response_model=DecisionResponse, status_code=201)
async def make_decision(
    request: MakeDecisionRequest,
    service: ModerationService = Depends(get_moderation_service),
):
    """Make a moderation decision (approve / reject / escalate) on a flag."""
    try:
        decision = await service.make_decision(
            flag_id=request.flag_id,
            decision=request.decision,
            moderator_id=request.moderator_id,
            notes=request.notes,
        )
    except ModerationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _decision_to_response(decision)


@router.get("/strikes/{creator_id}", response_model=StrikesResponse)
async def get_strikes(
    creator_id: UUID,
    service: ModerationService = Depends(get_moderation_service),
):
    """Get the full strike history for a creator."""
    strikes = await service.get_strikes(creator_id)
    active_count = sum(1 for s in strikes if s.is_active)
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
