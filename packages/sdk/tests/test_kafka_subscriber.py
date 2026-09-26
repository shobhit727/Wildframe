"""Behavioural tests for ``KafkaEventSubscriber`` — the aiokafka consumer adapter.

A real broker proves nothing extra here: the adapter touches exactly two
aiokafka symbols, both imported *inside* methods::

    subscriber.py:348   from aiokafka import AIOKafkaConsumer   (start)
    subscriber.py:387   from aiokafka import AIOKafkaConsumer   (_reconnect)

so ``patch("aiokafka.AIOKafkaConsumer", FakeConsumer)`` intercepts them. There
is deliberately no ``TopicPartition`` / ``OffsetAndMetadata`` / admin-client
surface in this adapter — offsets are committed with a bare
``await consumer.commit()`` because ``enable_auto_commit=False``.

The tests cover the lifecycle (start / poll loop / reconnect / stop), the
security branches (ssl/sasl/env resolution), the deserialisation coercions,
the dedup-key logic, and every DLQ failure branch.
"""

from __future__ import annotations

import asyncio
import json
import ssl
import sys
from typing import Any

import pytest

import wildframe_events.subscriber as sub_mod
from wildframe_events.event import DomainEvent
from wildframe_events.publisher import EventTooLargeError, InMemoryEventPublisher
from wildframe_events.subscriber import (
    MAX_RETRY_BACKOFF_MS,
    KafkaEventSubscriber,
    RedisDeduplicationStore,
    _CounterProxy,
    _run_with_event_correlation,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeMessage:
    """Minimal stand-in for an aiokafka ConsumerRecord."""

    def __init__(
        self,
        value: Any,
        *,
        topic: str = "test.topic",
        key: Any = b"k",
        timestamp: int | None = 1_700_000_000_000,
    ) -> None:
        self.topic = topic
        self.key = key
        self.value = value
        self.timestamp = timestamp


class FakeConsumer:
    """Async-iterable stand-in for ``AIOKafkaConsumer``.

    ``scripts`` is a per-test QUEUE of message scripts: the Nth consumer
    constructed pops the Nth entry. Each entry is a list consumed by
    ``__anext__``:
      * ``BaseException`` instance  -> raised instead of yielding
      * anything else              -> yielded as a message

    Once a script is exhausted the consumer *parks* on an unset
    ``asyncio.Event`` instead of raising ``StopAsyncIteration``. This mirrors
    the real client (whose ``__anext__`` always awaits a broker fetch) and
    matters: the adapter's poll loop is ``while True: async for ...``, so a
    fake that returned instantly would spin a tight, non-yielding loop and
    starve the event loop.
    """

    #: Queue of per-consumer scripts, and every instance built during a test.
    scripts: list[list[Any]] = []
    instances: list["FakeConsumer"] = []

    def __init__(self, *topics: str, **kwargs: Any) -> None:
        self.topics = list(topics)
        self.kwargs = kwargs
        queue = type(self).scripts
        self.script: list[Any] = queue.pop(0) if queue else []
        self.idle = asyncio.Event()
        self.commits = 0
        self.started = 0
        self.stopped = 0
        self.start_error: Exception | None = None
        self.stop_error: Exception | None = None
        self.commit_error: Exception | None = None
        self._iter = 0
        type(self).instances.append(self)

    async def start(self) -> None:
        self.started += 1
        if type(self).start_error is not None:
            raise type(self).start_error

    async def stop(self) -> None:
        self.stopped += 1
        if type(self).stop_error is not None:
            raise type(self).stop_error

    async def commit(self) -> None:
        self.commits += 1
        if type(self).commit_error is not None:
            raise type(self).commit_error

    def __aiter__(self) -> "FakeConsumer":
        return self

    async def __anext__(self) -> Any:
        if self._iter >= len(self.script):
            await self.idle.wait()  # idle consumer: block until cancelled
            raise StopAsyncIteration
        item = self.script[self._iter]
        self._iter += 1
        if isinstance(item, BaseException):
            raise item
        return item


def install_consumer(
    monkeypatch, consumer_cls=FakeConsumer, scripts: list[list[Any]] | None = None
) -> list[Any]:
    """Patch ``aiokafka.AIOKafkaConsumer`` with a recording class.

    ``scripts`` is pre-loaded into the per-consumer script queue, which is how
    a test controls what each (re)connected consumer yields without racing
    the poll task.
    """
    import aiokafka

    consumer_cls.instances = []  # type: ignore[attr-defined]
    consumer_cls.scripts = [list(s) for s in (scripts or [])]  # type: ignore[attr-defined]
    for attr in ("start_error", "stop_error", "commit_error"):
        setattr(consumer_cls, attr, None)  # type: ignore[attr-defined]
    monkeypatch.setattr(aiokafka, "AIOKafkaConsumer", consumer_cls)
    return consumer_cls.instances  # type: ignore[attr-defined,return-value]


EventHandler = Any

#: The real ``asyncio.sleep``, captured before any fixture patches it.
_REAL_SLEEP = asyncio.sleep


def _declared_insecure_default(module) -> bool:
    """Read ``KAFKA_SSL_INSECURE``'s default straight out of the module source.

    The default has been flipped once already (``"true"`` -> ``"false"``, i.e.
    insecure-by-default -> verifying-by-default). Tests must not hard-code it:
    the contract is "the observed behaviour matches the declared default, and
    both branches are reachable via the env var", not a specific literal.
    """
    import ast
    import inspect

    source = inspect.getsource(module)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "getenv" and node.args[0].value == "KAFKA_SSL_INSECURE":
            default = node.args[1].value
            return default.lower() not in ("false", "0", "no")
    raise AssertionError("KAFKA_SSL_INSECURE default not found in " + module.__name__)


def assert_default_matches_declaration(module, ssl_context) -> None:
    """The context the adapter built must agree with the declared default."""
    if _declared_insecure_default(module):
        assert ssl_context is not None
        assert ssl_context.check_hostname is False
        assert ssl_context.verify_mode == ssl.CERT_NONE
    else:
        assert ssl_context is None


def env_bytes(event: DomainEvent) -> bytes:
    return json.dumps(event.to_dict()).encode()


def sub(**kw: Any) -> KafkaEventSubscriber:
    defaults: dict[str, Any] = {
        "bootstrap_servers": "kafka:9092",
        "group_id": "billing-group",
        "client_id": "billing-consumer",
        "max_retries": 2,
        "retry_backoff_ms": 1,
        "dlq_publisher": InMemoryEventPublisher(),
    }
    defaults.update(kw)
    s = KafkaEventSubscriber(**defaults)
    s._lazy_dlq_publisher = s.dlq_publisher
    return s


@pytest.fixture(autouse=True)
def _no_kafka_env(monkeypatch):
    for var in (
        "KAFKA_SECURITY_PROTOCOL",
        "KAFKA_SASL_MECHANISM",
        "KAFKA_SASL_USERNAME",
        "KAFKA_SASL_PASSWORD",
        "KAFKA_SSL_CA_LOCATION",
        "KAFKA_SSL_INSECURE",
    ):
        monkeypatch.delenv(var, raising=False)
    # Kill retry/reconnect sleeps so the tests are fast and deterministic.
    monkeypatch.setattr(sub_mod.asyncio, "sleep", _async_noop)
    monkeypatch.setattr(sub_mod.random, "uniform", lambda a, b: 1.0)


async def _async_noop(_seconds: float = 0.0) -> None:
    """Stand-in for ``asyncio.sleep`` — returns instantly instead of waiting."""
    return None


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    # NB: the autouse fixture patches ``asyncio.sleep`` process-wide (that is
    # how the adapter's retry/reconnect delays are neutralised), so polling
    # must use the ORIGINAL sleep captured at import time.
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await _REAL_SLEEP(0.001)
    raise AssertionError("condition never became true")


# ---------------------------------------------------------------------------
# Construction / security config (subscriber.py:251-309)
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_defaults(self):
        s = KafkaEventSubscriber("kafka:9092", "g1")
        assert s.bootstrap_servers == "kafka:9092"
        assert s.group_id == "g1"
        assert s.client_id == "wildframe-consumer"
        assert s.max_retries == 3
        assert s.retry_backoff_ms == 1000
        assert s.dedup_store is None
        assert s.dedup_ttl_seconds == 86_400.0
        assert s.dlq_publisher is None
        assert s.security_protocol == "PLAINTEXT"
        assert s.ssl_context is None
        assert s._consumer is None
        assert s._task is None
        assert s._lazy_dlq_publisher is None
        assert s._reconnect_attempt == 0

    def test_sasl_from_env(self, monkeypatch):
        monkeypatch.setenv("KAFKA_SASL_MECHANISM", "SCRAM-SHA-512")
        monkeypatch.setenv("KAFKA_SASL_USERNAME", "svc")
        monkeypatch.setenv("KAFKA_SASL_PASSWORD", "pw")
        s = KafkaEventSubscriber("kafka:9092", "g1")
        assert s.sasl_mechanism == "SCRAM-SHA-512"
        assert s.sasl_username == "svc"
        assert s.sasl_password == "pw"

    def test_explicit_sasl_beats_env(self, monkeypatch):
        monkeypatch.setenv("KAFKA_SASL_USERNAME", "from-env")
        assert KafkaEventSubscriber("kafka:9092", "g1", sasl_username="arg").sasl_username == "arg"

    def test_explicit_ssl_context_used(self):
        ctx = ssl.create_default_context()
        assert KafkaEventSubscriber("kafka:9092", "g1", ssl_context=ctx).ssl_context is ctx

    def test_ca_env_context(self, monkeypatch):
        seen: list[Any] = []
        monkeypatch.setenv("KAFKA_SSL_CA_LOCATION", "/certs/ca.pem")
        monkeypatch.setattr(
            ssl, "create_default_context",
            lambda *, cafile=None: seen.append(cafile) or ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        )
        s = KafkaEventSubscriber("kafka:9092", "g1")
        assert seen == ["/certs/ca.pem"]
        assert s.ssl_context is not None

    @pytest.mark.parametrize("protocol", ["SSL", "SASL_SSL"])
    def test_ssl_insecure_branch_disables_verification(self, monkeypatch, protocol):
        monkeypatch.setenv("KAFKA_SSL_INSECURE", "true")
        monkeypatch.setattr(ssl, "create_default_context", lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
        s = KafkaEventSubscriber("kafka:9092", "g1", security_protocol=protocol)
        assert s.ssl_context.check_hostname is False
        assert s.ssl_context.verify_mode == ssl.CERT_NONE

    @pytest.mark.parametrize("protocol", ["SSL", "SASL_SSL"])
    def test_ssl_default_matches_the_declared_default(self, protocol):
        """Env var unset -> the behaviour the module declares, whichever that is."""
        s = KafkaEventSubscriber("kafka:9092", "g1", security_protocol=protocol)
        assert_default_matches_declaration(sub_mod, s.ssl_context)

    def test_ssl_verifying_when_insecure_disabled(self, monkeypatch):
        monkeypatch.setenv("KAFKA_SSL_INSECURE", "false")
        monkeypatch.setattr(ssl, "create_default_context", lambda: pytest.fail("must not build"))
        assert KafkaEventSubscriber("kafka:9092", "g1", security_protocol="SSL").ssl_context is None

    def test_plaintext_has_no_context(self):
        assert KafkaEventSubscriber("kafka:9092", "g1", security_protocol="PLAINTEXT").ssl_context is None

    def test_security_protocol_from_env_drives_the_ssl_branch(self, monkeypatch):
        """KAFKA_SECURITY_PROTOCOL alone decides that the SSL branch runs at all."""
        monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SSL")
        monkeypatch.setenv("KAFKA_SSL_INSECURE", "true")
        monkeypatch.setattr(ssl, "create_default_context", lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
        assert KafkaEventSubscriber("kafka:9092", "g1").ssl_context is not None


# ---------------------------------------------------------------------------
# _consumer_kwargs (subscriber.py:317-335)
# ---------------------------------------------------------------------------


class TestConsumerKwargs:
    def test_manual_ack_and_offsets_earliest(self):
        s = sub()
        kw = s._consumer_kwargs()
        assert kw["enable_auto_commit"] is False
        assert kw["auto_offset_reset"] == "earliest"
        assert kw["bootstrap_servers"] == "kafka:9092"
        assert kw["group_id"] == "billing-group"
        assert kw["client_id"] == "billing-consumer"
        assert kw["security_protocol"] == "PLAINTEXT"

    def test_fetch_bytes_leave_headroom_over_payload_cap(self):
        assert sub(max_payload_bytes=1234)._consumer_kwargs()["max_partition_fetch_bytes"] == 1234 + 8192

    def test_sasl_and_ssl_keys_absent_by_default(self):
        kw = sub()._consumer_kwargs()
        for key in ("ssl_context", "sasl_mechanism", "sasl_plain_username", "sasl_plain_password"):
            assert key not in kw

    def test_sasl_keys_included_when_configured(self):
        kw = sub(sasl_mechanism="PLAIN", sasl_username="u", sasl_password="p")._consumer_kwargs()
        assert kw["sasl_mechanism"] == "PLAIN"
        assert kw["sasl_plain_username"] == "u"
        assert kw["sasl_plain_password"] == "p"

    def test_ssl_context_included_when_present(self):
        ctx = ssl.create_default_context()
        assert sub(ssl_context=ctx)._consumer_kwargs()["ssl_context"] is ctx

    def test_partial_sasl_only_emits_what_was_set(self):
        kw = sub(sasl_mechanism="PLAIN")._consumer_kwargs()
        assert kw["sasl_mechanism"] == "PLAIN"
        assert "sasl_plain_username" not in kw
        assert "sasl_plain_password" not in kw


# ---------------------------------------------------------------------------
# subscribe / start (subscriber.py:337-361)
# ---------------------------------------------------------------------------


class TestStart:
    @pytest.mark.asyncio
    async def test_subscribe_registers_multiple_handlers_per_topic(self):
        s = sub()
        await s.subscribe("t", _noop)
        await s.subscribe("t", _noop)
        await s.subscribe("u", _noop)
        assert len(s._handlers) == 2
        assert len(s._handlers["t"]) == 2

    @pytest.mark.asyncio
    async def test_start_without_topics_raises(self):
        with pytest.raises(ValueError, match="at least one topic"):
            await sub().start()

    @pytest.mark.asyncio
    async def test_start_is_a_noop_when_already_started(self, monkeypatch):
        created = install_consumer(monkeypatch)
        s = sub()
        await s.subscribe("t", _noop)
        await s.start()
        await s.start()
        assert len(created) == 1
        await s.stop()

    @pytest.mark.asyncio
    async def test_start_constructs_consumer_with_topics_and_kwargs(self, monkeypatch):
        created = install_consumer(monkeypatch)
        s = sub()
        await s.subscribe("a", _noop)
        await s.subscribe("b", _noop)
        await s.start()
        assert created[0].topics == ["a", "b"]
        assert created[0].kwargs["group_id"] == "billing-group"
        assert created[0].kwargs["enable_auto_commit"] is False
        assert created[0].started == 1
        assert s._task is not None and not s._task.done()
        await s.stop()

    @pytest.mark.asyncio
    async def test_start_spawns_named_poll_task(self, monkeypatch):
        install_consumer(monkeypatch)
        s = sub()
        await s.subscribe("t", _noop)
        await s.start()
        assert s._task.get_name() == "wildframe-subscriber-billing-group"
        await s.stop()

    @pytest.mark.asyncio
    async def test_start_resets_reconnect_backoff(self, monkeypatch):
        install_consumer(monkeypatch)
        s = sub()
        s._reconnect_attempt = 7
        await s.subscribe("t", _noop)
        await s.start()
        assert s._reconnect_attempt == 0
        await s.stop()

    @pytest.mark.asyncio
    async def test_consumer_start_failure_propagates(self, monkeypatch):
        install_consumer(monkeypatch)
        FakeConsumer.start_error = RuntimeError("no broker")
        s = sub()
        await s.subscribe("t", _noop)
        with pytest.raises(RuntimeError, match="no broker"):
            await s.start()
        assert s._task is None

    @pytest.mark.asyncio
    async def test_consumer_or_raise_asserts_when_absent(self):
        with pytest.raises(AssertionError, match="Consumer not started"):
            _ = sub()._consumer_or_raise


# ---------------------------------------------------------------------------
# _run + _reconnect (subscriber.py:363-400)
# ---------------------------------------------------------------------------


class TestRunLoop:
    @pytest.mark.asyncio
    async def test_processes_messages_from_the_consumer(self, monkeypatch):
        event = DomainEvent(topic="test.topic", key="k1", payload={})
        install_consumer(
            monkeypatch, scripts=[[FakeMessage(env_bytes(event), topic="test.topic")]]
        )
        s = sub()
        seen: list[str] = []
        await s.subscribe("test.topic", lambda e: seen.append(e.key) or _REAL_SLEEP(0))
        await s.start()
        await _wait_until(lambda: FakeConsumer.instances[0].commits == 1)
        await s.stop()
        assert seen == ["k1"]

    @pytest.mark.asyncio
    async def test_broker_error_backs_off_and_reconnects(self, monkeypatch):
        created = install_consumer(monkeypatch, scripts=[[RuntimeError("connection reset")]])
        s = sub()
        await s.subscribe("t", _noop)
        await s.start()

        # After the error, _reconnect() must build a *new* consumer, start it,
        # and stop the old one.
        await _wait_until(lambda: len(created) >= 2)
        await s.stop()
        assert len(created) == 2
        assert created[1] is not created[0]
        assert created[0].stopped == 1
        assert created[1].started == 1
        assert created[1].topics == ["t"]

    @pytest.mark.asyncio
    async def test_reconnect_backoff_is_exponential_and_capped(self, monkeypatch):
        """delay = retry_backoff_ms * 2**attempt, capped at MAX_RETRY_BACKOFF_MS."""
        delays: list[float] = []

        async def _record(seconds: float) -> None:
            delays.append(seconds)

        monkeypatch.setattr(sub_mod.asyncio, "sleep", _record)
        monkeypatch.setattr(sub_mod.random, "uniform", lambda a, b: 1.0)

        boom = [RuntimeError("broker down")]
        install_consumer(monkeypatch, scripts=[boom, boom, boom])
        s = sub(retry_backoff_ms=1000)
        await s.subscribe("t", _noop)
        await s.start()
        await _wait_until(lambda: len(delays) == 3)
        await s.stop()

        # attempt 0 -> 1.0s, attempt 1 -> 2.0s, attempt 2 -> 4.0s
        assert delays == [1.0, 2.0, 4.0]
        assert s._reconnect_attempt == 3

    @pytest.mark.asyncio
    async def test_reconnect_backoff_is_capped_at_sixty_seconds(self, monkeypatch):
        delays: list[float] = []

        async def _record(seconds: float) -> None:
            delays.append(seconds)

        monkeypatch.setattr(sub_mod.asyncio, "sleep", _record)
        monkeypatch.setattr(sub_mod.random, "uniform", lambda a, b: 1.0)

        install_consumer(monkeypatch, scripts=[[RuntimeError("boom")]])
        s = sub(retry_backoff_ms=1000)
        await s.subscribe("t", _noop)
        await s.start()
        # start() resets _reconnect_attempt to 0, so the counter must be primed
        # after start() (the poll task has not run yet at this point).
        s._reconnect_attempt = 30  # 2**30 would be enormous without the cap
        await _wait_until(lambda: len(delays) == 1)
        await s.stop()
        assert delays == [MAX_RETRY_BACKOFF_MS / 1000.0]

    @pytest.mark.asyncio
    async def test_backoff_saturates_at_the_cap_over_many_attempts(self, monkeypatch):
        delays: list[float] = []

        async def _record(seconds: float) -> None:
            delays.append(seconds)

        monkeypatch.setattr(sub_mod.asyncio, "sleep", _record)
        monkeypatch.setattr(sub_mod.random, "uniform", lambda a, b: 1.0)

        install_consumer(monkeypatch, scripts=[[RuntimeError("b")]] * 9)
        s = sub(retry_backoff_ms=1000)
        s._reconnect_attempt = 0
        await s.subscribe("t", _noop)
        await s.start()
        await _wait_until(lambda: len(delays) == 9)
        await s.stop()
        # 2**0..2**5, then 2**6=64s clamped by MAX_RETRY_BACKOFF_MS (60s),
        # and the exponent itself saturates at 2**6 (min(attempt, 6)).
        assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]

    @pytest.mark.asyncio
    async def test_jitter_scales_the_backoff_down(self, monkeypatch):
        """random.uniform(0.5, 1.0) is applied to the delay."""
        delays: list[float] = []

        async def _record(seconds: float) -> None:
            delays.append(seconds)

        monkeypatch.setattr(sub_mod.asyncio, "sleep", _record)
        monkeypatch.setattr(sub_mod.random, "uniform", lambda a, b: 0.5)
        install_consumer(monkeypatch, scripts=[[RuntimeError("boom")]])
        s = sub(retry_backoff_ms=1000)
        await s.subscribe("t", _noop)
        await s.start()
        await _wait_until(lambda: len(delays) == 1)
        await s.stop()
        assert delays == [0.5]

    @pytest.mark.asyncio
    async def test_cancellation_propagates_out_of_the_loop(self, monkeypatch):
        install_consumer(monkeypatch, scripts=[[asyncio.CancelledError()]])
        s = sub()
        await s.subscribe("t", _noop)
        await s.start()
        with pytest.raises(asyncio.CancelledError):
            await s._task
        assert s._reconnect_attempt == 0  # no reconnect attempted

    @pytest.mark.asyncio
    async def test_stopping_an_idle_consumer_cancels_the_task(self, monkeypatch):
        install_consumer(monkeypatch)
        s = sub()
        await s.subscribe("t", _noop)
        await s.start()
        task = s._task
        await s.stop()
        assert task.done()
        assert s._task is None

    @pytest.mark.asyncio
    async def test_reconnect_tolerates_a_failing_old_consumer_stop(self, monkeypatch):
        class BadStop(FakeConsumer):
            async def stop(self) -> None:
                raise RuntimeError("stop failed")

        created = install_consumer(monkeypatch, consumer_cls=BadStop)
        s = sub()
        await s.subscribe("t", _noop)
        await s.start()
        await s._reconnect()
        assert s._consumer is not None
        assert created[-1].started == 1
        await s.stop()

    @pytest.mark.asyncio
    async def test_reconnect_with_no_existing_consumer(self, monkeypatch):
        created = install_consumer(monkeypatch)
        s = sub()
        await s.subscribe("t", _noop)
        await s._reconnect()
        assert s._consumer is created[0]
        assert created[0].topics == ["t"]

    @pytest.mark.asyncio
    async def test_reconnect_consumer_start_failure_propagates(self, monkeypatch):
        created = install_consumer(monkeypatch)
        FakeConsumer.start_error = RuntimeError("reconnect refused")
        s = sub()
        await s.subscribe("t", _noop)
        with pytest.raises(RuntimeError, match="reconnect refused"):
            await s._reconnect()
        assert created[0].started == 1
        s._consumer = None  # leave no half-built state behind


# ---------------------------------------------------------------------------
# _dedup_keys (subscriber.py:402-409)
# ---------------------------------------------------------------------------


class TestDedupKeys:
    def test_event_id_key_only(self):
        e = DomainEvent(topic="t", key="k", payload={"a": 1})
        assert sub()._dedup_keys(e) == [f"t:{e.event_id}"]

    def test_idempotency_key_is_namespaced_and_appended(self):
        e = DomainEvent(topic="t", key="k", payload={"idempotency_key": "payout:42:2026-01"})
        assert sub()._dedup_keys(e) == [f"t:{e.event_id}", "t:idem:payout:42:2026-01"]

    def test_non_string_idempotency_key_ignored(self):
        e = DomainEvent(topic="t", key="k", payload={"idempotency_key": 7})
        assert sub()._dedup_keys(e) == [f"t:{e.event_id}"]

    def test_empty_idempotency_key_ignored(self):
        e = DomainEvent(topic="t", key="k", payload={"idempotency_key": ""})
        assert sub()._dedup_keys(e) == [f"t:{e.event_id}"]

    def test_keys_are_topic_scoped(self):
        a = DomainEvent(topic="a", key="k", payload={})
        b = DomainEvent(topic="b", key="k", payload={})
        s = sub()
        assert s._dedup_keys(a)[0] != s._dedup_keys(b)[0]


# ---------------------------------------------------------------------------
# _process_message deserialisation coercions (subscriber.py:411-466)
# ---------------------------------------------------------------------------


class TestProcessMessageCoercions:
    @pytest.mark.asyncio
    async def test_none_value_becomes_empty_and_is_quarantined(self, monkeypatch):
        s = sub()
        s._consumer = FakeConsumer()
        await s._process_message(FakeMessage(None))
        assert s.dlq_publisher.sent[0].payload["reason"] == "malformed"
        assert s.dlq_publisher.sent[0].payload["payload_size_bytes"] == 0

    @pytest.mark.asyncio
    async def test_str_value_is_encoded_to_utf8(self, monkeypatch):
        s = sub()
        s._consumer = FakeConsumer()
        event = DomainEvent(topic="test.topic", key="k", payload={"a": 1})
        await s._process_message(FakeMessage(json.dumps(event.to_dict())))
        assert s.dlq_publisher.sent == []  # parsed fine, no quarantine
        assert s._consumer.commits == 1

    @pytest.mark.asyncio
    async def test_non_utf8_bytes_are_quarantined_not_crashed(self, monkeypatch):
        s = sub()
        s._consumer = FakeConsumer()
        await s._process_message(FakeMessage(b"\xff\xfe\x00garbage"))
        assert s.dlq_publisher.sent[0].payload["reason"] == "malformed"
        assert s._consumer.commits == 1

    @pytest.mark.asyncio
    async def test_bytearray_value_accepted(self, monkeypatch):
        s = sub()
        s._consumer = FakeConsumer()
        event = DomainEvent(topic="test.topic", key="k", payload={"a": 1})
        await s._process_message(FakeMessage(bytearray(env_bytes(event))))
        assert s.dlq_publisher.sent == []

    @pytest.mark.asyncio
    async def test_missing_topic_and_key_are_tolerated(self, monkeypatch):
        s = sub()
        s._consumer = FakeConsumer()
        msg = FakeMessage(b"{not json", topic=None, key=None)
        await s._process_message(msg)
        dlq = s.dlq_publisher.sent[0]
        assert dlq.topic == ".dlq"  # empty original topic
        assert dlq.key == ""

    @pytest.mark.asyncio
    async def test_str_key_is_not_re_decoded(self, monkeypatch):
        s = sub()
        s._consumer = FakeConsumer()
        await s._process_message(FakeMessage(b"{bad", key="plain-str"))
        assert s.dlq_publisher.sent[0].key == "plain-str"

    @pytest.mark.asyncio
    async def test_broker_timestamp_becomes_server_time(self, monkeypatch):
        from datetime import datetime, timezone

        s = sub()
        s._consumer = FakeConsumer()
        seen: list[DomainEvent] = []

        async def capture(e: DomainEvent) -> None:
            seen.append(e)

        await s.subscribe("test.topic", capture)
        event = DomainEvent(topic="test.topic", key="k", payload={})
        await s._process_message(FakeMessage(env_bytes(event), timestamp=1_700_000_000_500))

        assert len(seen) == 1
        assert seen[0].server_time is not None
        # Server-controlled, derived from the broker timestamp (never occurred_at).
        assert datetime.fromisoformat(seen[0].server_time) == datetime.fromtimestamp(
            1_700_000_000.5, tz=timezone.utc
        )
        assert seen[0].server_time != seen[0].occurred_at

    @pytest.mark.asyncio
    async def test_server_time_preserved_when_already_set(self, monkeypatch):
        s = sub()
        s._consumer = FakeConsumer()
        seen: list[DomainEvent] = []

        async def capture(e: DomainEvent) -> None:
            seen.append(e)

        await s.subscribe("test.topic", capture)
        event = DomainEvent(topic="test.topic", key="k", payload={}, server_time="2026-01-01T00:00:00+00:00")
        await s._process_message(FakeMessage(env_bytes(event), timestamp=1_700_000_000_500))
        assert seen[0].server_time == "2026-01-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_missing_broker_timestamp_leaves_server_time_none(self, monkeypatch):
        s = sub()
        s._consumer = FakeConsumer()
        seen: list[DomainEvent] = []

        async def capture(e: DomainEvent) -> None:
            seen.append(e)

        await s.subscribe("test.topic", capture)
        event = DomainEvent(topic="test.topic", key="k", payload={})
        await s._process_message(FakeMessage(env_bytes(event), timestamp=None))
        assert seen[0].server_time is None

    @pytest.mark.asyncio
    async def test_commit_skipped_when_consumer_absent(self, monkeypatch):
        s = sub()
        s._consumer = None
        await s._process_message(FakeMessage(b"{bad"))  # must not raise


# ---------------------------------------------------------------------------
# DLQ publisher selection + _quarantine_raw / _send_to_dlq / _publish_dlq
# ---------------------------------------------------------------------------


class TestDlqPublishing:
    def test_injected_dlq_publisher_is_preferred(self):
        injected = InMemoryEventPublisher()
        s = KafkaEventSubscriber("kafka:9092", "g", dlq_publisher=injected)
        assert s._get_dlq_publisher() is injected

    def test_lazy_kafka_dlq_publisher_is_created_once(self):
        s = KafkaEventSubscriber("kafka:9092", "g", client_id="billing")
        first = s._get_dlq_publisher()
        assert first is s._get_dlq_publisher()
        assert first.client_id == "billing-dlq"
        assert first.bootstrap_servers == "kafka:9092"

    def test_lazy_dlq_publisher_inherits_security_settings(self):
        ctx = ssl.create_default_context()
        s = KafkaEventSubscriber(
            "kafka:9092",
            "g",
            security_protocol="SASL_SSL",
            sasl_mechanism="PLAIN",
            sasl_username="u",
            sasl_password="p",
            ssl_context=ctx,
        )
        lazy = s._get_dlq_publisher()
        assert lazy.security_protocol == "SASL_SSL"
        assert lazy.sasl_mechanism == "PLAIN"
        assert lazy.sasl_username == "u"
        assert lazy.sasl_password == "p"
        assert lazy.ssl_context is ctx

    @pytest.mark.asyncio
    async def test_quarantine_raw_never_embeds_the_payload(self, monkeypatch):
        s = sub(max_payload_bytes=1_000)
        s._consumer = FakeConsumer()
        raw = b"x" * 5_000
        await s._process_message(FakeMessage(raw, topic="big.topic", key=b"kk"))
        dlq = s.dlq_publisher.sent[0]
        assert dlq.topic == "big.topic.dlq"
        assert dlq.payload["original_topic"] == "big.topic"
        assert dlq.payload["original_key"] == "kk"
        assert dlq.payload["reason"] == "payload_too_large"
        assert dlq.payload["payload_size_bytes"] == 5_000
        assert "raw_preview" not in dlq.payload  # only malformed keeps a preview
        assert dlq.payload["consumer_group"] == "billing-group"
        assert dlq.producer == "billing-consumer"

    @pytest.mark.asyncio
    async def test_quarantine_raw_preview_is_bounded_to_512_bytes(self, monkeypatch):
        s = sub()
        s._consumer = FakeConsumer()
        await s._process_message(FakeMessage("é".encode() * 900))
        preview = s.dlq_publisher.sent[0].payload["raw_preview"]
        assert len(preview.encode("utf-8")) <= 512

    @pytest.mark.asyncio
    async def test_send_to_dlq_preserves_the_original_envelope(self, monkeypatch):
        s = sub()
        event = DomainEvent(topic="billing.payout.accrued", key="c-1", payload={"amount": 5.0})
        await s._send_to_dlq(event, ValueError("nope"), attempts=3, reason="retries_exhausted")
        payload = s.dlq_publisher.sent[0].payload
        assert s.dlq_publisher.sent[0].topic == "billing.payout.accrued.dlq"
        assert payload["original_event"]["event_id"] == event.event_id
        assert payload["original_event"]["payload"] == {"amount": 5.0}
        assert payload["error_type"] == "ValueError"
        assert payload["error"] == "nope"
        assert payload["attempts"] == 3
        assert payload["reason"] == "retries_exhausted"
        assert payload["consumer_group"] == "billing-group"
        assert "dlq_time" in payload

    @pytest.mark.asyncio
    async def test_publish_dlq_shrinks_an_oversized_dlq_event(self, monkeypatch):
        """The DLQ must never itself become a poison message: on
        EventTooLargeError the original envelope is dropped and retried."""
        events: list[DomainEvent] = []
        sizes: list[int] = []

        class FlakyPublisher:
            async def publish(self, event: DomainEvent) -> None:
                # Snapshot: the adapter mutates the same event in place on retry.
                events.append(json.loads(json.dumps(event.to_dict())))
                sizes.append(len(json.dumps(event.to_dict())))
                if len(events) == 1:
                    raise EventTooLargeError("dlq record too big")

        s = KafkaEventSubscriber("kafka:9092", "g", dlq_publisher=FlakyPublisher())
        event = DomainEvent(topic="t", key="k", payload={"blob": "z" * 100_000})
        await s._send_to_dlq(event, RuntimeError("handler died"), 1, "retries_exhausted")

        assert len(events) == 2
        assert "original_event" in events[0]["payload"]
        assert "original_event" not in events[1]["payload"]
        assert sizes[1] < sizes[0]
        # error context survives the shrink
        assert events[1]["payload"]["error"] == "handler died"

    @pytest.mark.asyncio
    async def test_publish_dlq_swallows_a_second_failure_after_shrinking(self, monkeypatch, caplog):
        import logging

        class AlwaysTooBig:
            async def publish(self, event: DomainEvent) -> None:
                raise EventTooLargeError("still too big")

        s = KafkaEventSubscriber("kafka:9092", "g", dlq_publisher=AlwaysTooBig())
        with caplog.at_level(logging.CRITICAL):
            await s._send_to_dlq(
                DomainEvent(topic="t", key="k", payload={}), RuntimeError("x"), 1, "retries_exhausted"
            )
        assert any("failed to publish DLQ event" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_publish_dlq_never_raises_on_transport_failure(self, monkeypatch, caplog):
        import logging

        class Broken:
            async def publish(self, event: DomainEvent) -> None:
                raise OSError("dlq broker down")

        s = KafkaEventSubscriber("kafka:9092", "g", dlq_publisher=Broken())
        with caplog.at_level(logging.CRITICAL):
            await s._send_to_dlq(
                DomainEvent(topic="t", key="k", payload={}), RuntimeError("x"), 1, "retries_exhausted"
            )
        assert any("dlq broker down" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_a_dlq_publish_failure_still_commits_the_offset(self, monkeypatch):
        """A dead DLQ must not stall the partition forever."""
        class Broken:
            async def publish(self, event: DomainEvent) -> None:
                raise OSError("dlq broker down")

        s = KafkaEventSubscriber(
            "kafka:9092", "g", max_retries=1, dlq_publisher=Broken(), retry_backoff_ms=1
        )
        consumer = FakeConsumer()
        s._consumer = consumer
        s._lazy_dlq_publisher = s.dlq_publisher

        async def bad(e: DomainEvent) -> None:
            raise RuntimeError("always")

        await s.subscribe("test.topic", bad)
        await s._process_message(
            FakeMessage(env_bytes(DomainEvent(topic="test.topic", key="k", payload={})))
        )
        assert consumer.commits == 1

    @pytest.mark.asyncio
    async def test_publish_dlq_handles_a_non_dlq_topic_name(self, monkeypatch):
        """The orig_topic derivation at :616-620 has a non-DLQ fallback."""
        s = sub()
        await s._publish_dlq(
            DomainEvent(topic="not-a-dlq-topic", key="k", payload={"error": "boom"})
        )
        assert s.dlq_publisher.sent[0].topic == "not-a-dlq-topic"


# ---------------------------------------------------------------------------
# stop (subscriber.py:629-651)
# ---------------------------------------------------------------------------


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_without_task_or_consumer_is_a_noop(self):
        s = sub()
        await s.stop()
        assert s._task is None and s._consumer is None

    @pytest.mark.asyncio
    async def test_stop_cancels_task_then_stops_consumer(self, monkeypatch):
        install_consumer(monkeypatch)
        s = sub()
        await s.subscribe("t", _noop)
        await s.start()
        consumer = FakeConsumer.instances[0]
        await s.stop()
        assert consumer.stopped == 1
        assert s._consumer is None
        assert s._task is None

    @pytest.mark.asyncio
    async def test_stop_closes_the_lazy_dlq_publisher(self, monkeypatch):
        closed: list[int] = []

        class Counting(InMemoryEventPublisher):
            async def close(self) -> None:
                closed.append(1)

        s = KafkaEventSubscriber("kafka:9092", "g", dlq_publisher=None)
        s._lazy_dlq_publisher = Counting()
        await s.stop()
        assert closed == [1]
        assert s._lazy_dlq_publisher is None

    @pytest.mark.asyncio
    async def test_stop_ignores_a_consumer_stop_failure(self, monkeypatch):
        class BadStop(FakeConsumer):
            async def stop(self) -> None:
                raise RuntimeError("stop timed out")

        created = install_consumer(monkeypatch, consumer_cls=BadStop)
        s = sub()
        await s.subscribe("t", _noop)
        await s.start()
        await s.stop()  # must not raise
        assert s._consumer is None
        assert created[0].stopped == 0

    @pytest.mark.asyncio
    async def test_stop_survives_a_task_that_raises_a_real_exception(self, monkeypatch):
        async def _explode() -> None:
            raise RuntimeError("poll loop exploded")

        s = sub()
        s._task = asyncio.create_task(_explode())
        await asyncio.sleep(0)
        await s.stop()  # must not raise
        assert s._task is None

    @pytest.mark.asyncio
    async def test_stop_does_not_commit_in_flight_work(self, monkeypatch):
        event = DomainEvent(topic="test.topic", key="k", payload={})
        install_consumer(
            monkeypatch, scripts=[[FakeMessage(env_bytes(event), topic="test.topic")]]
        )
        s = sub()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow(e: DomainEvent) -> None:
            entered.set()
            await release.wait()

        await s.subscribe("test.topic", slow)
        await s.start()
        await asyncio.wait_for(entered.wait(), 5)
        await s.stop()
        assert FakeConsumer.instances[0].commits == 0


# ---------------------------------------------------------------------------
# Correlation helper (subscriber.py:87-107)
# ---------------------------------------------------------------------------


class TestCorrelationHelper:
    @pytest.mark.asyncio
    async def test_correlation_id_is_injected_for_the_handler(self, monkeypatch):
        from wildframe_observability.logging import get_correlation_id

        observed: list[str] = []
        event = DomainEvent(topic="t", key="k", payload={}, correlation_id="corr-999")

        async def handler(e: DomainEvent) -> None:
            observed.append(get_correlation_id())

        await _run_with_event_correlation(event, handler)
        assert observed == ["corr-999"]
        # ...and reset afterwards so the next event is not mis-correlated.
        assert get_correlation_id() != "corr-999"

    @pytest.mark.asyncio
    async def test_correlation_id_is_cleared_even_when_the_handler_raises(self, monkeypatch):
        from wildframe_observability.logging import get_correlation_id

        event = DomainEvent(topic="t", key="k", payload={}, correlation_id="corr-1")

        async def handler(e: DomainEvent) -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await _run_with_event_correlation(event, handler)
        assert get_correlation_id() != "corr-1"

    @pytest.mark.asyncio
    async def test_blank_correlation_id_takes_the_plain_path(self, monkeypatch):
        from wildframe_observability.logging import get_correlation_id

        set_calls: list[str] = []
        monkeypatch.setattr(
            "wildframe_observability.logging.set_correlation_id", lambda v: set_calls.append(v)
        )
        called: list[int] = []

        async def handler(e: DomainEvent) -> None:
            called.append(1)

        await _run_with_event_correlation(
            DomainEvent(topic="t", key="k", payload={}, correlation_id="   "), handler
        )
        assert called == [1]
        assert set_calls == []  # nothing set for a blank id
        assert get_correlation_id() is not None

    @pytest.mark.asyncio
    async def test_handler_still_runs_when_observability_is_unavailable(self, monkeypatch):
        """The SDK is optional: an ImportError must not lose the event."""
        called: list[int] = []

        async def handler(e: DomainEvent) -> None:
            called.append(1)

        event = DomainEvent(topic="t", key="k", payload={}, correlation_id="c-1")
        with patch_dict_none("wildframe_observability.logging"):
            await _run_with_event_correlation(event, handler)
        assert called == [1]


def patch_dict_none(name: str):
    from unittest.mock import patch

    return patch.dict(sys.modules, {name: None})


# ---------------------------------------------------------------------------
# RedisDeduplicationStore (subscriber.py:159-183)
# ---------------------------------------------------------------------------


class FakeRedis:
    def __init__(self, existing: bool = False) -> None:
        self.existing = existing
        self.exists_keys: list[str] = []
        self.set_calls: list[tuple] = []

    async def exists(self, key: str) -> int:
        self.exists_keys.append(key)
        return 1 if self.existing else 0

    async def set(self, key: str, value: str, px: int) -> None:
        self.set_calls.append((key, value, px))


class TestRedisDedupStore:
    @pytest.mark.asyncio
    async def test_check_true_when_key_absent(self):
        redis = FakeRedis(existing=False)
        store = RedisDeduplicationStore(redis_client=redis)
        assert await store.check("t:abc", 60) is True
        assert redis.exists_keys == ["wf:dedup:t:abc"]

    @pytest.mark.asyncio
    async def test_check_false_when_key_present(self):
        store = RedisDeduplicationStore(redis_client=FakeRedis(existing=True))
        assert await store.check("t:abc", 60) is False

    @pytest.mark.asyncio
    async def test_mark_uses_set_with_px_ttl(self):
        redis = FakeRedis()
        store = RedisDeduplicationStore(redis_client=redis)
        await store.mark("t:abc", 90)
        assert redis.set_calls == [("wf:dedup:t:abc", "1", 90_000)]

    @pytest.mark.asyncio
    async def test_custom_key_prefix(self):
        redis = FakeRedis()
        store = RedisDeduplicationStore(redis_client=redis, key_prefix="custom")
        await store.mark("k", 1)
        assert redis.set_calls[0][0] == "custom:k"

    def test_builds_redis_from_url_when_no_client(self, monkeypatch):
        import redis.asyncio as redis_asyncio

        seen: dict[str, Any] = {}

        class FakeRedisClass:
            @staticmethod
            def from_url(url: str) -> Any:
                seen["url"] = url
                return "redis-from-url"

            def __init__(self, **kwargs: Any) -> None:
                seen["default"] = True

        monkeypatch.setattr(redis_asyncio, "Redis", FakeRedisClass)
        store = RedisDeduplicationStore(redis_url="redis://cache:6379/2")
        assert store._redis == "redis-from-url"
        assert seen == {"url": "redis://cache:6379/2"}

    def test_builds_default_redis_when_no_url_and_no_client(self, monkeypatch):
        import redis.asyncio as redis_asyncio

        seen: dict[str, Any] = {}

        class FakeRedisClass:
            def __init__(self) -> None:
                seen["default"] = True

        monkeypatch.setattr(redis_asyncio, "Redis", FakeRedisClass)
        RedisDeduplicationStore()
        assert seen == {"default": True}

    @pytest.mark.asyncio
    async def test_redis_store_works_end_to_end_in_a_subscriber(self, monkeypatch):
        redis = FakeRedis()
        s = KafkaEventSubscriber(
            "kafka:9092", "g", dedup_store=RedisDeduplicationStore(redis_client=redis)
        )
        consumer = FakeConsumer()
        s._consumer = consumer
        calls: list[int] = []

        async def handler(e: DomainEvent) -> None:
            calls.append(1)

        await s.subscribe("test.topic", handler)
        event = DomainEvent(topic="test.topic", key="k", payload={})
        msg = FakeMessage(env_bytes(event))
        await s._process_message(msg)
        assert calls == [1]
        assert redis.set_calls, "the event_id key must be marked after success"
        # Now the key is present -> the redelivery is skipped.
        redis.existing = True
        await s._process_message(msg)
        assert calls == [1]
        assert consumer.commits == 2


# ---------------------------------------------------------------------------
# Counter fallback (subscriber.py:654-691)
# ---------------------------------------------------------------------------


class TestCounterFallback:
    def test_counter_proxy_is_inert(self):
        proxy = _CounterProxy()
        assert proxy.labels(topic="t", reason="r") is proxy
        assert proxy.labels(anything="x").inc() is None
        assert proxy.inc() is None

    def test_real_counters_are_installed_when_prometheus_is_present(self):
        from prometheus_client import Counter

        assert isinstance(sub_mod._RETRIES_TOTAL, Counter)
        assert isinstance(sub_mod._DLQ_TOTAL, Counter)
        assert isinstance(sub_mod._DUPLICATES_TOTAL, Counter)
        assert isinstance(sub_mod._PROCESSED_TOTAL, Counter)

    @pytest.mark.asyncio
    async def test_counters_increment_during_dispatch_and_dlq(self):
        s = sub(max_retries=2, retry_backoff_ms=1)
        before_retries = _value(s, "wildframe_event_handler_retries_total", {"topic": "t.metrics"})
        before_dlq = _value(s, "wildframe_event_dlq_total", {"topic": "t.metrics", "reason": "dlq"})

        async def always_fail(e: DomainEvent) -> None:
            raise RuntimeError("nope")

        s._handlers = {"t.metrics": [always_fail]}
        await s._dispatch(DomainEvent(topic="t.metrics", key="k", payload={}))

        after_retries = _value(s, "wildframe_event_handler_retries_total", {"topic": "t.metrics"})
        after_dlq = _value(s, "wildframe_event_dlq_total", {"topic": "t.metrics", "reason": "dlq"})
        assert after_retries - before_retries == 1  # one retry, one DLQ
        assert after_dlq - before_dlq == 1

    @pytest.mark.asyncio
    async def test_processed_counter_increments_even_with_no_handlers(self):
        s = sub()
        s._handlers = {"t.nohandler": []}
        before = _value(s, "wildframe_event_processed_total", {"topic": "t.nohandler"})
        await s._dispatch(DomainEvent(topic="t.nohandler", key="k", payload={}))
        assert _value(s, "wildframe_event_processed_total", {"topic": "t.nohandler"}) == before + 1

    def test_duplicate_counter_increments(self):
        s = sub()
        before = _value(s, "wildframe_event_duplicates_total", {"topic": "t.dup"})
        s._note_duplicate(DomainEvent(topic="t.dup", key="k", payload={}))
        assert _value(s, "wildframe_event_duplicates_total", {"topic": "t.dup"}) == before + 1

    def test_module_degrades_to_noop_counters_without_prometheus(self):
        """Re-executes the module body with prometheus_client broken so the
        ``except`` fallback is exercised. A separate module object is used so
        the real module's counters and the global Prometheus registry are not
        disturbed (re-registering the same metric names would raise)."""
        import importlib.util
        from unittest.mock import patch

        spec = importlib.util.spec_from_file_location(
            "wildframe_events._subscriber_no_prometheus", sub_mod.__file__
        )
        assert spec and spec.loader
        reloaded = importlib.util.module_from_spec(spec)
        with patch("prometheus_client.Counter", side_effect=RuntimeError("no prometheus")):
            spec.loader.exec_module(reloaded)

        for name in (
            "_RETRIES_TOTAL",
            "_DLQ_TOTAL",
            "_DUPLICATES_TOTAL",
            "_PROCESSED_TOTAL",
        ):
            assert isinstance(getattr(reloaded, name), reloaded._CounterProxy), name


def _value(_sub: Any, metric: str, labels: dict[str, str]) -> float:
    """Read a counter sample from the default Prometheus registry."""
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(metric, labels) or 0.0


async def _noop(*_: Any) -> None:
    return None
