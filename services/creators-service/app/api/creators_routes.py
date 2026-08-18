"""Creators service API routes."""

from typing import Annotated
from uuid import UUID

from jose import JWTError, jwt
from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.settings import settings
from app.repositories import (
    CreatorAccountRepository,
    CreatorPoolBalanceRepository,
    EffectiveFloorRepository,
    MilestoneRepository,
    PayoutLedgerRepository,
)
from app.schemas.creator import (
    CreatorAccountCreate,
    CreatorAccountResponse,
    CreatorAccountUpdate,
    CreatorPoolBalanceResponse,
    EffectiveFloorResponse,
    MilestoneCreate,
    MilestoneResponse,
    MilestoneTrancheResponse,
    PayoutAccrualRequest,
    PayoutLedgerResponse,
    TrancheCreate,
)
from app.services import CreatorService

# /creators
router = APIRouter(prefix="/api/v1/creators", tags=["creators"])
# /admin/creators
admin_router = APIRouter(prefix="/api/v1/admin/creators", tags=["admin-creators"])


async def current_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UUID:
    """Resolve the authenticated user_id from the validated JWT sub claim.

    The API gateway validates the access token at the edge; this verifier
    re-checks the signature so that direct callers (or a misconfigured gateway)
    cannot act as a hard-coded identity. Replace this with a shared verifier
    if the SDK introduces one.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
        )
    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
        )
        # Token-type separation (#221): refresh tokens share the audience but
        # must never be accepted as access tokens.
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
            )
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    sub = payload.get("sub") or payload.get("user_id")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        )
    try:
        return UUID(str(sub))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        )


async def current_admin(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UUID:
    """Admin-only guard for the /admin/creators routes.

    The api-gateway is a transparent proxy that does not enforce roles, so
    this dependency must re-verify the token signature, the JWT audience, and
    the admin role claim at the service boundary. Plain user tokens and
    unauthenticated callers are rejected (403 / 401) — UI hiding is not a
    security boundary.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
        )
    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
        )
        # Token-type separation (#221): refresh tokens share the audience but
        # must never be accepted as access tokens.
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
            )
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if payload.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )
    sub = payload.get("sub") or payload.get("user_id")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        )
    try:
        return UUID(str(sub))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        )


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> CreatorService:
    return CreatorService(
        CreatorAccountRepository(db),
        EffectiveFloorRepository(db),
        CreatorPoolBalanceRepository(db),
        MilestoneRepository(db),
        PayoutLedgerRepository(db),
    )


def _to_ms_response(ms) -> MilestoneResponse:
    return MilestoneResponse(
        id=ms.id,
        title=ms.title,
        creator_id=ms.creator_id,
        status=ms.status,
        total_cents=ms.total_cents,
        currency=ms.currency,
        goal=ms.goal,
        kill_reason=ms.kill_reason,
        created_at=ms.created_at,
        updated_at=ms.updated_at,
    )


def _to_t_response(t) -> MilestoneTrancheResponse:
    return MilestoneTrancheResponse(
        id=t.id,
        milestone_id=t.milestone_id,
        threshold=t.threshold,
        amount_cents=t.amount_cents,
        status=t.status,
        release_condition=t.release_condition,
        released_at=t.released_at,
    )


# ---------------------------------------------------------------- onboarding
@router.post("/onboard", response_model=CreatorAccountResponse)
async def onboard(
    payload: CreatorAccountCreate,
    user_id: Annotated[UUID, Depends(current_user)],
    service: Annotated[CreatorService, Depends(get_service)],
):
    """Onboard a new creator. KYC defaults to pending (verified only after
    identity review — see PRODUCT_VISION §4)."""
    existing = await service.acct_repo.get_by_user(user_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="creator already onboarded")
    acct = await service.acct_repo.create(
        user_id=user_id,
        display_name=payload.display_name,
        bio=payload.bio,
        region_code=payload.region_code,
        currency=payload.currency,
    )
    return CreatorAccountResponse(
        id=acct.id,
        user_id=acct.user_id,
        display_name=acct.display_name,
        bio=acct.bio,
        region_code=acct.region_code,
        currency=acct.currency,
        stripe_connect_account_id=acct.stripe_connect_account_id,
        kyc_status=acct.kyc_status,
        kyc_verified_at=acct.kyc_verified_at,
        is_active=acct.is_active,
        created_at=acct.created_at,
        updated_at=acct.updated_at,
    )


# --------------------------------------------------------------------- me
@router.get("/me", response_model=CreatorAccountResponse)
async def get_me(
    user_id: Annotated[UUID, Depends(current_user)],
    service: Annotated[CreatorService, Depends(get_service)],
):
    acct = await service.get_profile(user_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="creator not found")
    return CreatorAccountResponse(
        id=acct.id,
        user_id=acct.user_id,
        display_name=acct.display_name,
        bio=acct.bio,
        region_code=acct.region_code,
        currency=acct.currency,
        stripe_connect_account_id=acct.stripe_connect_account_id,
        kyc_status=acct.kyc_status,
        kyc_verified_at=acct.kyc_verified_at,
        is_active=acct.is_active,
        created_at=acct.created_at,
        updated_at=acct.updated_at,
    )


@router.put("/me", response_model=CreatorAccountResponse)
async def update_me(
    payload: CreatorAccountUpdate,
    user_id: Annotated[UUID, Depends(current_user)],
    service: Annotated[CreatorService, Depends(get_service)],
):
    acct = await service.update_profile(user_id, **payload.model_dump(exclude_unset=True))
    if acct is None:
        raise HTTPException(status_code=404, detail="creator not found")
    return CreatorAccountResponse(
        id=acct.id,
        user_id=acct.user_id,
        display_name=acct.display_name,
        bio=acct.bio,
        region_code=acct.region_code,
        currency=acct.currency,
        stripe_connect_account_id=acct.stripe_connect_account_id,
        kyc_status=acct.kyc_status,
        kyc_verified_at=acct.kyc_verified_at,
        is_active=acct.is_active,
        created_at=acct.created_at,
        updated_at=acct.updated_at,
    )


# -------------------------------------------------------------------- floor
@router.get("/me/floor", response_model=EffectiveFloorResponse)
async def get_my_floor(
    user_id: Annotated[UUID, Depends(current_user)],
    service: Annotated[CreatorService, Depends(get_service)],
):
    acct = await service.get_profile(user_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="creator not found")
    floor = await service.get_floor(acct.id)
    if floor is None:
        raise HTTPException(status_code=404, detail="no floor configured")
    return EffectiveFloorResponse(
        id=floor.id,
        creator_id=floor.creator_id,
        per_minute_amount=floor.per_minute_amount,
        currency=floor.currency,
        effective_from=floor.effective_from,
        last_adjusted_at=floor.last_adjusted_at,
        reason=floor.reason,
    )


# ------------------------------------------------------------------ balance
@router.get("/me/balance", response_model=CreatorPoolBalanceResponse)
async def get_my_balance(
    user_id: Annotated[UUID, Depends(current_user)],
    service: Annotated[CreatorService, Depends(get_service)],
):
    acct = await service.get_profile(user_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="creator not found")
    bal = await service.pool_repo.get_or_create(acct.id)
    return CreatorPoolBalanceResponse(
        id=bal.id,
        creator_id=bal.creator_id,
        accrued_cents=bal.accrued_cents,
        contributed_cents=bal.contributed_cents,
        last_payout_at=bal.last_payout_at,
    )


# ------------------------------------------------------------------- ledger
@router.get("/me/ledger", response_model=list[PayoutLedgerResponse])
async def get_my_ledger(
    user_id: Annotated[UUID, Depends(current_user)],
    service: Annotated[CreatorService, Depends(get_service)],
):
    acct = await service.get_profile(user_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="creator not found")
    from sqlalchemy import select

    from app.models import PayoutLedger

    stmt = select(PayoutLedger).where(PayoutLedger.creator_id == acct.id)
    result = await service.ledger_repo.session.execute(stmt)
    rows = result.scalars().all()
    return [
        PayoutLedgerResponse(
            id=r.id,
            creator_id=r.creator_id,
            idempotency_key=r.idempotency_key,
            period_start=r.period_start,
            period_end=r.period_end,
            view_minutes=r.view_minutes,
            floor_cents=r.floor_cents,
            pool_topup_cents=r.pool_topup_cents,
            share_cents=r.share_cents,
            stripe_fee_cents=r.stripe_fee_cents,
            net_cents=r.net_cents,
            stripe_transfer_id=r.stripe_transfer_id,
            status=r.status,
            created_at=r.created_at,
        )
        for r in rows
    ]


# ------------------------------------------------------------------ payouts
@router.post("/me/payouts", response_model=PayoutLedgerResponse)
async def accrue_my_payout(
    payload: PayoutAccrualRequest,
    user_id: Annotated[UUID, Depends(current_user)],
    service: Annotated[CreatorService, Depends(get_service)],
):
    """Accrue a payout period. Idempotent on (creator, period) — re-posting the
    same period returns the existing ledger row unchanged."""
    acct = await service.get_profile(user_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="creator not found")
    row = await service.accrue_payout(
        creator_id=acct.id,
        period_start=payload.period_start,
        period_end=payload.period_end,
        view_minutes=payload.view_minutes,
        earned_cents=payload.earned_cents,
        stripe_fee_cents=payload.stripe_fee_cents,
    )
    return PayoutLedgerResponse(
        id=row.id,
        creator_id=row.creator_id,
        idempotency_key=row.idempotency_key,
        period_start=row.period_start,
        period_end=row.period_end,
        view_minutes=row.view_minutes,
        floor_cents=row.floor_cents,
        pool_topup_cents=row.pool_topup_cents,
        share_cents=row.share_cents,
        stripe_fee_cents=row.stripe_fee_cents,
        net_cents=row.net_cents,
        stripe_transfer_id=row.stripe_transfer_id,
        status=row.status,
        created_at=row.created_at,
    )


# -------------------------------------------------------------------- admin
# Admin routes operate on a creator by id. current_admin re-verifies the
# token signature, JWT audience, and admin role claim at this boundary (the
# gateway is a transparent proxy and does not enforce roles).


@admin_router.post("/{creator_id}/milestones", response_model=MilestoneResponse)
async def admin_create_milestone(
    creator_id: UUID,
    payload: MilestoneCreate,
    admin_id: Annotated[UUID, Depends(current_admin)],
    service: Annotated[CreatorService, Depends(get_service)],
):
    acct = await service.acct_repo.get(creator_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="creator not found")
    ms = await service.create_milestone(
        title=payload.title,
        creator_id=creator_id,
        total_cents=payload.total_cents,
        currency=payload.currency,
        goal=payload.goal,
    )
    return _to_ms_response(ms)


@admin_router.post(
    "/{creator_id}/milestones/{mid}/tranches", response_model=MilestoneTrancheResponse
)
async def admin_add_tranche(
    creator_id: UUID,
    mid: UUID,
    payload: TrancheCreate,
    admin_id: Annotated[UUID, Depends(current_admin)],
    service: Annotated[CreatorService, Depends(get_service)],
):
    ms = await service.milestone_repo.get(mid)
    if ms is None or ms.creator_id != creator_id:
        raise HTTPException(status_code=404, detail="milestone not found")
    t = await service.add_tranche(
        mid, payload.threshold, payload.amount_cents, payload.release_condition
    )
    return _to_t_response(t)


@admin_router.post(
    "/{creator_id}/milestones/{mid}/release", response_model=MilestoneTrancheResponse
)
async def admin_release_tranche(
    creator_id: UUID,
    mid: UUID,
    admin_id: Annotated[UUID, Depends(current_admin)],
    threshold: int = Body(...),
    service: CreatorService = Depends(get_service),  # noqa: B008
):
    ms = await service.milestone_repo.get(mid)
    if ms is None or ms.creator_id != creator_id:
        raise HTTPException(status_code=404, detail="milestone not found")
    t = await service.release_tranche(mid, threshold)
    if t is None:
        raise HTTPException(status_code=404, detail="tranche not found")
    return _to_t_response(t)


@admin_router.post("/{creator_id}/milestones/{mid}/kill", response_model=MilestoneResponse)
async def admin_kill_milestone(
    creator_id: UUID,
    mid: UUID,
    admin_id: Annotated[UUID, Depends(current_admin)],
    reason: str | None = Body(None),
    service: CreatorService = Depends(get_service),  # noqa: B008
):
    ms = await service.milestone_repo.get(mid)
    if ms is None or ms.creator_id != creator_id:
        raise HTTPException(status_code=404, detail="milestone not found")
    killed = await service.kill_milestone(mid, reason=reason)
    return _to_ms_response(killed)
