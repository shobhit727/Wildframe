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
    monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
    repo_cls = MagicMock()
    repo = repo_cls.return_value
    repo.delete_for_content = AsyncMock(return_value=2)
    repo.session.commit = AsyncMock()
    monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)

    await events_mod._handle_content_deleted(_event({"content_id": content_id}))

    repo_cls.assert_called_once()
    repo.delete_for_content.assert_awaited_once_with(UUID(content_id))
    _FakeSessionFactory.instances[-1].commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_handler_evicts_rows_for_unpublished_content(monkeypatch):
    """content.unpublished removes stored recommendations for the title."""
    from app.core import events as events_mod

    content_id = str(uuid4())
    monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
    repo_cls = MagicMock()
    repo = repo_cls.return_value
    repo.delete_for_content = AsyncMock(return_value=1)
    repo.session.commit = AsyncMock()
    monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)

    await events_mod._handle_content_unpublished(
        _event({"content_id": content_id}, "content.unpublished")
    )

    repo.delete_for_content.assert_awaited_once_with(UUID(content_id))
    _FakeSessionFactory.instances[-1].commit.assert_awaited_once()


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
    repo_cls.return_value.delete_for_content = AsyncMock(return_value=1)
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
