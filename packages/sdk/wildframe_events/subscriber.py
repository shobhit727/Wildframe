"""Event subscriber port + adapters.

The EventSubscriber port is the *only* way services consume events.
Two adapters are provided:

  - InMemoryEventSubscriber: calls registered handlers directly. Used
    in dev/test so no Kafka is needed.
  - KafkaEventSubscriber: real Kafka consumer backed by aiokafka.

Both use the same DomainEvent envelope and the same topic names from
``wildframe_events.topics``.

Failure semantics (KafkaEventSubscriber)
----------------------------------------
- **Acknowledge only after success.** Offsets are committed only after
  every handler for the message completed (or the message was
  quarantined). A crash or cancellation mid-processing redelivers the
  message (at-least-once).
- **Bounded retries with jittered exponential backoff.** Transient
  handler failures are retried up to ``max_retries`` times with
  exponential backoff (``retry_backoff_ms`` base, 60 s cap) plus
  jitter. Failures that are deterministic and permanent can raise
  :class:`PermanentFailure` from a handler to skip retries and go
  straight to the DLQ.
- **Dead-letter queue.** After retries are exhausted (or for
  malformed/oversized/schema-incompatible messages) the event is
  published to ``<original_topic>.dlq`` with error context: original
  envelope, error type/message, attempts, reason, consumer group, and
  DLQ time. A DLQ publish failure never blocks the partition: it is
  logged at CRITICAL and the offset still commits (bounded-retry +
  quarantine is the poison-message contract).
- **Poison-message isolation.** One bad message cannot stall the
  partition: oversized, malformed, and unsupported-schema events are
  quarantined on first sight, without retries.
- **Durable deduplication (optional).** Pass a
  :class:`DeduplicationStore` (e.g. :class:`RedisDeduplicationStore`)
  to skip duplicate deliveries after consumer restarts, keyed by
  ``event_id`` (and the payload ``idempotency_key`` if present) with a
  TTL longer than the retry window. In-memory dedup is for dev/tests —
  it does not survive restarts.
- **Server-controlled timestamps.** ``event.server_time`` is filled
  from the broker message timestamp, never from the producer-controlled
  ``occurred_at``.
- **Graceful shutdown.** ``start()`` runs the poll loop in a background
  task; ``stop()`` cancels it, never committing a message that was not
  fully processed, then closes the consumer and any lazily created DLQ
  publisher.
- **Metrics.** Retry/DLQ/dedup/processed counters are exported to
  Prometheus (``wildframe_event_*``) when ``prometheus_client`` is
  installed; otherwise the same events are visible in structured logs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from wildframe_events.event import (
    DomainEvent,
    PayloadValidationError,
    SchemaVersionError,
)
from wildframe_events.publisher import (
    DEFAULT_MAX_PAYLOAD_BYTES,
    EventPublisher,
    EventTooLargeError,
    KafkaEventPublisher,
)
from wildframe_events.topics import Topic

logger = logging.getLogger(__name__)

# Type alias: a handler is an async callable that takes a DomainEvent.
EventHandler = Callable[[DomainEvent], Any]

#: Cap on the exponential retry backoff (ms) — bounded even for large
#: ``max_retries`` values.
MAX_RETRY_BACKOFF_MS = 60_000


class PermanentFailure(Exception):
    """Raise from an event handler to quarantine the event immediately.

    Use for deterministic, non-retryable failures (invalid business
    state, validation error at the domain level). The event goes
    straight to the DLQ without retries.
    """


class DeduplicationStore(ABC):
    """Protocol for durable duplicate-event detection.

    The consumer checks before dispatching and marks AFTER successful
    processing, so a crash mid-handler redelivers the event
    (at-least-once) instead of silently dropping it.
    """

    @abstractmethod
    async def check(self, key: str, ttl_seconds: float) -> bool:
        """Return True if ``key`` is NOT known to be processed already."""
        raise NotImplementedError

    @abstractmethod
    async def mark(self, key: str, ttl_seconds: float) -> None:
        """Record ``key`` as processed (atomic, expires after TTL)."""
        raise NotImplementedError


class InMemoryDeduplicationStore(DeduplicationStore):
    """Dev/test dedup store. NOT durable — cleared on process restart.

    Use :class:`RedisDeduplicationStore` in production so dedup state
    survives consumer restarts and rebalances.
    """

    def __init__(self) -> None:
        self._seen: Dict[str, datetime] = {}

    async def check(self, key: str, ttl_seconds: float) -> bool:
        expires_at = self._seen.get(key)
        return expires_at is None or expires_at <= datetime.now(timezone.utc)

    async def mark(self, key: str, ttl_seconds: float) -> None:
        self._seen[key] = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)


class RedisDeduplicationStore(DeduplicationStore):
    """Redis-backed dedup store — survives consumer restarts.

    ``check`` uses EXISTS, ``mark`` uses SET with PX TTL (must exceed the
    maximum retry/DLQ window). Keys live under ``key_prefix``.
    """

    def __init__(
        self,
        redis_client: Any = None,
        redis_url: Optional[str] = None,
        key_prefix: str = "wf:dedup",
    ) -> None:
        if redis_client is None:
            from redis.asyncio import Redis

            redis_client = Redis.from_url(redis_url) if redis_url else Redis()
        self._redis = redis_client
        self._key_prefix = key_prefix

    async def check(self, key: str, ttl_seconds: float) -> bool:
        return not bool(await self._redis.exists(f"{self._key_prefix}:{key}"))

    async def mark(self, key: str, ttl_seconds: float) -> None:
        await self._redis.set(
            f"{self._key_prefix}:{key}", "1", px=int(ttl_seconds * 1000)
        )


class EventSubscriber(ABC):
    """Port (interface) for consuming domain events."""

    @abstractmethod
    async def subscribe(self, topic: str, handler: EventHandler) -> None:
        """Register a handler for a topic."""
        raise NotImplementedError

    @abstractmethod
    async def start(self) -> None:
        """Start consuming events."""
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        """Stop consuming events."""
        raise NotImplementedError


class InMemoryEventSubscriber(EventSubscriber):
    """In-process event subscriber for dev/test.

    When an event is published via InMemoryEventPublisher, this
    subscriber's handlers are called directly (no serialization).
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, List[EventHandler]] = {}

    async def subscribe(self, topic: str, handler: EventHandler) -> None:
        self._handlers.setdefault(topic, []).append(handler)

    async def start(self) -> None:
        pass  # No background consumer needed.

    async def stop(self) -> None:
        pass

    async def deliver(self, event: DomainEvent) -> None:
        """Deliver an event to all registered handlers for its topic.

        Called by the test harness or a shared InMemory event bus.
        """
        handlers = self._handlers.get(event.topic, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception(
                    "handler failed for topic=%s event_id=%s",
                    event.topic,
                    event.event_id,
                )


class KafkaEventSubscriber(EventSubscriber):
    """Kafka-backed consumer (aiokafka).

    Subscribes to one or more topics and dispatches incoming events to
    the registered handlers. See the module docstring for the full
    failure-semantics contract: ack-after-process, bounded jittered
    backoff, DLQ with error context, poison-message isolation, durable
    dedup, server timestamps, and graceful shutdown.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str,
        client_id: str = "wildframe-consumer",
        max_retries: int = 3,
        retry_backoff_ms: int = 1000,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
        dedup_store: Optional[DeduplicationStore] = None,
        dedup_ttl_seconds: float = 86_400.0,
        dlq_publisher: Optional[EventPublisher] = None,
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.client_id = client_id
        self.max_retries = max_retries
        self.retry_backoff_ms = retry_backoff_ms
        self.max_payload_bytes = max_payload_bytes
        self.dedup_store = dedup_store
        self.dedup_ttl_seconds = dedup_ttl_seconds
        self.dlq_publisher = dlq_publisher
        self._handlers: Dict[str, List[EventHandler]] = {}
        self._consumer = None
        self._task: Optional[asyncio.Task] = None
        self._lazy_dlq_publisher: Optional[EventPublisher] = None
        self._reconnect_attempt = 0

    async def subscribe(self, topic: str, handler: EventHandler) -> None:
        self._handlers.setdefault(topic, []).append(handler)

    async def start(self) -> None:
        """Start the Kafka consumer in a background task.

        Returns once the consumer is connected; the poll loop runs as an
        asyncio task so the caller can shut down gracefully with
        ``stop()``. Commit happens per message, only after all handlers
        completed — in-flight work is never acknowledged.
        """
        from aiokafka import AIOKafkaConsumer  # type: ignore[import-untyped]

        if self._consumer is not None:  # type: ignore[unreachable]
            return  # Already started.  # type: ignore[unreachable]
        topics = list(self._handlers.keys())
        if not topics:
            raise ValueError("subscribe() at least one topic before start()")
        self._consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            client_id=self.client_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            max_partition_fetch_bytes=self.max_payload_bytes + 8192,
        )
        await self._consumer.start()  # type: ignore[attr-defined]
        self._reconnect_attempt = 0
        self._task = asyncio.create_task(
            self._run(), name=f"wildframe-subscriber-{self.group_id}"
        )

    async def _run(self) -> None:
        """Poll loop. Reconnects with bounded backoff on consumer errors."""
        while True:
            try:
                async for message in self._consumer:  # type: ignore[attr-defined]
                    await self._process_message(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - loop must survive broker errors
                delay_ms = min(
                    MAX_RETRY_BACKOFF_MS,
                    self.retry_backoff_ms * (2 ** min(self._reconnect_attempt, 6)),
                )
                self._reconnect_attempt += 1
                delay_s = delay_ms / 1000.0 * random.uniform(0.5, 1.0)
                logger.exception(
                    "consumer loop error (%s) — reconnecting in %.1fs",
                    exc,
                    delay_s,
                )
                await asyncio.sleep(delay_s)
                await self._reconnect()

    async def _reconnect(self) -> None:
        from aiokafka import AIOKafkaConsumer

        old = self._consumer
        self._consumer = None
        if old is not None:
            try:  # type: ignore[unreachable]
                await old.stop()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
        self._consumer = AIOKafkaConsumer(
            *list(self._handlers.keys()),
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            client_id=self.client_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            max_partition_fetch_bytes=self.max_payload_bytes + 8192,
        )
        await self._consumer.start()  # type: ignore[attr-defined]

    def _dedup_keys(self, event: DomainEvent) -> List[str]:
        """Dedup keys for an event: globally-unique event_id, plus the
        business idempotency key when the producer attaches one."""
        keys = [f"{event.topic}:{event.event_id}"]
        idem_key = event.payload.get("idempotency_key")
        if isinstance(idem_key, str) and idem_key:
            keys.append(f"{event.topic}:idem:{idem_key}")
        return keys

    async def _process_message(self, message: Any) -> None:
        """Handle one raw broker message: validate, dedup, dispatch, commit.

        Commit happens only after full processing (or quarantine), so a
        crash or cancellation redelivers the message.
        """
        topic = getattr(message, "topic", None) or ""
        key_raw = getattr(message, "key", None)
        raw = getattr(message, "value", None)
        if raw is None:
            raw = b""
        if not isinstance(raw, (bytes, bytearray)):
            raw = str(raw).encode("utf-8")
        key = key_raw.decode("utf-8") if isinstance(key_raw, bytes) else (key_raw or "")

        # Size bound BEFORE deserialization (no JSON parse of giant blobs).
        if len(raw) > self.max_payload_bytes:
            await self._quarantine_raw(
                topic,
                key,
                raw,
                "payload_too_large",
                f"payload {len(raw)} bytes exceeds max_payload_bytes={self.max_payload_bytes}",
            )
            await self._commit(message)
            return

        try:
            envelope = json.loads(raw.decode("utf-8"))
            event = DomainEvent.from_dict(envelope)
        except SchemaVersionError as exc:
            await self._quarantine_raw(
                topic, key, raw, "schema_version_unsupported", str(exc)
            )
            await self._commit(message)
            return
        except (PayloadValidationError, ValueError, TypeError) as exc:
            await self._quarantine_raw(
                topic, key, raw, "malformed", f"{type(exc).__name__}: {exc}"
            )
            await self._commit(message)
            return

        # Server-controlled ordering timestamp (broker clock), never the
        # producer-supplied occurred_at.
        ts_ms = getattr(message, "timestamp", None)
        if ts_ms and not event.server_time:
            event.server_time = datetime.fromtimestamp(
                ts_ms / 1000.0, tz=timezone.utc
            ).isoformat()

        if self.dedup_store is not None:
            marks: List[str] = []
            for dedup_key in self._dedup_keys(event):
                if not await self.dedup_store.check(
                    dedup_key, self.dedup_ttl_seconds
                ):
                    self._note_duplicate(event)
                    await self._commit(message)
                    return
                marks.append(dedup_key)
            await self._dispatch(event)
            # Mark only AFTER successful processing: a crash mid-handler
            # redelivers the event instead of losing it.
            for dedup_key in marks:
                await self.dedup_store.mark(dedup_key, self.dedup_ttl_seconds)
        else:
            await self._dispatch(event)
        await self._commit(message)

    async def _commit(self, message: Any) -> None:
        if self._consumer is not None:
            await self._consumer.commit()  # type: ignore[unreachable]

    async def _dispatch(self, event: DomainEvent) -> None:
        """Dispatch an event to ALL registered handlers with retry + DLQ.

        Each handler runs independently: a failure in one handler never
        skips the others, and each handler gets its own bounded retry
        budget before the event is quarantined.
        """
        handlers = self._handlers.get(event.topic, [])
        for handler in handlers:
            attempt = 0
            while True:
                attempt += 1
                try:
                    await handler(event)
                    break
                except PermanentFailure as exc:
                    logger.error(
                        "handler permanent failure topic=%s event_id=%s: %s",
                        event.topic,
                        event.event_id,
                        exc,
                    )
                    await self._send_to_dlq(event, exc, attempt, "permanent_failure")
                    break
                except Exception as exc:  # noqa: BLE001 - handler errors are quarantined, never fatal
                    if attempt >= self.max_retries:
                        await self._send_to_dlq(event, exc, attempt, "retries_exhausted")
                        break
                    delay_ms = min(
                        MAX_RETRY_BACKOFF_MS,
                        self.retry_backoff_ms * (2 ** (attempt - 1)),
                    )
                    delay_s = delay_ms / 1000.0 * random.uniform(0.5, 1.0)
                    logger.warning(
                        "handler failed (attempt %d/%d) topic=%s event_id=%s: %s "
                        "— retrying in %.0fms",
                        attempt,
                        self.max_retries,
                        event.topic,
                        event.event_id,
                        exc,
                        delay_s * 1000,
                    )
                    _RETRIES_TOTAL.labels(topic=event.topic).inc()
                    await asyncio.sleep(delay_s)
        _PROCESSED_TOTAL.labels(topic=event.topic).inc()

    def _note_duplicate(self, event: DomainEvent) -> None:
        logger.info(
            "duplicate event skipped (dedup): topic=%s key=%s event_id=%s",
            event.topic,
            event.key,
            event.event_id,
        )
        _DUPLICATES_TOTAL.labels(topic=event.topic).inc()

    # -- Dead-letter queue -------------------------------------------------

    def _get_dlq_publisher(self) -> EventPublisher:
        if self.dlq_publisher is not None:
            return self.dlq_publisher
        if self._lazy_dlq_publisher is None:
            self._lazy_dlq_publisher = KafkaEventPublisher(
                bootstrap_servers=self.bootstrap_servers,
                client_id=f"{self.client_id}-dlq",
            )
        return self._lazy_dlq_publisher

    async def _quarantine_raw(
        self, topic: str, key: str, raw: bytes, reason: str, detail: str
    ) -> None:
        """DLQ a message that could not be deserialized at all.

        The raw payload is never embedded (it may be huge or hostile);
        only a bounded preview is kept for malformed messages.
        """
        payload: Dict[str, Any] = {
            "original_topic": topic,
            "original_key": key,
            "reason": reason,
            "error": detail,
            "payload_size_bytes": len(raw),
            "consumer_group": self.group_id,
            "dlq_time": datetime.now(timezone.utc).isoformat(),
        }
        if reason == "malformed":
            payload["raw_preview"] = raw[:512].decode("utf-8", errors="replace")
        dlq_event = DomainEvent(
            topic=topic + Topic.DLQ_SUFFIX,
            key=key,
            payload=payload,
            producer=self.client_id,
        )
        await self._publish_dlq(dlq_event)

    async def _send_to_dlq(
        self, event: DomainEvent, error: Exception, attempts: int, reason: str
    ) -> None:
        """DLQ an event whose handlers failed, preserving error context."""
        dlq_event = DomainEvent(
            topic=event.topic + Topic.DLQ_SUFFIX,
            key=event.key,
            payload={
                "original_event": event.to_dict(),
                "error_type": type(error).__name__,
                "error": str(error),
                "reason": reason,
                "attempts": attempts,
                "consumer_group": self.group_id,
                "dlq_time": datetime.now(timezone.utc).isoformat(),
            },
            producer=self.client_id,
        )
        await self._publish_dlq(dlq_event)

    async def _publish_dlq(self, dlq_event: DomainEvent) -> None:
        publisher = self._get_dlq_publisher()
        try:
            await publisher.publish(dlq_event)
        except EventTooLargeError:
            # The original payload may itself have been near the size
            # cap; retry with metadata only so DLQ never fails on size.
            try:
                dlq_event.payload.pop("original_event", None)
                await publisher.publish(dlq_event)
            except Exception as exc:  # noqa: BLE001
                logger.critical(
                    "failed to publish DLQ event topic=%s key=%s: %s",
                    dlq_event.topic,
                    dlq_event.key,
                    exc,
                )
                return
        except Exception as exc:  # noqa: BLE001
            # A failed DLQ write must never block the partition forever —
            # raise the alarm and let the offset commit (the message is
            # redeliverable only if we do not commit; quarantine wins).
            logger.critical(
                "failed to publish DLQ event topic=%s key=%s: %s",
                dlq_event.topic,
                dlq_event.key,
                exc,
            )
            return
        orig_topic = (
            dlq_event.topic[: -len(Topic.DLQ_SUFFIX)]
            if dlq_event.topic.endswith(Topic.DLQ_SUFFIX)
            else dlq_event.topic
        )
        _DLQ_TOTAL.labels(topic=orig_topic, reason="dlq").inc()
        logger.error(
            "event sent to DLQ: topic=%s key=%s error=%s",
            dlq_event.topic,
            dlq_event.key,
            dlq_event.payload.get("error"),
        )

    async def stop(self) -> None:
        """Stop the Kafka consumer and the DLQ publisher.

        Cancel first, then close: a cancellation mid-processing means
        the in-flight message was NOT committed, so it is redelivered
        (at-least-once) — no acknowledged-but-lost work.
        """
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._consumer is not None:
            try:  # type: ignore[unreachable]
                await self._consumer.stop()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
            self._consumer = None
        if self._lazy_dlq_publisher is not None:
            await self._lazy_dlq_publisher.close()
            self._lazy_dlq_publisher = None


# ---------------------------------------------------------------------------
# Metrics: retry / DLQ / dedup / processed counters.
# prometheus_client is optional — without it, the same events are visible
# in structured logs (the counters become no-ops).
# ---------------------------------------------------------------------------


class _CounterProxy:
    """No-op counter when prometheus_client is unavailable."""

    def inc(self, **labels) -> None:  # noqa: ARG002
        pass

    def labels(self, **labels) -> "_CounterProxy":  # noqa: ARG002
        return self


try:
    from prometheus_client import Counter as _PromCounter

    _RETRIES_TOTAL = _PromCounter(
        "wildframe_event_handler_retries_total",
        "Event handler retry attempts",
        ["topic"],
    )
    _DLQ_TOTAL = _PromCounter(
        "wildframe_event_dlq_total",
        "Events quarantined to the dead-letter queue",
        ["topic", "reason"],
    )
    _DUPLICATES_TOTAL = _PromCounter(
        "wildframe_event_duplicates_total",
        "Duplicate events skipped by dedup",
        ["topic"],
    )
    _PROCESSED_TOTAL = _PromCounter(
        "wildframe_event_processed_total",
        "Events fully processed",
        ["topic"],
    )
except Exception:  # prometheus_client optional — degrade to no-op counters
    _RETRIES_TOTAL = _CounterProxy()  # type: ignore[assignment]
    _DLQ_TOTAL = _CounterProxy()  # type: ignore[assignment]
    _DUPLICATES_TOTAL = _CounterProxy()  # type: ignore[assignment]
    _PROCESSED_TOTAL = _CounterProxy()  # type: ignore[assignment]