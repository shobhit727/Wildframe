"""Event publisher port + adapters.

The EventPublisher port is the *only* way services emit events. Two
adapters are provided:

  - InMemoryEventPublisher: records events in a list. Used in dev/test
    so no Kafka is needed. Also useful for assertions in tests.
  - KafkaEventPublisher: real Kafka producer backed by aiokafka.

Both use the same DomainEvent envelope and the same topic names from
``wildframe_events.topics``.

Important: the Kafka adapter is only imported when actually used (lazy
import) so services that don't have aiokafka installed (e.g. in a
minimal dev environment) still boot fine.

Delivery semantics
------------------
- Every event is validated before any side effect: the payload must be
  JSON-safe (no NaN/Infinity, no bytes, no non-string keys), must not
  carry secret-shaped keys, and must fit within ``max_payload_bytes``.
  producer-side retries cannot create duplicate broker records.
- Retries are bounded (``max_retries`` with ``retry_backoff_ms``
  backoff) — no hot-looping on broker failure; after they are
  exhausted ``publish`` raises, so callers that require delivery see
  the failure instead of silently losing the event.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import List

from wildframe_events.event import DomainEvent, validate_payload

logger = logging.getLogger(__name__)

#: Default upper bound for a serialized event (1 MiB). Exceeding this
#: raises :class:`EventTooLargeError` before anything is sent, so an
#: oversized event can never exhaust broker or consumer memory.
DEFAULT_MAX_PAYLOAD_BYTES = 1_000_000


class EventTooLargeError(ValueError):
    """Raised when a serialized event exceeds ``max_payload_bytes``."""


class EventPublisher(ABC):
    """Port (interface) for publishing domain events.

    Any transport (in-memory, Kafka, pub/sub, SNS) implements ``publish``
    and optionally ``publish_many``. The service depends only on this
    abstraction.
    """

    #: Serialized-size cap enforced by every adapter before sending.
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES

    @abstractmethod
    async def publish(self, event: DomainEvent) -> None:
        """Publish a single event."""
        raise NotImplementedError

    async def publish_many(self, events: List[DomainEvent]) -> None:
        """Publish multiple events (default: sequential)."""
        for event in events:
            await self.publish(event)

    async def close(self) -> None:
        """Clean up resources (override in adapters that hold connections)."""

    def validate_event(self, event: DomainEvent) -> None:
        """Validate an event before publishing.

        Raises :class:`~wildframe_events.event.PayloadValidationError`
        for non-JSON-safe payloads or secret-shaped keys, and
        :class:`EventTooLargeError` when the serialized event exceeds
        ``max_payload_bytes``. Deterministic — identical input always
        produces the same verdict.
        """
        validate_payload(event.payload)
        size = len(event.to_json().encode("utf-8"))
        if size > self.max_payload_bytes:
            raise EventTooLargeError(
                f"event {event.event_id} serialized size {size} bytes exceeds "
                f"max_payload_bytes={self.max_payload_bytes} (topic={event.topic})"
            )


class InMemoryEventPublisher(EventPublisher):
    """No-op-ish publisher that records events in memory + structured log.

    This is the *default* adapter for dev/test: the service is fully
    functional and testable with no Kafka. The recorded events are also
    available for test assertions. The same validation and size caps as
    the Kafka adapter apply, so dev/test catches oversized or
    non-JSON-safe events early.
    """

    def __init__(self, max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES) -> None:
        self.sent: List[DomainEvent] = []
        self.max_payload_bytes = max_payload_bytes

    async def publish(self, event: DomainEvent) -> None:
        self.validate_event(event)
        self.sent.append(event)
        logger.info(
            "event published (in-memory): topic=%s key=%s event_id=%s payload=%s",
            event.topic,
            event.key,
            event.event_id,
            json.dumps(event.payload, default=str),
        )


class KafkaEventPublisher(EventPublisher):
    """Kafka-backed publisher (aiokafka).

    This is a real adapter behind the same EventPublisher port. It is only
    instantiated when the service is configured for Kafka production use;
    until then, aiokafka is never imported at module load time.

    Features:
      - Idempotent producer (acks=all) — retries cannot duplicate
        broker records; the event key is used for partition ordering.
      - Bounded retries with backoff (no hot-loop when the broker is
        down); ``publish`` raises once retries are exhausted so the
        caller can decide how to handle a failed delivery.
      - ``max_payload_bytes`` enforcement before send (and
        ``max_request_size`` on the producer) — oversized events are
        rejected with :class:`EventTooLargeError`.
      - Structured logging of every published event.
      - Graceful startup/shutdown of the Kafka producer.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        client_id: str = "wildframe",
        acks: str = "all",
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
        max_retries: int = 5,
        retry_backoff_ms: int = 500,
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.client_id = client_id
        self.acks = acks
        self.max_payload_bytes = max_payload_bytes
        self.max_retries = max_retries
        self.retry_backoff_ms = retry_backoff_ms
        self._producer = None

    async def _get_producer(self):
        """Lazy-start the Kafka producer (avoids import when not used)."""
        if self._producer is None:
            from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]

            self._producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                client_id=self.client_id,
                acks=self.acks,
                # Idempotent producer: producer-side retries cannot create
                # duplicate broker records (Kafka dedups by producer-id +
                # sequence number). Requires acks=all; honoured only then.
                enable_idempotence=self.acks == "all",
                retries=self.max_retries,
                retry_backoff_ms=self.retry_backoff_ms,
                # Broker-side cap: the producer refuses records larger
                # than this instead of failing later at the broker.
                max_request_size=self.max_payload_bytes + 4096,
                value_serializer=lambda v: v.to_json().encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
            )
            await self._producer.start()  # type: ignore[attr-defined]
        return self._producer

    async def publish(self, event: DomainEvent) -> None:
        """Publish a domain event to Kafka.

        Validates the event first; raises
        :class:`~wildframe_events.event.PayloadValidationError`,
        :class:`EventTooLargeError`, or the aiokafka error after bounded
        retries — delivery failures are never silently swallowed.
        """
        self.validate_event(event)
        producer = await self._get_producer()
        await producer.send_and_wait(
            topic=event.topic,
            key=event.key,
            value=event,
        )
        logger.info(
            "event published (kafka): topic=%s key=%s event_id=%s",
            event.topic,
            event.key,
            event.event_id,
        )

    async def close(self) -> None:
        """Stop the Kafka producer."""
        if self._producer is not None:
            await self._producer.stop()  # type: ignore[unreachable]
            self._producer = None

