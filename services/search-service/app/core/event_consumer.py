"""Kafka consumer keeping the search index in sync with the catalog.

content-service publishes lifecycle events (published / unpublished /
deleted). This consumer applies them incrementally to Elasticsearch via
SearchService.index_content / delete_content, so search reflects new
titles without waiting for a manual reindex (startup warm-up and the
reindex endpoint remain the backstop / rebuild path).

At-least-once: index_content is an upsert and delete_content is
idempotent (404-tolerant), so redelivery is harmless.
"""

import logging
import os

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "search-service"
TOPICS = ("content.published", "content.deleted", "content.unpublished")


async def _handle(catalog, search_service, event: dict) -> None:
    from uuid import UUID

    topic = event.get("topic")
    payload = event.get("payload", event)
    content_id = payload.get("content_id")
    if not content_id:
        logger.warning("content event without content_id: %s", topic)
        return

    if topic == "content.published":
        detail = await catalog._fetch_detail(content_id)
        from app.services import content_to_doc

        doc = content_to_doc(detail)
        await search_service.index_content(
            UUID(content_id),
            title=doc.get("title", ""),
            description=doc.get("description", ""),
            content_type=doc.get("content_type", "movie"),
            **{
                k: v
                for k, v in doc.items()
                if k not in ("title", "description", "content_type", "id")
            },
        )
        logger.info("indexed published content %s", content_id)
    elif topic in ("content.deleted", "content.unpublished"):
        await search_service.delete_content(UUID(content_id))
        logger.info("removed %s content %s", topic.split(".")[1], content_id)
    else:
        logger.debug("ignoring topic %s", topic)


async def run_content_sync_consumer(es_client) -> None:
    """Long-running consumer task. Exits quietly when Kafka is unreachable."""
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
    try:
        from aiokafka import AIOKafkaConsumer
    except ImportError:  # pragma: no cover
        logger.warning("aiokafka not installed; content sync consumer disabled")
        return

    from app.core.database import DatabaseManager
    from app.repositories import SearchIndexRepository, SearchQueryRepository
    from app.services import ContentCatalogClient, SearchService

    search_service = SearchService(
        es_client=es_client,
        query_repo=SearchQueryRepository(DatabaseManager.session_factory()),
        index_repo=SearchIndexRepository(DatabaseManager.session_factory()),
    )
    catalog = ContentCatalogClient()

    consumer = AIOKafkaConsumer(
        *TOPICS,
        bootstrap_servers=bootstrap,
        group_id=CONSUMER_GROUP,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    try:
        await consumer.start()
        logger.info("content sync consumer started (%s) on %s", bootstrap, TOPICS)
        async for msg in consumer:
            try:
                import json

                event = json.loads(msg.value.decode("utf-8"))
                await _handle(catalog, search_service, event)
            except Exception:  # noqa: BLE001 - never kill the consumer loop
                logger.exception("failed to apply content event")
            finally:
                await consumer.commit()
    except Exception:  # noqa: BLE001
        logger.exception("content sync consumer stopped")
    finally:
        try:
            await consumer.stop()
        except Exception:  # noqa: BLE001
            pass
        if catalog is not None:
            await catalog.aclose()
