"""Event-bus subscriber for search-service.

Consumes content lifecycle events so the search index stays in sync with
the catalog without waiting for a full reindex (#227):

- ``content.deleted`` — remove the document (idempotent: delete by _id).
- ``content.unpublished`` — remove the document (it must not stay
  discoverable; the index only ever serves published content).

The subscriber behind the ``EventSubscriber`` port is selected via
``settings.EVENT_PUBLISHER``: in-memory for dev/test, Kafka in production.
Deliveries are deduplicated (``RedisDeduplicationStore``) so retries can
never double-process an event; a duplicate delivery is a no-op because
deleting an already-absent document is a no-op.
"""

import logging
from contextlib import suppress
from uuid import UUID

from wildframe_events import DomainEvent, EventSubscriber, InMemoryEventSubscriber
from wildframe_events.subscriber import KafkaEventSubscriber, RedisDeduplicationStore

from app.core.database import DatabaseManager
from app.core.settings import settings
from app.repositories import SearchIndexRepository, SearchQueryRepository
from app.services import SearchService

logger = logging.getLogger(__name__)

_subscriber: EventSubscriber | None = None


async def _handle_content_gone(event: DomainEvent, action: str) -> None:
    """Delete a document from ES + SQL mirror for deleted/unpublished events.

    Idempotent by construction: delete-by-_id on an absent document is a
    no-op, and the subscriber dedups at the event level (key
    ``deleted:{content_id}`` / ``unpublished:{content_id}``).
    """
    content_id = event.payload.get("content_id")
    if not content_id:
        logger.warning("dropping %s event without content_id: %s", action, event.event_id)
        return
    try:
        UUID(content_id)
    except (ValueError, TypeError):
        logger.warning("dropping %s event with invalid content_id=%r", action, content_id)
        return
    from app.api.search_routes import es_client

    factory = DatabaseManager.session_factory
    if factory is None:
        await DatabaseManager.init()
        factory = DatabaseManager.session_factory
    async with factory() as session:
        service = SearchService(
            es_client(), SearchQueryRepository(session), SearchIndexRepository(session)
        )
        removed = await service.delete_content(content_id)
        logger.info("content %s %s -> removed=%s", content_id, action, removed)


async def _handle_content_deleted(event: DomainEvent) -> None:
    await _handle_content_gone(event, "deleted")


async def _handle_content_unpublished(event: DomainEvent) -> None:
    await _handle_content_gone(event, "unpublished")


def get_event_subscriber() -> EventSubscriber:
    """Return the process-wide subscriber selected by ``EVENT_PUBLISHER``."""
    global _subscriber
    if _subscriber is None:
        if settings.EVENT_PUBLISHER == "kafka":
            from wildframe_events import KafkaEventPublisher

            dedup_store = None
            try:
                dedup_store = RedisDeduplicationStore(
                    redis_url=settings.REDIS_URL, key_prefix="wf:dedup:search"
                )
            except Exception:
                logger.exception("failed to build Redis dedup store; dedup disabled")
            _subscriber = KafkaEventSubscriber(
                bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
                group_id="search-service",
                client_id="search-service",
                dedup_store=dedup_store,
                dlq_publisher=KafkaEventPublisher(
                    bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
                    client_id="search-service-dlq",
                ),
            )
            logger.info("event subscriber: kafka (%s)", settings.KAFKA_BOOTSTRAP_SERVERS)
        else:
            _subscriber = InMemoryEventSubscriber()
            logger.info("event subscriber: in-memory")
    return _subscriber


def reset_event_subscriber() -> None:
    """Drop the cached subscriber (test seam)."""
    global _subscriber
    _subscriber = None


async def start_event_subscriber() -> None:
    """Register handlers and start consuming (tolerant of failures)."""
    try:
        subscriber = get_event_subscriber()
        await subscriber.subscribe("content.deleted", _handle_content_deleted)
        await subscriber.subscribe("content.unpublished", _handle_content_unpublished)
        await subscriber.start()
    except Exception:
        logger.exception("event subscriber failed to start; index stays fresh via reindex")


async def stop_event_subscriber() -> None:
    """Stop the subscriber if it is running."""
    if _subscriber is not None:
        with suppress(Exception):
            await _subscriber.stop()
