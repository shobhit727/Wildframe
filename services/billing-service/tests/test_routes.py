"""Route-level tests for the Billing Service HTTP API.

Exercises the real FastAPI routers (billing + Stripe webhooks) via TestClient
with ``get_billing_service`` dependency-overridden to inject a fake service and
StripeClient's webhook parsing patched. Covers subscriptions, TVOD purchases,
floors, pool, milestones, payouts and webhook idempotency.
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.billing_routes import get_billing_service as billing_di
from app.api.webhook_routes import get_billing_service as webhook_di
from app.main import app


@pytest.fixture
def fake_service():
    service = MagicMock()
    service.sub_repo = AsyncMock()
    service.purchase_repo = AsyncMock()
    service.inv_repo = AsyncMock()
    service.floor_repo = AsyncMock()
    service.pool_repo = AsyncMock()
    service.milestone_repo = AsyncMock()
    service.payout_repo = AsyncMock()
    return service


@pytest.fixture(autouse=True)
def override_deps(fake_service):
    app.dependency_overrides[billing_di] = lambda: fake_service
    app.dependency_overrides[webhook_di] = lambda: fake_service
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


class FakeSubscription:
    user_id = None
    tier = None
    monthly_price = None
    is_active = True


def make_subscription():
    s = MagicMock()
    s.user_id = uuid4()
    s.tier = MagicMock()
    s.tier.value = "svod"
    s.monthly_price = Decimal("7.99")
    s.is_active = True
    return s


class TestSubscriptionRoutes:
    def test_get_subscription(self, client, fake_service):
        sub = make_subscription()
        fake_service.get_subscription = AsyncMock(return_value=sub)

        response = client.get(f"/api/v1/billing/subscription/{sub.user_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["tier"] == "svod"
        assert body["monthly_price"] == "7.99"

    def test_get_subscription_missing_returns_404(self, client, fake_service):
        fake_service.get_subscription = AsyncMock(return_value=None)

        response = client.get(f"/api/v1/billing/subscription/{uuid4()}")

        assert response.status_code == 404

    def test_subscribe(self, client, fake_service):
        sub = make_subscription()
        fake_service.subscribe = AsyncMock(return_value=sub)

        response = client.post(f"/api/v1/billing/subscribe/{sub.user_id}", json={"tier": "svod"})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "subscribed"
        assert body["tier"] == "svod"
        fake_service.subscribe.assert_awaited_once_with(sub.user_id, "svod")

    def test_subscribe_invalid_tier_returns_400(self, client, fake_service):
        from app.services import TierInvalidError

        fake_service.subscribe = AsyncMock(side_effect=TierInvalidError("Unknown tier: diamond"))

        response = client.post(f"/api/v1/billing/subscribe/{uuid4()}", json={"tier": "diamond"})

        assert response.status_code == 400

    def test_subscribe_rejects_missing_tier(self, client):
        response = client.post(f"/api/v1/billing/subscribe/{uuid4()}", json={})

        assert response.status_code == 422

    def test_cancel_subscription(self, client, fake_service):
        sub = make_subscription()
        sub.tier.value = "avod"
        fake_service.cancel_subscription = AsyncMock(return_value=sub)

        response = client.post(f"/api/v1/billing/cancel/{sub.user_id}")

        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        fake_service.cancel_subscription.assert_awaited_once_with(sub.user_id)

    def test_cancel_subscription_missing_returns_404(self, client, fake_service):
        fake_service.cancel_subscription = AsyncMock(return_value=None)

        response = client.post(f"/api/v1/billing/cancel/{uuid4()}")

        assert response.status_code == 404


class TestPurchaseRoutes:
    def test_purchase_title(self, client, fake_service):
        purchase = MagicMock()
        purchase.id = uuid4()
        fake_service.purchase_title = AsyncMock(return_value=purchase)

        response = client.post(
            "/api/v1/billing/purchase",
            json={"user_id": str(uuid4()), "content_id": str(uuid4()), "price": "4.99"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "completed"

    def test_purchase_title_rejects_zero_price(self, client):
        response = client.post(
            "/api/v1/billing/purchase",
            json={"user_id": str(uuid4()), "content_id": str(uuid4()), "price": "0"},
        )

        assert response.status_code == 422


class TestFloorRoutes:
    def test_get_floor(self, client, fake_service):
        floor = MagicMock()
        floor.region_code = "US"
        floor.currency = "USD"
        floor.floor_low = Decimal("3.00")
        floor.floor_high = Decimal("5.00")
        fake_service.get_floor = AsyncMock(return_value=floor)

        response = client.get("/api/v1/billing/floor/US")

        assert response.status_code == 200
        body = response.json()
        assert body["region_code"] == "US"
        assert body["floor_low"] == "3.00"

    def test_get_floor_missing_returns_404(self, client, fake_service):
        fake_service.get_floor = AsyncMock(return_value=None)

        response = client.get("/api/v1/billing/floor/XX")

        assert response.status_code == 404

    def test_list_floors(self, client, fake_service):
        floor = MagicMock()
        floor.region_code = "US"
        floor.currency = "USD"
        floor.floor_low = Decimal("3.00")
        floor.floor_high = Decimal("5.00")
        fake_service.list_floors = AsyncMock(return_value=[floor])

        response = client.get("/api/v1/billing/floors")

        assert response.status_code == 200
        assert len(response.json()) == 1


class TestPoolRoutes:
    def test_get_pool_status(self, client, fake_service):
        pool = MagicMock()
        pool.cycle_start = datetime(2026, 1, 1, tzinfo=UTC)
        pool.cycle_end = datetime(2026, 1, 31, tzinfo=UTC)
        pool.net_revenue = Decimal("1000.00")
        pool.pool_percentage = Decimal("0.55")
        pool.pool_amount = Decimal("550.00")
        pool.redistributed_amount = Decimal("0.00")
        fake_service.get_pool_status = AsyncMock(return_value=pool)

        response = client.get("/api/v1/billing/pool")

        assert response.status_code == 200
        body = response.json()
        assert body["pool_amount"] == "550.00"

    def test_get_pool_status_no_cycles(self, client, fake_service):
        fake_service.get_pool_status = AsyncMock(return_value=None)

        response = client.get("/api/v1/billing/pool")

        assert response.status_code == 200
        assert response.json()["status"] == "no_cycles_yet"


class TestMilestoneRoutes:
    def test_create_milestone(self, client, fake_service):
        ms = MagicMock()
        ms.id = uuid4()
        ms.status = MagicMock()
        ms.status.value = "funded"
        ms.total_commitment = Decimal("1000.00")
        fake_service.create_milestone = AsyncMock(return_value=ms)

        response = client.post(
            "/api/v1/billing/milestones",
            json={
                "creator_id": str(uuid4()),
                "project_title": "Film",
                "total_commitment": "1000.00",
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "funded"

    def test_create_milestone_rejects_nonpositive(self, client):
        response = client.post(
            "/api/v1/billing/milestones",
            json={"creator_id": str(uuid4()), "project_title": "Film", "total_commitment": "0"},
        )

        assert response.status_code == 422

    def test_release_tranche(self, client, fake_service):
        tranche = MagicMock()
        tranche.tranche_number = 1
        tranche.percentage = Decimal("10")
        tranche.amount = Decimal("100.00")
        tranche.status = MagicMock()
        tranche.status.value = "released"
        fake_service.release_tranche = AsyncMock(return_value=tranche)

        response = client.post(
            f"/api/v1/billing/milestones/{uuid4()}/release", json={"tranche_number": 1}
        )

        assert response.status_code == 200
        assert response.json()["tranche_number"] == 1

    def test_release_tranche_rejects_bad_number(self, client):
        response = client.post(
            f"/api/v1/billing/milestones/{uuid4()}/release", json={"tranche_number": 5}
        )

        assert response.status_code == 422

    def test_kill_milestone(self, client, fake_service):
        ms = MagicMock()
        ms.id = uuid4()
        ms.status = MagicMock()
        ms.status.value = "killed"
        fake_service.kill_milestone = AsyncMock(return_value=ms)

        response = client.post(f"/api/v1/billing/milestones/{uuid4()}/kill")

        assert response.status_code == 200
        assert response.json()["status"] == "killed"


class TestPayoutRoutes:
    def test_get_payout_history(self, client, fake_service):
        payout = MagicMock()
        payout.id = uuid4()
        payout.amount = Decimal("550.00")
        payout.currency = "USD"
        payout.status = MagicMock()
        payout.status.value = "paid"
        payout.cycle_start = None
        payout.cycle_end = None
        fake_service.get_payout_history = AsyncMock(return_value=[payout])

        response = client.get(f"/api/v1/billing/payouts/{uuid4()}")

        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["amount"] == "550.00"

    def test_creator_share_floor(self, client):
        response = client.get("/api/v1/billing/creator-share?svod_revenue=1000")

        assert response.status_code == 200
        body = response.json()
        assert body["percentage"] == "55%"
        assert body["creator_share_floor"] == "550.00"

    def test_creator_share_requires_revenue(self, client):
        response = client.get("/api/v1/billing/creator-share")

        assert response.status_code == 422


class TestWebhookRoutes:
    def test_webhook_rejects_bad_signature(self, client):
        with patch(
            "app.api.webhook_routes.StripeClient.handle_webhook", side_effect=Exception("sig")
        ):
            from app.core.stripe_client import StripeError

            from app.api import webhook_routes

            real = webhook_routes.StripeClient.handle_webhook
            webhook_routes.StripeClient.handle_webhook = staticmethod(
                lambda *a, **k: (_ for _ in ()).throw(StripeError("Invalid signature"))
            )
            try:
                response = client.post(
                    "/api/v1/billing/webhooks/stripe",
                    json={"type": "checkout.session.completed"},
                    headers={"Stripe-Signature": "bad"},
                )
            finally:
                webhook_routes.StripeClient.handle_webhook = real

        assert response.status_code == 400

    def test_webhook_checkout_session_completed_svod(self, client, fake_service):
        event = {
            "id": "evt_checkout_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_1",
                    "client_reference_id": str(uuid4()),
                    "metadata": {"tier": "svod", "user_id": str(uuid4())},
                }
            },
        }
        with patch("app.api.webhook_routes.StripeClient.handle_webhook", return_value=event):
            response = client.post(
                "/api/v1/billing/webhooks/stripe", json=event, headers={"Stripe-Signature": "x"}
            )

        assert response.status_code == 200
        body = response.json()
        assert body["handled"] is True
        fake_service.sub_repo.get_by_user.assert_awaited_once()

    def test_webhook_idempotent_on_replay(self, client, fake_service):
        event = {
            "id": "evt_checkout_2",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_2",
                    "client_reference_id": str(uuid4()),
                    "metadata": {"tier": "svod", "user_id": str(uuid4())},
                }
            },
        }
        with patch("app.api.webhook_routes.StripeClient.handle_webhook", return_value=event):
            first = client.post(
                "/api/v1/billing/webhooks/stripe", json=event, headers={"Stripe-Signature": "x"}
            )
            second = client.post(
                "/api/v1/billing/webhooks/stripe", json=event, headers={"Stripe-Signature": "x"}
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["idempotent"] is True

    def test_webhook_unhandled_event_type(self, client, fake_service):
        event = {
            "id": "evt_unknown_1",
            "type": "charge.refunded",
            "data": {"object": {}},
        }
        with patch("app.api.webhook_routes.StripeClient.handle_webhook", return_value=event):
            response = client.post(
                "/api/v1/billing/webhooks/stripe", json=event, headers={"Stripe-Signature": "x"}
            )

        assert response.status_code == 200
        body = response.json()
        assert body["handled"] is False
