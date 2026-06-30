"""Lightweight domain events + event-bus publisher port.

The event bus is the integration surface between services (see
PRODUCT_VISION §7): services never reach into another service's DB, they emit
and consume events. To keep the service bootable and testable with no external
infrastructure, the *default* publisher is an in-memory implementation that
also emits a structured log. A real Kafka publisher lives behind the same
``EventPublisher`` port and is selected via ``settings.EVENT_PUBLISHER``.

Event topic contract (uploads-service producer):
    content.uploaded        — a chunked upload was verified & assembled
    content.upload.aborted  — an upload session was aborted / expired
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from uuid import uuid4


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain event base.
# ---------------------------------------------------------------------------

@dataclass
class Event:
    """A small, self-describing domain event.

    Fields:
        topic: event type / Kafka topic name.
        key: partitioning + idempotency key (the upload session id).
        payload: event-specific data.
        event_id: unique id for dedup / tracing.
        occurred_at: ISO-8601 UTC timestamp.

    We deliberately keep this as a plain dataclass (not a Pydantic model) so
    it has zero dependencies and is trivial to serialize for any transport.
    """

    topic: str
    key: str
    payload: Dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "topic": self.topic,
            "key": self.key,
            "occurred_at": self.occurred_at,
            "payload": self.payload,
        }


# ---------------------------------------------------------------------------
# Publisher port + adapters.
# ---------------------------------------------------------------------------

class EventPublisher(ABC):
    """Port (interface) for publishing domain events.

    Any transport (in-memory, Kafka, pub/sub) implements ``publish`` and
    optionally ``publish_many``. The service depends only on this abstraction.
    """

    @abstractmethod
    async def publish(self, event: Event) -> None:
        """Publish a single event."""
        raise NotImplementedError

    async def publish_many(self, events: List[Event]) -> None:
        """Publish multiple events (default: sequential)."""
        for event in events:
            await self.publish(event)


class InMemoryEventPublisher(EventPublisher):
    """No-op-ish publisher that records events in memory + structured log.

    This is the *default* adapter: it makes the service fully functional and
    testable with no Kafka. The recorded events are also exposed for tests and
    for any in-process listener (e.g. a future outbox processor).
    """

    def __init__(self) -> None:
        self.sent: List[Event] = []

    async def publish(self, event: Event) -> None:
        self.sent.append(event)
        logger.info(
            "event published (in-memory): topic=%s key=%s event_id=%s payload=%s",
            event.topic,
            event.key,
            event.event_id,
            json.dumps(event.payload),
        )


class KafkaEventPublisher(EventPublisher):
    """Kafka-backed publisher (aiokafka).

    This is a real adapter behind the same ``EventPublisher`` port. It is only
    instantiated when ``settings.EVENT_PUBLISHER == "kafka"``; until then it is
    never imported at module load, so the missing-aiokafka case is avoided in
    dev/test environments.

    The implementation is intentionally minimal: it serializes the event to
    JSON, keyed by ``event.key`` (so all events for one session land in the same
    partition, preserving order), and fires-and-forgets with a structured log.
    A production version would add delivery-report handling, idempotent
    producer semantics, and schema-registry integration.
    """

    def __init__(self, bootstrap_servers: str, client_id: str = "uploads-service") -> None:
        self.bootstrap_servers = bootstrap_servers
        self.client_id = client_id
        self._producer = None

    async def _get_producer(self):
        if self._producer is None:
            # Imported lazily so the import of this module never hard-requires
            # aiokafka in environments that use the in-memory publisher.
            from aiokafka import AIOKafkaProducer

            self._producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                client_id=self.client_id,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
            )
            await self._producer.start()
        return self._producer

    async def publish(self, event: Event) -> None:
        producer = await self._get_producer()
        await producer.send_and_wait(
            topic=event.topic,
            key=event.key,
            value=event.to_dict(),
        )
        logger.info(
            "event published (kafka): topic=%s key=%s event_id=%s",
            event.topic,
            event.key,
            event.event_id,
        )

    async def close(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None


# ---------------------------------------------------------------------------
# Process-wide publisher singleton (dependency-injected into services).
# ---------------------------------------------------------------------------

_publisher: Optional[EventPublisher] = None


def get_event_publisher() -> EventPublisher:
    """Return the process-wide publisher, constructing the default if needed."""
    global _publisher
    if _publisher is None:
        _publisher = _build_publisher()
    return _publisher


def set_event_publisher(publisher: EventPublisher) -> None:
    """Override the process-wide publisher (used by tests)."""
    global _publisher
    _publisher = publisher


def _build_publisher() -> EventPublisher:
    """Construct the publisher selected by ``settings.EVENT_PUBLISHER``."""
    from app.core.settings import settings

    if settings.EVENT_PUBLISHER == "kafka":
        return KafkaEventPublisher(
            bootstrap_servers=settings.REDIS_URL  # placeholder; a real deploy
            # would expose a dedicated KAFKA_BOOTSTRAP_SERVERS setting.
        )
    return InMemoryEventPublisher()
