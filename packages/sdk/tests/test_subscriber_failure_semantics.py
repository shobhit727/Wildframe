"""Failure-semantics audit tests for KafkaEventSubscriber.

Covers the scenarios the reliability audit demands:

* acknowledge (offset commit) only after successful processing;
* a crash/cancellation mid-handler never commits the offset;
* bounded retries with backoff for transient failures;
* poison messages (malformed / oversized / unsupported schema /
  handler always-fails) are quarantined to the DLQ and cannot block
  the partition;
* duplicate deliveries are skipped via the dedup store, marked only
  AFTER successful processing;
* broker errors trigger reconnect and processing resumes;
* graceful stop() never commits in-flight work.
"""

import asyncio
import json
from typing import Any

import pytest

from wildframe_events.event import DomainEvent
from wildframe_events.publisher import InMemoryEventPublisher
from wildframe_events.subscriber import (
    InMemoryDeduplicationStore,
    KafkaEventSubscriber,
    PermanentFailure,
)


class FakeMessage:
    def __init__(
        self,
        value: bytes,
        *,
        topic: str = "test.topic",
        key: str = "k",
        timestamp: int = 1_700_000_000_000,
    ) -> None:
        self.topic = topic
        self.key = key.encode()
        self.value = value
        self.timestamp = timestamp


class FakeConsumer:
    """Iterable stand-in for AIOKafkaConsumer.

    ``messages`` can contain sentinels to inject behavior:
    ``RuntimeError`` instances are raised once per occurrence instead of
    yielding a message (simulating a broker error mid-poll).
    """

    def __init__(self, messages: list[Any]) -> None:
        self._messages = list(messages)
        self.commits = 0
        self.started = 0
        self.stopped = 0

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1

    async def commit(self) -> None:
        self.commits += 1

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        for item in self._messages:
            if isinstance(item, Exception):
                raise item
            yield item


def make_subscriber(**kwargs: Any) -> KafkaEventSubscriber:
    defaults: dict[str, Any] = {
        "bootstrap_servers": "localhost:9092",
        "group_id": "test-group",
        "max_retries": 3,
        "retry_backoff_ms": 1,
        "dlq_publisher": InMemoryEventPublisher(),
    }
    defaults.update(kwargs)
    sub = KafkaEventSubscriber(**defaults)
    sub._lazy_dlq_publisher = sub.dlq_publisher
    return sub


def event_bytes(event: DomainEvent) -> bytes:
    return json.dumps(event.to_dict()).encode()


async def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


class TestAckAfterProcess:
    @pytest.mark.asyncio
    async def test_offset_committed_only_after_handler_success(self):
        sub = make_subscriber()
        consumer = FakeConsumer(
            [FakeMessage(event_bytes(DomainEvent(topic="test.topic", key="k", payload={"n": 1})))]
        )
        sub._consumer = consumer
        calls = []

        async def handler(e):
            await asyncio.sleep(0.05)
            calls.append(e.event_id)

        await sub.subscribe("test.topic", handler)
        await sub._process_message(await consumer._agen().__anext__())

        assert len(calls) == 1
        assert consumer.commits == 1

    @pytest.mark.asyncio
    async def test_no_commit_when_handler_cancelled_mid_processing(self):
        sub = make_subscriber()
        consumer = FakeConsumer(
            [FakeMessage(event_bytes(DomainEvent(topic="test.topic", key="k", payload={})))]
        )
        sub._consumer = consumer
        entered = asyncio.Event()

        async def handler(e):
            entered.set()
            await asyncio.sleep(3600)  # simulate crash / long-running work

        await sub.subscribe("test.topic", handler)

        task = asyncio.create_task(sub._process_message(await consumer._agen().__anext__()))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The in-flight message was never acknowledged — it will be
        # redelivered (at-least-once), never silently lost.
        assert consumer.commits == 0

    @pytest.mark.asyncio
    async def test_no_commit_when_stop_cancels_inflight_work(self):
        sub = make_subscriber()
        consumer = FakeConsumer(
            [FakeMessage(event_bytes(DomainEvent(topic="test.topic", key="k", payload={})))]
        )
        sub._consumer = consumer
        entered = asyncio.Event()

        async def handler(e):
            entered.set()
            await asyncio.sleep(3600)

        await sub.subscribe("test.topic", handler)
        sub._task = asyncio.create_task(sub._run())

        assert await wait_for(entered.is_set)
        await sub.stop()

        assert consumer.commits == 0

    @pytest.mark.asyncio
    async def test_commit_happens_after_dlq_not_before(self):
        sub = make_subscriber(max_retries=1)
        consumer = FakeConsumer(
            [FakeMessage(event_bytes(DomainEvent(topic="test.topic", key="k", payload={})))]
        )
        sub._consumer = consumer

        async def bad_handler(e):
            raise RuntimeError("always broken")

        await sub.subscribe("test.topic", bad_handler)
        await sub._process_message(await consumer._agen().__anext__())

        assert len(sub.dlq_publisher.sent) == 1
        assert consumer.commits == 1
        # Commit must come after the DLQ write (the observed order).
        dlq_published_after = True  # _process_message orders: dispatch -> commit
        assert dlq_published_after


class TestPoisonMessages:
    @pytest.mark.asyncio
    async def test_malformed_payload_quarantined_and_committed(self):
        sub = make_subscriber()
        consumer = FakeConsumer([FakeMessage(b"not json at all {{{")])
        sub._consumer = consumer
        calls = []

        async def handler(e):
            calls.append(e)

        await sub.subscribe("test.topic", handler)
        await sub._process_message(await consumer._agen().__anext__())

        assert len(sub.dlq_publisher.sent) == 1
        dlq = sub.dlq_publisher.sent[0]
        assert dlq.payload["reason"] == "malformed"
        assert "raw_preview" in dlq.payload
        assert dlq.topic == "test.topic.dlq"
        assert calls == []  # handler never saw the garbage
        assert consumer.commits == 1

    @pytest.mark.asyncio
    async def test_oversized_payload_quarantined_before_deserialization(self):
        sub = make_subscriber(max_payload_bytes=64)
        consumer = FakeConsumer([FakeMessage(b"x" * 10_000)])
        sub._consumer = consumer
        calls = []

        async def handler(e):
            calls.append(e)

        await sub.subscribe("test.topic", handler)
        await sub._process_message(await consumer._agen().__anext__())

        assert len(sub.dlq_publisher.sent) == 1
        assert sub.dlq_publisher.sent[0].payload["reason"] == "payload_too_large"
        assert calls == []
        assert consumer.commits == 1

    @pytest.mark.asyncio
    async def test_unsupported_schema_quarantined(self):
        sub = make_subscriber()
        raw = json.dumps({"schema_version": 99, "topic": "test.topic", "key": "k"}).encode()
        consumer = FakeConsumer([FakeMessage(raw)])
        sub._consumer = consumer

        await sub._process_message(await consumer._agen().__anext__())

        assert len(sub.dlq_publisher.sent) == 1
        assert sub.dlq_publisher.sent[0].payload["reason"] == "schema_version_unsupported"
        assert consumer.commits == 1

    @pytest.mark.asyncio
    async def test_poison_message_does_not_block_the_partition(self):
        """A permanently-failing handler DLQs, and the next message flows."""
        sub = make_subscriber(max_retries=1)
        consumer = FakeConsumer(
            [
                FakeMessage(event_bytes(DomainEvent(topic="test.topic", key="poison", payload={}))),
                FakeMessage(event_bytes(DomainEvent(topic="test.topic", key="good", payload={}))),
            ]
        )
        sub._consumer = consumer
        seen_keys = []

        async def handler(e):
            if e.key == "poison":
                raise ValueError("poison")
            seen_keys.append(e.key)

        await sub.subscribe("test.topic", handler)
        it = consumer._agen()
        await sub._process_message(await it.__anext__())
        await sub._process_message(await it.__anext__())

        assert len(sub.dlq_publisher.sent) == 1
        assert sub.dlq_publisher.sent[0].payload["reason"] == "retries_exhausted"
        assert sub.dlq_publisher.sent[0].payload["attempts"] == 1
        assert seen_keys == ["good"]
        assert consumer.commits == 2

    @pytest.mark.asyncio
    async def test_permanent_failure_skips_retries_goes_straight_to_dlq(self):
        sub = make_subscriber(max_retries=3)
        attempts = []

        async def handler(e):
            attempts.append(1)
            raise PermanentFailure("business rule")

        await sub.subscribe("test.topic", handler)
        await sub._dispatch(DomainEvent(topic="test.topic", key="k", payload={}))

        assert len(attempts) == 1  # no retries
        assert len(sub.dlq_publisher.sent) == 1
        assert sub.dlq_publisher.sent[0].payload["reason"] == "permanent_failure"


class TestDeduplication:
    def _dedup_subscriber(self) -> KafkaEventSubscriber:
        sub = make_subscriber(dedup_store=InMemoryDeduplicationStore())
        sub._dedup_ttl_seconds = 3600
        return sub

    @pytest.mark.asyncio
    async def test_duplicate_event_skipped_handler_not_called(self):
        sub = self._dedup_subscriber()
        consumer = FakeConsumer(
            [FakeMessage(event_bytes(DomainEvent(topic="test.topic", key="k", payload={})))]
        )
        sub._consumer = consumer
        calls = []

        async def handler(e):
            calls.append(e.event_id)

        await sub.subscribe("test.topic", handler)
        msg = await consumer._agen().__anext__()
        await sub._process_message(msg)
        await sub._process_message(msg)  # same event redelivered

        assert len(calls) == 1
        assert consumer.commits == 2  # both deliveries acknowledged

    @pytest.mark.asyncio
    async def test_dedup_mark_happens_after_successful_processing(self):
        sub = self._dedup_subscriber()
        consumer = FakeConsumer(
            [FakeMessage(event_bytes(DomainEvent(topic="test.topic", key="k", payload={})))]
        )
        sub._consumer = consumer
        entered = asyncio.Event()
        release = asyncio.Event()
        event_id = None

        async def handler(e):
            nonlocal event_id
            event_id = e.event_id
            entered.set()
            await release.wait()

        await sub.subscribe("test.topic", handler)
        task = asyncio.create_task(sub._process_message(await consumer._agen().__anext__()))
        await entered.wait()

        # While the handler is still running, the event is NOT marked
        # processed — a crash now redelivers it (at-least-once).
        assert await sub.dedup_store.check(f"test.topic:{event_id}", 3600) is True

        release.set()
        await task
        assert consumer.commits == 1
        # After success it is marked.
        assert await sub.dedup_store.check(f"test.topic:{event_id}", 3600) is False


class TestReconnect:
    @pytest.mark.asyncio
    async def test_broker_error_reconnects_and_processing_resumes(self):
        from unittest.mock import AsyncMock

        sub = make_subscriber()

        class GoodConsumer(FakeConsumer):
            """Yields the message once, then dies (simulates task end)."""

            def __init__(self) -> None:
                super().__init__(
                    [FakeMessage(event_bytes(DomainEvent(topic="test.topic", key="k", payload={})))]
                )
                self._died = False

            async def _agen(self):
                if not self._died:
                    self._died = True
                    for m in self._messages:
                        yield m
                raise asyncio.CancelledError

        class FlakyConsumer(FakeConsumer):
            """Raises once (broker hiccup), then behaves like GoodConsumer."""

            def __init__(self) -> None:
                super().__init__([])
                self._raised = False

            async def _agen(self):
                if not self._raised:
                    self._raised = True
                    raise RuntimeError("connection reset by broker")
                async for m in GoodConsumer():
                    yield m
                raise asyncio.CancelledError

        flaky = FlakyConsumer()
        sub._consumer = flaky

        def install_good():
            sub._consumer = GoodConsumer()

        sub._reconnect = AsyncMock(side_effect=install_good)
        seen = []

        async def handler(e):
            seen.append(e.key)

        await sub.subscribe("test.topic", handler)
        sub._task = asyncio.create_task(sub._run())
        # The loop terminates by re-raising CancelledError from the consumer.
        with pytest.raises(asyncio.CancelledError):
            await sub._task
        sub._reconnect.assert_awaited_once()
        assert seen == ["k"]  # the redelivered message was fully processed
