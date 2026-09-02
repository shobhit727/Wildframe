"""Kafka consumer for user.registered events.

auth-service owns registration; user-service owns profiles. This consumer
provisions the default profile (+ preferences + free subscription) the moment
an account is created, so the account page never 404s for a fresh user.

At-least-once delivery: create_user_profile is effectively idempotent for a
given user (unique profile row); duplicates log and move on.
"""

import logging
import os

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "user-service"
USER_REGISTERED_TOPIC = "user.registered"


async def _provision_profile(session_factory, user_id: str) -> None:
    """Create the default profile row for a freshly registered user."""
    from uuid import UUID

    from app.repositories import (
        UserDeviceRepository,
        UserPreferenceRepository,
        UserProfileRepository,
        UserSubscriptionProfileRepository,
    )
    from app.services import UserService

    async with session_factory() as session:
        service = UserService(
            UserProfileRepository(session),
            UserDeviceRepository(session),
            UserPreferenceRepository(session),
            UserSubscriptionProfileRepository(session),
        )
        try:
            await service.create_user_profile(UUID(user_id))
        except Exception:  # noqa: BLE001 - at-least-once: log and continue
            logger.exception("profile provisioning failed for %s", user_id)


async def run_user_registered_consumer(session_factory) -> None:
    """Long-running consumer task. Exits quietly when Kafka is unreachable."""
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
    try:
        from aiokafka import AIOKafkaConsumer  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover
        logger.warning("aiokafka not installed; user.registered consumer disabled")
        return

    consumer = AIOKafkaConsumer(
        USER_REGISTERED_TOPIC,
        bootstrap_servers=bootstrap,
        group_id=CONSUMER_GROUP,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    try:
        await consumer.start()
        logger.info("user.registered consumer started (%s)", bootstrap)
        async for msg in consumer:
            try:
                import json

                event = json.loads(msg.value.decode("utf-8"))
                payload = event.get("payload", event)
                await _provision_profile(session_factory, payload["user_id"])
            except Exception:  # noqa: BLE001 - never kill the consumer loop
                logger.exception("failed to provision profile from user.registered")
            finally:
                await consumer.commit()
    except Exception:  # noqa: BLE001
        logger.exception("user.registered consumer stopped")
    finally:
        try:
            await consumer.stop()
        except Exception:  # noqa: BLE001
            pass
