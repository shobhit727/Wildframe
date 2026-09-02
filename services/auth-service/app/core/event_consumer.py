"""Kafka consumer for user.moderated events.

admin-service owns moderation decisions; auth-service owns the login
boundary. This consumer applies moderation status to the local user row
(``is_active``) so suspended/banned accounts are rejected at login and
refresh without a cross-service call on the hot path.

At-least-once delivery: applying ``status == "active" -> is_active=True``
is idempotent, so redelivery is harmless.
"""

import logging
import os
from datetime import UTC, datetime

# type: ignore[import-untyped] - aiokafka has no stubs
from aiokafka import AIOKafkaConsumer

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "auth-service"
USER_MODERATED_TOPIC = "user.moderated"


async def _apply_moderation(session_factory, user_id: str, status: str) -> None:
    """Set users.is_active according to the moderation status."""
    from sqlalchemy import update

    from app.models import User

    active = status == "active"
    async with session_factory() as session:
        result = await session.execute(
            update(User).where(User.id == user_id).values(is_active=active)
        )
        await session.commit()
        logger.info(
            "moderation applied: user=%s status=%s rows=%d", user_id, status, result.rowcount
        )


async def run_user_moderation_consumer(session_factory) -> None:
    """Long-running consumer task. Exits quietly when Kafka is unreachable —
    the admin-service publish path is the source of truth and moderation can
    also be re-applied by re-issuing the decision."""
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
    try:
        pass
    except ImportError:  # pragma: no cover - aiokafka is an auth dependency
        logger.warning("aiokafka not installed; user.moderated consumer disabled")
        return

    consumer = AIOKafkaConsumer(
        USER_MODERATED_TOPIC,
        bootstrap_servers=bootstrap,
        group_id=CONSUMER_GROUP,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    try:
        await consumer.start()
        logger.info("user.moderated consumer started (%s)", bootstrap)
        async for msg in consumer:
            try:
                import json

                event = json.loads(msg.value.decode("utf-8"))
                # SDK envelope: {event_id, topic, payload: {...}, ...}
                payload = event.get("payload", event)
                await _apply_moderation(session_factory, payload["user_id"], payload["status"])
            except Exception:  # noqa: BLE001 - never kill the consumer loop
                logger.exception("failed to apply user.moderated message")
            finally:
                await consumer.commit()
    except Exception:  # noqa: BLE001 - observability must not crash the app
        logger.exception("user.moderated consumer stopped")
    finally:
        try:
            await consumer.stop()
        except Exception:  # noqa: BLE001
            pass


def moderation_timestamp() -> str:
    """Current UTC timestamp for event idempotency keys."""
    return datetime.now(UTC).isoformat()
