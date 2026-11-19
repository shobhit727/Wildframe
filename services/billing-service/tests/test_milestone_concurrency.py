"""A concurrent kill and release must serialise on the milestone row (#1142).

Billing runs READ COMMITTED. ``kill_milestone`` and ``release_tranche`` used to
read the milestone and its tranches with plain SELECTs, check the status, and
write. Two admins on different workers could then both pass their checks, and
the money was spent twice: once as a payout accrual for a released tranche and
once as a REVERTED tranche returned to the Creator Pool.

These tests drive the real service over two real PostgreSQL sessions (SQLite
ignores ``FOR UPDATE``). The interleaving is fixed without sleeps: the first
operation runs to completion but does not commit, the second starts as a task,
and the test waits until that task has either finished or is blocked on a row
lock held by the first (read from ``pg_blocking_pids``) before committing the
first. Whatever the locking, the committed result must equal one of the two
serial orders.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.billing_routes import get_billing_service
from app.models import (
    Base,
    Milestone,
    MilestoneStatus,
    MilestoneTranche,
    PayoutLedger,
    TrancheStatus,
)
from app.repositories import MilestoneRepository
from app.services import MilestoneKillError

ADMIN = {"caller_id": uuid4(), "caller_is_admin": True}

# Hang guard only. Ordering never depends on it: the latch below waits on
# observed lock state, and the deadline just turns a deadlock into a failure.
_DEADLINE_SECONDS = 30


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    """TEST_DATABASE_URL, or a throwaway postgres:15 container."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:15") as postgres:
        yield postgres.get_connection_url()


@pytest_asyncio.fixture
async def engine(postgres_url: str) -> AsyncIterator[AsyncEngine]:
    """An engine whose connections see only a private, committed schema.

    The race needs two connections that really commit, so the rollback-only
    ``db_session`` fixture of ``test_repositories`` cannot be used. A private
    schema (dropped afterwards) keeps the committed rows out of the shared
    TEST_DATABASE_URL database that the rest of the suite uses.
    """
    url = make_url(postgres_url).set(drivername="postgresql+asyncpg")
    schema = f"t1142_{uuid4().hex[:12]}"
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with scoped.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield scoped
    finally:
        await scoped.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@dataclass
class _Committed:
    milestone_status: MilestoneStatus
    tranches: dict[int, MilestoneTranche]
    ledger_keys: list[str]


async def _seed_milestone(factory: async_sessionmaker[AsyncSession]) -> UUID:
    async with factory() as session:
        milestone = await MilestoneRepository(session).create(uuid4(), "Race", Decimal("1000.00"))
        await session.commit()
        return milestone.id


async def _committed_state(
    factory: async_sessionmaker[AsyncSession], milestone_id: UUID
) -> _Committed:
    async with factory() as session:
        milestone = await session.get(Milestone, milestone_id)
        assert milestone is not None
        tranches = await MilestoneRepository(session).get_tranches(milestone_id)
        ledger = await session.execute(
            select(PayoutLedger.idempotency_key).where(
                PayoutLedger.idempotency_key.like(f"tranche:{milestone_id}:%")
            )
        )
        return _Committed(
            milestone_status=milestone.status,
            tranches={t.tranche_number: t for t in tranches},
            ledger_keys=sorted(ledger.scalars().all()),
        )


async def _backend_pid(session: AsyncSession) -> int:
    """Pin the session to its connection and return that backend's pid."""
    return (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()


async def _finished_or_blocked(task: asyncio.Task, engine: AsyncEngine, pid: int) -> None:
    """Latch: return once ``task`` has finished or ``pid`` waits on a lock."""
    async with engine.connect() as monitor:
        while not task.done():
            blocked = await monitor.execute(
                text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}
            )
            if blocked.scalar_one():
                return


async def test_kill_racing_an_uncommitted_release_does_not_revert_the_paid_tranche(engine):
    """release_tranche checks first; kill_milestone runs before it commits.

    The only serial result is release-then-kill: tranche 1 stays RELEASED with
    its payout accrual, and only tranches 2-4 return to the pool.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    milestone_id = await _seed_milestone(factory)

    async with asyncio.timeout(_DEADLINE_SECONDS):
        async with factory() as releaser, factory() as killer:
            release_svc = await get_billing_service(releaser)
            kill_svc = await get_billing_service(killer)

            await release_svc.release_tranche(milestone_id, 1, **ADMIN)
            await releaser.flush()

            killer_pid = await _backend_pid(killer)
            kill = asyncio.create_task(kill_svc.kill_milestone(milestone_id, **ADMIN))
            try:
                await _finished_or_blocked(kill, engine, killer_pid)
                await releaser.commit()
                await kill
                await killer.commit()
            finally:
                if not kill.done():
                    kill.cancel()

    state = await _committed_state(factory, milestone_id)
    tranche_1 = state.tranches[1]
    assert state.ledger_keys == [f"tranche:{milestone_id}:1"]
    assert tranche_1.status == TrancheStatus.RELEASED and tranche_1.reverted_at is None, (
        "kill_milestone returned tranche 1 to the Creator Pool after release_tranche "
        f"had accrued its payout (status={tranche_1.status}, "
        f"reverted_at={tranche_1.reverted_at}, ledger={state.ledger_keys})"
    )
    assert state.milestone_status == MilestoneStatus.KILLED
    assert [state.tranches[n].status for n in (2, 3, 4)] == [TrancheStatus.REVERTED] * 3


async def test_release_racing_an_uncommitted_kill_is_refused(engine):
    """kill_milestone checks first; release_tranche runs before it commits.

    The only serial result is kill-then-release: the release is refused with
    MilestoneKillError, every tranche returns to the pool, and nothing accrues.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    milestone_id = await _seed_milestone(factory)

    async with asyncio.timeout(_DEADLINE_SECONDS):
        async with factory() as killer, factory() as releaser:
            kill_svc = await get_billing_service(killer)
            release_svc = await get_billing_service(releaser)

            await kill_svc.kill_milestone(milestone_id, **ADMIN)
            await killer.flush()

            releaser_pid = await _backend_pid(releaser)
            release = asyncio.create_task(release_svc.release_tranche(milestone_id, 1, **ADMIN))
            try:
                await _finished_or_blocked(release, engine, releaser_pid)
                await killer.commit()
                (outcome,) = await asyncio.gather(release, return_exceptions=True)
            finally:
                if not release.done():
                    release.cancel()
            if isinstance(outcome, BaseException):
                await releaser.rollback()
            else:
                await releaser.commit()

    state = await _committed_state(factory, milestone_id)
    tranche_1 = state.tranches[1]
    assert state.ledger_keys == [], (
        "release_tranche accrued a payout for a tranche that kill_milestone had "
        f"already returned to the Creator Pool (tranche 1 status={tranche_1.status}, "
        f"reverted_at={tranche_1.reverted_at}, ledger={state.ledger_keys})"
    )
    assert isinstance(outcome, MilestoneKillError), f"release was not refused: {outcome!r}"
    assert state.milestone_status == MilestoneStatus.KILLED
    assert [t.status for _, t in sorted(state.tranches.items())] == [TrancheStatus.REVERTED] * 4


async def test_locked_getters_refresh_rows_already_in_the_identity_map(engine):
    """A lock taken on a row the session already holds must return fresh data.

    Without ``populate_existing`` SQLAlchemy hands back the identity-mapped
    instance unchanged, so the status checks after the lock would read the
    stale PENDING/LOCKED values and the lock would prove nothing.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    milestone_id = await _seed_milestone(factory)

    async with factory() as stale, factory() as writer:
        repo = MilestoneRepository(stale)
        # Strong references keep these in the (weak) identity map.
        cached = await repo.get(milestone_id)
        cached_tranches = await repo.get_tranches(milestone_id)
        await stale.commit()

        kill_svc = await get_billing_service(writer)
        await kill_svc.kill_milestone(milestone_id, **ADMIN)
        await writer.commit()

        locked = await repo.get_for_update(milestone_id)
        locked_tranches = await repo.get_tranches_for_update(milestone_id)
        assert locked is cached
        assert locked_tranches == cached_tranches
        assert locked.status == MilestoneStatus.KILLED, "get_for_update returned a stale row"
        assert [t.status for t in locked_tranches] == [
            TrancheStatus.REVERTED
        ] * 4, "get_tranches_for_update returned stale rows"
        await stale.rollback()
