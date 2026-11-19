"""A lost refund-id race must not discard the rest of the webhook transaction (#1144).

A ``charge.refunded`` event applies every refund of the charge in one handler
session, and the inbox row is marked PROCESSED once the loop returns. When two
workers race on one ``refund_id``, the loser hits the unique constraint. The old
``RefundRepository.create`` answered with ``session.rollback()``, which threw
away the refunds (and ``refunded_amount`` increments) the same session had
already applied earlier in the loop, while the event still completed and was
never retried.

These tests use two real PostgreSQL sessions that really commit. The
interleaving is fixed without sleeps: the winner inserts the refund but does not
commit, the loser runs as a task, and the test waits until that task has either
finished or is blocked on a lock the winner holds (read from ``pg_blocking_pids``)
before committing the winner.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.webhook_routes import get_billing_service
from app.models import Base, Invoice, Refund, RefundStatus
from app.repositories import InvoiceRepository, RefundRepository

# Hang guard only. Ordering never depends on it: the latch waits on observed
# lock state, and the deadline just turns a deadlock into a failure.
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

    The race needs connections that really commit, so the rollback-only
    ``db_session`` fixture of ``test_repositories`` cannot be used. A private
    schema (dropped afterwards) keeps the committed rows out of the shared
    TEST_DATABASE_URL database.
    """
    url = make_url(postgres_url).set(drivername="postgresql+asyncpg")
    schema = f"t1144_{uuid4().hex[:12]}"
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


async def _seed_invoice(factory: async_sessionmaker[AsyncSession], amount: str) -> UUID:
    async with factory() as session:
        invoice = await InvoiceRepository(session).create(uuid4(), Decimal(amount))
        await session.commit()
        return invoice.id


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


async def _committed(factory: async_sessionmaker[AsyncSession], *invoice_ids: UUID):
    """Committed refund ids and ``refunded_amount`` per invoice."""
    async with factory() as session:
        refund_ids = (await session.execute(select(Refund.refund_id))).scalars().all()
        refunded = {
            i: (await session.execute(select(Invoice.refunded_amount).where(Invoice.id == i)))
            .scalar_one()
            .quantize(Decimal("0.01"))
            for i in invoice_ids
        }
        count = (await session.execute(select(func.count()).select_from(Refund))).scalar_one()
        return sorted(refund_ids), refunded, count


async def test_lost_refund_id_race_keeps_the_refunds_applied_earlier_in_the_session(engine):
    """Refund A is applied, then refund B loses the unique-key race.

    Only B's INSERT may be undone. A and its ``refunded_amount`` increment
    belong to the same handler transaction and must still commit.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    invoice_id = await _seed_invoice(factory, "100.00")
    ref_a, ref_b = f"re_a_{uuid4().hex}", f"re_b_{uuid4().hex}"

    async with asyncio.timeout(_DEADLINE_SECONDS):
        async with factory() as winner, factory() as loser:
            # The other worker inserted refund B first and has not committed.
            await RefundRepository(winner).create(ref_b, Decimal("5.00"), "USD")
            await winner.flush()

            repo = RefundRepository(loser)
            assert await repo.apply_to_invoice(invoice_id, Decimal("10.00"))
            await repo.create(ref_a, Decimal("10.00"), "USD", invoice_id=invoice_id)
            await loser.flush()

            loser_pid = await _backend_pid(loser)
            duplicate = asyncio.create_task(repo.create(ref_b, Decimal("5.00"), "USD"))
            try:
                await _finished_or_blocked(duplicate, engine, loser_pid)
                await winner.commit()
                returned = await duplicate
                assert returned.refund_id == ref_b
                # The session survives the lost race and keeps working.
                assert (await repo.get_by_refund_id(ref_a)) is not None
                await loser.commit()
            finally:
                if not duplicate.done():
                    duplicate.cancel()

    refund_ids, refunded, count = await _committed(factory, invoice_id)
    assert refund_ids == sorted([ref_a, ref_b]) and count == 2, (
        "the lost refund-id race discarded refund A, which was applied earlier "
        f"in the same session (committed refunds={refund_ids})"
    )
    assert refunded[invoice_id] == Decimal(
        "10.00"
    ), f"refund A's refunded_amount increment was discarded (got {refunded[invoice_id]})"


async def test_lost_refund_id_race_does_not_apply_the_duplicate_to_its_invoice_twice(engine):
    """Event loop A (invoice 1) then B (invoice 2); another worker owns B.

    The winner applied B to invoice 2 and has not committed. Without
    serialisation the loser misses B, waits on the winner's invoice row lock in
    ``apply_to_invoice`` and then increments ``refunded_amount`` a second time;
    by the time ``create`` runs, B is committed, so no IntegrityError ever
    signals the duplicate. The loser must apply nothing for B.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    invoice_1 = await _seed_invoice(factory, "100.00")
    invoice_2 = await _seed_invoice(factory, "100.00")
    ref_a, ref_b = f"re_a_{uuid4().hex}", f"re_b_{uuid4().hex}"

    async def process(session: AsyncSession, refund_id: str, invoice_id: UUID, amount: str):
        service = await get_billing_service(session)
        return await service.process_refund(
            refund_id,
            "ch_1144",
            Decimal(amount),
            "USD",
            invoice_id=invoice_id,
        )

    with (
        patch("app.services.StripeClient.retrieve_refund", return_value=None),
        patch("app.services.StripeClient.retrieve_charge", return_value=None),
    ):
        async with asyncio.timeout(_DEADLINE_SECONDS):
            async with factory() as winner, factory() as loser:
                won = await process(winner, ref_b, invoice_2, "5.00")
                await winner.flush()
                assert won.status == RefundStatus.PROCESSED

                loser_pid = await _backend_pid(loser)
                first = await process(loser, ref_a, invoice_1, "10.00")
                assert first.status == RefundStatus.PROCESSED
                second = asyncio.create_task(process(loser, ref_b, invoice_2, "5.00"))
                try:
                    await _finished_or_blocked(second, engine, loser_pid)
                    await winner.commit()
                    returned = await second
                    assert returned.refund_id == ref_b
                    await loser.commit()
                finally:
                    if not second.done():
                        second.cancel()

    refund_ids, refunded, count = await _committed(factory, invoice_1, invoice_2)
    assert (
        refund_ids == sorted([ref_a, ref_b]) and count == 2
    ), f"refund A was lost when refund B lost its race (committed refunds={refund_ids})"
    assert refunded[invoice_1] == Decimal(
        "10.00"
    ), f"refund A was not applied to invoice 1 (got {refunded[invoice_1]})"
    assert refunded[invoice_2] == Decimal(
        "5.00"
    ), f"refund B was applied to invoice 2 {refunded[invoice_2]} instead of once (5.00)"
