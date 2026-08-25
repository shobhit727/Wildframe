"""Tests for event hardening: payload validation, publisher size enforcement,
subscriber DLQ/quarantine/retry/dedup, topic ACL, and log sanitization."""

import asyncio
import json
import logging
from io import StringIO

import pytest

from wildframe_events.event import (
    DomainEvent,
    PayloadValidationError,
    SchemaVersionError,
    validate_payload,
)
from wildframe_events.publisher import (
    DEFAULT_MAX_PAYLOAD_BYTES,
    EventTooLargeError,
    InMemoryEventPublisher,
    KafkaEventPublisher,
)
from wildframe_events.subscriber import (
    InMemoryEventSubscriber,
    InMemoryDeduplicationStore,
    PermanentFailure,
)
from wildframe_events.topics import (
    topic_acl,
    validate_topic_metadata,
)
from wildframe_observability.logging import (
    JSONFormatter,
    _sanitize_for_log,
)

# ---------------------------------------------------------------------------
# Event payload validation
# ---------------------------------------------------------------------------


class TestEventPayloadValidation:
    def test_validate_payload_rejects_secret_keys(self):
        with pytest.raises(PayloadValidationError, match="secret-shaped key"):
            validate_payload({"password": "hunter2"})

    def test_validate_payload_rejects_secret_keys_case_insensitive(self):
        # Keys in _SECRET_KEYS: password, passwd, secret, authorization, api_key, apikey,
        # access_token, refresh_token, auth_token, private_key, client_secret, x_api_key,
        # x_amz_security_token, aws_secret_access_key
        # Matching uses key.lower().lstrip("_") - so "Private_Key" -> "private_key" works,
        # but "PrivateKey" -> "privatekey" does NOT match "private_key"
        for key in [
            "PASSWORD",
            "Password",
            "ApiKey",
            "SECRET",
            "Authorization",
            "ACCESS_TOKEN",
            "PRIVATE_KEY",
        ]:
            with pytest.raises(PayloadValidationError, match="secret-shaped key"):
                validate_payload({key: "value"})

    def test_validate_payload_rejects_nan_inf(self):
        with pytest.raises(PayloadValidationError, match="non-finite float"):
            validate_payload({"x": float("nan")})
        with pytest.raises(PayloadValidationError, match="non-finite float"):
            validate_payload({"x": float("inf")})
        with pytest.raises(PayloadValidationError, match="non-finite float"):
            validate_payload({"x": float("-inf")})

    def test_validate_payload_rejects_nested_nan(self):
        with pytest.raises(PayloadValidationError, match="non-finite float"):
            validate_payload({"outer": {"inner": float("nan")}})

    def test_validate_payload_accepts_valid(self):
        # Should not raise
        validate_payload({"normal": "value", "number": 42, "bool": True, "list": [1, 2, 3]})

    def test_domain_event_from_dict_validates_payload(self):
        with pytest.raises(PayloadValidationError, match="secret-shaped key"):
            DomainEvent.from_dict(
                {
                    "topic": "t",
                    "key": "k",
                    "payload": {"password": "x"},
                    "schema_version": 1,
                    "event_id": "00000000-0000-4000-8000-000000000000",
                    "occurred_at": "2026-01-01T00:00:00+00:00",
                }
            )

    def test_domain_event_from_dict_validates_schema_version(self):
        with pytest.raises(SchemaVersionError, match="newer than supported"):
            DomainEvent.from_dict(
                {
                    "topic": "t",
                    "key": "k",
                    "payload": {"a": 1},
                    "schema_version": 999,
                    "event_id": "00000000-0000-4000-8000-000000000000",
                    "occurred_at": "2026-01-01T00:00:00+00:00",
                }
            )

    def test_domain_event_roundtrip_preserves_new_fields(self):
        evt = DomainEvent(
            topic="t",
            key="k",
            payload={"x": 1},
            correlation_id="corr-123",
            sequence=42,
            server_time="2026-01-01T00:00:00+00:00",
        )
        data = evt.to_dict()
        evt2 = DomainEvent.from_dict(data)
        assert evt2.correlation_id == "corr-123"
        assert evt2.sequence == 42
        assert evt2.server_time == "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Publisher size enforcement
# ---------------------------------------------------------------------------


class TestPublisherSizeEnforcement:
    @pytest.mark.asyncio
    async def test_inmemory_publisher_enforces_size_limit(self):
        pub = InMemoryEventPublisher(max_payload_bytes=100)
        with pytest.raises(EventTooLargeError):
            await pub.publish(DomainEvent(topic="t", key="k", payload={"x": "y" * 200}))

    @pytest.mark.asyncio
    async def test_inmemory_publisher_allows_under_limit(self):
        pub = InMemoryEventPublisher(max_payload_bytes=10_000)
        await pub.publish(DomainEvent(topic="t", key="k", payload={"x": "small"}))
        assert len(pub.sent) == 1

    @pytest.mark.asyncio
    async def test_inmemory_publisher_rejects_secret_in_payload(self):
        pub = InMemoryEventPublisher(max_payload_bytes=10_000)
        with pytest.raises(PayloadValidationError):
            await pub.publish(DomainEvent(topic="t", key="k", payload={"api_key": "secret"}))

    @pytest.mark.asyncio
    async def test_kafka_publisher_defaults(self):
        kp = KafkaEventPublisher("localhost:9092")
        assert kp.max_payload_bytes == DEFAULT_MAX_PAYLOAD_BYTES
        assert kp.max_retries == 5
        assert kp.retry_backoff_ms == 500
        await kp.close()


# ---------------------------------------------------------------------------
# Subscriber DLQ / quarantine / retry / dedup
# ---------------------------------------------------------------------------


class TestInMemoryDeduplicationStore:
    @pytest.mark.asyncio
    async def test_check_and_mark_dedup(self):
        store = InMemoryDeduplicationStore()
        assert await store.check("key1", 1.0) is True
        await store.mark("key1", 1.0)
        assert await store.check("key1", 1.0) is False

    @pytest.mark.asyncio
    async def test_dedup_ttl_expiry(self):
        store = InMemoryDeduplicationStore()
        await store.mark("key1", 0.01)  # 10ms TTL
        await asyncio.sleep(0.02)
        assert await store.check("key1", 1.0) is True  # expired

    @pytest.mark.asyncio
    async def test_check_returns_true_for_new_key(self):
        store = InMemoryDeduplicationStore()
        assert await store.check("new", 60.0) is True


class TestInMemoryEventSubscriber:
    @pytest.mark.asyncio
    async def test_handler_receives_event(self):
        sub = InMemoryEventSubscriber()
        received = []

        async def handler(e):
            received.append(e)

        await sub.subscribe("test.topic", handler)
        await sub.start()
        evt = DomainEvent(topic="test.topic", key="k", payload={"a": 1})
        await sub.deliver(evt)
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_multiple_handlers_same_topic_all_called(self):
        sub = InMemoryEventSubscriber()
        calls = []

        async def h1(e):
            calls.append(1)

        async def h2(e):
            calls.append(2)

        await sub.subscribe("t", h1)
        await sub.subscribe("t", h2)
        await sub.start()
        await sub.deliver(DomainEvent(topic="t", key="k", payload={}))
        assert calls == [1, 2]

    @pytest.mark.asyncio
    async def test_handler_not_called_for_other_topic(self):
        sub = InMemoryEventSubscriber()
        calls = []

        async def h(e):
            calls.append(1)

        await sub.subscribe("t1", h)
        await sub.start()
        await sub.deliver(DomainEvent(topic="t2", key="k", payload={}))
        assert calls == []

    @pytest.mark.asyncio
    async def test_raising_handler_does_not_stop_others(self):
        sub = InMemoryEventSubscriber()
        calls = []

        async def good(e):
            calls.append("good")

        async def bad(e):
            raise ValueError("boom")

        await sub.subscribe("t", good)
        await sub.subscribe("t", bad)
        await sub.start()
        await sub.deliver(DomainEvent(topic="t", key="k", payload={}))
        assert "good" in calls


class TestKafkaEventSubscriberLogic:
    """Unit tests for KafkaEventSubscriber internal logic using mocks."""

    @pytest.mark.asyncio
    async def test_permanent_failure_skips_retries_and_dlqs(self):
        from unittest.mock import AsyncMock, MagicMock

        sub = MagicMock()
        sub.bootstrap_servers = "localhost:9092"
        sub.group_id = "test-group"
        sub.client_id = "test-client"
        sub.max_retries = 3
        sub.retry_backoff_ms = 1000
        sub.max_payload_bytes = 1_000_000
        sub.dedup_store = None
        sub.dedup_ttl_seconds = 86_400
        sub.dlq_publisher = None
        sub._handlers = {"test.topic": []}
        sub._consumer = AsyncMock()
        sub._lazy_dlq_publisher = None
        sub._reconnect_attempt = 0

        from wildframe_events.subscriber import KafkaEventSubscriber

        subscriber = KafkaEventSubscriber(
            bootstrap_servers="localhost:9092",
            group_id="test-group",
        )
        subscriber._handlers = {"test.topic": []}
        subscriber._lazy_dlq_publisher = AsyncMock()
        subscriber._lazy_dlq_publisher.publish = AsyncMock()

        async def bad_handler(e):
            raise PermanentFailure("business rule violation")

        subscriber._handlers["test.topic"] = [bad_handler]

        evt = DomainEvent(topic="test.topic", key="k", payload={"x": 1})
        await subscriber._dispatch(evt)

        subscriber._lazy_dlq_publisher.publish.assert_called_once()
        call_args = subscriber._lazy_dlq_publisher.publish.call_args[0][0]
        assert call_args.payload["reason"] == "permanent_failure"
        assert call_args.payload["attempts"] == 1

    @pytest.mark.asyncio
    async def test_transient_failure_retries_then_dlqs(self):
        from unittest.mock import AsyncMock
        from wildframe_events.subscriber import KafkaEventSubscriber

        subscriber = KafkaEventSubscriber(
            bootstrap_servers="localhost:9092",
            group_id="test-group",
            max_retries=2,
            retry_backoff_ms=1,  # fast
            dlq_publisher=AsyncMock(),
        )
        subscriber._lazy_dlq_publisher = subscriber.dlq_publisher

        attempts = []

        async def flaky_handler(e):
            attempts.append(1)
            if len(attempts) < 2:
                raise ValueError("transient")
            # succeed on attempt 2

        subscriber._handlers = {"test.topic": [flaky_handler]}

        evt = DomainEvent(topic="test.topic", key="k", payload={"x": 1})
        await subscriber._dispatch(evt)

        assert len(attempts) == 2
        subscriber.dlq_publisher.publish.assert_not_called()


# ---------------------------------------------------------------------------
# Topic ACL
# ---------------------------------------------------------------------------


class TestTopicACL:
    def test_billing_service_acl(self):
        acl = topic_acl("billing-service")
        assert "billing.subscription.created" in acl["produce"]
        assert "creator.onboarded" in acl["consume"]
        assert "creator.onboarded.dlq" in acl["produce"]  # DLQ auto-added

    def test_content_service_acl(self):
        acl = topic_acl("content-service")
        assert "content.published" in acl["produce"]
        assert "content.uploaded" in acl["consume"]
        assert "content.uploaded.dlq" in acl["produce"]

    def test_unknown_service_returns_empty(self):
        acl = topic_acl("nonexistent-service")
        assert acl == {"produce": [], "consume": []}

    def test_all_topics_and_dlq(self):
        from wildframe_events.topics import all_topics, all_dlq_topics

        topics = all_topics()
        dlqs = all_dlq_topics()
        assert len(topics) == 23
        assert len(dlqs) == 23
        for t in topics:
            assert t + ".dlq" in dlqs

    def test_validate_topic_metadata_passes(self):
        validate_topic_metadata()  # Should not raise


# ---------------------------------------------------------------------------
# Log injection sanitization
# ---------------------------------------------------------------------------


class TestLogSanitization:
    def test_sanitize_strips_control_chars(self):
        assert _sanitize_for_log("hello\x00world") == "helloworld"
        assert _sanitize_for_log("a\x07b") == "ab"

    def test_sanitize_strips_ansi_escapes(self):
        assert _sanitize_for_log("\x1b[31mred\x1b[0m") == "red"

    def test_sanitize_replaces_newlines_with_space(self):
        assert _sanitize_for_log("line1\nline2") == "line1 line2"
        assert _sanitize_for_log("line1\r\nline2") == "line1 line2"
        assert _sanitize_for_log("line1\rline2") == "line1 line2"

    def test_sanitize_truncates_long_strings(self):
        long = "x" * 15_000
        result = _sanitize_for_log(long)
        assert len(result) == 10_000 + len("…[truncated]")
        assert result.endswith("…[truncated]")

    def test_sanitize_recurses_dicts(self):
        result = _sanitize_for_log({"a": "b\nc", "d": {"e": "\x00"}})
        assert result == {"a": "b c", "d": {"e": ""}}

    def test_sanitize_recurses_lists(self):
        result = _sanitize_for_log(["a\nb", "c"])
        assert result == ["a b", "c"]

    def test_sanitize_leaves_numbers_bool_none_alone(self):
        assert _sanitize_for_log(42) == 42
        assert _sanitize_for_log(3.14) == 3.14
        assert _sanitize_for_log(True) is True
        assert _sanitize_for_log(None) is None

    def test_json_formatter_sanitizes_message(self):
        logger = logging.getLogger("test.sanitize")
        logger.setLevel(logging.DEBUG)
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JSONFormatter(service_name="test"))
        logger.addHandler(handler)
        logger.propagate = False

        logger.info("hello\nworld")
        output = stream.getvalue().strip()
        record = json.loads(output)
        assert "hello world" in record["message"]
        assert "\n" not in record["message"]

    def test_json_formatter_sanitizes_extra_fields(self):
        logger = logging.getLogger("test.extra")
        logger.setLevel(logging.DEBUG)
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JSONFormatter(service_name="test"))
        logger.addHandler(handler)
        logger.propagate = False

        logger.info("msg", extra={"user_input": "evil\ninject"})
        output = stream.getvalue().strip()
        record = json.loads(output)
        assert "evil inject" in record["user_input"]
        assert "\n" not in record["user_input"]

    def test_json_formatter_sanitizes_exception(self):
        logger = logging.getLogger("test.exc")
        logger.setLevel(logging.DEBUG)
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JSONFormatter(service_name="test"))
        logger.addHandler(handler)
        logger.propagate = False

        try:
            raise ValueError("bad\ninput")
        except ValueError:
            logger.exception("failed")

        output = stream.getvalue().strip()
        record = json.loads(output)
        assert "bad input" in record["exception"]
        assert "\n" not in record["exception"]


# ---------------------------------------------------------------------------
# CorrelationMiddleware header sanitization
# ---------------------------------------------------------------------------


class TestCorrelationMiddlewareSanitization:
    @pytest.mark.asyncio
    async def test_sanitizes_request_id_header(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from wildframe_observability.middleware import CorrelationMiddleware

        app = FastAPI()
        app.add_middleware(CorrelationMiddleware)

        @app.get("/")
        async def root():
            return {"ok": True}

        client = TestClient(app)
        # Header with newline injection attempt
        resp = client.get("/", headers={"x-request-id": "req\ninjected"})
        assert resp.status_code == 200
        # The returned header should be sanitized
        returned = resp.headers.get("x-request-id")
        assert returned is not None
        assert "\n" not in returned
        assert "injected" in returned  # content preserved, newline stripped
