"""Remaining billing tests."""


def test_billing_remaining1():
    from app.models.commerce import CommerceRecord

    assert CommerceRecord is not None


def test_billing_remaining2():
    from app.models.subscription_tier import SubscriptionTier

    assert SubscriptionTier is not None


def test_billing_remaining3():
    from app.models.payout_ledger import PayoutLedger

    assert PayoutLedger is not None


def test_billing_remaining4():
    from app.models.audit import BillingAudit

    assert BillingAudit is not None


def test_billing_remaining5():
    from app.schemas.commerce import CommerceCreate

    assert CommerceCreate is not None


def test_billing_remaining6():
    from app.schemas.billing import SubscriptionTierCreate

    assert SubscriptionTierCreate is not None


def test_billing_remaining7():
    from app.api.routes.billing_tiers import router

    assert router is not None


# ===========================================================================
# Route-body coverage for app/api/billing_routes.py
#
# Every route is exercised through the real ASGI app with the billing service
# dependency replaced by a mock, so the request-validation, authorization and
# error-translation logic in the route bodies is what is under test.
# ===========================================================================

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from tests._auth_tokens import mint_access_token

from app.core.settings import settings
from app.core.stripe_client import StripeError
from app.main import create_app
from app.models import (
    MilestoneStatus,
    PayoutStatus,
    RevenueTier,
    TrancheStatus,
)
from app.services import BillingError, MilestoneAuthorizationError, TierInvalidError


def _token(user_id, role: str = "user", **overrides) -> str:
    """A realistic auth-service access token (full claim set, RS256, `av`)."""
    return mint_access_token(user_id, role, **overrides)


def _auth(user_id, role: str = "user", **overrides) -> dict:
    return {"Authorization": f"Bearer {_token(user_id, role, **overrides)}"}


def _app(svc) -> "object":
    """The real app with only the billing service replaced."""
    from app.api import billing_routes

    app = create_app()

    async def _override():
        return svc

    app.dependency_overrides[billing_routes.get_billing_service] = _override
    return app


def _svc(**attrs):
    """A BillingService double: every method is an AsyncMock by default."""
    svc = MagicMock()
    for name in (
        "get_subscription",
        "subscribe",
        "cancel_subscription",
        "_fetch_content_price",
        "get_floor",
        "list_floors",
        "get_pool_status",
        "create_milestone",
        "release_tranche",
        "kill_milestone",
        "get_payout_history",
    ):
        setattr(svc, name, AsyncMock())
    for key, value in attrs.items():
        setattr(svc, key, value)
    return svc


async def _call(app, method: str, path: str, **kwargs):
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as ac:
        return await ac.request(method, path, **kwargs)


def _sub(**overrides):
    defaults = {
        "user_id": uuid4(),
        "tier": RevenueTier.SVOD,
        "monthly_price": Decimal("7.99"),
        "is_active": True,
    }
    sub = SimpleNamespace(**defaults)
    for key, value in overrides.items():
        setattr(sub, key, value)
    return sub


# ---------------------------------------------------------------------------
# get_billing_service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_billing_service_wires_nine_repositories_over_one_session():
    from app.api.billing_routes import get_billing_service

    db = AsyncMock()
    svc = await get_billing_service(db)
    repos = [
        svc.sub_repo,
        svc.purchase_repo,
        svc.inv_repo,
        svc.floor_repo,
        svc.pool_repo,
        svc.milestone_repo,
        svc.payout_repo,
        svc.refund_repo,
        svc.webhook_events_repo,
    ]
    assert len(repos) == 9
    # One session for the whole request: a partial commit must be impossible.
    assert all(repo.session is db for repo in repos)


# ---------------------------------------------------------------------------
# GET /subscription/{user_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_subscription_returns_the_current_tier():
    user = uuid4()
    sub = _sub(user_id=user)
    app = _app(_svc(get_subscription=AsyncMock(return_value=sub)))
    resp = await _call(app, "GET", f"/api/v1/billing/subscription/{user}", headers=_auth(user))
    assert resp.status_code == 200
    assert resp.json() == {
        "user_id": str(user),
        "tier": "svod",
        "monthly_price": "7.99",
        "is_active": True,
    }


@pytest.mark.asyncio
async def test_get_subscription_404s_when_there_is_no_subscription():
    user = uuid4()
    app = _app(_svc(get_subscription=AsyncMock(return_value=None)))
    resp = await _call(app, "GET", f"/api/v1/billing/subscription/{user}", headers=_auth(user))
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Subscription not found"


@pytest.mark.asyncio
async def test_get_subscription_blocks_another_users_subscription():
    user, other = uuid4(), uuid4()
    app = _app(_svc(get_subscription=AsyncMock(return_value=_sub())))
    resp = await _call(app, "GET", f"/api/v1/billing/subscription/{other}", headers=_auth(user))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "You can only access your own data"


@pytest.mark.asyncio
async def test_get_subscription_requires_authentication():
    user = uuid4()
    app = _app(_svc())
    resp = await _call(app, "GET", f"/api/v1/billing/subscription/{user}")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /subscribe/{user_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_activates_the_requested_tier():
    user = uuid4()
    svc = _svc(subscribe=AsyncMock(return_value=_sub(user_id=user, tier=RevenueTier.SVOD)))
    app = _app(svc)
    resp = await _call(
        app, "POST", f"/api/v1/billing/subscribe/{user}", headers=_auth(user), json={"tier": "svod"}
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "subscribed",
        "tier": "svod",
        "monthly_price": "7.99",
    }
    svc.subscribe.assert_awaited_once_with(user, "svod")


@pytest.mark.asyncio
async def test_subscribe_translates_an_invalid_tier_to_400():
    user = uuid4()
    svc = _svc(subscribe=AsyncMock(side_effect=TierInvalidError("Invalid tier 'platinum'")))
    app = _app(svc)
    resp = await _call(
        app, "POST", f"/api/v1/billing/subscribe/{user}", headers=_auth(user), json={"tier": "platinum"}
    )
    assert resp.status_code == 400
    assert "platinum" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_subscribe_rejects_a_missing_tier_field():
    user = uuid4()
    app = _app(_svc())
    resp = await _call(app, "POST", f"/api/v1/billing/subscribe/{user}", headers=_auth(user), json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_subscribe_blocks_another_user():
    user, other = uuid4(), uuid4()
    app = _app(_svc())
    resp = await _call(
        app, "POST", f"/api/v1/billing/subscribe/{other}", headers=_auth(user), json={"tier": "svod"}
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /cancel/{user_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_reverts_to_avod():
    user = uuid4()
    svc = _svc(
        cancel_subscription=AsyncMock(return_value=_sub(user_id=user, tier=RevenueTier.AVOD))
    )
    app = _app(svc)
    resp = await _call(app, "POST", f"/api/v1/billing/cancel/{user}", headers=_auth(user))
    assert resp.status_code == 200
    assert resp.json() == {"status": "cancelled", "tier": "avod"}


@pytest.mark.asyncio
async def test_cancel_404s_when_there_is_nothing_to_cancel():
    user = uuid4()
    app = _app(_svc(cancel_subscription=AsyncMock(return_value=None)))
    resp = await _call(app, "POST", f"/api/v1/billing/cancel/{user}", headers=_auth(user))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_blocks_another_user():
    user, other = uuid4(), uuid4()
    app = _app(_svc())
    resp = await _call(app, "POST", f"/api/v1/billing/cancel/{other}", headers=_auth(user))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /purchase
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_returns_the_checkout_session_details():
    user, content = uuid4(), uuid4()
    session = MagicMock(id="cs_1", url="https://checkout.stripe.com/pay/cs_1")
    svc = _svc(_fetch_content_price=AsyncMock(return_value=Decimal("4.99")))
    app = _app(svc)
    with patch(
        "app.api.billing_routes.StripeClient.create_tvod_purchase_session", return_value=session
    ) as create:
        resp = await _call(
            app,
            "POST",
            "/api/v1/billing/purchase",
            headers=_auth(user),
            json={"user_id": str(user), "content_id": str(content)},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["checkout_session_id"] == "cs_1"
    assert body["checkout_url"] == "https://checkout.stripe.com/pay/cs_1"
    assert body["price"] == "4.99"
    assert body["currency"] == settings.DEFAULT_CURRENCY
    assert create.call_args.args[2] == Decimal("4.99")


@pytest.mark.asyncio
async def test_purchase_blocks_buying_for_another_account():
    user, content, other = uuid4(), uuid4(), uuid4()
    app = _app(_svc())
    resp = await _call(
        app,
        "POST",
        "/api/v1/billing/purchase",
        headers=_auth(user),
        json={"user_id": str(other), "content_id": str(content)},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "You can only purchase content for your own account"


@pytest.mark.asyncio
async def test_purchase_400s_when_the_content_service_cannot_price_the_title():
    user, content = uuid4(), uuid4()
    svc = _svc(
        _fetch_content_price=AsyncMock(side_effect=ValueError("Content not found"))
    )
    app = _app(svc)
    resp = await _call(
        app,
        "POST",
        "/api/v1/billing/purchase",
        headers=_auth(user),
        json={"user_id": str(user), "content_id": str(content)},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Content not found"


@pytest.mark.asyncio
async def test_purchase_400s_on_an_unlisted_configured_currency():
    user, content = uuid4(), uuid4()
    svc = _svc(_fetch_content_price=AsyncMock(return_value=Decimal("1.00")))
    app = _app(svc)
    with patch.object(settings, "DEFAULT_CURRENCY", "ZZZ"):
        resp = await _call(
            app,
            "POST",
            "/api/v1/billing/purchase",
            headers=_auth(user),
            json={"user_id": str(user), "content_id": str(content)},
        )
    assert resp.status_code == 400
    assert "Unsupported currency code" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_purchase_502s_when_stripe_fails():
    user, content = uuid4(), uuid4()
    svc = _svc(_fetch_content_price=AsyncMock(return_value=Decimal("1.00")))
    app = _app(svc)
    with patch(
        "app.api.billing_routes.StripeClient.create_tvod_purchase_session",
        side_effect=StripeError("Failed to create TVOD purchase session: boom"),
    ):
        resp = await _call(
            app,
            "POST",
            "/api/v1/billing/purchase",
            headers=_auth(user),
            json={"user_id": str(user), "content_id": str(content)},
        )
    # 502, not 500: the fault is upstream in Stripe, not in the service.
    assert resp.status_code == 502
    assert "boom" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_purchase_reads_a_plain_dict_session():
    user, content = uuid4(), uuid4()
    svc = _svc(_fetch_content_price=AsyncMock(return_value=Decimal("1.00")))
    app = _app(svc)
    with patch(
        "app.api.billing_routes.StripeClient.create_tvod_purchase_session",
        return_value={"id": "cs_dict", "url": "https://pay/cs_dict"},
    ):
        resp = await _call(
            app,
            "POST",
            "/api/v1/billing/purchase",
            headers=_auth(user),
            json={"user_id": str(user), "content_id": str(content)},
        )
    assert resp.json()["checkout_session_id"] == "cs_dict"
    assert resp.json()["checkout_url"] == "https://pay/cs_dict"


@pytest.mark.asyncio
async def test_purchase_tolerates_a_session_without_a_url():
    user, content = uuid4(), uuid4()
    svc = _svc(_fetch_content_price=AsyncMock(return_value=Decimal("1.00")))
    app = _app(svc)
    with patch(
        "app.api.billing_routes.StripeClient.create_tvod_purchase_session",
        return_value=MagicMock(id="cs_nourl", url=None),
    ):
        resp = await _call(
            app,
            "POST",
            "/api/v1/billing/purchase",
            headers=_auth(user),
            json={"user_id": str(user), "content_id": str(content)},
        )
    body = resp.json()
    assert body["checkout_session_id"] == "cs_nourl"
    assert body["checkout_url"] is None


@pytest.mark.asyncio
async def test_purchase_handles_a_dict_session_with_no_url():
    user, content = uuid4(), uuid4()
    svc = _svc(_fetch_content_price=AsyncMock(return_value=Decimal("1.00")))
    app = _app(svc)
    with patch(
        "app.api.billing_routes.StripeClient.create_tvod_purchase_session",
        return_value={"id": "cs_2"},
    ):
        resp = await _call(
            app,
            "POST",
            "/api/v1/billing/purchase",
            headers=_auth(user),
            json={"user_id": str(user), "content_id": str(content)},
        )
    assert resp.json()["checkout_url"] is None


@pytest.mark.asyncio
async def test_purchase_tolerates_a_session_with_neither_id_nor_url():
    user, content = uuid4(), uuid4()
    svc = _svc(_fetch_content_price=AsyncMock(return_value=Decimal("1.00")))
    app = _app(svc)
    with patch(
        "app.api.billing_routes.StripeClient.create_tvod_purchase_session",
        return_value=MagicMock(id=None, url=None),
    ):
        resp = await _call(
            app,
            "POST",
            "/api/v1/billing/purchase",
            headers=_auth(user),
            json={"user_id": str(user), "content_id": str(content)},
        )
    assert resp.json()["checkout_session_id"] is None


@pytest.mark.asyncio
async def test_purchase_rejects_a_malformed_uuid_in_the_body():
    user = uuid4()
    app = _app(_svc())
    resp = await _call(
        app,
        "POST",
        "/api/v1/billing/purchase",
        headers=_auth(user),
        json={"user_id": str(user), "content_id": "not-a-uuid"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /floor/{region_code} and GET /floors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_floor_returns_the_configured_band():
    svc = _svc(
        get_floor=AsyncMock(
            return_value=SimpleNamespace(
                region_code="EU",
                currency="EUR",
                floor_low=Decimal("0.10"),
                floor_high=Decimal("0.20"),
            )
        )
    )
    app = _app(svc)
    resp = await _call(app, "GET", "/api/v1/billing/floor/EU", headers=_auth(uuid4()))
    assert resp.status_code == 200
    assert resp.json() == {
        "region_code": "EU",
        "currency": "EUR",
        "floor_low": "0.10",
        "floor_high": "0.20",
    }
    svc.get_floor.assert_awaited_once_with("EU")


@pytest.mark.asyncio
async def test_get_floor_404s_for_an_unconfigured_region():
    svc = _svc(get_floor=AsyncMock(return_value=None))
    app = _app(svc)
    resp = await _call(app, "GET", "/api/v1/billing/floor/ZZ", headers=_auth(uuid4()))
    assert resp.status_code == 404
    assert "ZZ" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_floors_returns_every_configured_region():
    floors = [
        SimpleNamespace(
            region_code="EU", currency="EUR", floor_low=Decimal("0.10"), floor_high=Decimal("0.20")
        ),
        SimpleNamespace(
            region_code="US", currency="USD", floor_low=Decimal("0.15"), floor_high=Decimal("0.30")
        ),
    ]
    app = _app(_svc(list_floors=AsyncMock(return_value=floors)))
    resp = await _call(app, "GET", "/api/v1/billing/floors", headers=_auth(uuid4()))
    assert resp.status_code == 200
    body = resp.json()
    assert [f["region_code"] for f in body] == ["EU", "US"]
    # Amounts are serialized as strings so no float can creep into the client.
    assert body[0]["floor_low"] == "0.10"


@pytest.mark.asyncio
async def test_list_floors_returns_an_empty_list_when_none_configured():
    app = _app(_svc(list_floors=AsyncMock(return_value=[])))
    resp = await _call(app, "GET", "/api/v1/billing/floors", headers=_auth(uuid4()))
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_status_returns_the_latest_cycle():
    pool = SimpleNamespace(
        cycle_start=datetime(2026, 1, 1),
        cycle_end=datetime(2026, 2, 1),
        net_revenue=Decimal("1000.00"),
        pool_percentage=Decimal("0.15"),
        pool_amount=Decimal("150.00"),
        redistributed_amount=Decimal("75.00"),
    )
    app = _app(_svc(get_pool_status=AsyncMock(return_value=pool)))
    resp = await _call(app, "GET", "/api/v1/billing/pool", headers=_auth(uuid4()))
    assert resp.status_code == 200
    body = resp.json()
    assert body["cycle_start"] == "2026-01-01T00:00:00"
    assert body["pool_amount"] == "150.00"
    assert body["pool_percentage"] == "0.15"


@pytest.mark.asyncio
async def test_pool_status_reports_no_cycles_yet():
    app = _app(_svc(get_pool_status=AsyncMock(return_value=None)))
    resp = await _call(app, "GET", "/api/v1/billing/pool", headers=_auth(uuid4()))
    assert resp.status_code == 200
    assert resp.json() == {"status": "no_cycles_yet"}


# ---------------------------------------------------------------------------
# POST /milestones
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_milestone_accepts_a_self_request_from_a_creator():
    user = uuid4()
    ms = SimpleNamespace(
        id=uuid4(), status=MilestoneStatus.PENDING, total_commitment=Decimal("10000.00")
    )
    svc = _svc(create_milestone=AsyncMock(return_value=ms))
    app = _app(svc)
    with patch("app.api.billing_routes._verify_creator", new=AsyncMock()) as verify:
        resp = await _call(
            app,
            "POST",
            "/api/v1/billing/milestones",
            headers=_auth(user),
            json={
                "creator_id": str(user),
                "project_title": "Feature",
                "total_commitment": "10000.00",
            },
        )
    assert resp.status_code == 200
    assert resp.json() == {
        "milestone_id": str(ms.id),
        "status": "pending",
        "total_commitment": "10000.00",
        "tranches": "10/20/30/40 (all locked)",
    }
    verify.assert_awaited_once()
    assert svc.create_milestone.call_args.kwargs == {
        "caller_id": user,
        "caller_is_admin": False,
    }


@pytest.mark.asyncio
async def test_create_milestone_by_an_admin_skips_creator_verification():
    admin, creator = uuid4(), uuid4()
    ms = SimpleNamespace(
        id=uuid4(), status=MilestoneStatus.PENDING, total_commitment=Decimal("500.00")
    )
    svc = _svc(create_milestone=AsyncMock(return_value=ms))
    app = _app(svc)
    with patch("app.api.billing_routes._verify_creator", new=AsyncMock()) as verify:
        resp = await _call(
            app,
            "POST",
            "/api/v1/billing/milestones",
            headers=_auth(admin, "admin"),
            json={
                "creator_id": str(creator),
                "project_title": "Grant",
                "total_commitment": "500.00",
            },
        )
    assert resp.status_code == 200
    # An admin needs no creator profile, so the introspection call is skipped.
    verify.assert_not_awaited()
    assert svc.create_milestone.call_args.kwargs["caller_is_admin"] is True


@pytest.mark.asyncio
async def test_create_milestone_rejects_a_non_uuid_token_subject():
    user = uuid4()
    svc = _svc()
    app = _app(svc)
    # Stands in for a decoded auth-service token, so it carries the same
    # `av` the presented bearer token does — `_enforce_auth_version` compares
    # the two and the request must get past that to reach the subject guard.
    with patch(
        "app.api.billing_routes.verify_jwt_token",
        new=AsyncMock(return_value={"sub": "not-a-uuid", "role": "user", "av": 0}),
    ):
        resp = await _call(
            app,
            "POST",
            "/api/v1/billing/milestones",
            headers=_auth(user),
            json={
                "creator_id": str(user),
                "project_title": "t",
                "total_commitment": "1.00",
            },
        )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid token subject"


@pytest.mark.asyncio
async def test_create_milestone_translates_authorization_failure_to_403():
    user = uuid4()
    svc = _svc(
        create_milestone=AsyncMock(side_effect=MilestoneAuthorizationError("nope"))
    )
    app = _app(svc)
    with patch("app.api.billing_routes._verify_creator", new=AsyncMock()):
        resp = await _call(
            app,
            "POST",
            "/api/v1/billing/milestones",
            headers=_auth(user),
            json={
                "creator_id": str(user),
                "project_title": "t",
                "total_commitment": "1.00",
            },
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_milestone_translates_a_domain_error_to_400():
    user = uuid4()
    svc = _svc(create_milestone=AsyncMock(side_effect=BillingError("bad commitment")))
    app = _app(svc)
    with patch("app.api.billing_routes._verify_creator", new=AsyncMock()):
        resp = await _call(
            app,
            "POST",
            "/api/v1/billing/milestones",
            headers=_auth(user),
            json={
                "creator_id": str(user),
                "project_title": "t",
                "total_commitment": "1.00",
            },
        )
    assert resp.status_code == 400
    assert "bad commitment" in resp.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("commitment", ["0.00", "-5.00"])
async def test_create_milestone_requires_a_positive_commitment(commitment):
    user = uuid4()
    app = _app(_svc())
    resp = await _call(
        app,
        "POST",
        "/api/v1/billing/milestones",
        headers=_auth(user),
        json={
            "creator_id": str(user),
            "project_title": "t",
            "total_commitment": commitment,
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /milestones/{id}/release and /kill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_tranche_returns_the_released_amount():
    admin = uuid4()
    tranche = SimpleNamespace(
        tranche_number=2,
        percentage=Decimal("20.00"),
        amount=Decimal("2000.00"),
        status=TrancheStatus.RELEASED,
    )
    mid = uuid4()
    svc = _svc(release_tranche=AsyncMock(return_value=tranche))
    app = _app(svc)
    resp = await _call(
        app,
        "POST",
        f"/api/v1/billing/milestones/{mid}/release",
        headers=_auth(admin, "admin"),
        json={"tranche_number": 2},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "tranche_number": 2,
        "percentage": "20.00",
        "amount": "2000.00",
        "status": "released",
    }
    assert svc.release_tranche.call_args.kwargs["caller_is_admin"] is True


@pytest.mark.asyncio
async def test_release_tranche_translates_authorization_failure_to_403():
    admin = uuid4()
    svc = _svc(release_tranche=AsyncMock(side_effect=MilestoneAuthorizationError("nope")))
    app = _app(svc)
    resp = await _call(
        app,
        "POST",
        f"/api/v1/billing/milestones/{uuid4()}/release",
        headers=_auth(admin, "admin"),
        json={"tranche_number": 1},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_release_tranche_translates_a_kill_clause_error_to_400():
    admin = uuid4()
    svc = _svc(
        release_tranche=AsyncMock(side_effect=BillingError("Cannot release tranches on a killed milestone"))
    )
    app = _app(svc)
    resp = await _call(
        app,
        "POST",
        f"/api/v1/billing/milestones/{uuid4()}/release",
        headers=_auth(admin, "admin"),
        json={"tranche_number": 1},
    )
    assert resp.status_code == 400
    assert "killed milestone" in resp.json()["detail"]


@pytest.mark.parametrize("tranche_number", [0, 5])
@pytest.mark.asyncio
async def test_release_tranche_rejects_an_out_of_range_tranche_number(tranche_number):
    admin = uuid4()
    app = _app(_svc())
    resp = await _call(
        app,
        "POST",
        f"/api/v1/billing/milestones/{uuid4()}/release",
        headers=_auth(admin, "admin"),
        json={"tranche_number": tranche_number},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_kill_milestone_reports_the_reverted_pool_message():
    admin = uuid4()
    ms = SimpleNamespace(id=uuid4(), status=MilestoneStatus.KILLED)
    svc = _svc(kill_milestone=AsyncMock(return_value=ms))
    app = _app(svc)
    mid = ms.id
    resp = await _call(
        app, "POST", f"/api/v1/billing/milestones/{mid}/kill", headers=_auth(admin, "admin")
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "milestone_id": str(mid),
        "status": "killed",
        "message": "All unreleased tranches reverted to Creator Pool.",
    }


@pytest.mark.asyncio
async def test_kill_milestone_translates_authorization_failure_to_403():
    admin = uuid4()
    svc = _svc(kill_milestone=AsyncMock(side_effect=MilestoneAuthorizationError("nope")))
    app = _app(svc)
    resp = await _call(
        app, "POST", f"/api/v1/billing/milestones/{uuid4()}/kill", headers=_auth(admin, "admin")
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_kill_milestone_translates_a_missing_milestone_to_400():
    admin = uuid4()
    mid = uuid4()
    svc = _svc(kill_milestone=AsyncMock(side_effect=BillingError(f"Milestone {mid} not found")))
    app = _app(svc)
    resp = await _call(
        app, "POST", f"/api/v1/billing/milestones/{mid}/kill", headers=_auth(admin, "admin")
    )
    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /payouts/{creator_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payout_history_returns_the_ledger_for_the_owner():
    creator = uuid4()
    payouts = [
        SimpleNamespace(
            id=uuid4(),
            amount=Decimal("55.00"),
            currency="USD",
            status=PayoutStatus.ACCRUED,
            cycle_start=datetime(2026, 1, 1),
            cycle_end=datetime(2026, 2, 1),
        )
    ]
    svc = _svc(get_payout_history=AsyncMock(return_value=payouts))
    app = _app(svc)
    resp = await _call(app, "GET", f"/api/v1/billing/payouts/{creator}", headers=_auth(creator))
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["amount"] == "55.00"
    assert body[0]["cycle_start"] == "2026-01-01T00:00:00"


@pytest.mark.asyncio
async def test_payout_history_serialises_missing_cycle_dates_as_null():
    creator = uuid4()
    payouts = [
        SimpleNamespace(
            id=uuid4(),
            amount=Decimal("1.00"),
            currency="USD",
            status=PayoutStatus.ACCRUED,
            cycle_start=None,
            cycle_end=None,
        )
    ]
    app = _app(_svc(get_payout_history=AsyncMock(return_value=payouts)))
    resp = await _call(app, "GET", f"/api/v1/billing/payouts/{creator}", headers=_auth(creator))
    body = resp.json()
    assert body[0]["cycle_start"] is None
    assert body[0]["cycle_end"] is None


@pytest.mark.asyncio
async def test_payout_history_blocks_another_creator():
    creator, other = uuid4(), uuid4()
    app = _app(_svc(get_payout_history=AsyncMock(return_value=[])))
    resp = await _call(app, "GET", f"/api/v1/billing/payouts/{other}", headers=_auth(creator))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "You can only access your own data"


# ---------------------------------------------------------------------------
# GET /creator-share
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "revenue,expected_share",
    [
        ("100.00", "55.0000"),
        ("0", "0.00"),
        ("7.99", "4.3945"),
        ("1.00", "0.5500"),
    ],
)
@pytest.mark.asyncio
async def test_creator_share_applies_the_55_percent_floor(revenue, expected_share):
    # Decimal arithmetic, so the response never carries binary-float drift.
    # The result keeps the product's scale (2dp x 2dp -> 4dp), which is exact
    # but means the string carries four decimal places.
    resp = await _call(
        create_app(), "GET", f"/api/v1/billing/creator-share?svod_revenue={revenue}"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["svod_revenue"] == revenue
    assert body["creator_share_floor"] == expected_share
    assert body["percentage"] == "55%"


@pytest.mark.asyncio
async def test_creator_share_requires_the_revenue_parameter():
    resp = await _call(create_app(), "GET", "/api/v1/billing/creator-share")
    assert resp.status_code == 422
