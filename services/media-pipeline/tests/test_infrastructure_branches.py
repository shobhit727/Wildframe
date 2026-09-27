"""Uncovered branches in the DB pool configuration and the Kafka publisher.

* ``core/database.py:29-30`` — the SQLite arm of ``DatabaseManager.init()``. The
  default ``DATABASE_URL`` is PostgreSQL, so the in-memory test path never ran:
  the assertion that SQLite gets *no* pool budget and *no* connect args (NullPool
  behaviour) was unverified.
* ``core/events.py:76-82`` — ``KafkaEventPublisher._get_producer``. Every
  existing test injected a pre-built ``_producer``, so the factory never ran and
  its two serializer lambdas — the actual Kafka wire encoding — were never
  executed once.
* ``core/events.py:48`` — the ``pass`` in the abstract ``EventPublisher.publish``.
  See :class:`TestAbstractPublisherContract`.
"""

import inspect
import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from app.core.database import DatabaseManager
from app.core.settings import settings
from app.core.events import (
    Event,
    EventPublisher,
    InMemoryEventPublisher,
    KafkaEventPublisher,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_db_manager():
    DatabaseManager.engine = None
    DatabaseManager.session_factory = None
    yield
    DatabaseManager.engine = None
    DatabaseManager.session_factory = None


class TestEnginePoolConfiguration:
    """``init()`` must size the pool differently for SQLite and PostgreSQL."""

    async def test_sqlite_gets_no_pool_budget_and_no_connect_args(self):
        with (
            patch.object(settings, "DATABASE_URL", "sqlite+aiosqlite:///:memory:"),
            patch("app.core.database.create_async_engine") as create_engine,
        ):
            await DatabaseManager.init()

        kwargs = create_engine.call_args.kwargs
        assert kwargs["connect_args"] == {}, "SQLite must not get asyncpg timeouts"
        for pool_kwarg in ("pool_size", "max_overflow", "pool_timeout", "pool_recycle"):
            assert pool_kwarg not in kwargs, f"{pool_kwarg} is QueuePool-only and breaks SQLite"

    async def test_postgres_gets_the_capped_pool_and_server_side_timeouts(self):
        with patch("app.core.database.create_async_engine") as create_engine:
            await DatabaseManager.init()  # default DATABASE_URL is PostgreSQL

        kwargs = create_engine.call_args.kwargs
        assert kwargs["pool_size"] == 5
        assert kwargs["max_overflow"] == 5
        assert kwargs["pool_pre_ping"] is True
        assert kwargs["connect_args"]["command_timeout"] == 30
        server_settings = kwargs["connect_args"]["server_settings"]
        assert server_settings["statement_timeout"] == "10000"
        assert server_settings["lock_timeout"] == "5000"
        assert server_settings["idle_in_transaction_session_timeout"] == "30000"

    async def test_the_url_is_handed_to_the_engine_unchanged(self):
        url = "postgresql+asyncpg://u:p@db.internal:5432/media_db"
        with (
            patch.object(settings, "DATABASE_URL", url),
            patch("app.core.database.create_async_engine") as create_engine,
        ):
            await DatabaseManager.init()
        assert create_engine.call_args.args[0] == url

    async def test_init_wires_a_session_factory_onto_the_new_engine(self):
        with patch("app.core.database.create_async_engine") as create_engine:
            await DatabaseManager.init()
        assert DatabaseManager.engine is create_engine.return_value
        assert DatabaseManager.session_factory is not None
        assert DatabaseManager.session_factory.kw["expire_on_commit"] is False

    async def test_health_check_reports_a_real_select_one(self):
        """The readiness probe must issue a statement, not a lambda."""
        with patch("app.core.database.create_async_engine") as create_engine:
            await DatabaseManager.init()
        conn = AsyncMock()
        conn.__aenter__.return_value = conn
        create_engine.return_value.connect.return_value = conn

        assert await DatabaseManager.health_check() is True
        statement = conn.execute.await_args.args[0]
        assert statement.text == "SELECT 1"
        assert str(statement.compile(dialect=postgresql.dialect())) == "SELECT 1"


@pytest.fixture
def producer_cls():
    """Patch ``AIOKafkaProducer`` with a double whose coroutines are awaitable.

    ``MagicMock`` auto-specs are not awaitable, and ``_get_producer`` awaits
    ``start()``; without this the tests would fail on the mock rather than on
    the code under test.
    """
    with patch("app.core.events.AIOKafkaProducer") as cls:
        cls.return_value.start = AsyncMock()
        cls.return_value.stop = AsyncMock()
        cls.return_value.send_and_wait = AsyncMock()
        yield cls


class TestKafkaProducerFactory:
    """``_get_producer`` constructs and caches the producer, serializers included."""

    async def test_the_producer_is_built_from_the_configured_bootstrap_servers(self, producer_cls):
        pub = KafkaEventPublisher("broker-1:9092,broker-2:9092", client_id="mp-test")
        producer = await pub._get_producer()

        assert producer_cls.call_args.kwargs["bootstrap_servers"] == "broker-1:9092,broker-2:9092"
        assert producer_cls.call_args.kwargs["client_id"] == "mp-test"
        assert producer is producer_cls.return_value

    async def test_the_producer_is_started_before_first_use(self, producer_cls):
        pub = KafkaEventPublisher("k:9092")
        producer = await pub._get_producer()
        producer.start.assert_awaited_once()

    async def test_the_producer_is_created_once_and_then_reused(self, producer_cls):
        pub = KafkaEventPublisher("k:9092")
        first = await pub._get_producer()
        second = await pub._get_producer()
        third = await pub._get_producer()

        assert first is second is third
        assert producer_cls.call_count == 1, "the producer must be cached, not rebuilt"
        assert first.start.await_count == 1

    async def test_the_value_serializer_emits_utf8_json(self, producer_cls):
        """The wire format for every domain event."""
        pub = KafkaEventPublisher("k:9092")
        await pub._get_producer()
        value_serializer = producer_cls.call_args.kwargs["value_serializer"]

        event = Event(topic="content.encoded", key="job-1", payload={"stage": "encode"})
        encoded = value_serializer(event.to_dict())

        assert isinstance(encoded, bytes)
        assert json.loads(encoded.decode("utf-8")) == event.to_dict()

    async def test_the_key_serializer_encodes_a_key_and_passes_none_through(self, producer_cls):
        """``None`` keys must stay ``None`` — Kafka uses them to round-robin."""
        pub = KafkaEventPublisher("k:9092")
        await pub._get_producer()
        key_serializer = producer_cls.call_args.kwargs["key_serializer"]

        assert key_serializer("job-1") == b"job-1"
        assert key_serializer(None) is None
        assert key_serializer("") is None, "an empty key must not become b''"

    async def test_publishing_builds_the_producer_and_sends_the_event(self, producer_cls):
        pub = KafkaEventPublisher("k:9092")
        await pub.publish(Event(topic="content.published", key="job-9", payload={"a": 1}))

        producer = producer_cls.return_value
        producer.start.assert_awaited_once()
        producer.send_and_wait.assert_awaited_once()
        call = producer.send_and_wait.await_args.kwargs
        assert call["topic"] == "content.published"
        assert call["key"] == "job-9"
        assert call["value"]["event_id"]

    async def test_close_then_reuse_rebuilds_the_producer(self, producer_cls):
        pub = KafkaEventPublisher("k:9092")
        await pub._get_producer()
        await pub.close()
        assert pub._producer is None
        await pub._get_producer()

        assert producer_cls.call_count == 2, "a closed producer must not be handed back out"


class TestAbstractPublisherContract:
    """``core/events.py:48`` is structurally dead — proven here rather than faked.

    ``EventPublisher.publish`` is an ``@abstractmethod`` whose body is ``pass``.
    Python's ABC machinery refuses instantiation and requires a concrete
    override, so the base body is never executed. Asserting the *contract*
    (uninstantiable + still abstract) fails if someone drops the decorator and
    silently turns the base body into a real no-op that drops events.
    """

    def test_the_base_publisher_cannot_be_instantiated(self):
        with pytest.raises(TypeError):
            EventPublisher()  # type: ignore[abstract]

    def test_publish_is_still_declared_abstract(self):
        assert getattr(EventPublisher.publish, "__isabstractmethod__", False) is True

    def test_every_concrete_publisher_implements_publish(self):
        for impl in (InMemoryEventPublisher, KafkaEventPublisher):
            assert not getattr(impl.publish, "__isabstractmethod__", False)
            assert impl.publish is not EventPublisher.publish

    def test_the_abstract_signature_is_a_coroutine(self):
        assert inspect.iscoroutinefunction(EventPublisher.publish)
