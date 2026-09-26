"""Behavioural tests for ``app/core/events.py`` (moderation-service).

The event bus is the service's only integration surface, and it was at 56%:
the ``Event`` envelope, the ``publish_many`` default, the singleton lifecycle
and the whole ``KafkaEventPublisher`` adapter had never been executed.

The Kafka adapter is tested against a stubbed ``aiokafka.AIOKafkaProducer``
injected into ``sys.modules`` — no broker is contacted, but the adapter's own
logic (lazy producer construction, start/stop cleanup on failure, serializers,
``send_and_wait`` arguments) genuinely runs.
"""

import json
import sys
import types

import pytest

from app.core import events as events_mod
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
def _reset_singleton():
    saved = events_mod._publisher
    yield
    events_mod._publisher = saved


@pytest.fixture
def stub_aiokafka(monkeypatch):
    """A recording stand-in for aiokafka's producer."""
    state = {
        "constructors": [],
        "started": 0,
        "stopped": 0,
        "sent": [],
        "start_error": None,
        "stop_error": None,
    }

    class AIOKafkaProducer:
        def __init__(self, **kwargs):
            state["constructors"].append(kwargs)

        async def start(self):
            state["started"] += 1
            if state["start_error"] is not None:
                raise state["start_error"]

        async def stop(self):
            state["stopped"] += 1
            if state["stop_error"] is not None:
                raise state["stop_error"]

        async def send_and_wait(self, **kwargs):
            state["sent"].append(kwargs)

    module = types.ModuleType("aiokafka")
    module.AIOKafkaProducer = AIOKafkaProducer
    monkeypatch.setitem(sys.modules, "aiokafka", module)
    return state


# ---------------------------------------------------------------------------
# Event envelope
# ---------------------------------------------------------------------------


class TestEventEnvelope:
    def test_requires_a_topic_and_a_key(self):
        event = Event(topic="content.flagged", key="k-1")
        assert event.payload == {}

    def test_payload_defaults_are_not_shared_between_instances(self):
        first = Event(topic="t", key="1")
        second = Event(topic="t", key="2")
        first.payload["a"] = 1
        assert second.payload == {}

    def test_event_id_is_a_fresh_uuid_per_event(self):
        assert Event(topic="t", key="1").event_id != Event(topic="t", key="1").event_id

    def test_occurred_at_is_iso_utc(self):
        assert Event(topic="t", key="1").occurred_at.endswith("+00:00")

    def test_to_dict_carries_every_field(self):
        event = Event(topic="content.flagged", key="k-1", payload={"flag_id": "f-1"})
        decoded = event.to_dict()
        assert decoded == {
            "event_id": event.event_id,
            "topic": "content.flagged",
            "key": "k-1",
            "occurred_at": event.occurred_at,
            "payload": {"flag_id": "f-1"},
        }

    def test_to_dict_is_json_serialisable(self):
        event = Event(topic="t", key="k", payload={"nested": {"n": 1}})
        assert json.loads(json.dumps(event.to_dict()))["payload"]["nested"]["n"] == 1

    def test_to_dict_reflects_a_mutated_payload(self):
        event = Event(topic="t", key="k")
        event.payload["late"] = True
        assert event.to_dict()["payload"] == {"late": True}


# ---------------------------------------------------------------------------
# Port + in-memory adapter
# ---------------------------------------------------------------------------


class TestEventPublisherPort:
    def test_the_port_cannot_be_instantiated(self):
        with pytest.raises(TypeError):
            EventPublisher()

    def test_publish_is_abstract(self):
        assert "publish" in EventPublisher.__abstractmethods__

    async def test_the_abstract_body_raises_not_implemented(self):
        # Unreachable through normal subclassing (the ABC refuses to
        # instantiate), so clear the guard explicitly to reach the body.
        class _Concrete(EventPublisher):
            __abstractmethods__ = frozenset()

            async def publish(self, event):
                # Delegates straight to the port body under test.
                return await EventPublisher.publish(self, event)

        with pytest.raises(NotImplementedError):
            await _Concrete().publish(Event(topic="t", key="k"))

    async def test_publish_many_defaults_to_sequential_publishing(self):
        seen: list[str] = []

        class _Recorder(EventPublisher):
            async def publish(self, event):
                seen.append(event.key)

        await _Recorder().publish_many([Event(topic="t", key="a"), Event(topic="t", key="b")])
        assert seen == ["a", "b"]

    async def test_publish_many_on_an_empty_list_is_a_noop(self):
        publisher = InMemoryEventPublisher()
        await publisher.publish_many([])
        assert publisher.sent == []


class TestInMemoryPublisher:
    async def test_publish_records_the_event(self):
        publisher = InMemoryEventPublisher()
        event = Event(topic="content.flagged", key="k-1")
        await publisher.publish(event)
        assert publisher.sent == [event]

    async def test_publish_preserves_order(self):
        publisher = InMemoryEventPublisher()
        events = [Event(topic="t", key=str(i)) for i in range(5)]
        await publisher.publish_many(events)
        assert [e.key for e in publisher.sent] == ["0", "1", "2", "3", "4"]


# ---------------------------------------------------------------------------
# Singleton lifecycle
# ---------------------------------------------------------------------------


class TestPublisherSingleton:
    def test_get_event_publisher_constructs_the_default_once(self):
        assert settings.EVENT_PUBLISHER == "memory"
        first = get_event_publisher()
        assert isinstance(first, InMemoryEventPublisher)
        assert get_event_publisher() is first

    def test_set_event_publisher_overrides_the_singleton(self):
        override = InMemoryEventPublisher()
        set_event_publisher(override)
        assert get_event_publisher() is override

    def test_set_event_publisher_accepts_none_to_reset(self):
        set_event_publisher(None)
        assert isinstance(get_event_publisher(), InMemoryEventPublisher)

    def test_build_publisher_returns_in_memory_in_the_default_mode(self):
        assert isinstance(_build_publisher(), InMemoryEventPublisher)

    def test_build_publisher_selects_kafka_when_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "kafka")
        monkeypatch.setattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "broker:19092")
        publisher = _build_publisher()
        assert isinstance(publisher, KafkaEventPublisher)
        assert publisher.bootstrap_servers == "broker:19092"
        assert publisher.client_id == "moderation-service"

    def test_build_publisher_falls_back_for_an_unknown_value(self, monkeypatch):
        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "carrier-pigeon")
        assert isinstance(_build_publisher(), InMemoryEventPublisher)

    def test_get_event_publisher_builds_lazily_only_once(self, monkeypatch):
        builds: list[int] = []
        real_build = events_mod._build_publisher

        def counting_build():
            builds.append(1)
            return real_build()

        events_mod._publisher = None
        monkeypatch.setattr(events_mod, "_build_publisher", counting_build)
        get_event_publisher()
        get_event_publisher()
        get_event_publisher()
        assert len(builds) == 1


# ---------------------------------------------------------------------------
# Kafka adapter
# ---------------------------------------------------------------------------


class TestKafkaPublisherConstruction:
    def test_records_the_connection_settings(self):
        publisher = KafkaEventPublisher(
            bootstrap_servers="broker:9092", client_id="moderation-test"
        )
        assert publisher.bootstrap_servers == "broker:9092"
        assert publisher.client_id == "moderation-test"
        assert publisher._producer is None

    def test_the_default_client_id_is_the_service_name(self):
        assert KafkaEventPublisher("broker:9092").client_id == "moderation-service"

    def test_no_producer_is_created_at_construction(self, stub_aiokafka):
        KafkaEventPublisher("broker:9092")
        assert stub_aiokafka["constructors"] == []


class TestKafkaPublisherLazyProducer:
    async def test_starts_a_producer_on_first_publish(self, stub_aiokafka):
        publisher = KafkaEventPublisher("broker:9092")
        await publisher.publish(Event(topic="content.flagged", key="k-1"))
        assert stub_aiokafka["started"] == 1
        assert len(stub_aiokafka["constructors"]) == 1

    async def test_producer_configuration_matches_the_adapter_contract(
        self, stub_aiokafka
    ):
        publisher = KafkaEventPublisher("broker:9092", client_id="svc")
        await publisher.publish(Event(topic="t", key="k"))
        kwargs = stub_aiokafka["constructors"][0]
        assert kwargs["bootstrap_servers"] == "broker:9092"
        assert kwargs["client_id"] == "svc"
        # Payload and key are JSON/bytes encoded on the producer side.
        assert json.loads(kwargs["value_serializer"]({"a": 1})) == {"a": 1}
        assert kwargs["key_serializer"]("k") == b"k"
        assert kwargs["key_serializer"]("") is None
        assert kwargs["key_serializer"](None) is None

    async def test_the_producer_is_reused_across_publishes(self, stub_aiokafka):
        publisher = KafkaEventPublisher("broker:9092")
        await publisher.publish(Event(topic="t", key="1"))
        await publisher.publish(Event(topic="t", key="2"))
        assert len(stub_aiokafka["constructors"]) == 1
        assert stub_aiokafka["started"] == 1
        assert len(stub_aiokafka["sent"]) == 2

    async def test_publish_sends_the_serialised_envelope(self, stub_aiokafka):
        publisher = KafkaEventPublisher("broker:9092")
        event = Event(topic="creator.suspended", key="c-1", payload={"strikes": 3})
        await publisher.publish(event)
        sent = stub_aiokafka["sent"][0]
        assert sent["topic"] == "creator.suspended"
        assert sent["key"] == "c-1"
        assert sent["value"] == event.to_dict()
        assert sent["value"]["payload"] == {"strikes": 3}

    async def test_publish_many_goes_through_the_single_producer(self, stub_aiokafka):
        publisher = KafkaEventPublisher("broker:9092")
        await publisher.publish_many(
            [Event(topic="t", key="1"), Event(topic="t", key="2")]
        )
        assert stub_aiokafka["started"] == 1
        assert [s["key"] for s in stub_aiokafka["sent"]] == ["1", "2"]


class TestKafkaPublisherStartupFailure:
    async def test_a_failed_start_propagates_and_cleans_up(self, stub_aiokafka):
        stub_aiokafka["start_error"] = RuntimeError("broker unreachable")
        publisher = KafkaEventPublisher("broker:9092")
        with pytest.raises(RuntimeError, match="broker unreachable"):
            await publisher.publish(Event(topic="t", key="k"))
        # The half-built producer is stopped, never left dangling.
        assert stub_aiokafka["stopped"] == 1
        assert publisher._producer is None

    async def test_a_failing_cleanup_does_not_mask_the_original_error(
        self, stub_aiokafka
    ):
        stub_aiokafka["start_error"] = RuntimeError("broker unreachable")
        stub_aiokafka["stop_error"] = RuntimeError("stop also failed")
        publisher = KafkaEventPublisher("broker:9092")
        with pytest.raises(RuntimeError, match="broker unreachable"):
            await publisher.publish(Event(topic="t", key="k"))
        assert stub_aiokafka["stopped"] == 1

    async def test_a_retry_after_failure_creates_a_fresh_producer(self, stub_aiokafka):
        stub_aiokafka["start_error"] = RuntimeError("broker unreachable")
        publisher = KafkaEventPublisher("broker:9092")
        with pytest.raises(RuntimeError):
            await publisher.publish(Event(topic="t", key="k"))
        stub_aiokafka["start_error"] = None
        await publisher.publish(Event(topic="t", key="k"))
        assert len(stub_aiokafka["constructors"]) == 2
        assert stub_aiokafka["started"] == 2


class TestKafkaPublisherClose:
    async def test_close_stops_a_live_producer_and_clears_it(self, stub_aiokafka):
        publisher = KafkaEventPublisher("broker:9092")
        await publisher.publish(Event(topic="t", key="k"))
        await publisher.close()
        assert stub_aiokafka["stopped"] == 1
        assert publisher._producer is None

    async def test_close_is_a_noop_before_any_publish(self, stub_aiokafka):
        publisher = KafkaEventPublisher("broker:9092")
        await publisher.close()
        assert stub_aiokafka["stopped"] == 0

    async def test_close_twice_only_stops_once(self, stub_aiokafka):
        publisher = KafkaEventPublisher("broker:9092")
        await publisher.publish(Event(topic="t", key="k"))
        await publisher.close()
        await publisher.close()
        assert stub_aiokafka["stopped"] == 1

    async def test_publishing_after_close_rebuilds_the_producer(self, stub_aiokafka):
        publisher = KafkaEventPublisher("broker:9092")
        await publisher.publish(Event(topic="t", key="k"))
        await publisher.close()
        await publisher.publish(Event(topic="t", key="k2"))
        assert len(stub_aiokafka["constructors"]) == 2


class TestEventTopicsUsedByModeration:
    def test_the_three_producer_topics_are_constructible(self):
        for topic in ("content.flagged", "moderation.decision_made", "creator.suspended"):
            assert Event(topic=topic, key="k").topic == topic
