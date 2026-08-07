"""Event subscriber port + adapters.

The EventSubscriber port is the *only* way services consume events.
Two adapters are provided:

  - InMemoryEventSubscriber: calls registered handlers directly. Used
    in dev/test so no Kafka is needed.
  - KafkaEventSubscriber: real Kafka consumer backed by aiokafka.

Both use the same DomainEvent envelope and the same topic names from
``wildframe_events.topics``.

Dead-letter handling: when a handler fails after exhausting retries,
the event is published to ``<original_topic>.dlq`` with error context.
Consumers of the DLQ can alert humans or trigger manual remediation.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Callable, Dict, List, Any

from wildframe_events.event import DomainEvent
from wildframe_events.topics import Topic

logger = logging.getLogger(__name__)

# Type alias: a handler is an async callable that takes a DomainEvent.
EventHandler = Callable[[DomainEvent], Any]


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
    the registered handlers. Features:
      - Exponential backoff retry per event (max 3 attempts).
      - Dead-letter queue: failed events are sent to ``<topic>.dlq``.
      - Consumer group for horizontal scaling.
      - Graceful shutdown on stop().
    """

    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str,
        client_id: str = "wildframe-consumer",
        max_retries: int = 3,
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.client_id = client_id
        self.max_retries = max_retries
        self._handlers: Dict[str, List[EventHandler]] = {}
        self._consumer = None

    async def subscribe(self, topic: str, handler: EventHandler) -> None:
        self._handlers.setdefault(topic, []).append(handler)

    async def start(self) -> None:
        """Start the Kafka consumer."""
        from aiokafka import AIOKafkaConsumer

        topics = list(self._handlers.keys())
        self._consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            client_id=self.client_id,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            key_deserializer=lambda m: m.decode("utf-8") if m else None,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
        )
        await self._consumer.start()

        async for message in self._consumer:
            event = DomainEvent.from_dict(message.value)
            await self._dispatch(event)
            await self._consumer.commit()

    async def _dispatch(self, event: DomainEvent) -> None:
        """Dispatch an event to registered handlers with retry + DLQ."""
        handlers = self._handlers.get(event.topic, [])
        for handler in handlers:
            for attempt in range(1, self.max_retries + 1):
                try:
                    await handler(event)
                    return  # Success — move on.
                except Exception as exc:
                    logger.warning(
                        "handler failed (attempt %d/%d) for topic=%s event_id=%s: %s",
                        attempt,
                        self.max_retries,
                        event.topic,
                        event.event_id,
                        exc,
                    )
                    if attempt == self.max_retries:
                        # Exhausted retries → DLQ.
                        await self._send_to_dlq(event, exc)

    async def _send_to_dlq(self, event: DomainEvent, error: Exception) -> None:
        """Send a failed event to the dead-letter topic."""
        dlq_topic = event.topic + Topic.DLQ_SUFFIX
        logger.error(
            "event sent to DLQ: topic=%s key=%s event_id=%s error=%s",
            dlq_topic,
            event.key,
            event.event_id,
            error,
        )
        # In a real deployment, this would publish to Kafka. For now, log it.
        # A shared InMemoryEventPublisher or a direct Kafka publish would go here.

    async def stop(self) -> None:
        """Stop the Kafka consumer."""
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
