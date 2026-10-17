"""Creators service API routes."""

from typing import Annotated
from uuid import UUID

from jose import JWTError
from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from wildframe_auth import JWKSUnavailableError, verify_token_with_jwks

from app.core.database import get_db
from app.core.settings import settings
from app.models import CreatorSuspendedError
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


async def _decode_token(token: str) -> dict:
    """Verify an access token against auth-service's published JWKS.

    Both guards below used to hand-roll their own shared-secret HMAC decode,
    independently of each other. That made the service a shared-secret verifier
    (#941): ``JWT_SECRET_KEY`` is a committed development value and
    ``DEV_ENVIRONMENTS`` exempts it from the production validator, so a forged
    HS256 token with any ``sub`` and ``role: "admin"`` was accepted. The HS256
    path is deleted rather than rotated -- rotating would keep every service on
    one symmetric key and reject genuine RS256 tokens.
    """
    # JWKSUnavailableError must be caught *before* JWTError: it is a JWTError
    # subclass, and it is the branch that keeps a JWKS outage (fetch failure,
    # or a body that is not a JWKS) a 503 instead of a 401. Everything else the
    # verifier rejects -- bad signature, expired, wrong audience, unknown kid --
    # stays a 401.
    try:
        return await verify_token_with_jwks(
            token,
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            url=settings.JWT_JWKS_URL,
            expected_type="access",
        )
    except JWKSUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token verification is unavailable",
        ) from exc
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
        )
    return authorization.removeprefix("Bearer ")


def _subject(payload: dict) -> UUID:
    """Resolve the caller's UUID from a verified access-token payload."""
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


async def current_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UUID:
    """Resolve the authenticated user_id from the verified access-token subject.

    The API gateway validates the access token at the edge; this dependency
    re-verifies the signature against the JWKS so that direct callers (or a
    misconfigured gateway) cannot act as a hard-coded identity. Token-type
    separation (#221) is now enforced by the shared verifier via
    ``expected_type="access"``: a refresh token shares the audience but is not
    accepted here.
    """
    payload = await _decode_token(_bearer_token(authorization))
    return _subject(payload)


async def current_admin(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UUID:
    """Admin-only guard for the /admin/creators routes.

    The api-gateway is a transparent proxy that does not enforce roles, so
    this dependency must re-verify the token signature, the JWT audience, and
    the admin role claim at the service boundary. Plain user tokens and
    unauthenticated callers are rejected (403 / 401) — UI hiding is not a
    security boundary. Signature verification is delegated to
    :func:`_decode_token`; only the role claim is decided here.
    """
    payload = await _decode_token(_bearer_token(authorization))
    if payload.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )
    return _subject(payload)


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
    return [_to_payout_response(r) for r in rows]


def _to_payout_response(r) -> PayoutLedgerResponse:
    return PayoutLedgerResponse(
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


# ------------------------------------------------------------------ payouts
# Accrual is an internal accounting operation: earned_cents, view_minutes and
# stripe_fee_cents come from platform records, never from the creator's own
# client. There is deliberately no /me/payouts self-service write.


@admin_router.post("/{creator_id}/payouts", response_model=PayoutLedgerResponse)
async def admin_accrue_payout(
    creator_id: UUID,
    payload: PayoutAccrualRequest,
    admin_id: Annotated[UUID, Depends(current_admin)],
    service: Annotated[CreatorService, Depends(get_service)],
):
    """Accrue a payout period. Idempotent on (creator, period) — re-posting the
    same period returns the existing ledger row unchanged."""
    acct = await service.acct_repo.get(creator_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="creator not found")
    if not acct.is_active:
        raise HTTPException(status_code=403, detail="creator suspended")
    try:
        row = await service.accrue_payout(
            creator_id=acct.id,
            period_start=payload.period_start,
            period_end=payload.period_end,
            view_minutes=payload.view_minutes,
            earned_cents=payload.earned_cents,
            stripe_fee_cents=payload.stripe_fee_cents,
        )
    except CreatorSuspendedError:
        raise HTTPException(status_code=403, detail="creator suspended")
    return _to_payout_response(row)


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
        raise HTTPException(
            status_code=404,
            detail="tranche not releasable (missing, already released/rolled back, or killed)",
        )
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
