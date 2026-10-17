"""Behavioural tests for ``app.core.events`` (admin-service).

admin-service is the producer of ``user.moderated``; auth-service consumes it
to enforce suspension/ban at the login boundary, so the publisher selection
and the event envelope are asserted directly. Kafka is never contacted: the
SDK's ``InMemoryEventPublisher`` is the real adapter used in dev/test, and
the Kafka adapter is exercised with a stubbed aiokafka producer.
"""

import pytest
from wildframe_events import InMemoryEventPublisher, KafkaEventPublisher, Topic

from app.core import events as events_mod
from app.core.events import (
    get_event_publisher,
    reset_event_publisher,
    user_moderated_event,
)
from app.core.settings import settings


@pytest.fixture(autouse=True)
def _reset_publisher():
    reset_event_publisher()
    yield
    reset_event_publisher()


class TestGetEventPublisher:
    def test_returns_in_memory_publisher_by_default(self):
        assert settings.EVENT_PUBLISHER == "memory"
        publisher = get_event_publisher()
        assert isinstance(publisher, InMemoryEventPublisher)
        assert not isinstance(publisher, KafkaEventPublisher)

    def test_publisher_is_cached_process_wide(self):
        assert get_event_publisher() is get_event_publisher()

    def test_reset_drops_the_cached_instance(self):
        first = get_event_publisher()
        reset_event_publisher()
        second = get_event_publisher()
        assert second is not first

    def test_kafka_selection_constructs_the_kafka_adapter(self, monkeypatch):
        captured = {}

        class StubKafka:
            def __init__(self, bootstrap_servers, client_id):
                captured["bootstrap_servers"] = bootstrap_servers
                captured["client_id"] = client_id

        monkeypatch.setattr(events_mod, "KafkaEventPublisher", StubKafka)
        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "kafka")
        monkeypatch.setattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "broker:19092")

        publisher = get_event_publisher()
        assert isinstance(publisher, StubKafka)
        assert captured == {"bootstrap_servers": "broker:19092", "client_id": "admin-service"}

    def test_unknown_publisher_name_falls_back_to_in_memory(self, monkeypatch):
        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "something-else")
        assert isinstance(get_event_publisher(), InMemoryEventPublisher)

    def test_reset_is_safe_when_no_publisher_was_built(self):
        reset_event_publisher()
        reset_event_publisher()
        assert isinstance(get_event_publisher(), InMemoryEventPublisher)


class TestUserModeratedEvent:
    def test_uses_the_user_moderated_topic(self):
        event = user_moderated_event("u1", "banned", "admin-1", "2026-01-01T00:00:00+00:00")
        assert event.topic == Topic.USER_MODERATED
        assert event.producer == "admin-service"

    def test_payload_carries_every_field_auth_service_needs(self):
        event = user_moderated_event("u1", "suspended", "admin-1", "2026-01-01T00:00:00+00:00")
        assert event.payload == {
            "user_id": "u1",
            "status": "suspended",
            "moderated_by": "admin-1",
            "moderated_at": "2026-01-01T00:00:00+00:00",
        }

    def test_idempotency_key_identifies_user_status_and_timestamp(self):
        event = user_moderated_event("u1", "banned", "admin-1", "2026-01-01T00:00:00+00:00")
        assert event.key == "moderated:u1:banned:2026-01-01T00:00:00+00:00"

    def test_repeated_moderation_of_the_same_status_still_gets_a_fresh_key(self):
        # The timestamp is in the key on purpose so auth-service's
        # last-write-wins sees both deliveries instead of deduping them.
        first = user_moderated_event("u1", "banned", "admin-1", "2026-01-01T00:00:00+00:00")
        second = user_moderated_event("u1", "banned", "admin-1", "2026-01-01T00:00:05+00:00")
        assert first.key != second.key

    def test_different_users_do_not_collide(self):
        a = user_moderated_event("u1", "banned", "admin-1", "2026-01-01T00:00:00+00:00")
        b = user_moderated_event("u2", "banned", "admin-1", "2026-01-01T00:00:00+00:00")
        assert a.key != b.key

    def test_event_serialises_to_json_for_the_bus(self):
        import json

        event = user_moderated_event("u1", "active", "admin-1", "2026-01-01T00:00:00+00:00")
        decoded = json.loads(event.to_json())
        assert decoded["topic"] == Topic.USER_MODERATED
        assert decoded["key"].startswith("moderated:u1:active")
        assert decoded["payload"]["user_id"] == "u1"


class TestPublishingThroughTheInMemoryAdapter:
    async def test_publish_records_the_event(self):
        publisher = InMemoryEventPublisher()
        event = user_moderated_event("u1", "banned", "admin-1", "2026-01-01T00:00:00+00:00")
        await publisher.publish(event)
        assert publisher.sent == [event]

    async def test_publish_many_preserves_order(self):
        publisher = InMemoryEventPublisher()
        events = [
            user_moderated_event(f"u{i}", "banned", "admin-1", "2026-01-01T00:00:00+00:00")
            for i in range(3)
        ]
        await publisher.publish_many(events)
        assert [e.payload["user_id"] for e in publisher.sent] == ["u0", "u1", "u2"]

    async def test_publisher_validates_payload_before_recording(self):
        # The SDK refuses secret-shaped payload keys; admin must not be able to
        # leak a credential into the bus through user.moderated.
        from wildframe_events.event import PayloadValidationError

        publisher = InMemoryEventPublisher()
        event = user_moderated_event("u1", "banned", "admin-1", "2026-01-01T00:00:00+00:00")
        event.payload["api_key"] = "sk_live_leak"
        with pytest.raises(PayloadValidationError):
            await publisher.publish(event)
        assert publisher.sent == []
