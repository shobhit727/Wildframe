"""Behavioural tests for the event plumbing in ``app.core``.

Two modules:

* ``app.core.events`` — publisher selection by ``settings.EVENT_PUBLISHER``.
  Exercised with the SDK's ``InMemoryEventPublisher`` so no broker is needed.
* ``app.core.event_consumer`` — the ``user.moderated`` Kafka consumer that
  applies moderation decisions to ``users.is_active`` at the login boundary.
  The consumer is driven with a fake ``AIOKafkaConsumer``.
"""

import contextlib
import functools
import json
import logging
import re
from uuid import UUID
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.core import event_consumer
from app.models import Base
from app.core.events import (
    get_event_publisher,
    reset_event_publisher,
    user_registered_event,
)
from app.core.settings import settings
from sqlalchemy.sql.sqltypes import Uuid
from wildframe_events import DomainEvent, InMemoryEventPublisher, KafkaEventPublisher, Topic


@pytest.fixture(autouse=True)
def _reset_publisher():
    """The publisher is a process-wide singleton; never leak it between tests."""
    reset_event_publisher()
    yield
    reset_event_publisher()


# --------------------------------------------------------------------------
# app/core/events.py
# --------------------------------------------------------------------------


class TestGetEventPublisher:
    def test_default_publisher_is_in_memory(self):
        publisher = get_event_publisher()

        assert isinstance(publisher, InMemoryEventPublisher)

    def test_publisher_is_cached_across_calls(self):
        first = get_event_publisher()
        second = get_event_publisher()

        assert first is second

    def test_kafka_publisher_is_built_when_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "kafka")
        monkeypatch.setattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
        created = MagicMock(spec=KafkaEventPublisher)
        created.return_value = MagicMock(spec=KafkaEventPublisher)

        with patch("app.core.events.KafkaEventPublisher", created):
            publisher = get_event_publisher()

        created.assert_called_once_with(bootstrap_servers="kafka:29092", client_id="auth-service")
        assert publisher is created.return_value

    def test_kafka_publisher_is_only_built_once(self, monkeypatch):
        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "kafka")
        created = MagicMock(spec=KafkaEventPublisher)

        with patch("app.core.events.KafkaEventPublisher", created):
            first = get_event_publisher()
            second = get_event_publisher()

        assert first is second
        created.assert_called_once()

    def test_logs_which_publisher_was_selected(self, monkeypatch, caplog):
        with caplog.at_level(logging.INFO, logger="app.core.events"):
            get_event_publisher()

        assert "event publisher: in-memory" in caplog.text

    def test_logs_kafka_selection_with_bootstrap_servers(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "kafka")
        monkeypatch.setattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "broker:9092")

        with patch("app.core.events.KafkaEventPublisher", MagicMock()), caplog.at_level(
            logging.INFO, logger="app.core.events"
        ):
            get_event_publisher()

        assert "event publisher: kafka (broker:9092)" in caplog.text


class TestResetEventPublisher:
    def test_reset_drops_the_cached_instance(self):
        first = get_event_publisher()

        reset_event_publisher()
        second = get_event_publisher()

        assert first is not second
        assert isinstance(second, InMemoryEventPublisher)

    def test_reset_allows_switching_publisher_implementation(self, monkeypatch):
        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "kafka")
        kafka = MagicMock(spec=KafkaEventPublisher)
        with patch("app.core.events.KafkaEventPublisher", kafka):
            assert get_event_publisher() is kafka.return_value

        reset_event_publisher()
        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "memory")
        assert isinstance(get_event_publisher(), InMemoryEventPublisher)


class TestUserRegisteredEvent:
    def test_builds_the_canonical_envelope(self):
        event = user_registered_event("user-1", "user@example.com")

        assert isinstance(event, DomainEvent)
        assert event.topic == Topic.USER_REGISTERED
        assert event.key == "registered:user-1"
        assert event.payload == {"user_id": "user-1", "email": "user@example.com"}
        assert event.producer == "auth-service"

    def test_idempotency_key_is_per_user(self):
        first = user_registered_event("user-1", "a@example.com")
        second = user_registered_event("user-1", "a@example.com")

        assert first.key == second.key
        assert first.event_id != second.event_id

    def test_is_publishable_through_the_in_memory_publisher(self):
        """The envelope must satisfy the SDK's payload validation contract."""
        import asyncio

        publisher = InMemoryEventPublisher()
        event = user_registered_event("user-2", "user2@example.com")

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            publisher.publish(event)
        )

        assert publisher.sent == [event]

    def test_publisher_selection_drives_a_real_publish(self):
        import asyncio

        publisher = get_event_publisher()
        event = user_registered_event("user-3", "user3@example.com")

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(publisher.publish(event))
        finally:
            loop.close()

        assert [e.key for e in publisher.sent] == ["registered:user-3"]


# --------------------------------------------------------------------------
# app/core/event_consumer.py
# --------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, payload: dict):
        self.value = json.dumps(payload).encode("utf-8")


class _FakeConsumer:
    """Minimal AIOKafkaConsumer stand-in that yields a fixed message list."""

    instances: list["_FakeConsumer"] = []

    def __init__(self, *topics, **kwargs):
        self.topics = topics
        self.kwargs = kwargs
        self.messages: list[_FakeMessage] = []
        self.started = False
        self.stopped = False
        self.commits = 0
        self.stop_error: Exception | None = None
        self.start_error: Exception | None = None
        type(self).instances.append(self)

    async def start(self):
        if self.start_error:
            raise self.start_error
        self.started = True

    def __aiter__(self):
        async def gen():
            for msg in self.messages:
                yield msg

        return gen()

    async def commit(self):
        self.commits += 1

    async def stop(self):
        if self.stop_error:
            raise self.stop_error
        self.stopped = True


@pytest.fixture
def fake_consumer():
    _FakeConsumer.instances = []
    with patch.object(event_consumer, "AIOKafkaConsumer", _FakeConsumer):
        yield _FakeConsumer
    _FakeConsumer.instances = []


@contextlib.contextmanager
def _seeded(message):
    """Pre-load the fake consumer's queue for the instance built inside the task.

    ``run_user_moderation_consumer`` constructs the consumer synchronously, so
    seeding its ``__init__`` is enough to make the async-for drain the message.
    """
    original_init = _FakeConsumer.__init__

    def init_with_message(self, *topics, **kwargs):
        original_init(self, *topics, **kwargs)
        self.messages = [message if not isinstance(message, dict) else _FakeMessage(message)]

    with patch.object(_FakeConsumer, "__init__", init_with_message):
        yield


@contextlib.contextmanager
def _uuid_binds_accept_str():
    """Let SQLAlchemy's emulated UUID type accept a plain string, like asyncpg.

    ``_apply_moderation`` (app/core/event_consumer.py:33) binds the raw
    ``user_id`` — typed ``str``, straight out of the JSON event — against the
    ``postgresql.UUID`` column. On Postgres ``supports_native_uuid`` is True, so
    no bind processor runs and asyncpg's uuid codec parses the string. The
    SQLite test backend has no native UUID type, so the *emulated* processor
    calls ``value.hex`` and raises on a str. Wrapping ``Uuid.bind_processor``
    reproduces the asyncpg behaviour for the duration of a test, which lets the
    shipped code be asserted on SQLite.

    Must be installed *before* the first statement of the test: SQLAlchemy
    memoises the resolved bind processor on ``dialect._type_memos``, so a
    processor cached before the patch would still be used. Use the
    ``uuid_str_binds`` fixture, which also evicts the memo on teardown.
    """
    original = Uuid.bind_processor

    @functools.wraps(original)
    def bind_processor(self, dialect):
        inner = original(self, dialect)
        if inner is None:
            return None

        def process(value):
            if isinstance(value, str):
                value = UUID(value)
            return inner(value)

        return process

    Uuid.bind_processor = bind_processor
    try:
        yield
    finally:
        Uuid.bind_processor = original


def _evict_uuid_bind_memos(dialect) -> None:
    """Drop memoised UUID bind processors so the next compile re-reads them."""
    for key in [k for k in list(dialect._type_memos) if isinstance(k, Uuid)]:
        dialect._type_memos[key].pop("bind", None)


@pytest.fixture
def uuid_str_binds(test_session, test_engine):
    with _uuid_binds_accept_str():
        yield test_session
    _evict_uuid_bind_memos(test_engine.dialect)


def _make_factory(session):
    """An async context manager factory that yields the given session."""

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    return _Factory()


class TestApplyModeration:
    async def test_status_active_reactivates_the_user(self, test_session, uuid_str_binds):
        from app.models import User
        from app.security import PasswordManager

        user = User(
            email="mod-active@example.com",
            password_hash=PasswordManager.hash_password("Secret1234!"),
            is_active=False,
        )
        test_session.add(user)
        await test_session.commit()

        await event_consumer._apply_moderation(
            _make_factory(uuid_str_binds), str(user.id), "active"
        )

        await test_session.refresh(user)
        assert user.is_active is True

    async def test_status_suspended_deactivates_the_user(self, test_session, uuid_str_binds):
        from app.models import User
        from app.security import PasswordManager

        user = User(
            email="mod-suspend@example.com",
            password_hash=PasswordManager.hash_password("Secret1234!"),
            is_active=True,
        )
        test_session.add(user)
        await test_session.commit()

        await event_consumer._apply_moderation(
            _make_factory(uuid_str_binds), str(user.id), "suspended"
        )

        await test_session.refresh(user)
        assert user.is_active is False

    async def test_status_banned_deactivates_the_user(self, test_session, uuid_str_binds):
        from app.models import User
        from app.security import PasswordManager

        user = User(
            email="mod-ban@example.com",
            password_hash=PasswordManager.hash_password("Secret1234!"),
            is_active=True,
        )
        test_session.add(user)
        await test_session.commit()

        await event_consumer._apply_moderation(
            _make_factory(uuid_str_binds), str(user.id), "banned"
        )

        await test_session.refresh(user)
        assert user.is_active is False

    async def test_reapplying_the_same_status_is_idempotent(
        self, test_session, uuid_str_binds
    ):
        from app.models import User
        from app.security import PasswordManager

        user = User(
            email="mod-idem@example.com",
            password_hash=PasswordManager.hash_password("Secret1234!"),
            is_active=True,
        )
        test_session.add(user)
        await test_session.commit()

        factory = _make_factory(uuid_str_binds)
        await event_consumer._apply_moderation(factory, str(user.id), "suspended")
        await event_consumer._apply_moderation(factory, str(user.id), "suspended")

        await test_session.refresh(user)
        assert user.is_active is False

    async def test_unknown_user_matches_zero_rows_without_raising(
        self, test_session, uuid_str_binds
    ):
        # The UPDATE is a no-op for a user that no longer exists.
        await event_consumer._apply_moderation(
            _make_factory(uuid_str_binds), "00000000-0000-0000-0000-000000000000", "suspended"
        )


class TestRunUserModerationConsumer:
    async def test_subscribes_to_the_documented_topic_and_group(self, fake_consumer):
        await event_consumer.run_user_moderation_consumer(_make_factory(AsyncMock()))

        consumer = fake_consumer.instances[0]
        assert consumer.topics == (event_consumer.USER_MODERATED_TOPIC,)
        assert consumer.kwargs["group_id"] == event_consumer.CONSUMER_GROUP
        assert consumer.kwargs["enable_auto_commit"] is False
        assert consumer.kwargs["auto_offset_reset"] == "earliest"
        assert consumer.started is True
        assert consumer.stopped is True

    async def test_applies_a_sdk_enveloped_moderation_event(
        self, fake_consumer, test_session, uuid_str_binds
    ):
        from app.models import User
        from app.security import PasswordManager

        user = User(
            email="consumer@example.com",
            password_hash=PasswordManager.hash_password("Secret1234!"),
            is_active=True,
        )
        test_session.add(user)
        await test_session.commit()

        with _seeded(
            {
                "event_id": "evt-1",
                "topic": "user.moderated",
                "payload": {"user_id": str(user.id), "status": "suspended"},
            }
        ):
            await event_consumer.run_user_moderation_consumer(_make_factory(uuid_str_binds))

        await test_session.refresh(user)
        assert user.is_active is False

    async def test_applies_a_bare_payload_without_an_envelope(
        self, fake_consumer, test_session, uuid_str_binds
    ):
        from app.models import User
        from app.security import PasswordManager

        user = User(
            email="bare@example.com",
            password_hash=PasswordManager.hash_password("Secret1234!"),
            is_active=False,
        )
        test_session.add(user)
        await test_session.commit()

        with _seeded({"user_id": str(user.id), "status": "active"}):
            await event_consumer.run_user_moderation_consumer(_make_factory(uuid_str_binds))

        await test_session.refresh(user)
        assert user.is_active is True

    async def test_commits_the_offset_even_for_a_malformed_message(
        self, fake_consumer, test_session, uuid_str_binds
    ):
        # "status" is missing, so the apply raises and the inner except fires.
        with _seeded({"user_id": "x"}):
            await event_consumer.run_user_moderation_consumer(_make_factory(uuid_str_binds))

        consumer = fake_consumer.instances[0]
        assert consumer.commits == 1
        assert consumer.stopped is True

    async def test_invalid_json_does_not_kill_the_loop(
        self, fake_consumer, test_session, uuid_str_binds, caplog
    ):
        class _BadMessage:
            value = b"not-json"

        with _seeded(_BadMessage()), caplog.at_level(
            logging.ERROR, logger="app.core.event_consumer"
        ):
            await event_consumer.run_user_moderation_consumer(_make_factory(test_session))

        assert "failed to apply user.moderated message" in caplog.text
        assert fake_consumer.instances[0].stopped is True

    async def test_broker_failure_is_swallowed_and_stops_the_consumer(self, fake_consumer):
        original_init = _FakeConsumer.__init__

        def failing_init(self, *topics, **kwargs):
            original_init(self, *topics, **kwargs)
            self.start_error = ConnectionError("no broker")

        with patch.object(_FakeConsumer, "__init__", failing_init), patch(
            "asyncio.sleep", AsyncMock()
        ):
            await event_consumer.run_user_moderation_consumer(_make_factory(AsyncMock()))

        consumer = fake_consumer.instances[0]
        assert consumer.started is False
        assert consumer.stopped is True

    async def test_stop_failure_does_not_propagate(self, fake_consumer):
        original_init = _FakeConsumer.__init__

        def failing_stop_init(self, *topics, **kwargs):
            original_init(self, *topics, **kwargs)
            self.stop_error = RuntimeError("already closed")

        with patch.object(_FakeConsumer, "__init__", failing_stop_init):
            await event_consumer.run_user_moderation_consumer(_make_factory(AsyncMock()))

    async def test_bootstrap_servers_come_from_the_environment(
        self, fake_consumer, monkeypatch
    ):
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "broker-from-env:9092")

        await event_consumer.run_user_moderation_consumer(_make_factory(AsyncMock()))

        assert fake_consumer.instances[0].kwargs["bootstrap_servers"] == (
            "broker-from-env:9092"
        )

    async def test_bootstrap_servers_fall_back_to_the_compose_default(
        self, fake_consumer, monkeypatch
    ):
        monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)

        await event_consumer.run_user_moderation_consumer(_make_factory(AsyncMock()))

        assert fake_consumer.instances[0].kwargs["bootstrap_servers"] == "kafka:29092"


class TestModerationTimestamp:
    def test_returns_an_iso_utc_timestamp(self):
        stamp = event_consumer.moderation_timestamp()

        parsed = datetime.fromisoformat(stamp)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timedelta(0)

    def test_advances_between_calls(self):
        first = datetime.fromisoformat(event_consumer.moderation_timestamp())
        second = datetime.fromisoformat(event_consumer.moderation_timestamp())

        assert second >= first
        assert (second - first) < timedelta(seconds=5)
