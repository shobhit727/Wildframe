"""Tests for the recommendation-service event subscriber (#228 F3)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from wildframe_events import DomainEvent


class _FakeSessionFactory:
    """Stand-in for async_sessionmaker: one mock session per use.

    Sessions handed out are recorded so tests can assert on the exact
    session the handler used.
    """

    instances: list = []

    async def __aenter__(self):
        session = MagicMock()
        session.commit = AsyncMock()
        self.instances.append(session)
        return session

    async def __aexit__(self, *exc):
        return False


def _fake_sessions():
    _FakeSessionFactory.instances = []
    return _FakeSessionFactory()


def _event(payload, topic="content.deleted"):
    return DomainEvent(
        event_id=str(uuid4()),
        topic=topic,
        producer="content-service",
        key="deleted:{content_id}",
        payload=payload,
    )


@pytest.mark.asyncio
async def test_handler_evicts_rows_for_deleted_content(monkeypatch):
    """content.deleted removes stored recommendations for the title."""
    from app.core import events as events_mod

    content_id = str(uuid4())
    affected_users = [uuid4(), uuid4()]
    monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
    repo_cls = MagicMock()
    repo = repo_cls.return_value
    # delete_for_content -> list[UUID]: the affected user ids, not a row count.
    repo.delete_for_content = AsyncMock(return_value=affected_users)
    repo.session.commit = AsyncMock()
    monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)
    cache_invalidate = AsyncMock()
    monkeypatch.setattr(events_mod, "_cache_invalidate", cache_invalidate)

    await events_mod._handle_content_deleted(_event({"content_id": content_id}))

    repo_cls.assert_called_once()
    repo.delete_for_content.assert_awaited_once_with(UUID(content_id))
    assert [c.args[0] for c in cache_invalidate.await_args_list] == affected_users
    _FakeSessionFactory.instances[-1].commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_handler_evicts_rows_for_unpublished_content(monkeypatch):
    """content.unpublished removes stored recommendations for the title."""
    from app.core import events as events_mod

    content_id = str(uuid4())
    affected_users = [uuid4()]
    monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
    repo_cls = MagicMock()
    repo = repo_cls.return_value
    repo.delete_for_content = AsyncMock(return_value=affected_users)
    repo.session.commit = AsyncMock()
    monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)
    cache_invalidate = AsyncMock()
    monkeypatch.setattr(events_mod, "_cache_invalidate", cache_invalidate)

    await events_mod._handle_content_unpublished(
        _event({"content_id": content_id}, "content.unpublished")
    )

    repo.delete_for_content.assert_awaited_once_with(UUID(content_id))
    _FakeSessionFactory.instances[-1].commit.assert_awaited_once()
    assert [c.args[0] for c in cache_invalidate.await_args_list] == affected_users


@pytest.mark.asyncio
async def test_handler_drops_event_without_content_id(monkeypatch):
    """Payloads without content_id are dropped, not retried."""
    from app.core import events as events_mod

    monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
    repo_cls = MagicMock()
    monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)

    await events_mod._handle_content_deleted(_event({"foo": "bar"}))

    repo_cls.assert_not_called()


@pytest.mark.asyncio
async def test_handler_drops_event_with_invalid_content_id(monkeypatch):
    """Malformed content_id payloads are dropped, not retried."""
    from app.core import events as events_mod

    monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
    repo_cls = MagicMock()
    monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)

    await events_mod._handle_content_deleted(_event({"content_id": "not-a-uuid"}))

    repo_cls.assert_not_called()


@pytest.mark.asyncio
async def test_subscriber_registers_both_topics(monkeypatch):
    """start_event_subscriber subscribes to deleted + unpublished + billing."""
    from app.core import events as events_mod

    subscriber = MagicMock()
    subscriber.subscribe = AsyncMock()
    subscriber.start = AsyncMock()
    monkeypatch.setattr(events_mod, "get_event_subscriber", lambda: subscriber)

    await events_mod.start_event_subscriber()

    calls = [c.args[0] for c in subscriber.subscribe.await_args_list]
    assert "content.deleted" in calls
    assert "content.unpublished" in calls
    assert "billing.subscription.created" in calls
    assert "billing.subscription.updated" in calls
    assert "billing.subscription.cancelled" in calls
    subscriber.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_subscriber_tolerates_failures(monkeypatch):
    """A broker outage must not crash startup (#228 F3 is best-effort)."""
    from app.core import events as events_mod

    subscriber = MagicMock()
    subscriber.subscribe = AsyncMock(side_effect=RuntimeError("kafka down"))
    subscriber.start = AsyncMock()
    monkeypatch.setattr(events_mod, "get_event_subscriber", lambda: subscriber)

    await events_mod.start_event_subscriber()  # must not raise

    subscriber.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_subscriber_round_trip(monkeypatch):
    """In-memory subscriber delivers a published event to the handler."""
    from app.core import events as events_mod

    monkeypatch.setattr(events_mod, "settings", MagicMock(EVENT_PUBLISHER="memory"))
    events_mod.reset_event_subscriber()
    content_id = str(uuid4())
    monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
    repo_cls = MagicMock()
    repo_cls.return_value.delete_for_content = AsyncMock(return_value=[uuid4()])
    monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)

    sub = events_mod.get_event_subscriber()
    await events_mod.start_event_subscriber()
    await sub.deliver(_event({"content_id": content_id}))

    repo_cls.return_value.delete_for_content.assert_awaited_once_with(UUID(content_id))
    await events_mod.stop_event_subscriber()
    events_mod.reset_event_subscriber()


@pytest.mark.asyncio
async def test_billing_subscription_created_evicts_user_recs(monkeypatch):
    """billing.subscription.created clears user recommendations and cache."""
    from app.core import events as events_mod

    user_id = str(uuid4())
    monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
    repo_cls = MagicMock()
    repo = repo_cls.return_value
    repo.clear_for_user = AsyncMock(return_value=3)
    repo.session.commit = AsyncMock()
    monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)
    cache_invalidate = AsyncMock()
    monkeypatch.setattr(events_mod, "_cache_invalidate", cache_invalidate)

    await events_mod._handle_billing_subscription_change(
        _event({"user_id": user_id}, "billing.subscription.created")
    )

    repo_cls.assert_called_once()
    repo.clear_for_user.assert_awaited_once_with(UUID(user_id))
    _FakeSessionFactory.instances[-1].commit.assert_awaited_once()
    cache_invalidate.assert_awaited_once_with(UUID(user_id))


@pytest.mark.asyncio
async def test_billing_subscription_updated_evicts_user_recs(monkeypatch):
    """billing.subscription.updated clears user recommendations and cache."""
    from app.core import events as events_mod

    user_id = str(uuid4())
    monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
    repo_cls = MagicMock()
    repo = repo_cls.return_value
    repo.clear_for_user = AsyncMock(return_value=1)
    repo.session.commit = AsyncMock()
    monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)
    cache_invalidate = AsyncMock()
    monkeypatch.setattr(events_mod, "_cache_invalidate", cache_invalidate)

    await events_mod._handle_billing_subscription_change(
        _event({"user_id": user_id}, "billing.subscription.updated")
    )

    repo.clear_for_user.assert_awaited_once_with(UUID(user_id))
    cache_invalidate.assert_awaited_once_with(UUID(user_id))


@pytest.mark.asyncio
async def test_billing_subscription_cancelled_evicts_user_recs(monkeypatch):
    """billing.subscription.cancelled clears user recommendations and cache."""
    from app.core import events as events_mod

    user_id = str(uuid4())
    monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
    repo_cls = MagicMock()
    repo = repo_cls.return_value
    repo.clear_for_user = AsyncMock(return_value=5)
    repo.session.commit = AsyncMock()
    monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)
    cache_invalidate = AsyncMock()
    monkeypatch.setattr(events_mod, "_cache_invalidate", cache_invalidate)

    await events_mod._handle_billing_subscription_change(
        _event({"user_id": user_id}, "billing.subscription.cancelled")
    )

    repo.clear_for_user.assert_awaited_once_with(UUID(user_id))
    cache_invalidate.assert_awaited_once_with(UUID(user_id))


@pytest.mark.asyncio
async def test_billing_handler_drops_event_without_user_id(monkeypatch):
    """Billing events without user_id are dropped."""
    from app.core import events as events_mod

    monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
    repo_cls = MagicMock()
    monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)

    await events_mod._handle_billing_subscription_change(
        _event({"foo": "bar"}, "billing.subscription.created")
    )

    repo_cls.assert_not_called()


@pytest.mark.asyncio
async def test_billing_handler_drops_event_with_invalid_user_id(monkeypatch):
    """Billing events with invalid user_id are dropped."""
    from app.core import events as events_mod

    monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
    repo_cls = MagicMock()
    monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)

    await events_mod._handle_billing_subscription_change(
        _event({"user_id": "not-a-uuid"}, "billing.subscription.updated")
    )

    repo_cls.assert_not_called()


@pytest.mark.asyncio
async def test_subscriber_registers_billing_topics(monkeypatch):
    """start_event_subscriber subscribes to billing topics."""
    from app.core import events as events_mod

    subscriber = MagicMock()
    subscriber.subscribe = AsyncMock()
    subscriber.start = AsyncMock()
    monkeypatch.setattr(events_mod, "get_event_subscriber", lambda: subscriber)

    await events_mod.start_event_subscriber()

    calls = [c.args[0] for c in subscriber.subscribe.await_args_list]
    assert "billing.subscription.created" in calls
    assert "billing.subscription.updated" in calls
    assert "billing.subscription.cancelled" in calls
    subscriber.start.assert_awaited_once()


# ----------------------------------------------------------------------
# End-to-end through the SDK in-memory bus (no broker required)
# ----------------------------------------------------------------------


@pytest.fixture
def in_memory_bus(monkeypatch):
    """Wire the service subscriber onto the SDK in-memory adapters."""
    from wildframe_events import InMemoryEventPublisher, InMemoryEventSubscriber

    from app.core import events as events_mod

    monkeypatch.setattr(events_mod, "settings", MagicMock(EVENT_PUBLISHER="memory"))
    events_mod.reset_event_subscriber()
    monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
    repo_cls = MagicMock()
    repo = repo_cls.return_value
    repo.delete_for_content = AsyncMock(return_value=[])
    repo.clear_for_user = AsyncMock(return_value=0)
    repo.session.commit = AsyncMock()
    monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)
    cache_invalidate = AsyncMock()
    monkeypatch.setattr(events_mod, "_cache_invalidate", cache_invalidate)

    subscriber = InMemoryEventSubscriber()
    publisher = InMemoryEventPublisher()
    # Seed the process-wide subscriber so start_event_subscriber registers the
    # handlers on *this* bus rather than minting a second one.
    monkeypatch.setattr(events_mod, "_subscriber", subscriber)
    yield type(
        "Bus",
        (),
        {
            "publisher": publisher,
            "subscriber": subscriber,
            "repo_cls": repo_cls,
            "repo": repo,
            "cache_invalidate": cache_invalidate,
        },
    )

    events_mod.reset_event_subscriber()


async def _deliver(bus, event: DomainEvent) -> None:
    await bus.publisher.publish(event)
    for published in bus.publisher.sent:
        await bus.subscriber.deliver(published)


@pytest.mark.asyncio
async def test_publisher_to_subscriber_evicts_on_content_deleted(in_memory_bus, monkeypatch):
    """A published event travels publisher -> subscriber -> handler with no broker."""
    from app.core import events as events_mod

    await events_mod.start_event_subscriber()
    affected = [uuid4()]
    in_memory_bus.repo.delete_for_content = AsyncMock(return_value=affected)
    content_id = str(uuid4())

    await _deliver(
        in_memory_bus,
        _event({"content_id": content_id}, "content.deleted"),
    )

    assert len(in_memory_bus.publisher.sent) == 1
    in_memory_bus.repo.delete_for_content.assert_awaited_once_with(UUID(content_id))
    assert [c.args[0] for c in in_memory_bus.cache_invalidate.await_args_list] == affected
    _FakeSessionFactory.instances[-1].commit.assert_awaited_once()
    await events_mod.stop_event_subscriber()


@pytest.mark.asyncio
async def test_publisher_to_subscriber_evicts_on_content_unpublished(in_memory_bus, monkeypatch):
    from app.core import events as events_mod

    await events_mod.start_event_subscriber()
    content_id = str(uuid4())

    await _deliver(
        in_memory_bus, _event({"content_id": content_id}, "content.unpublished")
    )

    in_memory_bus.repo.delete_for_content.assert_awaited_once_with(UUID(content_id))
    await events_mod.stop_event_subscriber()


@pytest.mark.asyncio
async def test_publisher_to_subscriber_clears_on_billing_change(in_memory_bus, monkeypatch):
    """Subscription changes clear the user's rows and evict the cached rail."""
    from app.core import events as events_mod

    await events_mod.start_event_subscriber()
    user_id = str(uuid4())

    await _deliver(
        in_memory_bus, _event({"user_id": user_id}, "billing.subscription.created")
    )

    in_memory_bus.repo.clear_for_user.assert_awaited_once_with(UUID(user_id))
    in_memory_bus.repo.delete_for_content.assert_not_called()
    in_memory_bus.cache_invalidate.assert_awaited_once_with(UUID(user_id))
    await events_mod.stop_event_subscriber()


@pytest.mark.asyncio
async def test_publisher_validates_the_payload_before_delivery(in_memory_bus):
    """A non-JSON-safe payload is rejected by the port, not by the handler."""
    with pytest.raises(Exception):
        await in_memory_bus.publisher.publish(
            _event({"content_id": object()}, "content.deleted")
        )

    assert in_memory_bus.publisher.sent == []
    in_memory_bus.repo.delete_for_content.assert_not_called()


@pytest.mark.asyncio
async def test_an_unregistered_topic_never_reaches_the_handler(in_memory_bus, monkeypatch):
    from app.core import events as events_mod

    await events_mod.start_event_subscriber()

    await _deliver(in_memory_bus, _event({"content_id": str(uuid4())}, "content.published"))

    in_memory_bus.repo.delete_for_content.assert_not_called()
    await events_mod.stop_event_subscriber()


@pytest.mark.asyncio
async def test_redelivery_of_a_deleted_event_is_idempotent(in_memory_bus, monkeypatch):
    """At-least-once delivery: the second delete removes nothing but never fails."""
    from app.core import events as events_mod

    await events_mod.start_event_subscriber()
    content_id = str(uuid4())
    in_memory_bus.repo.delete_for_content = AsyncMock(return_value=[uuid4()])
    event = _event({"content_id": content_id}, "content.deleted")

    await in_memory_bus.subscriber.deliver(event)
    await in_memory_bus.subscriber.deliver(event)

    assert in_memory_bus.repo.delete_for_content.await_count == 2
    # Both deliveries targeted the same content id.
    assert {c.args[0] for c in in_memory_bus.repo.delete_for_content.await_args_list} == {
        UUID(content_id)
    }
    await events_mod.stop_event_subscriber()
