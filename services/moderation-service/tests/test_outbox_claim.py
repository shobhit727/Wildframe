"""#1145: concurrent outbox drains must never publish the same row twice.

Every gunicorn worker of every replica runs ``drain_outbox`` (8 loops in
production). The drain must claim its batch exclusively (``FOR UPDATE SKIP
LOCKED``) and must not hold a transaction open across the publish: engines set
``idle_in_transaction_session_timeout=30000`` (``app/core/database.py``), and a
publish stalled past it gets the backend killed, its row locks released, and the
rows re-published by the other loops.

The SQLite tests run everywhere. The PostgreSQL tests need ``TEST_DATABASE_URL``
(set by CI's backend test job) and simulate the idle timeout firing with
``pg_terminate_backend`` while the publish is parked on an ``asyncio.Event``.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.events import Event, EventPublisher
from app.models import Base, OutboxEvent, OutboxEventStatus
from app.repositories import ContentFlagRepository
from app.services import ModerationService

STEP_TIMEOUT = 10.0
TTL = timedelta(minutes=5)


class RecordingPublisher(EventPublisher):
    def __init__(self) -> None:
        self.published: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.published.append(event)

    async def publish_many(self, events: list[Event]) -> None:
        for event in events:
            await self.publish(event)


class GatedPublisher(RecordingPublisher):
    """Parks the first publish until released, like a broker stuck in retries."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def publish(self, event: Event) -> None:
        self.entered.set()
        await self.release.wait()
        await super().publish(event)


def _service(session: AsyncSession, publisher: EventPublisher) -> ModerationService:
    return ModerationService(
        flag_repo=ContentFlagRepository(session),
        decision_repo=None,  # type: ignore[arg-type]  # the drain never touches it
        strike_repo=None,  # type: ignore[arg-type]
        publisher=publisher,
    )


async def _seed(factory: async_sessionmaker[AsyncSession], count: int) -> list[str]:
    """Insert ``count`` PENDING rows, one second apart, oldest first."""
    base = datetime.now(UTC) - timedelta(minutes=1)
    async with factory() as session:
        repo = ContentFlagRepository(session)
        keys = [str(uuid4()) for _ in range(count)]
        for offset, key in enumerate(keys):
            row = await repo.enqueue_event(
                topic="content.flagged", event_key=key, payload={"flag_id": key}
            )
            row.created_at = base + timedelta(seconds=offset)
        await session.commit()
        return keys


# ---------------------------------------------------------------------------
# SQLite: the drain's transaction shape and the claim fencing.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sqlite_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


class TestDrainShape:
    async def test_the_drain_claims_with_for_update_skip_locked(self, sqlite_factory):
        await _seed(sqlite_factory, 1)
        async with sqlite_factory() as session:
            statements: list[Any] = []
            execute = session.execute

            async def recording_execute(statement, *args, **kwargs):
                statements.append(statement)
                return await execute(statement, *args, **kwargs)

            session.execute = recording_execute  # type: ignore[method-assign]
            await _service(session, RecordingPublisher()).drain_outbox()

        rendered = [str(s.compile(dialect=postgresql.dialect())) for s in statements]
        assert any(
            "outbox_events" in sql and "FOR UPDATE SKIP LOCKED" in sql for sql in rendered
        ), "the drain must claim outbox rows with FOR UPDATE SKIP LOCKED"

    async def test_no_transaction_is_open_while_publishing(self, sqlite_factory):
        await _seed(sqlite_factory, 2)
        async with sqlite_factory() as session:
            in_transaction: list[bool] = []

            class ProbingPublisher(RecordingPublisher):
                async def publish(self, event: Event) -> None:
                    in_transaction.append(session.in_transaction())
                    await super().publish(event)

            processed = await _service(session, ProbingPublisher()).drain_outbox()

        assert processed == 2
        assert in_transaction == [False, False], (
            "a transaction held across the publish dies with the backend when "
            "idle_in_transaction_session_timeout fires"
        )

    async def test_the_event_id_is_the_outbox_row_id(self, sqlite_factory):
        await _seed(sqlite_factory, 2)
        publisher = RecordingPublisher()
        async with sqlite_factory() as session:
            await _service(session, publisher).drain_outbox()
            ids = (await session.execute(select(OutboxEvent.id))).scalars().all()

        assert sorted(e.event_id for e in publisher.published) == sorted(str(i) for i in ids)


class TestClaimFencing:
    async def test_a_claimed_row_is_not_claimed_again_until_the_claim_expires(self, sqlite_factory):
        await _seed(sqlite_factory, 1)
        async with sqlite_factory() as session:
            repo = ContentFlagRepository(session)
            [row] = await repo.claim_pending_events("drain-a", limit=10, claim_ttl=TTL)
            await session.commit()
            assert await repo.claim_pending_events("drain-b", limit=10, claim_ttl=TTL) == []

            # Drain A crashed: once its claim is older than the TTL, B takes over.
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == row.id)
                .values(claimed_at=row.claimed_at - TTL - timedelta(seconds=1))
            )
            await session.commit()
            [taken] = await repo.claim_pending_events("drain-b", limit=10, claim_ttl=TTL)
            await session.commit()
            assert taken.id == row.id and taken.claimed_by == "drain-b"

            # A came back: its stale claim no longer lets it retire the row.
            assert await repo.mark_dispatched(row.id, "drain-a") is False
            await session.commit()
            assert (await session.get(OutboxEvent, row.id)).status == OutboxEventStatus.PENDING
            assert await repo.mark_dispatched(row.id, "drain-b") is True
            await session.commit()
            await session.refresh(taken)
            assert taken.status == OutboxEventStatus.DISPATCHED
            assert taken.dispatched_at is not None

    async def test_a_dispatched_row_is_never_marked_or_claimed_again(self, sqlite_factory):
        await _seed(sqlite_factory, 1)
        async with sqlite_factory() as session:
            repo = ContentFlagRepository(session)
            [row] = await repo.claim_pending_events("drain-a", limit=10, claim_ttl=TTL)
            assert await repo.mark_dispatched(row.id, "drain-a") is True
            await session.commit()
            await session.refresh(row)
            stamped = row.dispatched_at
            assert stamped is not None

            assert await repo.mark_dispatched(row.id, "drain-a") is False
            # A zero TTL makes every claim stale, so only the status keeps it out.
            assert await repo.claim_pending_events("drain-b", 10, timedelta(0)) == []
            await session.commit()
            await session.refresh(row)
            assert row.dispatched_at == stamped

    async def test_releasing_a_claim_makes_the_row_claimable_at_once(self, sqlite_factory):
        await _seed(sqlite_factory, 1)
        async with sqlite_factory() as session:
            repo = ContentFlagRepository(session)
            [row] = await repo.claim_pending_events("drain-a", limit=10, claim_ttl=TTL)
            await repo.release_claim(row.id, "drain-b")  # not B's claim: no-op
            await session.commit()
            assert await repo.claim_pending_events("drain-b", 10, TTL) == []
            await repo.release_claim(row.id, "drain-a")
            await session.commit()
            [again] = await repo.claim_pending_events("drain-b", 10, TTL)
            assert again.id == row.id and again.claimed_by == "drain-b"

    async def test_the_claim_takes_the_oldest_rows_first(self, sqlite_factory):
        keys = await _seed(sqlite_factory, 3)
        async with sqlite_factory() as session:
            repo = ContentFlagRepository(session)
            first = await repo.claim_pending_events("drain-a", limit=2, claim_ttl=TTL)
            second = await repo.claim_pending_events("drain-b", limit=2, claim_ttl=TTL)
        assert [r.event_key for r in first] == keys[:2]
        assert [r.event_key for r in second] == keys[2:]


# ---------------------------------------------------------------------------
# PostgreSQL: two real drains, real row locks, the idle timeout firing.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pg() -> AsyncIterator[dict[str, Any]]:
    """Separate engines (separate backends) for drain A, drain B and an admin."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set (CI's backend test job sets it)")
    url = make_url(url).set(drivername="postgresql+asyncpg")
    engines = {role: create_async_engine(url, future=True) for role in ("a", "b", "admin")}
    try:
        async with engines["admin"].begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("TRUNCATE outbox_events"))
        yield {
            "admin": engines["admin"],
            **{
                role: async_sessionmaker(engines[engine], expire_on_commit=False)
                for role, engine in (("a", "a"), ("b", "b"), ("seed", "admin"))
            },
        }
    finally:
        async with engines["admin"].begin() as conn:
            await conn.execute(text("TRUNCATE outbox_events"))
        for engine in engines.values():
            await engine.dispose()


async def _fire_idle_in_transaction_timeout(admin: AsyncEngine) -> int:
    """Do what ``idle_in_transaction_session_timeout`` does, right now."""
    async with admin.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND state = 'idle in transaction' AND pid <> pg_backend_pid()"
            )
        )
        killed = int(result.scalar_one())
        await conn.commit()
        return killed


async def _statuses(admin: AsyncEngine) -> dict[str, str]:
    async with admin.connect() as conn:
        result = await conn.execute(text("SELECT event_key, status FROM outbox_events"))
        return {key: status for key, status in result.all()}


async def _settle(task: asyncio.Task, publisher: GatedPublisher) -> None:
    """Never leave drain A parked, whatever the test outcome."""
    publisher.release.set()
    if not task.done():
        await asyncio.wait({task}, timeout=STEP_TIMEOUT)
    if task.done() and not task.cancelled():
        task.exception()  # mark retrieved; the test asserts on the outcome itself


class TestConcurrentDrainsOnPostgres:
    async def test_a_publish_stalled_past_the_idle_timeout_is_not_republished(self, pg):
        keys = await _seed(pg["seed"], 3)
        gated = GatedPublisher()
        async with pg["a"]() as session_a:
            task = asyncio.create_task(_service(session_a, gated).drain_outbox())
            try:
                await asyncio.wait_for(gated.entered.wait(), STEP_TIMEOUT)
                killed = await _fire_idle_in_transaction_timeout(pg["admin"])

                other = RecordingPublisher()
                async with pg["b"]() as session_b:
                    await asyncio.wait_for(_service(session_b, other).drain_outbox(), STEP_TIMEOUT)
                assert [
                    e.key for e in other.published
                ] == [], "drain B re-published rows drain A is still publishing"
                assert killed == 0, "no transaction may stay open across the publish"

                gated.release.set()
                processed = await asyncio.wait_for(task, STEP_TIMEOUT)
            finally:
                await _settle(task, gated)

        assert processed == 3
        assert sorted(e.key for e in gated.published) == sorted(keys)
        assert await _statuses(pg["admin"]) == {key: "DISPATCHED" for key in keys}

    async def test_concurrent_claims_skip_locked_rows_instead_of_sharing_them(self, pg):
        await _seed(pg["seed"], 4)
        async with pg["a"]() as session_a, pg["b"]() as session_b:
            # A's claim is not committed yet, so its rows are only row-locked.
            mine = await ContentFlagRepository(session_a).claim_pending_events(
                "drain-a", limit=2, claim_ttl=TTL
            )
            theirs = await asyncio.wait_for(
                ContentFlagRepository(session_b).claim_pending_events(
                    "drain-b", limit=10, claim_ttl=TTL
                ),
                STEP_TIMEOUT,
            )
            await session_b.commit()
            await session_a.commit()

        assert len(mine) == 2 and len(theirs) == 2
        assert {r.id for r in mine}.isdisjoint({r.id for r in theirs})
        async with pg["seed"]() as session:
            claims: dict[UUID, str | None] = dict(
                (await session.execute(select(OutboxEvent.id, OutboxEvent.claimed_by))).all()
            )
        assert {claims[r.id] for r in mine} == {"drain-a"}
        assert {claims[r.id] for r in theirs} == {"drain-b"}
