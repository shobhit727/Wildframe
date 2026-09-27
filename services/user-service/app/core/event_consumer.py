"""Kafka consumer for user.registered events.

auth-service owns registration; user-service owns profiles. This consumer
provisions the default profile (+ preferences + free subscription) the moment
an account is created, so the account page never 404s for a fresh user.

At-least-once delivery: create_user_profile is effectively idempotent for a
given user (unique profile row). The offset is committed only after the event
has been applied, so a failed provisioning stops the consumer and is
redelivered on restart instead of being committed and lost. A payload with no
usable user_id can never be applied, so it is committed as skipped rather than
blocking the partition forever.
"""

import logging
import os
from uuid import UUID

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "user-service"
USER_REGISTERED_TOPIC = "user.registered"


async def _provision_profile(session_factory, user_id: str) -> None:
    """Create the default profile row for a freshly registered user."""
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
        await service.create_user_profile(UUID(user_id))


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
            import json

            try:
                event = json.loads(msg.value.decode("utf-8"))
                payload = event.get("payload", event) if isinstance(event, dict) else {}
                user_id = str(UUID(str(payload["user_id"])))
            except (ValueError, TypeError, KeyError):
                # A payload with no usable user_id can never be applied, so it
                # is committed as skipped instead of blocking the partition.
                logger.exception("skipping malformed user.registered at offset %s", msg.offset)
                await consumer.commit()
                continue

            await _provision_profile(session_factory, user_id)
            # Commit only after the event landed. Committing from a finally
            # block (or before the next event) would advance the committed
            # offset past an unapplied registration and lose it for good.
            await consumer.commit()
    except Exception:  # noqa: BLE001
        logger.exception("user.registered consumer stopped")
    finally:
        try:
            await consumer.stop()
        except Exception:  # noqa: BLE001
            pass
