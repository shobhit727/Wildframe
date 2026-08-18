"""Tests for the search-service event subscriber (#227).

Verifies that content.deleted / content.unpublished events remove documents
from the index, that malformed payloads are tolerated, and that the
subscriber wiring selects the right transport per EVENT_PUBLISHER.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from wildframe_events import DomainEvent, InMemoryEventSubscriber


@pytest.fixture(autouse=True)
def _fresh_subscriber():
    from app.core.events import reset_event_subscriber

    reset_event_subscriber()
    yield
    reset_event_subscriber()


def _event(topic: str, content_id: str | None, key: str | None = None) -> DomainEvent:
    payload = {"content_id": content_id} if content_id is not None else {}
    return DomainEvent(
        topic=topic,
        key=key or f"key:{content_id}",
        payload=payload,
        producer="content-service",
    )


class _FakeSessionFactory:
    """Stand-in for async_sessionmaker: yields one mock session per use."""

    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, *exc):
        return False


def _fake_sessions():
    return _FakeSessionFactory()


class TestDeleteHandlers:
    @pytest.mark.asyncio
    async def test_deleted_event_removes_document(self, monkeypatch):
        from app.core import events as events_mod

        content_id = str(uuid4())
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
        es = MagicMock()
        es.delete = AsyncMock(return_value={"result": "deleted"})
        monkeypatch.setattr("app.api.search_routes.es_client", lambda: es)

        await events_mod._handle_content_deleted(_event("content.deleted", content_id))

        es.delete.assert_awaited_once()
        assert es.delete.await_args.kwargs["id"] == content_id

    @pytest.mark.asyncio
    async def test_unpublished_event_removes_document(self, monkeypatch):
        from app.core import events as events_mod

        content_id = str(uuid4())
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
        es = MagicMock()
        es.delete = AsyncMock(return_value={"result": "deleted"})
        monkeypatch.setattr("app.api.search_routes.es_client", lambda: es)

        await events_mod._handle_content_unpublished(_event("content.unpublished", content_id))

        es.delete.assert_awaited_once()
        assert es.delete.await_args.kwargs["id"] == content_id

    @pytest.mark.asyncio
    async def test_double_delivery_is_tolerated(self, monkeypatch):
        """Re-delivery must not raise: delete-by-_id on an absent doc is a no-op."""
        from app.core import events as events_mod

        content_id = str(uuid4())
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
        es = MagicMock()
        es.delete = AsyncMock(return_value={"result": "not_found"})
        monkeypatch.setattr("app.api.search_routes.es_client", lambda: es)

        event = _event("content.deleted", content_id)
        await events_mod._handle_content_deleted(event)
        await events_mod._handle_content_deleted(event)

        assert es.delete.await_count == 2

    @pytest.mark.asyncio
    async def test_event_without_content_id_is_dropped(self, monkeypatch):
        from app.core import events as events_mod

        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
        es = MagicMock()
        es.delete = AsyncMock(return_value={"result": "deleted"})
        monkeypatch.setattr("app.api.search_routes.es_client", lambda: es)

        await events_mod._handle_content_deleted(_event("content.deleted", None))

        es.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_event_with_invalid_content_id_is_dropped(self, monkeypatch):
        from app.core import events as events_mod

        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
        es = MagicMock()
        es.delete = AsyncMock(return_value={"result": "deleted"})
        monkeypatch.setattr("app.api.search_routes.es_client", lambda: es)

        await events_mod._handle_content_deleted(_event("content.deleted", "not-a-uuid"))

        es.delete.assert_not_awaited()


class TestSubscriberWiring:
    @pytest.mark.asyncio
    async def test_inmemory_subscriber_registers_handlers(self, monkeypatch):
        from app.core import events as events_mod

        sub = InMemoryEventSubscriber()
        monkeypatch.setattr(events_mod, "get_event_subscriber", lambda: sub)

        await events_mod.start_event_subscriber()

        assert "content.deleted" in sub._handlers
        assert "content.unpublished" in sub._handlers

    @pytest.mark.asyncio
    async def test_inmemory_subscriber_delivers_to_handler(self, monkeypatch):
        """End-to-end through the in-memory bus: deliver -> document removed."""
        from app.core import events as events_mod

        sub = InMemoryEventSubscriber()
        monkeypatch.setattr(events_mod, "get_event_subscriber", lambda: sub)
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _fake_sessions)
        es = MagicMock()
        es.delete = AsyncMock(return_value={"result": "deleted"})
        monkeypatch.setattr("app.api.search_routes.es_client", lambda: es)
        await events_mod.start_event_subscriber()

        content_id = str(uuid4())
        await sub.deliver(_event("content.deleted", content_id))

        es.delete.assert_awaited_once()
        assert es.delete.await_args.kwargs["id"] == content_id

    def test_kafka_selection(self, monkeypatch):
        from app.core import events as events_mod
        from app.core.settings import settings

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "kafka")
        monkeypatch.setattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
        with patch.object(events_mod, "RedisDeduplicationStore") as dedup:
            with patch.object(events_mod, "KafkaEventSubscriber") as ksub:
                sub = events_mod.get_event_subscriber()
        assert sub is not None
        ksub.assert_called_once()
        assert ksub.call_args.kwargs["group_id"] == "search-service"
        assert ksub.call_args.kwargs["bootstrap_servers"] == "kafka:9092"
        dedup.assert_called_once()

    def test_memory_selection(self, monkeypatch):
        from app.core import events as events_mod
        from app.core.settings import settings

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "memory")
        assert isinstance(events_mod.get_event_subscriber(), InMemoryEventSubscriber)
