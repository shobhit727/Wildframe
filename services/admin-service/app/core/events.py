"""Event-bus publisher selection for admin-service.

admin-service produces user-moderation events (suspend / ban / activate)
that auth-service consumes to enforce status at the login boundary. The
publisher behind the ``EventPublisher`` port is selected via
``settings.EVENT_PUBLISHER``: in-memory by default (dev/test), Kafka in the
composed stack. See ``packages/sdk/wildframe_events`` for the contract.
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
                client_id="admin-service",
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


def user_moderated_event(
    user_id: str, status: str, moderated_by: str, moderated_at: str
) -> DomainEvent:
    """Event for user moderation changes.

    Idempotency key includes the timestamp so repeated moderations of the
    same user are all delivered (auth-service applies last-write-wins).
    """
    return DomainEvent(
        topic=Topic.USER_MODERATED,
        key=f"moderated:{user_id}:{status}:{moderated_at}",
        payload={
            "user_id": user_id,
            "status": status,
            "moderated_by": moderated_by,
            "moderated_at": moderated_at,
        },
        producer="admin-service",
    )
