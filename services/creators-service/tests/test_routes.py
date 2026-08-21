"""Tests for Creators Service API routes.

Creators routes override the DB session via ``get_db`` and construct the
service inline with real repositories; tests therefore patch the
``CreatorService`` class used by the routes to return in-memory stand-ins.
The admin routes in ``creators_routes.py`` are mounted by ``main.py`` and
covered by ``TestAdminCreatorAuth`` ([#43]).
"""

import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from datetime import UTC, datetime
import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient

from app.api.creators_routes import current_user
from app.main import app


def make_acct(**overrides):
    acct = MagicMock()
    acct.id = uuid4()
    acct.user_id = uuid4()
    acct.display_name = "Jane Creator"
    acct.bio = "Making things"
    acct.region_code = "US"
    acct.currency = "USD"
    acct.stripe_connect_account_id = None
    acct.kyc_status = "pending"
    acct.kyc_verified_at = None
    acct.is_active = True
    acct.created_at = datetime.now(UTC)
    acct.updated_at = datetime.now(UTC)
    acct.per_minute_amount = 0.02
    acct.effective_from = datetime.now(UTC)
    acct.last_adjusted_at = None
    acct.reason = None
    acct.accrued_cents = 100
    acct.contributed_cents = 50
    acct.last_payout_at = None
    acct.creator_id = uuid4()
    for k, v in overrides.items():
        setattr(acct, k, v)
    return acct


@pytest.fixture
def auth_user_id():
    return uuid4()


@pytest.fixture
def client(auth_user_id):
    app.dependency_overrides.clear()
    app.dependency_overrides[current_user] = lambda: auth_user_id
    app.state.shutting_down = False
    # Initialize global locks for the test
    import app.main as main_module

    main_module._in_flight_lock = asyncio.Lock()
    main_module._shutdown_event = asyncio.Event()
    yield TestClient(app, base_url="http://localhost")
    app.dependency_overrides.clear()


@pytest.fixture
def service():
    """A CreatorService stand-in whose repo attributes are AsyncMocks."""
    mock = MagicMock()
    mock.acct_repo = MagicMock()
    mock.acct_repo.get_by_user = AsyncMock(return_value=None)
    mock.acct_repo.create = AsyncMock(return_value=make_acct())
    mock.get_profile = AsyncMock(return_value=make_acct())
    mock.update_profile = AsyncMock(return_value=make_acct())
    mock.get_floor = AsyncMock(return_value=make_acct())
    mock.pool_repo = MagicMock()
    mock.pool_repo.get_or_create = AsyncMock(return_value=make_acct())
    mock.ledger_repo = MagicMock()
    return mock


@pytest.fixture(autouse=True)
def patch_creator_service(monkeypatch, service):
    """Routes build CreatorService inline; patch the class itself."""
    import app.api.creators_routes as routes

    monkeypatch.setattr(routes, "CreatorService", lambda *a: service)
    yield service


class TestOnboard:
    def test_onboard_success(self, client):
        response = client.post(
            "/api/v1/creators/onboard",
            json={"display_name": "Jane", "bio": "hi", "region_code": "US", "currency": "USD"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["display_name"] == "Jane Creator"
        assert body["kyc_status"] == "pending"

    def test_onboard_already_exists_returns_409(self, client, service):
        service.acct_repo.get_by_user.return_value = make_acct()

        response = client.post("/api/v1/creators/onboard", json={"display_name": "Jane"})

        assert response.status_code == 409


class TestMe:
    def test_get_me_success(self, client):
        response = client.get("/api/v1/creators/me")

        assert response.status_code == 200
        assert response.json()["display_name"] == "Jane Creator"

    def test_get_me_not_found(self, client, service):
        service.get_profile.return_value = None

        response = client.get("/api/v1/creators/me")

        assert response.status_code == 404

    def test_update_me_success(self, client):
        response = client.put("/api/v1/creators/me", json={"display_name": "New Name"})

        assert response.status_code == 200
        assert response.json()["display_name"] == "Jane Creator"

    def test_update_me_not_found(self, client, service):
        service.update_profile.return_value = None

        response = client.put("/api/v1/creators/me", json={"display_name": "New Name"})

        assert response.status_code == 404


class TestFloorBalanceLedger:
    def test_get_floor_success(self, client):
        response = client.get("/api/v1/creators/me/floor")

        assert response.status_code == 200
        assert response.json()["per_minute_amount"] == 0.02

    def test_get_floor_missing_returns_404(self, client, service):
        service.get_floor.return_value = None

        response = client.get("/api/v1/creators/me/floor")

        assert response.status_code == 404

    def test_get_balance_success(self, client):
        response = client.get("/api/v1/creators/me/balance")

        assert response.status_code == 200
        assert response.json()["accrued_cents"] == 100

    def test_get_ledger_success(self, client, service):
        row = MagicMock()
        row.id = uuid4()
        row.creator_id = uuid4()
        row.idempotency_key = "period:2026-01"
        row.period_start = datetime.now(UTC)
        row.period_end = datetime.now(UTC)
        row.view_minutes = 5
        row.floor_cents = 1
        row.pool_topup_cents = 0
        row.share_cents = 2
        row.stripe_fee_cents = 0
        row.net_cents = 3
        row.stripe_transfer_id = None
        row.status = "accrued"
        row.created_at = datetime.now(UTC)
        result = MagicMock()
        result.scalars.return_value.all.return_value = [row]
        service.ledger_repo.session.execute = AsyncMock(return_value=result)

        response = client.get("/api/v1/creators/me/ledger")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["net_cents"] == 3


class TestPayouts:
    def test_accrue_payout_success(self, client, service):
        row = MagicMock()
        row.id = uuid4()
        row.creator_id = uuid4()
        row.idempotency_key = "period:2026-01"
        row.period_start = datetime.now(UTC)
        row.period_end = datetime.now(UTC)
        row.view_minutes = 10
        row.floor_cents = 2
        row.pool_topup_cents = 0
        row.share_cents = 5
        row.stripe_fee_cents = 1
        row.net_cents = 6
        row.stripe_transfer_id = None
        row.status = "accrued"
        row.created_at = datetime.now(UTC)
        service.accrue_payout = AsyncMock(return_value=row)

        response = client.post(
            "/api/v1/creators/me/payouts",
            json={
                "period_start": "2026-01-01T00:00:00+00:00",
                "period_end": "2026-01-31T00:00:00+00:00",
                "view_minutes": 10,
                "earned_cents": 6,
                "stripe_fee_cents": 1,
            },
        )

        assert response.status_code == 200
        assert response.json()["net_cents"] == 6
        service.accrue_payout.assert_awaited_once()

    def test_accrue_payout_no_profile_returns_404(self, client, service):
        service.get_profile.return_value = None

        response = client.post(
            "/api/v1/creators/me/payouts",
            json={
                "period_start": "2026-01-01T00:00:00+00:00",
                "period_end": "2026-01-31T00:00:00+00:00",
            },
        )

        assert response.status_code == 404


class TestCurrentUserAuth:
    """Regression for [#48]: ``current_user`` used to return a hard-coded UUID
    instead of verifying the JWT, so every creator endpoint accepted anonymous
    callers. These tests clear the auth override and prove the endpoints now
    hold the 401 authentication boundary.
    """

    def test_no_token_rejected_on_me_profile(self, client, service):
        service.get_profile.return_value = make_acct()
        app.dependency_overrides.pop(current_user, None)
        response = client.get("/api/v1/creators/me")
        assert response.status_code == 401

    def test_no_token_rejected_on_create_profile(self, client, service):
        app.dependency_overrides.pop(current_user, None)
        response = client.post(
            "/api/v1/creators/onboard",
            json={
                "display_name": "Jane",
                "region_code": "US",
                "currency": "USD",
            },
        )
        assert response.status_code == 401


class TestAdminCreatorAuth:
    """[#43] Admin /admin/creators routes must reject unauthenticated callers
    and non-admin tokens (401/403) and accept admin-role tokens. The admin
    router is now mounted in main.py; the gateway is a transparent proxy so
    this service is the enforcement boundary.
    """

    MILESTONE_URL = "/api/v1/admin/creators/11111111-1111-1111-1111-111111111111/milestones"

    def _mint(self, role: str) -> str:
        from datetime import timedelta

        from jose import jwt

        from app.core.settings import settings

        payload = {
            "sub": str(uuid4()),
            "type": "access",
            "role": role,
            "aud": settings.JWT_AUDIENCE,
            "iss": settings.JWT_ISSUER,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=15),
        }
        return jwt.encode(
            payload,
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

    def _make_milestone(self):
        ms = MagicMock()
        ms.id = uuid4()
        ms.creator_id = uuid4()
        ms.title = "Milestone"
        ms.total_cents = 100_00
        ms.currency = "USD"
        ms.goal = "release"
        ms.kill_reason = None
        ms.status = "draft"
        ms.created_at = datetime.now(UTC).replace(tzinfo=None)
        ms.updated_at = datetime.now(UTC).replace(tzinfo=None)
        return ms

    def test_no_token_rejected_401(self, client, service):
        response = client.post(
            self.MILESTONE_URL, json={"title": "x", "total_cents": 100, "currency": "USD"}
        )
        assert response.status_code == 401

    def test_garbage_token_rejected_401(self, client, service):
        response = client.post(
            self.MILESTONE_URL,
            json={"title": "x", "total_cents": 100, "currency": "USD"},
            headers={"Authorization": "Bearer garbage"},
        )
        assert response.status_code == 401

    def test_regular_user_rejected_403(self, client, service):
        token = self._mint("user")
        response = client.post(
            self.MILESTONE_URL,
            json={"title": "x", "total_cents": 100, "currency": "USD"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    def test_admin_token_accepted_200(self, client, service):
        token = self._mint("admin")
        service.acct_repo.get = AsyncMock(return_value=make_acct())
        service.create_milestone = AsyncMock(return_value=self._make_milestone())
        response = client.post(
            self.MILESTONE_URL,
            json={"title": "x", "total_cents": 100, "currency": "USD"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    def test_admin_release_tranche_requires_admin(self, client, service):
        token = self._mint("user")
        response = client.post(
            "/api/v1/admin/creators/11111111-1111-1111-1111-111111111111/milestones/22222222-2222-2222-2222-222222222222/release",
            json={"threshold": 100},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
