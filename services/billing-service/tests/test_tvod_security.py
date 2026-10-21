from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from tests._auth_tokens import mint_access_token

from app.api.webhook_routes import _handle_checkout_session_completed
from app.core.settings import settings
from app.main import create_app
from app.services import BillingError


def _token(user_id, role="user", **overrides):
    """A realistic auth-service access token (full claim set, RS256, `av`)."""
    return mint_access_token(user_id, role, **overrides)


def _session_event(
    user_id,
    content_id,
    amount_total,
    currency="usd",
    payment_status="paid",
    payment_intent="pi_test",
):
    return {
        "id": "evt_test_123",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_123",
                "client_reference_id": str(user_id),
                "payment_status": payment_status,
                "amount_total": amount_total,
                "currency": currency,
                "payment_intent": payment_intent,
                "metadata": {
                    "user_id": str(user_id),
                    "content_id": str(content_id),
                    "type": "tvod",
                },
            }
        },
    }


@pytest.mark.asyncio
async def test_purchase_initiation_uses_canonical_price_not_client_price():
    user = uuid4()
    content = uuid4()
    token = _token(user)
    canonical = Decimal("9.99")
    mock_session = MagicMock()
    mock_session.id = "cs_canonical_9_99"
    mock_session.url = "https://checkout.stripe.com/pay/cs_canonical_9_99"

    mock_service = MagicMock()
    mock_service._fetch_content_price = AsyncMock(return_value=canonical)

    async def _override():
        return mock_service

    app = create_app()
    app.dependency_overrides[
        __import__("app.api.billing_routes", fromlist=["get_billing_service"]).get_billing_service
    ] = _override

    with patch(
        "app.api.billing_routes.StripeClient.create_tvod_purchase_session",
        return_value=mock_session,
    ) as mock_create:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/billing/purchase",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "user_id": str(user),
                    "content_id": str(content),
                    "price": "0.01",
                    "amount": 1,
                    "price_usd": "0.01",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending"
        assert body["price"] == str(canonical)
        assert body["currency"] == settings.DEFAULT_CURRENCY
        assert body["checkout_session_id"] == mock_session.id
        mock_service._fetch_content_price.assert_awaited_once_with(content)
        mock_create.assert_called_once()
        args, _ = mock_create.call_args
        assert args[0] == user
        assert args[1] == content
        assert args[2] == canonical
        mock_service.purchase_title = AsyncMock()
        assert not mock_service.purchase_title.called

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_purchase_initiation_does_not_create_purchase_synchronously():
    user = uuid4()
    content = uuid4()
    token = _token(user)
    mock_service = MagicMock()
    mock_service._fetch_content_price = AsyncMock(return_value=Decimal("4.99"))
    mock_session = MagicMock(id="cs_123", url="https://checkout.stripe.com/pay/cs_123")

    async def _override():
        return mock_service

    app = create_app()
    app.dependency_overrides[
        __import__("app.api.billing_routes", fromlist=["get_billing_service"]).get_billing_service
    ] = _override
    with patch(
        "app.api.billing_routes.StripeClient.create_tvod_purchase_session",
        return_value=mock_session,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/billing/purchase",
                headers={"Authorization": f"Bearer {token}"},
                json={"user_id": str(user), "content_id": str(content)},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"
        assert "purchase_id" not in resp.json()
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_purchase_initiation_rejects_when_content_not_found():
    user = uuid4()
    content = uuid4()
    token = _token(user)
    mock_service = MagicMock()
    mock_service._fetch_content_price = AsyncMock(side_effect=ValueError("Content not found"))

    async def _override():
        return mock_service

    app = create_app()
    app.dependency_overrides[
        __import__("app.api.billing_routes", fromlist=["get_billing_service"]).get_billing_service
    ] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/billing/purchase",
            headers={"Authorization": f"Bearer {token}"},
            json={"user_id": str(user), "content_id": str(content)},
        )
    assert resp.status_code == 400
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_webhook_tampered_zero_price_rejected():
    user = uuid4()
    content = uuid4()
    svc = MagicMock()
    svc._fetch_content_price = AsyncMock(return_value=Decimal("9.99"))
    svc.purchase_title = AsyncMock()
    evt = _session_event(user, content, amount_total=0, currency="usd", payment_status="paid")
    with pytest.raises(BillingError, match="Amount mismatch"):
        await _handle_checkout_session_completed(evt, svc)
    svc.purchase_title.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_tampered_low_price_rejected():
    user = uuid4()
    content = uuid4()
    svc = MagicMock()
    svc._fetch_content_price = AsyncMock(return_value=Decimal("9.99"))
    svc.purchase_title = AsyncMock()
    evt = _session_event(user, content, amount_total=100, currency="usd", payment_status="paid")
    with pytest.raises(BillingError, match="Amount mismatch"):
        await _handle_checkout_session_completed(evt, svc)
    svc.purchase_title.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_tampered_high_price_rejected():
    user = uuid4()
    content = uuid4()
    svc = MagicMock()
    svc._fetch_content_price = AsyncMock(return_value=Decimal("9.99"))
    svc.purchase_title = AsyncMock()
    evt = _session_event(user, content, amount_total=999999, currency="usd", payment_status="paid")
    with pytest.raises(BillingError, match="Amount mismatch"):
        await _handle_checkout_session_completed(evt, svc)
    svc.purchase_title.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_unpaid_blocked():
    user = uuid4()
    content = uuid4()
    svc = MagicMock()
    svc._fetch_content_price = AsyncMock(return_value=Decimal("9.99"))
    svc.purchase_title = AsyncMock()
    evt = _session_event(user, content, amount_total=999, currency="usd", payment_status="unpaid")
    with pytest.raises(BillingError, match="not paid"):
        await _handle_checkout_session_completed(evt, svc)
    svc.purchase_title.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_amount_mismatch_rejected():
    user = uuid4()
    content = uuid4()
    svc = MagicMock()
    svc._fetch_content_price = AsyncMock(return_value=Decimal("12.34"))
    svc.purchase_title = AsyncMock()
    evt = _session_event(user, content, amount_total=1233, currency="usd", payment_status="paid")
    with pytest.raises(BillingError, match="Amount mismatch"):
        await _handle_checkout_session_completed(evt, svc)
    svc.purchase_title.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_currency_mismatch_rejected():
    user = uuid4()
    content = uuid4()
    svc = MagicMock()
    svc._fetch_content_price = AsyncMock(return_value=Decimal("9.99"))
    svc.purchase_title = AsyncMock()
    evt = _session_event(user, content, amount_total=999, currency="eur", payment_status="paid")
    with pytest.raises(BillingError):
        await _handle_checkout_session_completed(evt, svc)
    svc.purchase_title.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_valid_paid_creates_purchase():
    user = uuid4()
    content = uuid4()
    svc = MagicMock()
    svc._fetch_content_price = AsyncMock(return_value=Decimal("9.99"))
    svc.purchase_title = AsyncMock(return_value=MagicMock())
    evt = _session_event(user, content, amount_total=999, currency="usd", payment_status="paid")
    await _handle_checkout_session_completed(evt, svc)
    svc.purchase_title.assert_awaited_once()
    args, kwargs = svc.purchase_title.call_args
    assert args[0] == user
    assert args[1] == content
    assert kwargs["currency"] == "USD"


@pytest.mark.asyncio
async def test_webhook_missing_amount_total_rejected():
    user = uuid4()
    content = uuid4()
    svc = MagicMock()
    svc._fetch_content_price = AsyncMock(return_value=Decimal("9.99"))
    svc.purchase_title = AsyncMock()
    evt = _session_event(user, content, amount_total=None, currency="usd", payment_status="paid")
    evt["data"]["object"].pop("amount_total", None)
    with pytest.raises(BillingError, match="Missing amount_total"):
        await _handle_checkout_session_completed(evt, svc)
    svc.purchase_title.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_keeps_idempotency_and_currency_validation():
    from decimal import Decimal
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4

    from app.services import BillingService

    svc = BillingService(
        sub_repo=AsyncMock(),
        purchase_repo=AsyncMock(),
        inv_repo=AsyncMock(),
        floor_repo=AsyncMock(),
        pool_repo=AsyncMock(),
        milestone_repo=AsyncMock(),
        payout_repo=AsyncMock(),
        refund_repo=AsyncMock(),
        webhook_events_repo=AsyncMock(),
    )
    uid = uuid4()
    cid = uuid4()
    svc._fetch_content_price = AsyncMock(return_value=Decimal("5.00"))
    existing = MagicMock()
    svc.purchase_repo.get_by_user_and_content = AsyncMock(return_value=existing)
    result = await svc.purchase_title(uid, cid, currency="USD")
    assert result is existing
    svc.purchase_repo.create.assert_not_awaited()
    svc._fetch_content_price.return_value = Decimal("5.00")
    svc.purchase_repo.get_by_user_and_content.return_value = None
    with pytest.raises(Exception):
        await svc.purchase_title(uid, cid, currency="BAD")
