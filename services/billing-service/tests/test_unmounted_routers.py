"""Tests for the two routers that are defined but never mounted in ``main.py``.

``app/api/routes/billing_tiers.py`` and ``app/api/routes/commerce.py`` are
constructed in their modules and never passed to ``app.include_router``, so no
request can reach them in the running service. The product decision was "no
refactor", so these tests mount each router into a *throwaway* FastAPI app
with a fake async session — the handlers are exercised without changing
production wiring.

The first test in each class asserts the unmounted state on purpose: if
someone does wire the router into ``main.py`` later, that test fails and
points at the tests below that assumed it was dead code.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.billing_tiers import router as tiers_router
from app.api.routes.commerce import router as commerce_router
from app.core.database import get_db
from app.main import create_app
from app.models.subscription_tier import SubscriptionTier


def _throwaway_app(router, db) -> FastAPI:
    """A bare FastAPI app that mounts `router` and fakes out the DB session."""
    app = FastAPI()

    async def _override():
        return db

    app.dependency_overrides[get_db] = _override
    app.include_router(router)
    return app


def _fake_session() -> AsyncMock:
    """An AsyncSession double whose refresh() backfills server defaults.

    The handler builds the row from request data only, so ``id`` and
    ``created_at`` (both column defaults) are unset until ``refresh()`` — which
    is exactly what a real session would populate.
    """
    db = AsyncMock()
    id_ = uuid4()
    created = datetime(2026, 1, 2, 3, 4, 5)

    async def _refresh(obj):
        obj.id = id_
        obj.created_at = created
        obj.cancellation_policy = "easy_cancel"
        obj.refund_days = 14
        obj.price_change_notice_days = 30
        obj.is_active = True

    db.refresh = AsyncMock(side_effect=_refresh)
    return db


async def _post(app: FastAPI, path: str, json: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.post(path, json=json)


def _app_paths(app: FastAPI) -> set[str]:
    """Every path the app serves, including nested/included routers."""
    paths = {getattr(route, "path", "") for route in app.routes}
    paths |= set(app.openapi().get("paths", {}))
    return {p for p in paths if p}


# ---------------------------------------------------------------------------
# Unmounted state (the reason this file exists)
# ---------------------------------------------------------------------------


class TestRoutersAreNotMountedInProduction:
    def test_tiers_router_is_absent_from_the_real_app(self):
        app = create_app()
        offenders = [p for p in _app_paths(app) if p.startswith("/tiers")]
        assert not offenders, (
            f"the tiers router is now mounted in main.py ({offenders}) — update this "
            "file and the unmounted-routers contract"
        )

    def test_commerce_router_is_absent_from_the_real_app(self):
        app = create_app()
        offenders = [p for p in _app_paths(app) if p.startswith("/commerce")]
        assert not offenders, (
            f"the commerce router is now mounted in main.py ({offenders}) — update this file"
        )

    def test_the_api_prefix_does_not_reach_them_either(self):
        # The routers carry a bare prefix, so a careless future mount under
        # /api/v1 would still expose them; assert the full path space.
        app = create_app()
        for path in _app_paths(app):
            assert not path.endswith("/tiers"), path
            assert not path.endswith("/commerce"), path

    def test_tiers_router_has_its_documented_prefix(self):
        assert tiers_router.prefix == "/tiers"

    def test_commerce_router_has_its_documented_prefix(self):
        assert commerce_router.prefix == "/commerce"

    def test_each_router_exposes_exactly_one_route(self):
        assert len(tiers_router.routes) == 1
        assert len(commerce_router.routes) == 1


# ---------------------------------------------------------------------------
# billing_tiers router
# ---------------------------------------------------------------------------


class TestBillingTiersRouter:
    async def test_create_tier_persists_and_returns_201(self):
        db = _fake_session()
        app = _throwaway_app(tiers_router, db)

        resp = await _post(
            app,
            "/tiers",
            {
                "name": "premium",
                "jurisdiction": "EU",
                "price_cents": 999,
                "currency": "EUR",
                "tax_rate": 0.2,
            },
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "premium"
        assert body["jurisdiction"] == "EU"
        assert body["price_cents"] == 999
        assert body["currency"] == "EUR"
        assert body["tax_rate"] == 0.2

    async def test_create_tier_adds_the_model_to_the_session(self):
        db = _fake_session()
        app = _throwaway_app(tiers_router, db)

        await _post(
            app,
            "/tiers",
            {"name": "basic", "jurisdiction": "US", "price_cents": 0, "currency": "USD"},
        )

        db.add.assert_called_once()
        added = db.add.call_args.args[0]
        assert isinstance(added, SubscriptionTier)
        assert added.name == "basic"
        assert added.jurisdiction == "US"
        assert added.price_cents == 0
        assert added.currency == "USD"

    async def test_create_tier_commits_before_responding(self):
        db = _fake_session()
        app = _throwaway_app(tiers_router, db)

        await _post(
            app,
            "/tiers",
            {"name": "family", "jurisdiction": "IN", "price_cents": 1500, "currency": "INR"},
        )

        db.flush.assert_awaited_once()
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once()

    async def test_defaults_from_the_schema_are_applied(self):
        db = _fake_session()
        app = _throwaway_app(tiers_router, db)

        resp = await _post(
            app,
            "/tiers",
            {"name": "basic", "jurisdiction": "US", "price_cents": 100, "currency": "USD"},
        )

        body = resp.json()
        assert body["tax_rate"] == 0.2
        assert body["trial_days"] == 0
        assert body["cooling_off_days"] == 14

    async def test_response_model_omits_the_refund_days_field(self):
        # SubscriptionTierResponse has no refund_days, so the request default is
        # stored on the row but never echoed back. Pinned because a client
        # cannot learn its refund window from this endpoint.
        db = _fake_session()
        app = _throwaway_app(tiers_router, db)

        resp = await _post(
            app,
            "/tiers",
            {"name": "basic", "jurisdiction": "US", "price_cents": 100, "currency": "USD"},
        )

        assert "refund_days" not in resp.json()
        assert set(resp.json()) == {
            "id",
            "name",
            "jurisdiction",
            "price_cents",
            "currency",
            "tax_rate",
            "trial_days",
            "cooling_off_days",
            "created_at",
        }

    @pytest.mark.parametrize(
        "payload,reason",
        [
            ({"name": "gold", "jurisdiction": "US", "price_cents": 1}, "name pattern"),
            ({"name": "basic", "price_cents": 1}, "missing jurisdiction"),
            ({"name": "basic", "jurisdiction": "US"}, "missing price_cents"),
            ({"name": "basic", "jurisdiction": "US", "price_cents": -1}, "negative price"),
            (
                {"name": "basic", "jurisdiction": "US", "price_cents": 1, "currency": "EU"},
                "currency length",
            ),
            (
                {"name": "basic", "jurisdiction": "US", "price_cents": 1, "tax_rate": 1.5},
                "tax_rate above 1",
            ),
            (
                {"name": "basic", "jurisdiction": "US", "price_cents": 1, "trial_days": -1},
                "negative trial_days",
            ),
        ],
    )
    async def test_invalid_tier_payloads_are_rejected_with_422(self, payload, reason):
        db = _fake_session()
        app = _throwaway_app(tiers_router, db)

        resp = await _post(app, "/tiers", payload)

        assert resp.status_code == 422, reason
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_tiers_route_is_not_in_the_openapi_schema_of_a_bare_mount(self):
        # The response model is the documented shape; confirm it is applied.
        db = _fake_session()
        app = _throwaway_app(tiers_router, db)
        schema = app.openapi()
        operation = schema["paths"]["/tiers"]["post"]
        assert operation["responses"]["201"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("/SubscriptionTierResponse")


# ---------------------------------------------------------------------------
# commerce router
# ---------------------------------------------------------------------------


class TestCommerceRouter:
    async def test_create_commerce_returns_the_stub_envelope(self):
        app = _throwaway_app(commerce_router, AsyncMock())

        resp = await _post(app, "/commerce", {"invoice_id": "in_1"})

        assert resp.status_code == 201
        assert resp.json() == {
            "id": "test",
            "invoice_id": "in_1",
            "tax_calculated": True,
            "ledger": True,
        }

    async def test_invoice_id_is_echoed_from_the_body(self):
        app = _throwaway_app(commerce_router, AsyncMock())

        resp = await _post(app, "/commerce", {"invoice_id": "in_abc-123"})

        assert resp.json()["invoice_id"] == "in_abc-123"

    async def test_missing_invoice_id_becomes_null(self):
        # The body is a bare dict, so there is no field-level validation; a
        # missing key surfaces as null rather than a 422.
        app = _throwaway_app(commerce_router, AsyncMock())

        resp = await _post(app, "/commerce", {})

        assert resp.status_code == 201
        assert resp.json()["invoice_id"] is None

    async def test_commerce_route_touches_no_database(self):
        db = AsyncMock()
        app = _throwaway_app(commerce_router, db)

        resp = await _post(app, "/commerce", {"invoice_id": "in_1"})

        assert resp.status_code == 201
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_commerce_accepts_an_arbitrary_body_shape(self):
        app = _throwaway_app(commerce_router, AsyncMock())

        resp = await _post(
            app, "/commerce", {"invoice_id": "in_2", "unexpected": {"nested": True}}
        )

        assert resp.status_code == 201
        assert resp.json()["invoice_id"] == "in_2"

    async def test_non_object_body_is_rejected(self):
        app = _throwaway_app(commerce_router, AsyncMock())

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/commerce", json=["not", "an", "object"])

        assert resp.status_code == 422
