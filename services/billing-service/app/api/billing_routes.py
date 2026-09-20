from http import HTTPStatus as http_status

import httpx
from jose import JWTError

from app.core.jwt_verifier import verify_token as verify_jwt_token
from app.core.settings import settings

"""Billing service API routes.

Exposes the Sustenance Engine endpoints:
  - Subscription management (AVOD/SVOD/TVOD)
  - TVOD per-title purchases
  - Living-wage floor lookup
  - Creator Pool status
  - Milestone-tranched funding management
  - Payout ledger history
"""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.money import CurrencyError, validate_currency
from app.core.stripe_client import StripeClient, StripeError
from app.repositories import (
    CreatorPoolRepository,
    InvoiceRepository,
    MilestoneRepository,
    PayoutLedgerRepository,
    PurchaseRepository,
    RegionFloorRepository,
    RefundRepository,
    SubscriptionRepository,
    WebhookEventRepository,
)
from app.services import BillingError, BillingService, MilestoneAuthorizationError, TierInvalidError

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


async def _verify_creator(auth_header: str | None) -> None:
    if not settings.CREATORS_SERVICE_URL:
        return
    if not auth_header:
        raise HTTPException(
            status_code=http_status.UNAUTHORIZED, detail="Missing or invalid authorization header"
        )
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(
                f"{settings.CREATORS_SERVICE_URL}/api/v1/creators/me",
                headers={"Authorization": auth_header},
            )
            if resp.status_code == 200:
                return
            if resp.status_code == 404:
                raise HTTPException(
                    status_code=http_status.FORBIDDEN, detail="Creator profile not found"
                )
            raise HTTPException(
                status_code=http_status.FORBIDDEN, detail="Creator verification failed"
            )
        except httpx.RequestError:
            raise HTTPException(
                status_code=http_status.FORBIDDEN, detail="Creator verification failed"
            )


async def _enforce_auth_version(authorization: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(
                f"{settings.AUTH_SERVICE_URL}/api/v1/auth/me",
                headers={"Authorization": authorization},
            )
    except Exception:
        raise HTTPException(status_code=http_status.UNAUTHORIZED, detail="Invalid or expired token")
    if resp.status_code != 200:
        raise HTTPException(status_code=http_status.UNAUTHORIZED, detail="Invalid or expired token")
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
                raise HTTPException(
                    status_code=http_status.UNAUTHORIZED, detail="Invalid or expired token"
                )
        except (ValueError, TypeError):
            raise HTTPException(status_code=http_status.UNAUTHORIZED, detail="Invalid token")


async def get_current_user_payload(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=http_status.UNAUTHORIZED,
            detail="Missing or invalid authorization header",
        )
    token = authorization.removeprefix("Bearer ")
    try:
        payload = await verify_jwt_token(token, expected_type="access")
    except JWTError:
        raise HTTPException(status_code=http_status.UNAUTHORIZED, detail="Invalid token")
    await _enforce_auth_version(authorization, payload)
    return payload


async def require_admin(
    payload: Annotated[dict, Depends(get_current_user_payload)],
) -> UUID:
    if payload.get("role") != "admin":
        raise HTTPException(status_code=http_status.FORBIDDEN, detail="Admin privileges required")
    sub = str(payload.get("sub") or payload.get("user_id") or "")
    if not sub:
        raise HTTPException(status_code=http_status.UNAUTHORIZED, detail="Invalid token subject")
    try:
        return UUID(sub)
    except ValueError:
        raise HTTPException(status_code=http_status.UNAUTHORIZED, detail="Invalid token subject")


async def get_current_user_id(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UUID:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=http_status.UNAUTHORIZED,
            detail="Missing or invalid authorization header",
        )
    token = authorization.removeprefix("Bearer ")
    try:
        payload = await verify_jwt_token(token, expected_type="access")
    except JWTError:
        raise HTTPException(status_code=http_status.UNAUTHORIZED, detail="Invalid token")
    await _enforce_auth_version(authorization, payload)
    sub = str(payload.get("sub") or payload.get("user_id"))
    if not sub:
        raise HTTPException(status_code=http_status.UNAUTHORIZED, detail="Invalid token subject")
    try:
        return UUID(sub)
    except ValueError:
        raise HTTPException(status_code=http_status.UNAUTHORIZED, detail="Invalid token subject")


async def require_self(
    jwt_user_id: Annotated[UUID, Depends(get_current_user_id)],
    request: Request,
) -> UUID:
    """Ensure the path user_id matches the authenticated user."""
    path_user_id = request.path_params.get("user_id")
    if path_user_id is None or str(path_user_id) == str(jwt_user_id):
        return jwt_user_id
    raise HTTPException(
        status_code=http_status.FORBIDDEN,
        detail="You can only access your own data",
    )


# ---------------------------------------------------------------------------
# DI helpers
# ---------------------------------------------------------------------------


async def get_billing_service(db: Annotated[AsyncSession, Depends(get_db)]) -> BillingService:
    """Wire up BillingService with all its repositories."""
    return BillingService(
        sub_repo=SubscriptionRepository(db),
        purchase_repo=PurchaseRepository(db),
        inv_repo=InvoiceRepository(db),
        floor_repo=RegionFloorRepository(db),
        pool_repo=CreatorPoolRepository(db),
        milestone_repo=MilestoneRepository(db),
        payout_repo=PayoutLedgerRepository(db),
        refund_repo=RefundRepository(db),
        webhook_events_repo=WebhookEventRepository(db),
    )


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class SubscribeRequest(BaseModel):
    tier: str = Field(..., description="Revenue tier: avod, svod, or tvod")


class PurchaseRequest(BaseModel):
    user_id: UUID
    content_id: UUID


class CreateMilestoneRequest(BaseModel):
    creator_id: UUID
    project_title: str
    total_commitment: Decimal = Field(..., gt=0)


class ReleaseTrancheRequest(BaseModel):
    tranche_number: int = Field(..., ge=1, le=4)


class SubscriptionResponse(BaseModel):
    user_id: UUID
    tier: str
    monthly_price: str
    is_active: bool


# ---------------------------------------------------------------------------
# Subscription routes
# ---------------------------------------------------------------------------


@router.get("/subscription/{user_id}", response_model=SubscriptionResponse)
async def get_subscription(
    user_id: Annotated[UUID, Depends(require_self)],
    service: Annotated[BillingService, Depends(get_billing_service)],
):
    """Get a user's current subscription details."""
    sub = await service.get_subscription(user_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return SubscriptionResponse(
        user_id=sub.user_id,
        tier=sub.tier.value,
        monthly_price=str(sub.monthly_price),
        is_active=sub.is_active,
    )


@router.post("/subscribe/{user_id}")
async def subscribe(
    user_id: Annotated[UUID, Depends(require_self)],
    request: SubscribeRequest,
    service: Annotated[BillingService, Depends(get_billing_service)],
):
    """Subscribe or upgrade to a revenue tier (AVOD/SVOD/TVOD)."""
    try:
        sub = await service.subscribe(user_id, request.tier)
    except TierInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "subscribed", "tier": sub.tier.value, "monthly_price": str(sub.monthly_price)}


@router.post("/cancel/{user_id}")
async def cancel_subscription(
    user_id: Annotated[UUID, Depends(require_self)],
    service: Annotated[BillingService, Depends(get_billing_service)],
):
    """Cancel a subscription (reverts to AVOD free tier)."""
    sub = await service.cancel_subscription(user_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"status": "cancelled", "tier": sub.tier.value}


# ---------------------------------------------------------------------------
# TVOD purchases
# ---------------------------------------------------------------------------


@router.post("/purchase")
async def purchase_title(
    request: PurchaseRequest,
    service: Annotated[BillingService, Depends(get_billing_service)],
    current_user: UUID = Depends(get_current_user_id),
):
    if request.user_id != current_user:
        raise HTTPException(
            status_code=http_status.FORBIDDEN,
            detail="You can only purchase content for your own account",
        )
    try:
        price = await service._fetch_content_price(request.content_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        validate_currency(settings.DEFAULT_CURRENCY)
    except CurrencyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        session = StripeClient.create_tvod_purchase_session(
            request.user_id,
            request.content_id,
            price,
            settings.STRIPE_SUCCESS_URL,
            settings.STRIPE_CANCEL_URL,
        )
    except StripeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    sid = getattr(session, "id", None)
    if sid is None and isinstance(session, dict):
        sid = session.get("id")
    url = getattr(session, "url", None)
    if url is None and isinstance(session, dict):
        url = session.get("url")
    return {
        "status": "pending",
        "checkout_session_id": str(sid) if sid else None,
        "checkout_url": url,
        "price": str(price),
        "currency": settings.DEFAULT_CURRENCY,
    }


# ---------------------------------------------------------------------------
# Sustenance Engine — Floor
# ---------------------------------------------------------------------------


@router.get("/floor/{region_code}")
async def get_floor(
    region_code: str,
    service: Annotated[BillingService, Depends(get_billing_service)],
):
    """Get the living-wage floor for a region."""
    floor = await service.get_floor(region_code)
    if not floor:
        raise HTTPException(
            status_code=404, detail=f"No floor configured for region '{region_code}'"
        )
    return {
        "region_code": floor.region_code,
        "currency": floor.currency,
        "floor_low": str(floor.floor_low),
        "floor_high": str(floor.floor_high),
    }


@router.get("/floors")
async def list_floors(
    service: Annotated[BillingService, Depends(get_billing_service)],
):
    """List all regional floor configurations."""
    floors = await service.list_floors()
    return [
        {
            "region_code": f.region_code,
            "currency": f.currency,
            "floor_low": str(f.floor_low),
            "floor_high": str(f.floor_high),
        }
        for f in floors
    ]


# ---------------------------------------------------------------------------
# Sustenance Engine — Creator Pool
# ---------------------------------------------------------------------------


@router.get("/pool")
async def get_pool_status(
    service: Annotated[BillingService, Depends(get_billing_service)],
):
    """Get the current Creator Pool balance and latest cycle info."""
    pool = await service.get_pool_status()
    if not pool:
        return {"status": "no_cycles_yet"}
    return {
        "cycle_start": pool.cycle_start.isoformat(),
        "cycle_end": pool.cycle_end.isoformat(),
        "net_revenue": str(pool.net_revenue),
        "pool_percentage": str(pool.pool_percentage),
        "pool_amount": str(pool.pool_amount),
        "redistributed_amount": str(pool.redistributed_amount),
    }


# ---------------------------------------------------------------------------
# Sustenance Engine — Milestones & Tranches
# ---------------------------------------------------------------------------


@router.post("/milestones")
async def create_milestone(
    request: CreateMilestoneRequest,
    service: Annotated[BillingService, Depends(get_billing_service)],
    payload: Annotated[dict, Depends(get_current_user_payload)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
):
    """Create a milestone commitment with 10/20/30/40 tranched funding."""
    sub = str(payload.get("sub") or payload.get("user_id") or "")
    try:
        current_user = UUID(sub)
    except ValueError:
        raise HTTPException(status_code=http_status.UNAUTHORIZED, detail="Invalid token subject")
    is_admin = payload.get("role") == "admin"
    if request.creator_id != current_user and not is_admin:
        raise HTTPException(
            status_code=http_status.FORBIDDEN,
            detail="You can only create milestones for your own account",
        )
    if not is_admin:
        await _verify_creator(authorization)
    try:
        ms = await service.create_milestone(
            request.creator_id,
            request.project_title,
            request.total_commitment,
            caller_id=current_user,
            caller_is_admin=is_admin,
        )
    except MilestoneAuthorizationError as exc:
        raise HTTPException(status_code=http_status.FORBIDDEN, detail=str(exc)) from exc
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "milestone_id": str(ms.id),
        "status": ms.status.value,
        "total_commitment": str(ms.total_commitment),
        "tranches": "10/20/30/40 (all locked)",
    }


@router.post("/milestones/{milestone_id}/release")
async def release_tranche(
    milestone_id: UUID,
    request: ReleaseTrancheRequest,
    service: Annotated[BillingService, Depends(get_billing_service)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Release a tranche after milestone verification."""
    try:
        tranche = await service.release_tranche(
            milestone_id, request.tranche_number, caller_id=admin_id, caller_is_admin=True
        )
    except MilestoneAuthorizationError as exc:
        raise HTTPException(status_code=http_status.FORBIDDEN, detail=str(exc)) from exc
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "tranche_number": tranche.tranche_number,
        "percentage": str(tranche.percentage),
        "amount": str(tranche.amount),
        "status": tranche.status.value,
    }


@router.post("/milestones/{milestone_id}/kill")
async def kill_milestone(
    milestone_id: UUID,
    service: Annotated[BillingService, Depends(get_billing_service)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Kill a milestone — revert all unreleased tranches to the Creator Pool."""
    try:
        ms = await service.kill_milestone(milestone_id, caller_id=admin_id, caller_is_admin=True)
    except MilestoneAuthorizationError as exc:
        raise HTTPException(status_code=http_status.FORBIDDEN, detail=str(exc)) from exc
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "milestone_id": str(ms.id),
        "status": ms.status.value,
        "message": "All unreleased tranches reverted to Creator Pool.",
    }


# ---------------------------------------------------------------------------
# Payout history
# ---------------------------------------------------------------------------


@router.get("/payouts/{creator_id}")
async def get_payout_history(
    creator_id: UUID,
    service: Annotated[BillingService, Depends(get_billing_service)],
    current_user: UUID = Depends(get_current_user_id),
):
    """Get a creator's full payout ledger history (owner only)."""
    if creator_id != current_user:
        raise HTTPException(
            status_code=http_status.FORBIDDEN, detail="You can only access your own data"
        )
    payouts = await service.get_payout_history(creator_id)
    return [
        {
            "id": str(p.id),
            "amount": str(p.amount),
            "currency": p.currency,
            "status": p.status.value,
            "cycle_start": p.cycle_start.isoformat() if p.cycle_start else None,
            "cycle_end": p.cycle_end.isoformat() if p.cycle_end else None,
        }
        for p in payouts
    ]


# ---------------------------------------------------------------------------
# Creator share (utility endpoint)
# ---------------------------------------------------------------------------


@router.get("/creator-share")
async def calculate_creator_share(
    svod_revenue: Annotated[Decimal, Query(..., description="Total SVOD revenue for the period")],
):
    """Calculate the minimum creator share from SVOD revenue (>=55% floor)."""
    share = BillingService.calculate_creator_share(svod_revenue)
    return {
        "svod_revenue": str(svod_revenue),
        "creator_share_floor": str(share),
        "percentage": "55%",
        "note": "Actual payouts may be higher with Creator Pool top-ups.",
    }
