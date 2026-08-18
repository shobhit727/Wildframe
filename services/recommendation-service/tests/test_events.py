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

    await events_mod._handle_content_unpublished(_event({"content_id": content_id}, "content.unpublished"))

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
    """start_event_subscriber subscribes to deleted + unpublished."""
    from app.core import events as events_mod

    subscriber = MagicMock()
    subscriber.subscribe = AsyncMock()
    subscriber.start = AsyncMock()
    monkeypatch.setattr(events_mod, "get_event_subscriber", lambda: subscriber)

    await events_mod.start_event_subscriber()

    calls = [c.args[0] for c in subscriber.subscribe.await_args_list]
    assert calls == ["content.deleted", "content.unpublished"]
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
