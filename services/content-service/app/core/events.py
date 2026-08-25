"""Event-bus publisher selection for content-service.

content-service produces content lifecycle events (published / deleted /
unpublished) that downstream services (search-service, ...) consume to keep
their indexes in sync. The publisher behind the ``EventPublisher`` port is
selected via ``settings.EVENT_PUBLISHER``: an in-memory publisher by default
(dev/test), a Kafka publisher in production. See
``packages/sdk/wildframe_events`` for the transport contract.
"""

import logging

from wildframe_events import (
    DomainEvent,
    EventPublisher,
    InMemoryEventPublisher,
    KafkaEventPublisher,
    Topic,
)

from app.core.settings import settings

logger = logging.getLogger(__name__)

_publisher: EventPublisher | None = None


def get_event_publisher() -> EventPublisher:
    """Return the process-wide publisher selected by ``EVENT_PUBLISHER``."""
    global _publisher
    if _publisher is None:
        if settings.EVENT_PUBLISHER == "kafka":
            _publisher = KafkaEventPublisher(
                bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,  # type: ignore[arg-type]
                client_id="content-service",
            )
            logger.info("event publisher: kafka (%s)", settings.KAFKA_BOOTSTRAP_SERVERS)
        else:
            _publisher = InMemoryEventPublisher()
            logger.info("event publisher: in-memory")
    return _publisher


def reset_event_publisher() -> None:
    """Drop the cached publisher (test seam)."""
    global _publisher
    _publisher = None


def content_deleted_event(content_id: str) -> DomainEvent:
    """Event for admin content deletion (idempotency key: deleted:{content_id})."""
    return DomainEvent(
        topic=Topic.CONTENT_DELETED,
        key=f"deleted:{content_id}",
        payload={"content_id": content_id},
        producer="content-service",
    )


def content_unpublished_event(content_id: str) -> DomainEvent:
    """Event for unpublishing/archiving content (idempotency key: unpublished:{content_id})."""
    return DomainEvent(
        topic=Topic.CONTENT_UNPUBLISHED,
        key=f"unpublished:{content_id}",
        payload={"content_id": content_id},
        producer="content-service",
    )
