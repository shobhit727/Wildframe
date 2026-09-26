"""Tests for creators-service routers that production ``main.py`` never mounts,
plus the JWT guards on the creator/admin routes.

* ``app/api/routes/onboarding.py`` and ``app/api/routes/payout.py`` are
  compliance routers that exist but are not registered on the app. They mount
  cleanly, so they are driven over HTTP against a real (SQLite) session.
* ``current_user`` / ``current_admin`` are the service-boundary authz checks
  (the api-gateway is a transparent proxy, so nothing upstream enforces them).
  They are called directly, and the admin routes are mounted against a service
  double to cover their 404/200 branches.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.creators_routes import (
    _to_ms_response,
    _to_t_response,
    admin_router,
    current_admin,
    current_user,
    get_service,
    router as user_router,
)
from app.api.routes import onboarding as onboarding_routes
from app.api.routes import payout as payout_routes
from app.core.database import get_db
from app.core.settings import settings
from app.models import CreatorOnboarding, CreatorPayout

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
NOW = datetime.now(UTC)


# ------------------------------------------------------------------- fixtures
@pytest_asyncio.fixture
async def session():
    """In-memory database holding the onboarding and payout tables."""
    from app.models import Base

    engine = create_async_engine(SQLITE_URL, echo=False)
    async with engine.begin() as conn:
        for table in (Base.metadata.tables["creator_onboarding"], Base.metadata.tables["creator_payouts"]):
            await conn.run_sync(lambda sync_conn, t=table: t.drop(sync_conn, checkfirst=True))
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn, checkfirst=True))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            yield db
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(session):
    """Throwaway app mounting the two unmounted compliance routers."""
    application = FastAPI()
    application.include_router(onboarding_routes.router)
    application.include_router(payout_routes.router)

    async def _override_get_db():
        yield session

    application.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
    ) as ac:
        yield ac


# ------------------------------------------------------------------- onboarding
class TestOnboardingRouter:
    async def test_create_onboarding_returns_the_pending_record(self, client, session):
        user_id = uuid4()

        response = await client.post(
            "/onboarding",
            json={
                "user_id": str(user_id),
                "kyc_type": "individual",
                "stripe_account_id": "acct_123",
                "tax_form_type": "W-9",
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["user_id"] == str(user_id)
        assert body["kyc_status"] == "pending"
        assert body["stripe_account_id"] == "acct_123"
        assert body["id"]

        stored = await session.get(CreatorOnboarding, UUID(body["id"]))
        assert stored.kyc_type == "individual"
        assert stored.tax_form_type == "W-9"
        assert stored.kyc_status == "pending"
        assert stored.tax_form_verified is False
        assert stored.living_wage_cents == 0

    async def test_stripe_account_and_tax_form_are_optional(self, client, session):
        response = await client.post(
            "/onboarding", json={"user_id": str(uuid4()), "kyc_type": "entity"}
        )

        assert response.status_code == 201
        assert response.json()["stripe_account_id"] is None
        stored = await session.get(CreatorOnboarding, UUID(response.json()["id"]))
        assert stored.tax_form_type is None
        assert stored.bank_verified is False

    @pytest.mark.parametrize("kyc_type", ["person", "Individual", "", "llc"])
    async def test_unknown_kyc_type_is_rejected(self, client, kyc_type):
        response = await client.post(
            "/onboarding", json={"user_id": str(uuid4()), "kyc_type": kyc_type}
        )

        assert response.status_code == 422

    @pytest.mark.parametrize("tax_form", ["W-8BEN-E", "w-9", "1040"])
    async def test_unknown_tax_form_is_rejected(self, client, tax_form):
        response = await client.post(
            "/onboarding",
            json={
                "user_id": str(uuid4()),
                "kyc_type": "individual",
                "tax_form_type": tax_form,
            },
        )

        assert response.status_code == 422

    async def test_malformed_user_id_is_rejected(self, client):
        response = await client.post(
            "/onboarding", json={"user_id": "not-a-uuid", "kyc_type": "individual"}
        )

        assert response.status_code == 422

    async def test_duplicate_onboarding_has_no_conflict_guard(self, client, session):
        """Reported behaviour: ``user_id`` is UNIQUE but the route does not
        translate the IntegrityError, so a replay surfaces as a 500."""
        user_id = str(uuid4())
        payload = {"user_id": user_id, "kyc_type": "individual"}

        first = await client.post("/onboarding", json=payload)
        second = await client.post("/onboarding", json=payload)

        assert first.status_code == 201
        assert second.status_code == 500
        await session.rollback()


# ---------------------------------------------------------------------- payout
class TestPayoutRouter:
    async def test_create_payout_defaults_to_usd_net_30(self, client, session):
        creator_id = uuid4()

        response = await client.post(
            "/payouts", json={"creator_id": str(creator_id), "amount_cents": 250_00}
        )

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "pending"
        assert body["id"]

        stored = await session.get(CreatorPayout, UUID(body["id"]))
        assert stored.creator_id == creator_id
        assert stored.amount_cents == 250_00
        assert stored.currency == "USD"
        assert stored.schedule == "net-30"
        assert stored.tax_withheld_cents == 0
        assert stored.stripe_transfer_id is None

    @pytest.mark.parametrize("schedule", ["net-15", "weekly", "net30"])
    async def test_unknown_schedule_is_rejected(self, client, schedule):
        response = await client.post(
            "/payouts",
            json={"creator_id": str(uuid4()), "amount_cents": 100, "schedule": schedule},
        )

        assert response.status_code == 422

    @pytest.mark.parametrize("currency", ["US", "USDD", "EURO"])
    async def test_currency_must_be_a_three_letter_code(self, client, currency):
        response = await client.post(
            "/payouts",
            json={"creator_id": str(uuid4()), "amount_cents": 100, "currency": currency},
        )

        assert response.status_code == 422

    async def test_currency_case_is_not_validated(self, client, session):
        """Only the length is checked — a lowercase code is accepted verbatim."""
        response = await client.post(
            "/payouts",
            json={"creator_id": str(uuid4()), "amount_cents": 100, "currency": "eur"},
        )

        assert response.status_code == 201
        stored = await session.get(CreatorPayout, UUID(response.json()["id"]))
        assert stored.currency == "eur"

    @pytest.mark.parametrize("amount", [-1, -100_00])
    async def test_negative_amounts_are_rejected(self, client, amount):
        response = await client.post(
            "/payouts", json={"creator_id": str(uuid4()), "amount_cents": amount}
        )

        assert response.status_code == 422

    async def test_multi_currency_payout_is_accepted(self, client, session):
        response = await client.post(
            "/payouts",
            json={
                "creator_id": str(uuid4()),
                "amount_cents": 1000,
                "currency": "EUR",
                "schedule": "net-60",
            },
        )

        stored = await session.get(CreatorPayout, UUID(response.json()["id"]))
        assert stored.currency == "EUR"
        assert stored.schedule == "net-60"


# ------------------------------------------------------------------ jwt guards
def _token(**claims) -> str:
    payload = {
        "sub": str(uuid4()),
        "type": "access",
        "role": "user",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=15),
    }
    payload.update(claims)
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


class TestCurrentUser:
    async def test_returns_the_subject_claim(self):
        sub = str(uuid4())

        assert await current_user(f"Bearer {_token(sub=sub)}") == UUID(sub)

    async def test_falls_back_to_the_user_id_claim(self):
        user_id = str(uuid4())

        token = jwt.encode(
            {
                "user_id": user_id,
                "type": "access",
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
                "exp": datetime.now(UTC) + timedelta(minutes=15),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        assert await current_user(f"Bearer {token}") == UUID(user_id)

    @pytest.mark.parametrize("header", [None, "", "Basic abc", "bearer lower-case"])
    async def test_missing_or_non_bearer_header_is_rejected(self, header):
        with pytest.raises(Exception) as exc:
            await current_user(header)

        assert exc.value.status_code == 401
        assert exc.value.detail == "Missing or invalid authorization header"

    async def test_garbage_token_is_rejected(self):
        with pytest.raises(Exception) as exc:
            await current_user("Bearer not-a-jwt")

        assert exc.value.detail == "Invalid token"

    async def test_refresh_token_is_rejected(self):
        with pytest.raises(Exception) as exc:
            await current_user(f"Bearer {_token(type='refresh')}")

        assert exc.value.detail == "Invalid token type"

    async def test_token_without_any_subject_is_rejected(self):
        token = jwt.encode(
            {
                "type": "access",
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
                "exp": datetime.now(UTC) + timedelta(minutes=15),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        with pytest.raises(Exception) as exc:
            await current_user(f"Bearer {token}")

        assert exc.value.detail == "Invalid token subject"

    async def test_non_uuid_subject_is_rejected(self):
        with pytest.raises(Exception) as exc:
            await current_user(f"Bearer {_token(sub='not-a-uuid')}")

        assert exc.value.detail == "Invalid token subject"

    async def test_token_with_the_wrong_audience_is_rejected(self):
        token = jwt.encode(
            {
                "sub": str(uuid4()),
                "type": "access",
                "aud": "some-other-api",
                "iss": settings.JWT_ISSUER,
                "exp": datetime.now(UTC) + timedelta(minutes=15),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        with pytest.raises(Exception) as exc:
            await current_user(f"Bearer {token}")

        assert exc.value.status_code == 401


class TestCurrentAdmin:
    async def test_returns_the_subject_for_an_admin_token(self):
        sub = str(uuid4())

        assert await current_admin(f"Bearer {_token(role='admin', sub=sub)}") == UUID(sub)

    @pytest.mark.parametrize("header", [None, "", "Basic abc"])
    async def test_missing_or_non_bearer_header_is_rejected(self, header):
        with pytest.raises(Exception) as exc:
            await current_admin(header)

        assert exc.value.status_code == 401

    async def test_garbage_token_is_rejected(self):
        with pytest.raises(Exception) as exc:
            await current_admin("Bearer not-a-jwt")

        assert exc.value.detail == "Invalid token"

    async def test_refresh_token_is_rejected(self):
        with pytest.raises(Exception) as exc:
            await current_admin(f"Bearer {_token(role='admin', type='refresh')}")

        assert exc.value.detail == "Invalid token type"

    async def test_non_admin_token_is_forbidden(self):
        with pytest.raises(Exception) as exc:
            await current_admin(f"Bearer {_token(role='user')}")

        assert exc.value.status_code == 403
        assert exc.value.detail == "Admin privileges required"

    async def test_admin_token_without_a_subject_is_rejected(self):
        token = jwt.encode(
            {
                "role": "admin",
                "type": "access",
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
                "exp": datetime.now(UTC) + timedelta(minutes=15),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        with pytest.raises(Exception) as exc:
            await current_admin(f"Bearer {token}")

        assert exc.value.detail == "Invalid token subject"

    async def test_admin_token_with_a_non_uuid_subject_is_rejected(self):
        with pytest.raises(Exception) as exc:
            await current_admin(f"Bearer {_token(role='admin', sub='admin')}")

        assert exc.value.detail == "Invalid token subject"


# ---------------------------------------------------------- user route guards
@pytest.fixture
def user_service():
    service = MagicMock()
    acct = MagicMock()
    acct.id = uuid4()
    acct.user_id = uuid4()
    acct.display_name = "Jane"
    acct.bio = "Films"
    acct.region_code = "US"
    acct.currency = "USD"
    acct.stripe_connect_account_id = None
    acct.kyc_status = "pending"
    acct.kyc_verified_at = None
    acct.is_active = True
    acct.created_at = NOW
    acct.updated_at = NOW
    service.acct_repo = MagicMock()
    service.acct_repo.get_by_user = AsyncMock(return_value=acct)
    service.get_profile = AsyncMock(return_value=acct)
    service.get_floor = AsyncMock(return_value=None)
    service.update_profile = AsyncMock(return_value=acct)
    service.accrue_payout = AsyncMock()
    service.pool_repo = MagicMock()
    service.pool_repo.get_or_create = AsyncMock(return_value=MagicMock())
    service.ledger_repo = MagicMock()
    service.ledger_repo.session = MagicMock()
    service.ledger_repo.session.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(all=MagicMock(return_value=[])))
    )
    return service


@pytest_asyncio.fixture
async def user_client(user_service):
    application = FastAPI()
    application.include_router(user_router)
    application.dependency_overrides[get_service] = lambda: user_service
    application.dependency_overrides[current_user] = lambda: uuid4()
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as ac:
        yield ac


class TestUserRouteGuards:
    """Every ``/me`` endpoint must 404 (or 403) instead of leaking a crash."""

    async def test_floor_404s_for_an_unknown_creator(self, user_client, user_service):
        user_service.get_profile.return_value = None

        response = await user_client.get("/api/v1/creators/me/floor")

        assert response.status_code == 404
        assert response.json()["detail"] == "creator not found"
        user_service.get_floor.assert_not_awaited()

    async def test_floor_404s_when_no_floor_is_configured(self, user_client, user_service):
        user_service.get_floor.return_value = None

        response = await user_client.get("/api/v1/creators/me/floor")

        assert response.status_code == 404
        assert response.json()["detail"] == "no floor configured"

    async def test_balance_404s_for_an_unknown_creator(self, user_client, user_service):
        user_service.get_profile.return_value = None

        response = await user_client.get("/api/v1/creators/me/balance")

        assert response.status_code == 404
        user_service.pool_repo.get_or_create.assert_not_awaited()

    async def test_ledger_404s_for_an_unknown_creator(self, user_client, user_service):
        user_service.get_profile.return_value = None

        response = await user_client.get("/api/v1/creators/me/ledger")

        assert response.status_code == 404
        user_service.ledger_repo.session.execute.assert_not_awaited()

    async def test_payout_accrual_404s_for_an_unknown_creator(self, user_client, user_service):
        user_service.get_profile.return_value = None

        response = await user_client.post(
            "/api/v1/creators/me/payouts",
            json={
                "period_start": "2026-01-01T00:00:00+00:00",
                "period_end": "2026-01-31T00:00:00+00:00",
            },
        )

        assert response.status_code == 404
        user_service.accrue_payout.assert_not_awaited()

    async def test_payout_accrual_403s_for_a_suspended_creator(
        self, user_client, user_service
    ):
        user_service.get_profile.return_value.is_active = False

        response = await user_client.post(
            "/api/v1/creators/me/payouts",
            json={
                "period_start": "2026-01-01T00:00:00+00:00",
                "period_end": "2026-01-31T00:00:00+00:00",
            },
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "creator suspended"

    async def test_payout_accrual_403s_when_the_service_reports_suspension(
        self, user_client, user_service
    ):
        from app.models import CreatorSuspendedError

        user_service.accrue_payout.side_effect = CreatorSuspendedError("suspended mid-flight")

        response = await user_client.post(
            "/api/v1/creators/me/payouts",
            json={
                "period_start": "2026-01-01T00:00:00+00:00",
                "period_end": "2026-01-31T00:00:00+00:00",
            },
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "creator suspended"

    async def test_ledger_returns_an_empty_list_for_a_fresh_creator(self, user_client):
        response = await user_client.get("/api/v1/creators/me/ledger")

        assert response.status_code == 200
        assert response.json() == []


# ---------------------------------------------------------------- admin routes
def make_milestone(creator_id):
    now = datetime.now(UTC).replace(tzinfo=None)
    ms = MagicMock()
    ms.id = uuid4()
    ms.creator_id = creator_id
    ms.title = "Launch film"
    ms.total_cents = 500_00
    ms.currency = "USD"
    ms.goal = "release"
    ms.kill_reason = None
    ms.status = "draft"
    ms.created_at = now
    ms.updated_at = now
    return ms


def make_tranche(milestone_id):
    now = datetime.now(UTC).replace(tzinfo=None)
    t = MagicMock()
    t.id = uuid4()
    t.milestone_id = milestone_id
    t.threshold = 50
    t.amount_cents = 250_00
    t.status = "released"
    t.release_condition = "views"
    t.released_at = now
    return t


@pytest.fixture
def admin_service():
    service = MagicMock()
    service.acct_repo = MagicMock()
    service.acct_repo.get = AsyncMock(return_value=MagicMock())
    service.milestone_repo = MagicMock()
    service.milestone_repo.get = AsyncMock(return_value=None)
    service.create_milestone = AsyncMock()
    service.add_tranche = AsyncMock()
    service.release_tranche = AsyncMock(return_value=None)
    service.kill_milestone = AsyncMock()
    return service


@pytest_asyncio.fixture
async def admin_client(admin_service):
    application = FastAPI()
    application.include_router(admin_router)
    application.dependency_overrides[get_service] = lambda: admin_service
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as ac:
        yield ac


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(role='admin')}"}


class TestAdminRoutes:
    async def test_create_milestone_for_an_unknown_creator_returns_404(
        self, admin_client, admin_service
    ):
        admin_service.acct_repo.get.return_value = None

        response = await admin_client.post(
            f"/api/v1/admin/creators/{uuid4()}/milestones",
            json={"title": "Launch film"},
            headers=_admin_headers(),
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "creator not found"
        admin_service.create_milestone.assert_not_awaited()

    async def test_create_milestone_returns_the_created_milestone(
        self, admin_client, admin_service
    ):
        creator_id = uuid4()
        admin_service.create_milestone.return_value = make_milestone(creator_id)

        response = await admin_client.post(
            f"/api/v1/admin/creators/{creator_id}/milestones",
            json={"title": "Launch film", "total_cents": 500_00, "currency": "USD"},
            headers=_admin_headers(),
        )

        body = response.json()
        assert response.status_code == 200
        assert body["title"] == "Launch film"
        assert body["status"] == "draft"
        assert body["total_cents"] == 500_00

    async def test_add_tranche_requires_a_matching_milestone(
        self, admin_client, admin_service
    ):
        admin_service.milestone_repo.get.return_value = None

        response = await admin_client.post(
            f"/api/v1/admin/creators/{uuid4()}/milestones/{uuid4()}/tranches",
            json={"threshold": 50, "amount_cents": 100},
            headers=_admin_headers(),
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "milestone not found"
        admin_service.add_tranche.assert_not_awaited()

    async def test_add_tranche_rejects_a_milestone_of_another_creator(
        self, admin_client, admin_service
    ):
        admin_service.milestone_repo.get.return_value = make_milestone(uuid4())

        response = await admin_client.post(
            f"/api/v1/admin/creators/{uuid4()}/milestones/{uuid4()}/tranches",
            json={"threshold": 50, "amount_cents": 100},
            headers=_admin_headers(),
        )

        assert response.status_code == 404

    async def test_add_tranche_returns_the_new_tranche(self, admin_client, admin_service):
        creator_id, milestone_id = uuid4(), uuid4()
        admin_service.milestone_repo.get.return_value = make_milestone(creator_id)
        admin_service.add_tranche.return_value = make_tranche(milestone_id)

        response = await admin_client.post(
            f"/api/v1/admin/creators/{creator_id}/milestones/{milestone_id}/tranches",
            json={"threshold": 50, "amount_cents": 250_00, "release_condition": "views"},
            headers=_admin_headers(),
        )

        body = response.json()
        assert response.status_code == 200
        assert body["threshold"] == 50
        assert body["status"] == "released"
        assert body["milestone_id"] == str(milestone_id)

    async def test_release_tranche_requires_a_matching_milestone(
        self, admin_client, admin_service
    ):
        admin_service.milestone_repo.get.return_value = None

        response = await admin_client.post(
            f"/api/v1/admin/creators/{uuid4()}/milestones/{uuid4()}/release",
            json=50,
            headers=_admin_headers(),
        )

        assert response.status_code == 404
        admin_service.release_tranche.assert_not_awaited()

    async def test_release_tranche_returns_404_when_no_tranche_matches(
        self, admin_client, admin_service
    ):
        creator_id, milestone_id = uuid4(), uuid4()
        admin_service.milestone_repo.get.return_value = make_milestone(creator_id)
        admin_service.release_tranche.return_value = None

        response = await admin_client.post(
            f"/api/v1/admin/creators/{creator_id}/milestones/{milestone_id}/release",
            json=50,
            headers=_admin_headers(),
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "tranche not found"

    async def test_release_tranche_returns_the_released_tranche(
        self, admin_client, admin_service
    ):
        creator_id, milestone_id = uuid4(), uuid4()
        admin_service.milestone_repo.get.return_value = make_milestone(creator_id)
        admin_service.add_tranche.return_value = make_tranche(milestone_id)
        admin_service.release_tranche.return_value = make_tranche(milestone_id)

        response = await admin_client.post(
            f"/api/v1/admin/creators/{creator_id}/milestones/{milestone_id}/release",
            json=50,
            headers=_admin_headers(),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "released"

    async def test_kill_milestone_requires_a_matching_milestone(
        self, admin_client, admin_service
    ):
        admin_service.milestone_repo.get.return_value = None

        response = await admin_client.post(
            f"/api/v1/admin/creators/{uuid4()}/milestones/{uuid4()}/kill",
            json="budget",
            headers=_admin_headers(),
        )

        assert response.status_code == 404
        admin_service.kill_milestone.assert_not_awaited()

    async def test_kill_milestone_returns_the_killed_milestone(
        self, admin_client, admin_service
    ):
        creator_id, milestone_id = uuid4(), uuid4()
        killed = make_milestone(creator_id)
        killed.status = "killed"
        killed.kill_reason = "budget"
        admin_service.milestone_repo.get.return_value = make_milestone(creator_id)
        admin_service.kill_milestone.return_value = killed

        response = await admin_client.post(
            f"/api/v1/admin/creators/{creator_id}/milestones/{milestone_id}/kill",
            json="budget",
            headers=_admin_headers(),
        )

        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "killed"
        assert body["kill_reason"] == "budget"
        admin_service.kill_milestone.assert_awaited_once_with(milestone_id, reason="budget")

    async def test_admin_routes_reject_non_admin_tokens(self, admin_client):
        response = await admin_client.post(
            f"/api/v1/admin/creators/{uuid4()}/milestones",
            json={"title": "x"},
            headers={"Authorization": f"Bearer {_token(role='user')}"},
        )

        assert response.status_code == 403

    async def test_admin_routes_reject_anonymous_callers(self, admin_client):
        response = await admin_client.post(
            f"/api/v1/admin/creators/{uuid4()}/milestones", json={"title": "x"}
        )

        assert response.status_code == 401


class TestResponseMappers:
    def test_milestone_mapper_copies_every_field(self):
        creator_id = uuid4()
        ms = make_milestone(creator_id)

        mapped = _to_ms_response(ms)

        assert mapped.id == ms.id
        assert mapped.creator_id == creator_id
        assert mapped.title == "Launch film"
        assert mapped.status == "draft"
        assert mapped.goal == "release"
        assert mapped.kill_reason is None
        assert mapped.created_at == ms.created_at

    def test_tranche_mapper_copies_every_field(self):
        milestone_id = uuid4()
        tranche = make_tranche(milestone_id)

        mapped = _to_t_response(tranche)

        assert mapped.id == tranche.id
        assert mapped.milestone_id == milestone_id
        assert mapped.threshold == 50
        assert mapped.amount_cents == 250_00
        assert mapped.status == "released"
        assert mapped.release_condition == "views"
        assert mapped.released_at == tranche.released_at
