"""Coverage for media-pipeline infrastructure: events, DB manager, repos."""

from unittest.mock import AsyncMock, MagicMock, patch

from app.core import events as events_module
from app.core.events import (
    Event,
    InMemoryEventPublisher,
    KafkaEventPublisher,
    get_event_publisher,
    set_event_publisher,
)


class TestEvent:
    def test_to_dict_includes_all_fields(self):
        ev = Event(topic="content.encoded", key="job-1", payload={"ok": True})
        d = ev.to_dict()
        assert d["topic"] == "content.encoded"
        assert d["key"] == "job-1"
        assert d["payload"] == {"ok": True}
        assert d["event_id"]
        assert d["occurred_at"]

    def test_defaults_are_unique_per_instance(self):
        ev1 = Event(topic="a", key="1")
        ev2 = Event(topic="a", key="1")
        assert ev1.event_id != ev2.event_id


class TestInMemoryPublisher:
    async def test_publishes_and_records(self):
        pub = InMemoryEventPublisher()
        ev = Event(topic="content.scanned", key="k")
        await pub.publish(ev)
        assert pub.sent == [ev]

    async def test_publish_many(self):
        pub = InMemoryEventPublisher()
        await pub.publish_many([Event(topic="a", key="1"), Event(topic="b", key="2")])
        assert len(pub.sent) == 2


class TestKafkaPublisher:
    async def test_publish_sends_to_producer(self):
        pub = KafkaEventPublisher("localhost:9092")
        producer = AsyncMock()
        pub._producer = producer
        ev = Event(topic="content.encoded", key="k", payload={"x": 1})

        await pub.publish(ev)

        producer.send_and_wait.assert_awaited_once()
        call = producer.send_and_wait.await_args
        assert call.kwargs["topic"] == "content.encoded"
        assert call.kwargs["key"] == "k"
        assert call.kwargs["value"]["topic"] == "content.encoded"

    async def test_close_stops_producer(self):
        pub = KafkaEventPublisher("localhost:9092")
        producer = AsyncMock()
        pub._producer = producer

        await pub.close()

        producer.stop.assert_awaited_once()
        assert pub._producer is None


class TestPublisherRegistry:
    def test_get_builds_default_on_first_call(self):
        set_event_publisher(None)
        with patch.object(events_module, "_build_publisher", return_value=InMemoryEventPublisher()):
            pub = get_event_publisher()
        assert isinstance(pub, InMemoryEventPublisher)
        assert get_event_publisher() is pub

    def test_set_overrides(self):
        replacement = MagicMock()
        set_event_publisher(replacement)
        assert get_event_publisher() is replacement
        set_event_publisher(None)

    def test_build_selects_inmemory_by_default(self):
        set_event_publisher(None)
        with patch("app.core.settings.settings", MagicMock(EVENT_PUBLISHER="memory")):
            pub = events_module._build_publisher()
        assert isinstance(pub, InMemoryEventPublisher)

    def test_build_selects_kafka(self):
        with patch(
            "app.core.settings.settings",
            MagicMock(EVENT_PUBLISHER="kafka", KAFKA_BOOTSTRAP_SERVERS="k:9092"),
        ):
            pub = events_module._build_publisher()
        assert isinstance(pub, KafkaEventPublisher)


class TestDatabaseManager:
    async def test_health_check_success(self):
        from app.core.database import DatabaseManager

        engine = MagicMock()
        conn = AsyncMock()
        conn.__aenter__.return_value = conn
        engine.connect.return_value = conn
        DatabaseManager.engine = engine
        try:
            assert await DatabaseManager.health_check() is True
            conn.execute.assert_awaited_once()
        finally:
            DatabaseManager.engine = None

    async def test_health_check_failure_returns_false(self):
        from app.core.database import DatabaseManager

        engine = MagicMock()
        conn = AsyncMock()
        conn.__aenter__.return_value = conn
        conn.execute.side_effect = Exception("db down")
        engine.connect.return_value = conn
        DatabaseManager.engine = engine
        try:
            assert await DatabaseManager.health_check() is False
        finally:
            DatabaseManager.engine = None

    async def test_init_and_close(self):
        from app.core.database import DatabaseManager

        DatabaseManager.engine = None
        DatabaseManager.session_factory = None
        with patch("app.core.database.create_async_engine") as ce:
            await DatabaseManager.init()
        ce.assert_called_once()
        assert DatabaseManager.engine is not None
        assert DatabaseManager.session_factory is not None

        engine = MagicMock()
        engine.dispose = AsyncMock()
        DatabaseManager.engine = engine
        await DatabaseManager.close()
        engine.dispose.assert_awaited_once()
        DatabaseManager.engine = None
        DatabaseManager.session_factory = None
