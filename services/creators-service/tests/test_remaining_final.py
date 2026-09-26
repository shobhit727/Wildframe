"""Remaining creators-service business logic: app/services.py.

``CreatorService`` is the payout/floor/milestone orchestrator. The profile,
floor and pool delegations, the suspension guard inside ``accrue_payout``, and
the inbound-event consumer (suspension processing plus the pending-event drain
with its retry bookkeeping) are all covered here against repository doubles.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from jose import jwt

from app.core.settings import settings
from app.models import CreatorSuspendedError
from app.models import MilestoneTranche
from app.repositories import InboundEventRepository
from app.services import CreatorService

# In-memory database for the repository tests (no PostgreSQL-specific SQL here).
SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def session():
    """In-memory session with the full creators schema created."""
    from app.models import Base
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(SQLITE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            yield db
    finally:
        await engine.dispose()

pytestmark = pytest.mark.unit

NOW = datetime(2026, 2, 1)


@pytest.fixture
def service():
    """CreatorService wired to repository doubles."""
    svc = CreatorService(
        acct_repo=pytest.importorskip("unittest.mock").AsyncMock(),
        floor_repo=pytest.importorskip("unittest.mock").AsyncMock(),
        pool_repo=pytest.importorskip("unittest.mock").AsyncMock(),
        milestone_repo=pytest.importorskip("unittest.mock").AsyncMock(),
        ledger_repo=pytest.importorskip("unittest.mock").AsyncMock(),
    )
    return svc


def _active_account(creator_id=None, is_active=True):
    acct = type("Acct", (), {})()
    acct.id = creator_id or uuid4()
    acct.is_active = is_active
    return acct


class TestProfileAndFloor:
    async def test_get_profile_delegates_to_the_account_repository(self, service):
        acct = _active_account()
        service.acct_repo.get_by_user = _async(return_value=acct)
        user_id = uuid4()

        assert await service.get_profile(user_id) is acct
        service.acct_repo.get_by_user.assert_awaited_once_with(user_id)

    async def test_update_profile_returns_none_for_an_unknown_creator(self, service):
        service.acct_repo.get_by_user = _async(return_value=None)

        assert await service.update_profile(uuid4(), display_name="New") is None
        service.acct_repo.update.assert_not_awaited()

    async def test_update_profile_forwards_only_the_given_fields(self, service):
        acct = _active_account()
        service.acct_repo.get_by_user = _async(return_value=acct)
        service.acct_repo.update = _async(return_value=acct)
        user_id = uuid4()

        result = await service.update_profile(user_id, display_name="New", region_code="EU")

        assert result is acct
        service.acct_repo.update.assert_awaited_once_with(
            acct, display_name="New", region_code="EU"
        )

    async def test_get_floor_delegates(self, service):
        creator_id = uuid4()
        service.floor_repo.get_floor_for_creator = _async(return_value=None)

        assert await service.get_floor(creator_id) is None
        service.floor_repo.get_floor_for_creator.assert_awaited_once_with(creator_id)

    async def test_set_floor_converts_to_decimal(self, service):
        creator_id = uuid4()
        service.floor_repo.set_floor = _async(return_value="floor")

        result = await service.set_floor(creator_id, 0.02, "USD", "guarantee")

        assert result == "floor"
        service.floor_repo.set_floor.assert_awaited_once_with(
            creator_id, Decimal("0.02"), "USD", "guarantee"
        )

    async def test_set_floor_refuses_a_negative_amount(self, service):
        with pytest.raises(AssertionError, match="effective floor must be >= 0"):
            await service.set_floor(uuid4(), -1)

    async def test_record_pool_contribution_delegates(self, service):
        creator_id = uuid4()
        service.pool_repo.record_contribution = _async(return_value=7)

        assert await service.record_pool_contribution(creator_id, 500) == 7
        service.pool_repo.record_contribution.assert_awaited_once_with(creator_id, 500)


class TestAccruePayout:
    async def test_suspended_creator_cannot_accrue(self, service):
        creator_id = uuid4()
        service.acct_repo.get = _async(return_value=_active_account(creator_id, False))

        with pytest.raises(CreatorSuspendedError):
            await service.accrue_payout(creator_id, NOW, NOW, 10, 100)

    async def test_unknown_creator_cannot_accrue(self, service):
        service.acct_repo.get = _async(return_value=None)

        with pytest.raises(CreatorSuspendedError):
            await service.accrue_payout(uuid4(), NOW, NOW, 10, 100)

    async def test_floor_topup_is_capped_at_the_gap_below_the_floor(self, service):
        creator_id = uuid4()
        service.acct_repo.get = _async(return_value=_active_account(creator_id))
        # 0.05/min over 100 minutes = 500 cents due, 200 earned -> 300 top-up.
        service.floor_repo.get_floor_for_creator = _async(
            return_value=type("Floor", (), {"per_minute_amount": 0.05})()
        )
        service.ledger_repo.accrued = _async(return_value="row")

        await service.accrue_payout(creator_id, NOW, NOW, 100, 200, 10)

        kwargs = service.ledger_repo.accrued.await_args.kwargs
        assert kwargs["floor_cents"] == 300
        assert kwargs["pool_topup_cents"] == int(300 * settings.POOL_RATE)
        assert kwargs["share_cents"] == 500
        assert kwargs["net_cents"] == 490

    async def test_no_floor_means_no_topup(self, service):
        creator_id = uuid4()
        service.acct_repo.get = _async(return_value=_active_account(creator_id))
        service.floor_repo.get_floor_for_creator = _async(return_value=None)
        service.ledger_repo.accrued = _async(return_value="row")

        await service.accrue_payout(creator_id, NOW, NOW, 100, 200)

        kwargs = service.ledger_repo.accrued.await_args.kwargs
        assert kwargs["floor_cents"] == 0
        assert kwargs["pool_topup_cents"] == 0
        assert kwargs["net_cents"] == 200

    async def test_earnings_above_the_floor_get_no_topup(self, service):
        creator_id = uuid4()
        service.acct_repo.get = _async(return_value=_active_account(creator_id))
        service.floor_repo.get_floor_for_creator = _async(
            return_value=type("Floor", (), {"per_minute_amount": 0.01})()
        )
        service.ledger_repo.accrued = _async(return_value="row")

        await service.accrue_payout(creator_id, NOW, NOW, 10, 5000)

        assert service.ledger_repo.accrued.await_args.kwargs["floor_cents"] == 0

    async def test_idempotency_key_is_creator_plus_period(self, service):
        creator_id = uuid4()
        service.acct_repo.get = _async(return_value=_active_account(creator_id))
        service.floor_repo.get_floor_for_creator = _async(return_value=None)
        service.ledger_repo.accrued = _async(return_value="row")
        period_end = NOW + timedelta(days=28)

        await service.accrue_payout(creator_id, NOW, period_end, 10, 100)

        kwargs = service.ledger_repo.accrued.await_args.kwargs
        assert kwargs["idempotency_key"] == f"{creator_id}:{NOW.isoformat()}:{period_end.isoformat()}"

    async def test_an_absurd_fee_cannot_break_the_55_percent_invariant(self, service):
        """The contractual assert is unreachable through the public API.

        ``stripe_fee_cents`` is clamped to >= 0 and ``share_cents >= earned_cents``,
        so ``net_cents <= share_cents`` always holds and the >=55% guard can never
        trip. Kept as a regression test on that reasoning.
        """
        creator_id = uuid4()
        service.acct_repo.get = _async(return_value=_active_account(creator_id))
        service.floor_repo.get_floor_for_creator = _async(return_value=None)
        service.ledger_repo.accrued = _async(return_value="row")

        await service.accrue_payout(creator_id, NOW, NOW, 10, 1000, 999)

        kwargs = service.ledger_repo.accrued.await_args.kwargs
        assert kwargs["net_cents"] == 1
        assert kwargs["share_cents"] >= 0.55 * kwargs["net_cents"]


class TestInboundSuspension:
    def _event(self, event_id=None, event_key=None, topic="creator.suspended", payload=None):
        event = type("Event", (), {})()
        event.id = event_id or uuid4()
        event.event_key = event_key or str(uuid4())
        event.topic = topic
        event.payload = payload if payload is not None else {"creator_id": str(uuid4())}
        return event

    async def test_inbound_repository_is_required(self, service):
        with pytest.raises(CreatorSuspendedError, match="inbound event repository"):
            await service.process_inbound_suspension(str(uuid4()), {})

    async def test_suspension_deactivates_the_creator(self, service):
        creator_id = uuid4()
        acct = _active_account(creator_id)
        service.acct_repo.get = _async(return_value=acct)
        service.acct_repo.update = _async(return_value=acct)
        service.inbound_repo = _inbound_repo()
        event_key = str(uuid4())

        await service.process_inbound_suspension(event_key, {"creator_id": str(creator_id)})

        service.acct_repo.update.assert_awaited_once_with(
            acct, is_active=False, kyc_status="suspended"
        )
        service.inbound_repo.mark_processed.assert_awaited_once_with(UUID(event_key))

    async def test_unknown_creator_is_rejected(self, service):
        service.acct_repo.get = _async(return_value=None)
        service.inbound_repo = _inbound_repo()

        with pytest.raises(CreatorSuspendedError, match="does not exist"):
            await service.process_inbound_suspension(str(uuid4()), {"creator_id": str(uuid4())})

    async def test_drain_without_a_repository_is_a_noop(self, service):
        assert await service.drain_inbound_events() == 0

    async def test_drain_processes_suspension_events_and_commits(self, service):
        event = self._event(payload={"creator_id": str(uuid4())})
        service.inbound_repo = _inbound_repo(pending=[event])
        service.acct_repo.get = _async(return_value=_active_account())
        service.acct_repo.update = _async(return_value=None)

        processed = await service.drain_inbound_events(limit=50)

        assert processed == 1
        service.inbound_repo.get_pending.assert_awaited_once_with(50)
        service.inbound_repo.session.commit.assert_awaited_once()
        service.inbound_repo.mark_failed.assert_not_awaited()

    async def test_drain_fails_unrelated_topics_instead_of_processing_them(self, service):
        event = self._event(topic="creator.updated")
        service.inbound_repo = _inbound_repo(pending=[event])

        processed = await service.drain_inbound_events()

        assert processed == 0
        service.inbound_repo.mark_failed.assert_awaited_once_with(event.id)

    async def test_drain_marks_a_failing_event_failed_and_keeps_going(self, service):
        bad = self._event(payload={"creator_id": "not-a-uuid"})
        good = self._event(payload={"creator_id": str(uuid4())})
        service.inbound_repo = _inbound_repo(pending=[bad, good])
        service.acct_repo.get = _async(return_value=_active_account())
        service.acct_repo.update = _async(return_value=None)

        processed = await service.drain_inbound_events()

        assert processed == 1
        service.inbound_repo.mark_failed.assert_awaited_once_with(bad.id)
        service.inbound_repo.session.commit.assert_awaited_once()


class TestInboundRepositoryWiring:
    async def test_repository_is_constructed_from_a_session(self):
        from unittest.mock import AsyncMock

        session = AsyncMock()
        repo = InboundEventRepository(session)

        assert repo.session is session


def _async(**kwargs):
    from unittest.mock import AsyncMock

    return AsyncMock(**kwargs)


def _inbound_repo(pending=None):
    from unittest.mock import AsyncMock, MagicMock

    repo = MagicMock()
    repo.get_pending = AsyncMock(return_value=pending or [])
    repo.mark_processed = AsyncMock()
    repo.mark_failed = AsyncMock()
    repo.session = MagicMock()
    repo.session.commit = AsyncMock()
    return repo


# --------------------------------------------------------------- repositories
class TestPayoutLedgerGuards:
    """``PayoutLedgerRepository.accrued`` validates before touching the database."""

    def _repo(self, session=None):
        from unittest.mock import MagicMock

        from app.repositories import PayoutLedgerRepository

        return PayoutLedgerRepository(session or MagicMock())

    def test_period_end_must_follow_period_start(self):
        repo = self._repo()

        with pytest.raises(ValueError, match="period_end must follow period_start"):
            _run(repo.accrued(uuid4(), NOW, NOW - timedelta(days=1), 10, 0, 0, 0, 0, 0, "k"))

    def test_period_bounds_may_not_be_equal(self):
        repo = self._repo()

        with pytest.raises(ValueError, match="period_end must follow period_start"):
            _run(repo.accrued(uuid4(), NOW, NOW, 10, 0, 0, 0, 0, 0, "k"))

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"view_minutes": -1},
            {"floor_cents": -1},
            {"pool_topup_cents": -1},
            {"share_cents": -1},
            {"stripe_fee_cents": -1},
            {"net_cents": -1},
        ],
    )
    def test_negative_amounts_are_rejected(self, kwargs):
        repo = self._repo()
        base = {
            "view_minutes": 10,
            "floor_cents": 0,
            "pool_topup_cents": 0,
            "share_cents": 100,
            "stripe_fee_cents": 0,
            "net_cents": 100,
        }
        base.update(kwargs)

        with pytest.raises(ValueError, match="nonnegative"):
            _run(
                repo.accrued(
                    uuid4(),
                    NOW,
                    NOW + timedelta(days=1),
                    base["view_minutes"],
                    base["floor_cents"],
                    base["pool_topup_cents"],
                    base["share_cents"],
                    base["stripe_fee_cents"],
                    base["net_cents"],
                    "k",
                )
            )

    def test_suspended_creator_is_rejected_and_the_transaction_rolled_back(self):
        from unittest.mock import AsyncMock, MagicMock

        session = MagicMock()  # execute() resolves to "no active creator"
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)
        session.rollback = AsyncMock()
        repo = self._repo(session)

        with pytest.raises(CreatorSuspendedError):
            _run(repo.accrued(uuid4(), NOW, NOW + timedelta(days=1), 10, 0, 0, 0, 0, 0, "k"))

        session.rollback.assert_awaited_once()


class TestInboundEventRepository:
    """``InboundEventRepository`` against a real (SQLite) session."""

    async def test_create_pending_then_replay_returns_the_same_row(self, session):
        repo = InboundEventRepository(session)
        key = str(uuid4())

        first = await repo.create_pending("creator.suspended", key, {"creator_id": "x"})
        second = await repo.create_pending("creator.suspended", key, {"creator_id": "x"})

        assert first.id == second.id
        assert (await repo.get_by_event_key(key)).id == first.id
        assert await repo.get_by_event_key("missing-key") is None

    async def test_mark_processed_and_mark_failed(self, session):
        repo = InboundEventRepository(session)
        processed = await repo.create_pending("creator.suspended", str(uuid4()), {})
        failed = await repo.create_pending("creator.updated", str(uuid4()), {})
        await session.commit()

        await repo.mark_processed(processed.id)
        await repo.mark_failed(failed.id)
        await session.commit()

        assert (await repo.get_by_event_key(processed.event_key)).status.value == "processed"
        assert (await repo.get_by_event_key(failed.event_key)).status.value == "failed"
        assert (await repo.get_by_event_key(processed.event_key)).processed_at is not None

    async def test_marking_an_unknown_event_is_a_noop(self, session):
        repo = InboundEventRepository(session)

        await repo.mark_processed(uuid4())
        await repo.mark_failed(uuid4())

    async def test_get_pending_returns_only_pending_events(self, session):
        repo = InboundEventRepository(session)
        pending = await repo.create_pending("creator.suspended", str(uuid4()), {})
        done = await repo.create_pending("creator.suspended", str(uuid4()), {})
        await repo.mark_processed(done.id)
        await session.commit()

        rows = await repo.get_pending(limit=10)

        assert [r.id for r in rows] == [pending.id]


class TestMilestoneRepositoryLookups:
    async def test_get_release_and_kill_against_a_real_session(self, session):
        from app.repositories import MilestoneRepository

        repo = MilestoneRepository(session)
        creator_id = uuid4()
        milestone = await repo.create("Launch film", creator_id, 1000, "USD", "ship")
        tranche = await repo.add_tranche(milestone.id, 50, 500, "views")
        await session.commit()

        assert (await repo.get(milestone.id)).id == milestone.id
        assert await repo.get(uuid4()) is None

        released = await repo.release_tranche(milestone.id, 50)
        assert released.id == tranche.id
        assert released.status.value == "released"
        assert released.released_at is not None
        assert await repo.release_tranche(milestone.id, 999) is None

        locked = await repo.add_tranche(milestone.id, 90, 500, "views")
        await session.commit()
        killed = await repo.kill_milestone(milestone.id, "budget")

        assert killed.status.value == "killed"
        assert killed.kill_reason == "budget"
        # Released tranches are capital-protected; only unreleased ones roll back.
        from sqlalchemy import select

        stored = await session.execute(
            select(MilestoneTranche).where(MilestoneTranche.milestone_id == milestone.id)
        )
        rows = {t.threshold: t for t in stored.scalars().all()}
        assert locked.threshold == 90
        assert rows[50].status.value == "released"
        assert rows[90].status.value == "rolled_back"
        assert await repo.kill_milestone(uuid4(), "budget") is None


class TestPoolBalanceGuards:
    async def test_negative_contribution_is_rejected(self, session):
        from app.repositories import CreatorPoolBalanceRepository

        with pytest.raises(ValueError, match="contribution must be nonnegative"):
            await CreatorPoolBalanceRepository(session).record_contribution(uuid4(), -1)

    async def test_negative_accrual_is_rejected(self, session):
        from app.repositories import CreatorPoolBalanceRepository

        with pytest.raises(ValueError, match="accrual must be nonnegative"):
            await CreatorPoolBalanceRepository(session).accrue(uuid4(), -1)

    async def test_get_for_creator_returns_none_for_an_unknown_creator(self, session):
        from app.repositories import CreatorPoolBalanceRepository

        assert await CreatorPoolBalanceRepository(session).get_for_creator(uuid4()) is None


def _run(coro):
    """Run a coroutine to completion from a synchronous test."""
    import asyncio

    return asyncio.run(coro)


# ------------------------------------------- PostgreSQL-only upsert paths
LOCAL_TEST_DATABASE_URL = "postgresql+asyncpg://postgres:test@127.0.0.1:55432/test_db"


def _local_instance_reachable(url: str) -> bool:
    """Cheap TCP probe so the suite also works without TEST_DATABASE_URL set."""
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.hostname:
        return False
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((parsed.hostname, parsed.port or 5432)) == 0


@pytest.fixture(scope="module")
def database_url():
    """TEST_DATABASE_URL, the documented local instance, else a container."""
    import os
    from contextlib import ExitStack

    with ExitStack() as stack:
        url = os.environ.get("TEST_DATABASE_URL")
        if url:
            yield url
            return
        if _local_instance_reachable(LOCAL_TEST_DATABASE_URL):
            yield LOCAL_TEST_DATABASE_URL
            return
        from testcontainers.postgres import PostgresContainer  # lazy import

        postgres = stack.enter_context(PostgresContainer("postgres:15"))
        yield postgres.get_connection_url()


@pytest_asyncio.fixture
async def pg_session(database_url):
    """PostgreSQL session — ``get_or_create``/``record_contribution`` use
    ``sqlalchemy.dialects.postgresql.insert(...).on_conflict_*``, which has no
    SQLite equivalent."""
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models import Base

    engine = create_async_engine(
        make_url(database_url).set(drivername="postgresql+asyncpg"), echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            yield db
    finally:
        await engine.dispose()


class TestPoolBalanceUpsertsAgainstPostgres:
    async def test_get_or_create_creates_then_reuses_the_row(self, pg_session):
        from app.repositories import CreatorPoolBalanceRepository

        repo = CreatorPoolBalanceRepository(pg_session)
        creator_id = uuid4()

        created = await repo.get_or_create(creator_id)
        again = await repo.get_or_create(creator_id)

        assert created.id == again.id
        assert await repo.get_for_creator(creator_id) is not None

    async def test_get_or_create_preserves_existing_balances(self, pg_session):
        from app.repositories import CreatorPoolBalanceRepository

        repo = CreatorPoolBalanceRepository(pg_session)
        creator_id = uuid4()
        created = await repo.get_or_create(creator_id)
        created.accrued_cents = 700
        await pg_session.commit()

        again = await repo.get_or_create(creator_id)

        assert again.id == created.id
        assert again.accrued_cents == 700

    async def test_record_contribution_inserts_then_accumulates(self, pg_session):
        from app.repositories import CreatorPoolBalanceRepository

        repo = CreatorPoolBalanceRepository(pg_session)
        creator_id = uuid4()

        first = await repo.record_contribution(creator_id, 500)
        # Snapshot the scalar: both calls return the same identity-mapped row.
        first_total, first_id = first.contributed_cents, first.id
        second = await repo.record_contribution(creator_id, 250)

        assert first_total == 500
        assert second.contributed_cents == 750
        assert second.id == first_id

    async def test_record_contribution_of_zero_is_allowed(self, pg_session):
        from app.repositories import CreatorPoolBalanceRepository

        repo = CreatorPoolBalanceRepository(pg_session)
        creator_id = uuid4()

        bal = await repo.record_contribution(creator_id, 0)

        assert bal.contributed_cents == 0


class TestJwtAudienceIsEnforcedElsewhere:
    """Guards the assumption the service layer makes about its tokens."""

    def test_settings_pin_the_api_audience(self):
        assert settings.JWT_AUDIENCE == "wildframe-api"
        assert settings.JWT_ISSUER == "wildframe-auth"

    def test_a_service_token_decodes_for_this_service(self):
        token = jwt.encode(
            {
                "sub": str(uuid4()),
                "type": "access",
                "role": "admin",
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
                "exp": datetime.now() + timedelta(minutes=15),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )

        assert payload["role"] == "admin"
