"""``app/core/events.py`` — the domain event, the publisher port, the adapters.

The event bus is the service's only integration surface with the rest of the
platform, so what matters here is:

* ``Event`` is a self-describing, JSON-serializable envelope with a unique id
  and an ISO-8601 UTC timestamp.
* the default ``InMemoryEventPublisher`` records events, so the service is fully
  functional with no broker.
* ``KafkaEventPublisher`` is a real adapter behind the same port: it keys by
  session id so one session's events stay in one partition, serializes to JSON,
  and — importantly — stops a half-started producer on a failed ``start()``
  instead of leaking it.

``aiokafka`` is not installed here (and must not be), so the Kafka adapter is
driven against an injected fake module.
"""

import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.events import (
    Event,
    EventPublisher,
    InMemoryEventPublisher,
    KafkaEventPublisher,
    _build_publisher,
    get_event_publisher,
    set_event_publisher,
)
from app.core.settings import settings


@pytest.fixture(autouse=True)
def _reset_publisher_singleton():
    set_event_publisher(InMemoryEventPublisher())
    yield
    set_event_publisher(InMemoryEventPublisher())


# ---------------------------------------------------------------------------
# Event.
# ---------------------------------------------------------------------------


def test_event_defaults_are_generated_per_instance():
    first, second = Event(topic="content.uploaded", key="s1"), Event(
        topic="content.uploaded", key="s1"
    )
    assert first.event_id != second.event_id
    uuid4  # uuid4 is a valid factory; the ids must be real UUID strings
    from uuid import UUID

    assert UUID(first.event_id)
    assert first.payload == {}


def test_event_timestamp_is_iso8601_utc():
    event = Event(topic="t", key="k")
    assert event.occurred_at.endswith("+00:00")
    from datetime import datetime

    assert datetime.fromisoformat(event.occurred_at).utcoffset().total_seconds() == 0


def test_event_to_dict_is_json_serializable_and_complete():
    event = Event(topic="content.uploaded", key="s1", payload={"size": 10})
    body = event.to_dict()
    assert body == {
        "event_id": event.event_id,
        "topic": "content.uploaded",
        "key": "s1",
        "occurred_at": event.occurred_at,
        "payload": {"size": 10},
    }
    # A transport must be able to serialize it without a custom encoder.
    assert json.loads(json.dumps(body))["payload"] == {"size": 10}


def test_event_payload_defaults_do_not_leak_between_instances():
    first = Event(topic="t", key="k")
    first.payload["x"] = 1
    assert Event(topic="t", key="k").payload == {}


# ---------------------------------------------------------------------------
# The publisher port.
# ---------------------------------------------------------------------------


async def test_publish_is_abstract():
    with pytest.raises(NotImplementedError):
        await EventPublisher.publish(object(), Event(topic="t", key="k"))


async def test_publish_many_defaults_to_sequential_publish():
    class Counting(EventPublisher):
        def __init__(self) -> None:
            self.seen: list[str] = []

        async def publish(self, event: Event) -> None:
            self.seen.append(event.event_id)

    publisher = Counting()
    events = [Event(topic="t", key="k") for _ in range(3)]
    await publisher.publish_many(events)
    assert publisher.seen == [e.event_id for e in events]


async def test_in_memory_publisher_records_events_in_order():
    publisher = InMemoryEventPublisher()
    assert publisher.sent == []
    first, second = Event(topic="a", key="k1"), Event(topic="b", key="k2")
    await publisher.publish(first)
    await publisher.publish(second)
    assert publisher.sent == [first, second]


async def test_in_memory_publisher_logs_a_structured_line():
    """The log line is a %-format template, so payloads are never interpolated."""
    publisher = InMemoryEventPublisher()
    with patch("app.core.events.logger") as logger:
        await publisher.publish(Event(topic="content.uploaded", key="s1", payload={"n": 1}))

    template, *args = logger.info.call_args.args
    assert template == ("event published (in-memory): topic=%s key=%s event_id=%s payload=%s")
    assert args == [
        "content.uploaded",
        "s1",
        publisher.sent[0].event_id,
        json.dumps({"n": 1}),
    ]


# ---------------------------------------------------------------------------
# KafkaEventPublisher.
# ---------------------------------------------------------------------------


class FakeProducer:
    """Stand-in for ``aiokafka.AIOKafkaProducer``."""

    instances: list["FakeProducer"] = []
    start_error: BaseException | None = None
    stop_error: BaseException | None = None

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.sent: list[dict] = []
        FakeProducer.instances.append(self)

    async def start(self) -> None:
        self.started = True
        if FakeProducer.start_error is not None:
            raise FakeProducer.start_error

    async def stop(self) -> None:
        self.stopped = True
        if FakeProducer.stop_error is not None:
            raise FakeProducer.stop_error

    async def send_and_wait(self, *, topic, key, value) -> None:
        self.sent.append({"topic": topic, "key": key, "value": value})


@pytest.fixture
def fake_aiokafka():
    FakeProducer.instances = []
    FakeProducer.start_error = None
    FakeProducer.stop_error = None
    module = types.ModuleType("aiokafka")
    module.AIOKafkaProducer = FakeProducer
    with patch.dict(sys.modules, {"aiokafka": module}):
        yield FakeProducer
    FakeProducer.instances = []


async def test_kafka_publisher_starts_a_producer_once_and_keys_by_session(fake_aiokafka):
    publisher = KafkaEventPublisher(bootstrap_servers="kafka:9092")
    assert publisher._producer is None

    await publisher.publish(Event(topic="content.uploaded", key="s1", payload={"n": 1}))
    await publisher.publish(Event(topic="content.upload.aborted", key="s1"))

    assert len(fake_aiokafka.instances) == 1, "the producer is reused"
    producer = fake_aiokafka.instances[0]
    assert producer.started is True
    assert producer.kwargs["bootstrap_servers"] == "kafka:9092"
    assert producer.kwargs["client_id"] == "uploads-service"
    # Both events land in the same partition because they share the key.
    assert [s["key"] for s in producer.sent] == ["s1", "s1"]
    assert [s["topic"] for s in producer.sent] == [
        "content.uploaded",
        "content.upload.aborted",
    ]
    # The value serializer must produce bytes for the wire.
    assert producer.kwargs["value_serializer"]({"a": 1}) == b'{"a": 1}'
    assert producer.kwargs["key_serializer"]("s1") == b"s1"
    assert producer.kwargs["key_serializer"](None) is None


async def test_kafka_publisher_accepts_a_custom_client_id(fake_aiokafka):
    publisher = KafkaEventPublisher(bootstrap_servers="kafka:9092", client_id="uploads-eu")
    await publisher.publish(Event(topic="t", key="k"))
    assert fake_aiokafka.instances[0].kwargs["client_id"] == "uploads-eu"


async def test_kafka_publisher_stops_a_partially_started_producer_on_failure(fake_aiokafka):
    """A failed start() must not leak a half-initialised producer."""
    fake_aiokafka.start_error = RuntimeError("no broker")
    publisher = KafkaEventPublisher(bootstrap_servers="kafka:9092")

    with pytest.raises(RuntimeError, match="no broker"):
        await publisher.publish(Event(topic="t", key="k"))

    assert len(fake_aiokafka.instances) == 1
    assert fake_aiokafka.instances[0].stopped is True, "stop() ran during cleanup"
    assert publisher._producer is None, "nothing is memoised after a failure"


async def test_kafka_publisher_swallows_a_stop_failure_during_startup_cleanup(fake_aiokafka):
    fake_aiokafka.start_error = RuntimeError("no broker")
    fake_aiokafka.stop_error = OSError("stop failed too")
    publisher = KafkaEventPublisher(bootstrap_servers="kafka:9092")

    with patch("app.core.events.logger") as logger:
        with pytest.raises(RuntimeError, match="no broker"):
            await publisher.publish(Event(topic="t", key="k"))

    # The original start() failure must still be the one that surfaces.
    assert logger.exception.call_args.args[0] == ("failed to clean producer after startup failure")


async def test_kafka_publisher_close_stops_and_releases_the_producer(fake_aiokafka):
    publisher = KafkaEventPublisher(bootstrap_servers="kafka:9092")
    await publisher.publish(Event(topic="t", key="k"))
    producer = fake_aiokafka.instances[0]

    await publisher.close()

    assert producer.stopped is True
    assert publisher._producer is None


async def test_kafka_publisher_close_is_a_noop_without_a_producer():
    await KafkaEventPublisher(bootstrap_servers="kafka:9092").close()


async def test_kafka_publisher_is_reusable_after_close(fake_aiokafka):
    publisher = KafkaEventPublisher(bootstrap_servers="kafka:9092")
    await publisher.publish(Event(topic="t", key="k"))
    await publisher.close()
    await publisher.publish(Event(topic="t", key="k2"))
    assert len(fake_aiokafka.instances) == 2, "a fresh producer is started after close"


# ---------------------------------------------------------------------------
# The process-wide publisher singleton.
# ---------------------------------------------------------------------------


def test_get_event_publisher_memoizes_the_default():
    set_event_publisher(InMemoryEventPublisher())
    first = get_event_publisher()
    assert isinstance(first, InMemoryEventPublisher)
    assert get_event_publisher() is first


def test_get_event_publisher_builds_one_on_first_use():
    """With nothing overridden, the factory runs and its result is memoized."""
    set_event_publisher(None)  # type: ignore[arg-type]
    with patch.object(settings, "EVENT_PUBLISHER", "memory"):
        first = get_event_publisher()
    assert isinstance(first, InMemoryEventPublisher)
    assert get_event_publisher() is first


def test_set_event_publisher_overrides_the_singleton():
    set_event_publisher(InMemoryEventPublisher())
    replacement = InMemoryEventPublisher()
    set_event_publisher(replacement)
    assert get_event_publisher() is replacement


def test_build_publisher_returns_the_in_memory_adapter_by_default():
    with patch.object(settings, "EVENT_PUBLISHER", "memory"):
        assert isinstance(_build_publisher(), InMemoryEventPublisher)


def test_build_publisher_returns_the_kafka_adapter_when_selected():
    with (
        patch.object(settings, "EVENT_PUBLISHER", "kafka"),
        patch.object(settings, "KAFKA_BOOTSTRAP_SERVERS", "kafka.example.com:9092"),
    ):
        publisher = _build_publisher()
    assert isinstance(publisher, KafkaEventPublisher)
    assert publisher.bootstrap_servers == "kafka.example.com:9092"


def test_build_publisher_never_imports_aiokafka_for_the_memory_path():
    """The lazy import is the whole point: no aiokafka in dev/test."""
    import builtins

    real_import = builtins.__import__

    def _tracking_import(name, *args, **kwargs):
        if name == "aiokafka":
            raise AssertionError("aiokafka must not be imported for the memory publisher")
        return real_import(name, *args, **kwargs)

    with (
        patch.object(settings, "EVENT_PUBLISHER", "memory"),
        patch.object(builtins, "__import__", _tracking_import),
    ):
        assert isinstance(_build_publisher(), InMemoryEventPublisher)


async def test_the_sdk_in_memory_adapter_publishes_a_domain_event_without_a_broker():
    """The shared SDK adapter is the broker-free implementation of the same port.

    It takes the SDK's ``DomainEvent`` (a separate dataclass with the same
    field set as this service's ``Event``) and enforces the payload size cap
    before recording, so dev/test catches oversized events early.
    """
    from wildframe_events.event import DomainEvent
    from wildframe_events.publisher import InMemoryEventPublisher as SdkPublisher

    publisher = SdkPublisher()
    event = DomainEvent(topic="content.uploaded", key="s1", payload={"n": 1})
    await publisher.publish(event)
    assert [e.topic for e in publisher.sent] == ["content.uploaded"]

    from wildframe_events.publisher import EventTooLargeError

    tiny = SdkPublisher(max_payload_bytes=1)
    with pytest.raises(EventTooLargeError):
        await tiny.publish(event)


def test_the_local_event_is_a_subset_of_the_sdk_domain_event():
    """The local ``Event`` mirrors the SDK ``DomainEvent`` wire contract.

    The SDK type is a superset (it adds ``correlation_id``, ``producer``,
    ``schema_version``, ``sequence``, ``server_time`` for the shared bus), so a
    local event can be published on the SDK adapter without losing a field.
    """
    from wildframe_events.event import DomainEvent

    local_fields = set(Event.__dataclass_fields__)
    sdk_fields = set(DomainEvent.__dataclass_fields__)
    assert local_fields < sdk_fields
    assert {"event_id", "topic", "key", "occurred_at", "payload"} <= local_fields


def test_the_publisher_port_is_a_real_abc():
    assert issubclass(InMemoryEventPublisher, EventPublisher)
    assert issubclass(KafkaEventPublisher, EventPublisher)
    with pytest.raises(TypeError):
        EventPublisher()  # type: ignore[abstract]
