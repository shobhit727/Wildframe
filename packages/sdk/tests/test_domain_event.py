"""Tests for wildframe_events.DomainEvent serialization and Topic constants."""

import json
import re
from datetime import datetime, timezone


from wildframe_events import DomainEvent, Topic


class TestDomainEvent:
    def test_defaults_exist(self):
        event = DomainEvent(topic=Topic.CONTENT_UPLOADED, key="upload:abc")
        assert event.event_id
        assert event.producer == ""
        assert event.schema_version == 1
        assert event.payload == {}
        datetime.fromisoformat(event.occurred_at)  # ISO-8601 parses

    def test_occurred_at_is_utc(self):
        event = DomainEvent(topic=Topic.CONTENT_UPLOADED, key="k")
        now = datetime.now(timezone.utc).isoformat()
        assert event.occurred_at[:19] == now[:19]

    def test_to_dict_roundtrip(self):
        event = DomainEvent(
            topic=Topic.CONTENT_UPLOADED,
            key="upload:abc",
            payload={"content_id": "123", "nested": {"x": 1}},
            producer="uploads-service",
        )
        data = event.to_dict()
        assert data["topic"] == Topic.CONTENT_UPLOADED
        assert data["key"] == "upload:abc"
        assert data["schema_version"] == 1
        assert data["payload"] == {"content_id": "123", "nested": {"x": 1}}
        restored = DomainEvent.from_dict(data)
        assert restored == event

    def test_to_json_roundtrip(self):
        event = DomainEvent(
            topic=Topic.BILLING_SUBSCRIPTION_CREATED,
            key="sub:created:cus_xyz",
            payload={"tier": "premium"},
            producer="billing-service",
        )
        json_str = event.to_json()
        parsed = json.loads(json_str)
        restored = DomainEvent.from_json(json_str)
        assert parsed["topic"] == event.topic
        assert restored.to_dict() == event.to_dict()

    def test_from_dict_with_missing_optional_fields(self):
        restored = DomainEvent.from_dict({"topic": "x", "key": "y"})
        assert restored.payload == {}
        assert restored.producer == ""
        assert restored.schema_version == 1

    def test_payload_always_copied_on_deserialize(self):
        payload = {"a": 1}
        original = DomainEvent(topic="t", key="k", payload=payload)
        restored = DomainEvent.from_dict(original.to_dict())
        payload["a"] = 999
        assert restored.payload == {"a": 1}

    def test_empty_list_publish(self):
        event = DomainEvent(topic="t", key="k")
        assert event.to_json()
        assert isinstance(event.event_id, str)
        assert len(event.event_id) == 36


class TestTopics:
    def test_topic_names_use_dot_notation(self):
        pattern = re.compile(r"^[a-z]+(\.[a-z_]+)+$")
        for attr in dir(Topic):
            if attr.startswith("_") or attr == "DLQ_SUFFIX":
                continue
            value = getattr(Topic, attr)
            if isinstance(value, str):
                assert pattern.match(value), f"{attr} -> {value}"

    def test_topic_values_are_unique(self):
        values = [getattr(Topic, a) for a in dir(Topic) if not a.startswith("_")]
        strings = [v for v in values if isinstance(v, str)]
        assert len(strings) == len(set(strings))

    def test_dlq_suffix(self):
        assert Topic.DLQ_SUFFIX == ".dlq"
        assert Topic.CONTENT_UPLOADED + Topic.DLQ_SUFFIX == "content.uploaded.dlq"

    def test_expected_core_topics_exist(self):
        for topic in (
            Topic.CONTENT_UPLOADED,
            Topic.CONTENT_SCANNED,
            Topic.CONTENT_METADATA_EXTRACTED,
            Topic.CONTENT_ENCODED,
            Topic.CONTENT_PACKAGED,
            Topic.CONTENT_PUBLISHED,
            Topic.CONTENT_PIPELINE_FAILED,
            Topic.BILLING_SUBSCRIPTION_CREATED,
            Topic.BILLING_SUBSCRIPTION_UPDATED,
            Topic.BILLING_SUBSCRIPTION_CANCELLED,
            Topic.BILLING_PAYOUT_ACCRUED,
            Topic.BILLING_PAYOUT_TRANSFERRED,
            Topic.CREATOR_ONBOARDED,
            Topic.CREATOR_MILESTONE_REACHED,
            Topic.CREATOR_FLOOR_ADJUSTED,
            Topic.CREATOR_SUSPENDED,
            Topic.MODERATION_FLAGGED,
            Topic.MODERATION_DECISION_MADE,
        ):
            assert isinstance(topic, str)

    def test_metadata_documented_topics_pass(self, capsys):
        from wildframe_events.topics import TOPIC_METADATA

        # Every non-DLQ topic constant has documentation metadata.
        for attr in dir(Topic):
            if attr.startswith("_") or attr == "DLQ_SUFFIX":
                continue
            topic = getattr(Topic, attr)
            assert topic in TOPIC_METADATA, f"{attr} ({topic}) lacks TOPIC_METADATA"
            meta = TOPIC_METADATA[topic]
            assert "producer" in meta and meta["producer"]
            assert "consumers" in meta and meta["consumers"]
            assert meta["idempotency_key_pattern"]
            # producer is one of the 14 services/media-pipeline
            assert meta["producer"] in {
                "auth-service",
                "user-service",
                "content-service",
                "streaming-service",
                "search-service",
                "recommendation-service",
                "billing-service",
                "analytics-service",
                "notification-service",
                "admin-service",
                "media-pipeline",
                "creators-service",
                "moderation-service",
                "uploads-service",
                "api-gateway",
            }

    def test_metadata_no_orphan_topics(self):
        from wildframe_events.topics import TOPIC_METADATA

        for topic in TOPIC_METADATA:
            assert "#" not in topic
