"""Tests for wildframe_events.DomainEvent serialization and Topic constants."""

import json
import re
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from wildframe_events import DomainEvent, Topic
from wildframe_events.event import validate_payload


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


class TestDomainEventCreate:
    def test_create_with_explicit_correlation_id(self):
        event = DomainEvent.create(
            topic=Topic.CONTENT_UPLOADED,
            key="test-key",
            payload={"foo": "bar"},
            producer="test-service",
            correlation_id="explicit-corr-123",
        )
        assert event.correlation_id == "explicit-corr-123"
        assert event.topic == Topic.CONTENT_UPLOADED
        assert event.key == "test-key"
        assert event.payload == {"foo": "bar"}
        assert event.producer == "test-service"

    def test_create_without_correlation_id_uses_none(self):
        event = DomainEvent.create(
            topic=Topic.CONTENT_UPLOADED,
            key="test-key",
        )
        # When observability not available, correlation_id is None
        assert event.correlation_id is None

    def test_create_auto_populates_from_context(self):
        # Mock the observability get_correlation_id to return a value
        with patch("wildframe_observability.logging.get_correlation_id") as mock_get:
            mock_get.return_value = "context-corr-456"
            event = DomainEvent.create(
                topic=Topic.CONTENT_UPLOADED,
                key="test-key",
            )
            assert event.correlation_id == "context-corr-456"

    def test_create_explicit_overrides_context(self):
        # Even if context has a value, explicit should win
        with patch("wildframe_observability.logging.get_correlation_id") as mock_get:
            mock_get.return_value = "context-corr-456"
            event = DomainEvent.create(
                topic=Topic.CONTENT_UPLOADED,
                key="test-key",
                correlation_id="explicit-corr-789",
            )
            assert event.correlation_id == "explicit-corr-789"

    def test_create_correlation_id_in_to_dict(self):
        event = DomainEvent.create(
            topic=Topic.CONTENT_UPLOADED,
            key="test-key",
            correlation_id="test-corr",
        )
        d = event.to_dict()
        assert d["correlation_id"] == "test-corr"

    def test_create_correlation_id_in_to_json(self):
        event = DomainEvent.create(
            topic=Topic.CONTENT_UPLOADED,
            key="test-key",
            correlation_id="test-corr",
        )
        json_str = event.to_json()
        assert '"correlation_id": "test-corr"' in json_str

    def test_from_dict_restores_correlation_id(self):
        data = {
            "topic": "content.uploaded",
            "key": "test-key",
            "correlation_id": "restored-corr",
        }
        event = DomainEvent.from_dict(data)
        assert event.correlation_id == "restored-corr"


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


# ---------------------------------------------------------------------------
# Envelope validation branches (the consumer-side trust boundary).
#
# `DomainEvent.from_dict` is what an untrusted Kafka message goes through, so
# every rejection path matters: a malformed or hostile envelope must raise
# PayloadValidationError / SchemaVersionError rather than producing a
# half-populated event.
# ---------------------------------------------------------------------------


class TestFromDictEnvelopeValidation:
    def _envelope(self, **overrides):
        base = {
            "topic": "content.uploaded",
            "key": "upload:1",
            "payload": {"a": 1},
            "event_id": "00000000-0000-4000-8000-000000000000",
            "occurred_at": "2026-01-01T00:00:00+00:00",
            "schema_version": 1,
        }
        base.update(overrides)
        return base

    def test_non_dict_envelope_rejected(self):
        from wildframe_events.event import PayloadValidationError

        for bad in ("a string", 42, None, ["list"]):
            with pytest.raises(PayloadValidationError, match="must be a dict"):
                DomainEvent.from_dict(bad)

    def test_missing_topic_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="'topic'"):
            DomainEvent.from_dict(self._envelope(topic=None))

    def test_empty_topic_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="'topic'"):
            DomainEvent.from_dict(self._envelope(topic=""))

    def test_non_string_topic_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="'topic'"):
            DomainEvent.from_dict(self._envelope(topic=7))

    def test_missing_key_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="'key'"):
            DomainEvent.from_dict(self._envelope(key=None))

    def test_empty_key_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="'key'"):
            DomainEvent.from_dict(self._envelope(key=""))

    def test_non_string_key_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="'key'"):
            DomainEvent.from_dict(self._envelope(key=99))

    def test_non_string_schema_version_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="schema_version must be an int"):
            DomainEvent.from_dict(self._envelope(schema_version="1"))

    def test_bool_schema_version_rejected(self):
        """``True`` is an int subclass; accepting it would silently treat a
        boolean as schema version 1."""
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="schema_version must be an int"):
            DomainEvent.from_dict(self._envelope(schema_version=True))

    def test_float_schema_version_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="schema_version must be an int"):
            DomainEvent.from_dict(self._envelope(schema_version=1.0))

    def test_missing_schema_version_defaults_to_current(self):
        envelope = self._envelope()
        envelope.pop("schema_version")
        assert DomainEvent.from_dict(envelope).schema_version == 1

    def test_malformed_occurred_at_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="occurred_at"):
            DomainEvent.from_dict(self._envelope(occurred_at="not-a-timestamp"))

    def test_non_string_occurred_at_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="occurred_at"):
            DomainEvent.from_dict(self._envelope(occurred_at=12345))

    def test_absent_occurred_at_is_filled_with_now(self):
        envelope = self._envelope()
        envelope.pop("occurred_at")
        event = DomainEvent.from_dict(envelope)
        assert datetime.fromisoformat(event.occurred_at).tzinfo is not None

    def test_malformed_server_time_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="server_time"):
            DomainEvent.from_dict(self._envelope(server_time="yesterday"))

    def test_non_string_server_time_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="server_time"):
            DomainEvent.from_dict(self._envelope(server_time=1700000000))

    def test_absent_server_time_stays_none(self):
        assert DomainEvent.from_dict(self._envelope()).server_time is None

    def test_non_int_sequence_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="sequence must be an int"):
            DomainEvent.from_dict(self._envelope(sequence="1"))

    def test_bool_sequence_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="sequence must be an int"):
            DomainEvent.from_dict(self._envelope(sequence=False))

    def test_int_sequence_accepted(self):
        assert DomainEvent.from_dict(self._envelope(sequence=7)).sequence == 7

    def test_missing_event_id_is_generated(self):
        envelope = self._envelope()
        envelope.pop("event_id")
        assert DomainEvent.from_dict(envelope).event_id

    def test_payload_defaults_to_empty_dict(self):
        envelope = self._envelope()
        envelope.pop("payload")
        assert DomainEvent.from_dict(envelope).payload == {}

    def test_producer_defaults_to_empty_string(self):
        envelope = self._envelope()
        envelope.pop("producer", None)
        assert DomainEvent.from_dict(envelope).producer == ""

    def test_unknown_top_level_fields_are_ignored(self):
        """Forward compatibility: a newer producer's extra envelope fields
        must not break an older consumer."""
        event = DomainEvent.from_dict(self._envelope(future_field={"x": 1}, another="y"))
        assert event.topic == "content.uploaded"

    def test_max_schema_version_argument_is_honoured(self):
        from wildframe_events.event import SchemaVersionError

        # Supported version 1 is the default cap...
        with pytest.raises(SchemaVersionError, match="newer than supported"):
            DomainEvent.from_dict(self._envelope(schema_version=2))
        # ...and raising the cap accepts it.
        assert DomainEvent.from_dict(self._envelope(schema_version=2), max_schema_version=2).schema_version == 2

    def test_lowering_the_cap_rejects_the_current_version(self):
        from wildframe_events.event import SchemaVersionError

        with pytest.raises(SchemaVersionError, match="newer than supported"):
            DomainEvent.from_dict(self._envelope(schema_version=1), max_schema_version=0)

    def test_from_json_roundtrip(self):
        original = DomainEvent(topic="t", key="k", payload={"a": 1}, producer="svc")
        assert DomainEvent.from_json(original.to_json()).event_id == original.event_id

    def test_from_json_rejects_malformed_json(self):
        with pytest.raises(json.JSONDecodeError):
            DomainEvent.from_json("{not json")


class TestValidatePayloadCoercionRules:
    def test_non_string_dict_key_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="non-string key"):
            validate_payload({1: "a"})

    def test_none_dict_key_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="non-string key"):
            validate_payload({None: "a"})

    def test_bytes_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="unsupported type bytes"):
            validate_payload({"blob": b"raw"})

    def test_set_rejected(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="unsupported type set"):
            validate_payload({"bag": {"a"}})

    def test_arbitrary_object_rejected(self):
        from wildframe_events.event import PayloadValidationError

        class Thing:
            pass

        with pytest.raises(PayloadValidationError, match="unsupported type Thing"):
            validate_payload({"obj": Thing()})

    def test_error_message_includes_the_path(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match=r"payload\.a\[0\]\.b"):
            validate_payload({"a": [{"b": b"bytes"}]})

    def test_finite_float_accepted(self):
        assert validate_payload({"x": 1.5}) is None

    def test_top_level_scalar_accepted(self):
        for value in (None, "s", 1, True, 1.5):
            assert validate_payload(value) is None

    def test_tuples_accepted_as_arrays(self):
        assert validate_payload({"a": (1, 2)}) is None

    def test_secret_key_check_can_be_disabled(self):
        # The escape hatch exists for trusted internal producers.
        assert validate_payload({"password": "x"}, forbid_secret_keys=False) is None

    def test_secret_check_is_applied_recursively(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="secret-shaped key"):
            validate_payload({"outer": {"inner": {"api_key": "leak"}}})

    def test_secret_check_is_applied_inside_lists(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="secret-shaped key"):
            validate_payload([{"api_key": "leak"}])
        with pytest.raises(PayloadValidationError, match="secret-shaped key"):
            validate_payload({"rows": [{"items": [{"access_token": "leak"}]}]})

    def test_known_asymmetry_bare_token_is_not_a_blocked_event_key(self):
        """The event bus blocks a NARROWER set of names than the log
        redactor. ``event._SECRET_KEYS`` has access_token / refresh_token /
        auth_token but not bare ``token``; ``observability.logging.REDACT_FIELDS``
        has all four plus ``cookie``/``stripe_key``.

        So an event payload may legitimately carry a ``token`` field. Pinned so
        a change to either list is a deliberate decision.
        """
        from wildframe_events.event import _SECRET_KEYS
        from wildframe_observability.logging import REDACT_FIELDS

        assert validate_payload({"token": "abc"}) is None
        assert "token" not in _SECRET_KEYS
        assert "token" in REDACT_FIELDS
        # Names blocked for events but not for log fields.
        assert _SECRET_KEYS - REDACT_FIELDS == frozenset(
            {
                "auth_token",
                "private_key",
                "client_secret",
                "x_api_key",
                "x_amz_security_token",
                "aws_secret_access_key",
            }
        )

    def test_unprefixed_secret_keys_match_too(self):
        from wildframe_events.event import PayloadValidationError

        with pytest.raises(PayloadValidationError, match="secret-shaped key"):
            validate_payload({"_password": "leak"})

    def test_near_miss_secret_names_are_allowed(self):
        # Over-stripping would silently drop legitimate business fields.
        assert validate_payload({"tokenizer": "gpt", "monkey": 3}) is None


class TestDomainEventCreateFactory:
    def test_uses_explicit_correlation_id(self):
        event = DomainEvent.create("t", "k", correlation_id="explicit")
        assert event.correlation_id == "explicit"

    def test_inherits_correlation_id_from_the_context(self):
        from wildframe_observability.logging import set_correlation_id

        set_correlation_id("from-context")
        try:
            assert DomainEvent.create("t", "k").correlation_id == "from-context"
        finally:
            set_correlation_id("")

    def test_empty_context_yields_none(self):
        from wildframe_observability.logging import set_correlation_id

        set_correlation_id("")
        assert DomainEvent.create("t", "k").correlation_id is None

    def test_payload_defaults_to_empty_dict(self):
        assert DomainEvent.create("t", "k").payload == {}

    def test_falsy_payload_is_normalised_to_empty_dict(self):
        assert DomainEvent.create("t", "k", payload=None).payload == {}

    def test_producer_is_recorded(self):
        assert DomainEvent.create("t", "k", producer="billing-service").producer == "billing-service"

    def test_works_without_the_observability_sdk(self):
        """The observability import is optional; losing correlation context
        must not stop an event from being created."""
        with patch.dict(sys.modules, {"wildframe_observability.logging": None}):
            event = DomainEvent.create("t", "k")
        assert event.correlation_id is None
        assert event.topic == "t"

    def test_to_json_rejects_an_invalid_payload(self):
        from wildframe_events.event import PayloadValidationError as _PVE

        event = DomainEvent(topic="t", key="k")
        event.payload["password"] = "leak"  # bypass __init__ to plant a bad payload
        with pytest.raises(_PVE):
            event.to_json()

    def test_to_dict_deep_copies_the_payload(self):
        event = DomainEvent(topic="t", key="k", payload={"nested": {"a": 1}})
        snapshot = event.to_dict()
        snapshot["payload"]["nested"]["a"] = 99
        assert event.payload["nested"]["a"] == 1


class TestParseIso:
    def test_valid_iso_parsed(self):
        from wildframe_events.event import _parse_iso

        assert _parse_iso("2026-01-01T00:00:00+00:00") is not None

    def test_invalid_returns_none(self):
        from wildframe_events.event import _parse_iso

        assert _parse_iso("nope") is None
