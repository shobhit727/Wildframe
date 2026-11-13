"""Regression tests for #1054: moderation state and its event are atomic.

The defect: ``moderate_user`` committed the moderation row, *then* published
``user.moderated``. If the broker (or the process) failed in between, the user
stayed suspended in ``admin_db`` while auth-service still saw them as active
and kept letting them log in. The fix writes the event to a transactional
outbox in the same transaction and delivers it afterwards, retrying until it
succeeds.

These tests run against SQLite and never touch a real broker.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from wildframe_events import InMemoryEventPublisher

from app.core import events as events_mod
from app.core.events import reset_event_publisher
from app.models.admin import Base
from app.services.admin import AdminService

DEFECT = (
    "moderate_user raised after committing the moderation row, so the user is "
    "suspended in admin_db while auth-service never receives user.moderated and "
    "still lets them log in (#1054)"
)


class FailingPublisher:
    """A broker that is reachable enough to accept a call, then fails."""

    def __init__(self):
        self.attempts = 0

    async def publish(self, _event):
        self.attempts += 1
        raise OSError("broker unreachable")


@pytest.fixture
async def service(tmp_path, monkeypatch):
    """A real AdminService over SQLite with a failing broker."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'outbox.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    publisher = FailingPublisher()
    monkeypatch.setattr(events_mod, "_publisher", publisher)
    async with factory() as session:
        yield AdminService(session), session, publisher
    reset_event_publisher()
    await engine.dispose()


async def _moderate(svc: AdminService, status: str = "suspended") -> dict:
    try:
        return await svc.moderate_user("u-1", status, "fraud", "admin-1", "10.0.0.1")
    except Exception as exc:  # noqa: BLE001 - the failure itself is the defect
        pytest.fail(f"{DEFECT}: {type(exc).__name__}: {exc}")


async def test_moderation_survives_a_broker_outage(service):
    svc, _, publisher = service

    result = await _moderate(svc)

    assert result["user_id"] == "u-1"
    assert result["status"] == "suspended"
    history = await svc.get_user_moderation_history("u-1")
    assert history is not None
    assert history["status"] == "suspended"
    assert publisher.attempts >= 1, "the outage was never exercised"


async def test_the_undelivered_event_is_kept_in_the_outbox(service):
    svc, session, _ = service
    await _moderate(svc)

    from app.models.admin import OutboxEvent

    rows = (await session.execute(select(OutboxEvent))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.topic == "user.moderated"
    assert row.status == "pending"
    assert row.event_key == "moderated:u-1"
    assert row.event_id
    assert row.payload["user_id"] == "u-1"
    assert row.payload["status"] == "suspended"
    assert row.payload["moderated_by"] == "admin-1"
    assert row.dispatched_at is None


async def test_recovery_delivers_the_pending_event_exactly_once(service, monkeypatch):
    svc, session, _ = service
    await _moderate(svc)

    recovered = InMemoryEventPublisher()
    monkeypatch.setattr(events_mod, "_publisher", recovered)

    drained = await svc.drain_outbox()

    assert drained == 1
    assert len(recovered.sent) == 1
    event = recovered.sent[0]
    assert event.topic == "user.moderated"
    assert event.event_id == row.event_id
    assert event.payload["user_id"] == "u-1"
    assert event.payload["status"] == "suspended"

    from app.models.admin import OutboxEvent

    row = (await session.execute(select(OutboxEvent))).scalar_one()
    assert row.status == "dispatched"
    assert row.dispatched_at is not None

    # A second drain has nothing left to deliver.
    assert await svc.drain_outbox() == 0
    assert len(recovered.sent) == 1


async def test_a_failed_delivery_stays_pending_for_the_next_attempt(service, monkeypatch):
    svc, session, _ = service
    await _moderate(svc)

    from app.models.admin import OutboxEvent

    row = (await session.execute(select(OutboxEvent))).scalar_one()
    assert row.status == "pending"
    event_id = row.event_id

    # Still failing: the drain must not drop or duplicate the row.
    assert await svc.drain_outbox() == 1
    await session.refresh(row)
    assert row.status == "pending"

    # Recovery drains the same row once, not twice.
    recovered = InMemoryEventPublisher()
    monkeypatch.setattr(events_mod, "_publisher", recovered)
    assert await svc.drain_outbox() == 1
    assert len(recovered.sent) == 1
    assert recovered.sent[0].event_id == event_id
    await session.refresh(row)
    assert row.status == "dispatched"


async def test_pending_releases_only_the_oldest_event_for_each_partition(service):
    svc, session, _ = service
    from app.models.admin import OutboxEvent

    now = datetime.now(UTC)
    first_user_event = OutboxEvent(
        topic="user.moderated",
        event_key="moderated:u-1",
        payload={"user_id": "u-1", "status": "suspended"},
        status="pending",
        created_at=now,
    )
    later_user_event = OutboxEvent(
        topic="user.moderated",
        event_key="moderated:u-1",
        payload={"user_id": "u-1", "status": "active"},
        status="pending",
        created_at=now + timedelta(seconds=1),
    )
    other_user_event = OutboxEvent(
        topic="user.moderated",
        event_key="moderated:u-2",
        payload={"user_id": "u-2", "status": "banned"},
        status="pending",
        created_at=now,
    )
    session.add_all([first_user_event, later_user_event, other_user_event])
    await session.commit()

    pending = await svc.outbox_repo.pending(10)
    assert {event.id for event in pending} == {
        first_user_event.id,
        other_user_event.id,
    }

    # A failed first event remains pending and must continue blocking the
    # later event on that user's partition, without blocking other users.
    await session.commit()
    pending_again = await svc.outbox_repo.pending(10)
    assert first_user_event.id in {event.id for event in pending_again}
    assert later_user_event.id not in {event.id for event in pending_again}

    first_user_event.status = "dispatched"
    await session.commit()
    pending_after_dispatch = await svc.outbox_repo.pending(10)
    assert later_user_event.id in {event.id for event in pending_after_dispatch}
