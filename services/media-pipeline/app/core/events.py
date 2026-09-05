from __future__ import annotations
from typing import Any
import json
import logging
from typing import TYPE_CHECKING
from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from app.core.settings import settings


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
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "key": self.key,
            "payload": self.payload,
            "event_id": self.event_id,
            "occurred_at": self.occurred_at,
        }


class EventPublisher(ABC):
    """Port for publishing domain events."""

    @abstractmethod
    async def publish(self, event: Event) -> None:
        pass


class InMemoryEventPublisher(EventPublisher):
    """In-memory event publisher for testing and local development."""

    def __init__(self):
        self.sent: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.sent.append(event)
        logging.getLogger(__name__).debug("InMemoryEventPublisher: published %s", event.topic)


class KafkaEventPublisher(EventPublisher):
    """Kafka-backed event publisher."""

    def __init__(self, bootstrap_servers: str, client_id: str = "media-pipeline") -> None:
        self.bootstrap_servers = bootstrap_servers
        self.client_id = client_id
        self._producer: AIOKafkaProducer | None = None

    async def _get_producer(self) -> AIOKafkaProducer:
        if self._producer is None:
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


# Default publisher (in-memory)
_event_publisher: EventPublisher = InMemoryEventPublisher()


def get_event_publisher() -> EventPublisher:
    """Get the current event publisher."""
    return _event_publisher


def set_event_publisher(publisher: EventPublisher) -> None:
    """Set a custom event publisher (e.g., for tests or Kafka)."""
    global _event_publisher
    _event_publisher = publisher


def _build_publisher() -> EventPublisher:
    """Build the appropriate event publisher based on settings."""
    if settings.EVENT_PUBLISHER == "kafka":
        return KafkaEventPublisher(settings.KAFKA_BOOTSTRAP_SERVERS)
    return InMemoryEventPublisher()
