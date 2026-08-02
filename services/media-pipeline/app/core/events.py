from typing import Any

"""Lightweight domain events + event-bus publisher port.

Shared event backbone for the media-pipeline (see PRODUCT_VISION §7). The
default publisher is in-memory + structured log so the service boots and tests
run with no Kafka; a real Kafka adapter lives behind the same port.

Pipeline event contract (media-pipeline producer):
    content.quarantined        — raw bytes landed in the quarantine bucket
    content.scanned            — virus scan passed (payload: clean/infected)
    content.metadata_extracted — ffprobe metadata captured
    content.thumbnailed        — poster/thumbnails generated
    content.audio_extracted    — audio tracks extracted
    content.subtitle_extracted — subtitle tracks extracted
    content.encoded            — ffmpeg multi-bitrate encode done
    content.packaged           — HLS/DASH packaging done
    content.uploaded_to_storage— moved to object storage (s3_upload)
    content.cdn_invalidated    — CDN cache purged (ready for playback)
    content.pipeline.failed    — DLQ: a job exhausted retries at some stage
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """A small, self-describing domain event.

    Fields:
        topic: event type / Kafka topic name.
        key: partitioning + idempotency key (the pipeline job id).
        payload: event-specific data.
        event_id: unique id for dedup / tracing.
        occurred_at: ISO-8601 UTC timestamp.
    """

    topic: str
    key: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
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
    """Port (interface) for publishing domain events."""

    @abstractmethod
    async def publish(self, event: Event) -> None:
        raise NotImplementedError

    async def publish_many(self, events: list[Event]) -> None:
        for event in events:
            await self.publish(event)


class InMemoryEventPublisher(EventPublisher):
    """No-op-ish publisher: records events in memory + structured log.

    Default adapter so the service is fully functional and testable with no
    Kafka. Recorded events are exposed for tests / in-process listeners.
    """

    def __init__(self) -> None:
        self.sent: list[Event] = []

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
    """Kafka-backed publisher (aiokafka). Same port, real transport.

    Only instantiated when ``settings.EVENT_PUBLISHER == "kafka"``; the import
    is lazy so dev/test environments never hard-require aiokafka.
    """

    def __init__(
        self, bootstrap_servers: str, client_id: str = "media-pipeline"
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.client_id = client_id
        self._producer = None

    async def _get_producer(self):
        if self._producer is None:
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
# Process-wide publisher singleton.
# ---------------------------------------------------------------------------

_publisher: EventPublisher | None = None


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
        return KafkaEventPublisher(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS)
    return InMemoryEventPublisher()
