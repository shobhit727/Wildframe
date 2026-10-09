"""Event-bus wiring for search-service: transport selection, start/stop and
the lazy database bootstrap inside the delete handler.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from wildframe_events import DomainEvent, InMemoryEventSubscriber

from app.core import events as events_mod


@pytest.fixture(autouse=True)
def _fresh_subscriber():
    events_mod.reset_event_subscriber()
    yield
    events_mod.reset_event_subscriber()


def _event(topic: str, content_id: str | None) -> DomainEvent:
    payload = {"content_id": content_id} if content_id is not None else {}
    return DomainEvent(
        topic=topic, key=f"key:{content_id}", payload=payload, producer="content-service"
    )


def _fake_es(result: str = "deleted") -> MagicMock:
    es = MagicMock()
    es.delete = AsyncMock(return_value={"result": result})
    return es


class _Session:
    """Async session scope; the session is spec'd so awaited repo calls work."""

    def __init__(self, factory_calls: list):
        factory_calls.append(self)

    async def __aenter__(self):
        return MagicMock(spec=AsyncSession)

    async def __aexit__(self, *exc):
        return False


class _Factory:
    def __init__(self):
        self.sessions: list = []

    def begin(self):
        return _Session(self.sessions)

    __call__ = begin


class TestLazyDatabaseBootstrap:
    @pytest.mark.asyncio
    async def test_handler_initialises_the_database_when_uninitialised(self, monkeypatch):
        """The handler must self-heal rather than drop the event when the
        subscriber starts before DatabaseManager.init()."""
        content_id = str(uuid4())
        factory = _Factory()
        initialised: list[str] = []

        async def fake_init():
            initialised.append("init")
            events_mod.DatabaseManager.session_factory = factory

        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", None)
        monkeypatch.setattr(events_mod.DatabaseManager, "init", fake_init)
        es = _fake_es()
        monkeypatch.setattr("app.api.search_routes.es_client", lambda: es)

        await events_mod._handle_content_deleted(_event("content.deleted", content_id))

        assert initialised == ["init"]
        assert len(factory.sessions) == 1
        assert es.delete.await_args.kwargs["id"] == content_id

    @pytest.mark.asyncio
    async def test_handler_uses_the_existing_factory_when_available(self, monkeypatch):
        content_id = str(uuid4())
        factory = _Factory()
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", factory)
        monkeypatch.setattr(
            events_mod.DatabaseManager,
            "init",
            AsyncMock(side_effect=AssertionError("must not re-init")),
        )
        monkeypatch.setattr("app.api.search_routes.es_client", lambda: _fake_es())

        await events_mod._handle_content_deleted(_event("content.deleted", content_id))

        assert len(factory.sessions) == 1


class TestTransportSelection:
    def test_kafka_subscriber_uses_the_redis_dedup_store(self, monkeypatch):
        from app.core.settings import settings

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "kafka")
        monkeypatch.setattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
        monkeypatch.setattr(settings, "REDIS_URL", "redis://redis:6379/2")

        with (
            patch.object(events_mod, "RedisDeduplicationStore") as dedup,
            patch.object(events_mod, "KafkaEventSubscriber") as ksub,
            patch("wildframe_events.KafkaEventPublisher") as kpub,
        ):
            sub = events_mod.get_event_subscriber()

        assert sub is not None
        dedup.assert_called_once_with(
            redis_url="redis://redis:6379/2", key_prefix="wf:dedup:search"
        )
        assert ksub.call_args.kwargs["dedup_store"] is dedup.return_value
        assert ksub.call_args.kwargs["dlq_publisher"] is kpub.return_value
        assert kpub.call_args.kwargs["client_id"] == "search-service-dlq"

    def test_redis_dedup_failure_degrades_to_no_dedup(self, monkeypatch):
        """A broken Redis must not stop the subscriber from starting."""
        from app.core.settings import settings

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "kafka")
        monkeypatch.setattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")

        with (
            patch.object(
                events_mod,
                "RedisDeduplicationStore",
                side_effect=RuntimeError("redis unreachable"),
            ),
            patch.object(events_mod, "KafkaEventSubscriber") as ksub,
            patch("wildframe_events.KafkaEventPublisher"),
        ):
            sub = events_mod.get_event_subscriber()

        assert sub is ksub.return_value
        assert ksub.call_args.kwargs["dedup_store"] is None

    def test_subscriber_is_cached_between_calls(self, monkeypatch):
        from app.core.settings import settings

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "memory")

        first = events_mod.get_event_subscriber()
        second = events_mod.get_event_subscriber()

        assert first is second

    def test_reset_drops_the_cache(self, monkeypatch):
        from app.core.settings import settings

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "memory")
        first = events_mod.get_event_subscriber()

        events_mod.reset_event_subscriber()

        assert events_mod.get_event_subscriber() is not first


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_registers_handlers_and_starts(self, monkeypatch):
        from app.core.settings import settings

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "memory")

        await events_mod.start_event_subscriber()

        sub = events_mod.get_event_subscriber()
        assert isinstance(sub, InMemoryEventSubscriber)
        assert sub._handlers["content.deleted"]
        assert sub._handlers["content.unpublished"]

    @pytest.mark.asyncio
    async def test_start_failure_is_swallowed(self, monkeypatch):
        """A broker outage must not crash startup: reindex remains the backstop."""
        subscriber = MagicMock()
        subscriber.subscribe = AsyncMock(side_effect=RuntimeError("kafka down"))
        subscriber.start = AsyncMock()
        monkeypatch.setattr(events_mod, "get_event_subscriber", lambda: subscriber)

        await events_mod.start_event_subscriber()  # must not raise

        subscriber.start.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_start_failure_on_start_is_swallowed(self, monkeypatch):
        subscriber = MagicMock()
        subscriber.subscribe = AsyncMock()
        subscriber.start = AsyncMock(side_effect=RuntimeError("kafka down"))
        monkeypatch.setattr(events_mod, "get_event_subscriber", lambda: subscriber)

        await events_mod.start_event_subscriber()  # must not raise

        subscriber.subscribe.assert_awaited()

    @pytest.mark.asyncio
    async def test_stop_closes_the_running_subscriber(self, monkeypatch):
        from app.core.settings import settings

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "memory")
        await events_mod.start_event_subscriber()
        sub = events_mod.get_event_subscriber()
        sub.stop = AsyncMock()

        await events_mod.stop_event_subscriber()

        sub.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_is_a_noop_without_a_subscriber(self):
        assert events_mod._subscriber is None

        await events_mod.stop_event_subscriber()  # must not raise

    @pytest.mark.asyncio
    async def test_stop_failure_is_suppressed(self, monkeypatch):
        subscriber = MagicMock()
        subscriber.stop = AsyncMock(side_effect=RuntimeError("already closed"))
        monkeypatch.setattr(events_mod, "get_event_subscriber", lambda: subscriber)

        await events_mod.stop_event_subscriber()  # must not raise
