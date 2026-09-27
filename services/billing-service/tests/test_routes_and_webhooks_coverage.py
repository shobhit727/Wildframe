"""Coverage for Billing HTTP boundaries and Stripe webhook dispatch."""

from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from app.api import billing_routes as routes
from app.api import webhook_routes as webhooks
from app.core.money import CurrencyError
from app.core.stripe_client import StripeError
from app.models import InvoiceStatus, MilestoneStatus, RevenueTier, SubscriptionStatus, TrancheStatus


@pytest.mark.asyncio
async def test_auth_helpers_cover_creator_and_auth_version_branches(monkeypatch):
    with pytest.raises(HTTPException):
        await routes._verify_creator(None)

    response = MagicMock(status_code=200)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=response)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: client)
    await routes._verify_creator("Bearer token")

    response.status_code = 404
    with pytest.raises(HTTPException) as exc:
        await routes._verify_creator("Bearer token")
    assert exc.value.status_code == 403

    response.status_code = 500
    with pytest.raises(HTTPException):
        await routes._verify_creator("Bearer token")

    client.get.side_effect = httpx.RequestError("down")
    with pytest.raises(HTTPException):
        await routes._verify_creator("Bearer token")

    client.get.side_effect = None
    response.status_code = 200
    response.json.return_value = {"auth_version": 3}
    await routes._enforce_auth_version("Bearer token", {"av": 3})
    response.json.return_value = {"user": {"av": 4}}
    with pytest.raises(HTTPException):
        await routes._enforce_auth_version("Bearer token", {"av": 3})
    response.json.side_effect = ValueError("bad json")
    await routes._enforce_auth_version("Bearer token", {"av": 3})
    client.get.side_effect = RuntimeError("down")
    with pytest.raises(HTTPException):
        await routes._enforce_auth_version("Bearer token", {"av": 3})


@pytest.mark.asyncio
async def test_auth_payload_and_identity_guards(monkeypatch):
    with pytest.raises(HTTPException):
        await routes.get_current_user_payload(None)
    monkeypatch.setattr(routes, "verify_jwt_token", AsyncMock(side_effect=Exception("unexpected")))
    with pytest.raises(Exception):
        await routes.get_current_user_payload("Bearer token")

    payload = {"sub": str(uuid4()), "role": "admin"}
    monkeypatch.setattr(routes, "verify_jwt_token", AsyncMock(return_value=payload))
    monkeypatch.setattr(routes, "_enforce_auth_version", AsyncMock())
    result = await routes.get_current_user_payload("Bearer token")
    assert result == payload

    assert await routes.require_admin(payload) == UUID(payload["sub"])
    with pytest.raises(HTTPException):
        await routes.require_admin({"role": "user", "sub": payload["sub"]})
    with pytest.raises(HTTPException):
        await routes.require_admin({"role": "admin"})
    with pytest.raises(HTTPException):
        await routes.require_admin({"role": "admin", "sub": "bad"})

    monkeypatch.setattr(routes, "verify_jwt_token", AsyncMock(return_value={"sub": str(uuid4())}))
    monkeypatch.setattr(routes, "_enforce_auth_version", AsyncMock())
    uid = await routes.get_current_user_id("Bearer token")
    assert isinstance(uid, UUID)
    with pytest.raises(HTTPException):
        await routes.get_current_user_id(None)
    monkeypatch.setattr(routes, "verify_jwt_token", AsyncMock(side_effect=routes.JWTError("bad")))
    with pytest.raises(HTTPException):
        await routes.get_current_user_id("Bearer token")

    current = uuid4()
    request = SimpleNamespace(path_params={"user_id": str(current)})
    assert await routes.require_self(current, request) == current
    request.path_params["user_id"] = str(uuid4())
    with pytest.raises(HTTPException):
        await routes.require_self(current, request)


def _billing_service():
    service = MagicMock()
    service.get_subscription = AsyncMock()
    service.subscribe = AsyncMock()
    service.cancel_subscription = AsyncMock()
    service._fetch_content_price = AsyncMock(return_value=Decimal("4.99"))
    service.get_floor = AsyncMock()
    service.list_floors = AsyncMock(return_value=[])
    service.get_pool_status = AsyncMock()
    service.create_milestone = AsyncMock()
    service.release_tranche = AsyncMock()
    service.kill_milestone = AsyncMock()
    service.get_payout_history = AsyncMock(return_value=[])
    service.commit = AsyncMock()
    service.rollback = AsyncMock()
    return service


@pytest.mark.asyncio
async def test_billing_subscription_and_catalog_routes():
    service = _billing_service()
    user = uuid4()
    sub = SimpleNamespace(user_id=user, tier=RevenueTier.SVOD, monthly_price=Decimal("7.99"), is_active=True)
    service.get_subscription.return_value = sub
    response = await routes.get_subscription(user, service)
    assert response.tier == "svod"

    service.get_subscription.return_value = None
    with pytest.raises(HTTPException):
        await routes.get_subscription(user, service)

    service.subscribe.side_effect = routes.TierInvalidError("bad tier")
    with pytest.raises(HTTPException):
        await routes.subscribe(user, routes.SubscribeRequest(tier="bad"), service)
    service.subscribe.side_effect = None
    service.subscribe.return_value = sub
    result = await routes.subscribe(user, routes.SubscribeRequest(tier="svod"), service)
    assert result["status"] == "subscribed"

    service.cancel_subscription.return_value = None
    with pytest.raises(HTTPException):
        await routes.cancel_subscription(user, service)
    service.cancel_subscription.return_value = sub
    assert (await routes.cancel_subscription(user, service))["status"] == "cancelled"

    floor = SimpleNamespace(region_code="US", currency="USD", floor_low=Decimal("10"), floor_high=Decimal("20"))
    service.get_floor.return_value = None
    with pytest.raises(HTTPException):
        await routes.get_floor("US", service)
    service.get_floor.return_value = floor
    assert (await routes.get_floor("US", service))["currency"] == "USD"

    service.list_floors.return_value = [floor]
    assert len(await routes.list_floors(service)) == 1

    service.get_pool_status.return_value = None
    assert (await routes.get_pool_status(service))["status"] == "no_cycles_yet"
    service.get_pool_status.return_value = SimpleNamespace(
        cycle_start=SimpleNamespace(isoformat=lambda: "a"),
        cycle_end=SimpleNamespace(isoformat=lambda: "b"),
        net_revenue=Decimal("100"),
        pool_percentage=Decimal("0.15"),
        pool_amount=Decimal("15"),
        redistributed_amount=Decimal("2"),
    )
    assert (await routes.get_pool_status(service))["net_revenue"] == "100"


@pytest.mark.asyncio
async def test_purchase_route_validation_and_success(monkeypatch):
    service = _billing_service()
    user, content = uuid4(), uuid4()
    request = routes.PurchaseRequest(user_id=user, content_id=content)

    with pytest.raises(HTTPException):
        await routes.purchase_title(request.model_copy(update={"user_id": uuid4()}), service, user)

    service._fetch_content_price.side_effect = ValueError("missing content")
    with pytest.raises(HTTPException):
        await routes.purchase_title(request, service, user)

    service._fetch_content_price.side_effect = None
    monkeypatch.setattr(routes, "validate_currency", MagicMock(side_effect=CurrencyError("bad")))
    with pytest.raises(HTTPException):
        await routes.purchase_title(request, service, user)

    monkeypatch.setattr(routes, "validate_currency", MagicMock())
    monkeypatch.setattr(routes.StripeClient, "create_tvod_purchase_session", MagicMock(side_effect=StripeError("stripe")))
    with pytest.raises(HTTPException):
        await routes.purchase_title(request, service, user)

    monkeypatch.setattr(
        routes.StripeClient,
        "create_tvod_purchase_session",
        MagicMock(return_value={"id": "cs_1", "url": "https://checkout"}),
    )
    result = await routes.purchase_title(request, service, user)
    assert result["checkout_session_id"] == "cs_1"


@pytest.mark.asyncio
async def test_milestone_and_payout_routes():
    service = _billing_service()
    creator = uuid4()
    payload = {"sub": str(creator), "role": "user"}
    request = routes.CreateMilestoneRequest(
        creator_id=creator, project_title="Project", total_commitment=Decimal("1000")
    )
    service.create_milestone.return_value = SimpleNamespace(
        id=uuid4(), status=MilestoneStatus.PENDING, total_commitment=Decimal("1000")
    )
    result = await routes.create_milestone(request, service, payload, "Bearer token")
    assert result["milestone_id"]

    other = uuid4()
    with pytest.raises(HTTPException):
        await routes.create_milestone(
            request.model_copy(update={"creator_id": other}), service, payload, "Bearer token"
        )

    tranche = SimpleNamespace(
        tranche_number=1,
        percentage=Decimal("10"),
        amount=Decimal("100"),
        status=TrancheStatus.RELEASED,
    )
    service.release_tranche.return_value = tranche
    admin = uuid4()
    result = await routes.release_tranche(
        uuid4(), routes.ReleaseTrancheRequest(tranche_number=1), service, admin
    )
    assert result["status"] == "released"

    service.release_tranche.side_effect = routes.MilestoneAuthorizationError("forbidden")
    with pytest.raises(HTTPException):
        await routes.release_tranche(uuid4(), routes.ReleaseTrancheRequest(tranche_number=1), service, admin)
    service.release_tranche.side_effect = routes.BillingError("bad")
    with pytest.raises(HTTPException):
        await routes.release_tranche(uuid4(), routes.ReleaseTrancheRequest(tranche_number=1), service, admin)

    ms = SimpleNamespace(id=uuid4(), status=MilestoneStatus.KILLED)
    service.kill_milestone.return_value = ms
    assert (await routes.kill_milestone(uuid4(), service, admin))["status"] == "killed"

    payout = SimpleNamespace(
        id=uuid4(), amount=Decimal("10"), currency="USD", status=SimpleNamespace(value="accrued"),
        cycle_start=None, cycle_end=None
    )
    service.get_payout_history.return_value = [payout]
    assert len(await routes.get_payout_history(admin, service, admin)) == 1
    with pytest.raises(HTTPException):
        await routes.get_payout_history(uuid4(), service, admin)

    share = await routes.calculate_creator_share(Decimal("100"))
    assert share["creator_share_floor"] == "55.00"


@pytest.mark.asyncio
async def test_webhook_handlers_cover_checkout_subscription_invoice():
    service = _billing_service()
    user = uuid4()
    event = {"data": {"object": {"metadata": {}}}}
    await webhooks._handle_checkout_session_completed(event, service)

    event["data"]["object"]["metadata"] = {"user_id": str(user), "tier": "unknown"}
    await webhooks._handle_checkout_session_completed(event, service)

    service.sub_repo = MagicMock()
    service.sub_repo.get_by_user = AsyncMock(return_value=None)
    service.subscribe = AsyncMock()
    event["data"]["object"]["metadata"] = {"user_id": str(user), "tier": "svod"}
    await webhooks._handle_checkout_session_completed(event, service)
    service.subscribe.assert_awaited_once()

    event["data"]["object"]["metadata"] = {"user_id": str(user), "type": "tvod", "content_id": str(uuid4())}
    event["data"]["object"].update({"payment_status": "unpaid", "currency": "USD", "amount_total": 499})
    with pytest.raises(webhooks.BillingError):
        await webhooks._handle_checkout_session_completed(event, service)
    event["data"]["object"]["payment_status"] = "paid"
    service._fetch_content_price.return_value = Decimal("4.99")
    service.purchase_title = AsyncMock()
    await webhooks._handle_checkout_session_completed(event, service)

    await webhooks._handle_subscription_updated({"data": {"object": {"metadata": {}}}}, service)
    await webhooks._handle_subscription_deleted({"data": {"object": {"metadata": {}}}}, service)
    service.sync_subscription_from_stripe = AsyncMock()
    event = {"created": 10, "data": {"object": {"metadata": {"user_id": str(user)}, "status": "active"}}}
    await webhooks._handle_subscription_updated(event, service)
    await webhooks._handle_subscription_deleted(event, service)

    service.inv_repo = MagicMock()
    service.inv_repo.get_by_stripe_invoice_id = AsyncMock(return_value=MagicMock())
    await webhooks._handle_invoice_paid({"data": {"object": {"id": "in_1"}}}, service)
    service.inv_repo.get_by_stripe_invoice_id.return_value = None
    service.inv_repo.create = AsyncMock(return_value=SimpleNamespace(status=InvoiceStatus.PAID, paid_at=None))
    invoice = {
        "id": "in_2", "currency": "USD", "total": 499,
        "lines": {"data": [{"metadata": {"user_id": str(user)}}]},
    }
    await webhooks._handle_invoice_paid({"data": {"object": invoice}}, service)


@pytest.mark.asyncio
async def test_webhook_payment_intent_refund_and_endpoint(monkeypatch):
    service = _billing_service()
    pi = {"id": "pi_1", "amount": 499, "currency": "USD", "metadata": {"content_id": str(uuid4())}}
    service._fetch_content_details = AsyncMock(return_value=(Decimal("4.99"), uuid4()))
    service.accrue_payout = AsyncMock()
    service.purchase_repo = MagicMock()
    service.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=None)

    await webhooks._handle_payment_intent_succeeded({"id": "evt", "data": {"object": pi}}, service)

    with pytest.raises(webhooks.BillingError):
        await webhooks._handle_payment_intent_succeeded({"data": {"object": {}}}, service)

    refund = {"id": "re_1", "amount": 100, "currency": "USD", "charge": "ch_1", "payment_intent": "pi_1"}
    service.process_refund = AsyncMock()
    await webhooks._process_single_refund_obj(refund, service)
    await webhooks._handle_refund({"type": "refund.created", "data": {"object": refund}}, service)

    charge = {"id": "ch_1", "refunds": {"data": [refund]}, "payment_intent": "pi_1"}
    await webhooks._handle_refund({"type": "charge.refunded", "data": {"object": charge}}, service)

    monkeypatch.setattr(webhooks.StripeClient, "handle_webhook", MagicMock(side_effect=StripeError("bad signature")))
    request = MagicMock()
    request.body = AsyncMock(return_value=b"{}")
    with pytest.raises(HTTPException):
        await webhooks.stripe_webhook(request, service, "bad")

    monkeypatch.setattr(
        webhooks.StripeClient,
        "handle_webhook",
        MagicMock(return_value={"id": "evt_1", "type": "unknown"}),
    )
    service.webhook_events_repo = MagicMock()
    service.webhook_events_repo.claim = AsyncMock(return_value=False)
    assert (await webhooks.stripe_webhook(request, service, ""))["idempotent"] is True

    service.webhook_events_repo.claim.return_value = True
    service.webhook_events_repo.complete = AsyncMock()
    result = await webhooks.stripe_webhook(request, service, "")
    assert result["handled"] is False

    monkeypatch.setitem(webhooks._EVENT_HANDLERS, "known", AsyncMock())
    monkeypatch.setattr(
        webhooks.StripeClient,
        "handle_webhook",
        MagicMock(return_value={"id": "evt_2", "type": "known"}),
    )
    result = await webhooks.stripe_webhook(request, service, "")
    assert result["handled"] is True
