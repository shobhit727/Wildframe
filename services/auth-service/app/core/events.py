"""Event-bus publisher selection for auth-service.

auth-service produces account-lifecycle events (registration) that
downstream services consume — user-service provisions the default profile,
notification-service sends the welcome message. Publisher selected via
``settings.EVENT_PUBLISHER``: in-memory by default (dev/test), Kafka in the
composed stack.
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
                client_id="auth-service",
            )  # type: ignore[arg-type]
            logger.info("event publisher: kafka (%s)", settings.KAFKA_BOOTSTRAP_SERVERS)
        else:
            _publisher = InMemoryEventPublisher()
            logger.info("event publisher: in-memory")
    return _publisher


def reset_event_publisher() -> None:
    """Drop the cached publisher (test seam)."""
    global _publisher
    _publisher = None


def user_registered_event(user_id: str, email: str) -> DomainEvent:
    """Event for account registration (idempotency key: registered:{user_id})."""
    return DomainEvent(
        topic=Topic.USER_REGISTERED,
        key=f"registered:{user_id}",
        payload={"user_id": user_id, "email": email},
        producer="auth-service",
    )
