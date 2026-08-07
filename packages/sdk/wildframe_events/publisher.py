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
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import List

from wildframe_events.event import DomainEvent

logger = logging.getLogger(__name__)


class EventPublisher(ABC):
    """Port (interface) for publishing domain events.

    Any transport (in-memory, Kafka, pub/sub, SNS) implements ``publish``
    and optionally ``publish_many``. The service depends only on this
    abstraction.
    """

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


class InMemoryEventPublisher(EventPublisher):
    """No-op-ish publisher that records events in memory + structured log.

    This is the *default* adapter for dev/test: the service is fully
    functional and testable with no Kafka. The recorded events are also
    available for test assertions.
    """

    def __init__(self) -> None:
        self.sent: List[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
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
      - Idempotent producer (idempotency_key used as message key for
        partition ordering and dedup at the consumer).
      - Structured logging of every published event.
      - Graceful startup/shutdown of the Kafka producer.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        client_id: str = "wildframe",
        acks: str = "all",
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.client_id = client_id
        self.acks = acks
        self._producer = None

    async def _get_producer(self):
        """Lazy-start the Kafka producer (avoids import when not used)."""
        if self._producer is None:
            from aiokafka import AIOKafkaProducer

            self._producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                client_id=self.client_id,
                acks=self.acks,
                value_serializer=lambda v: v.to_json().encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
            )
            await self._producer.start()
        return self._producer

    async def publish(self, event: DomainEvent) -> None:
        """Publish a domain event to Kafka."""
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
            await self._producer.stop()
            self._producer = None
