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
import ssl
from datetime import UTC, datetime

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


def _kafka_consumer_options() -> dict:
    security_protocol = os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
    options = {
        "security_protocol": security_protocol,
        "client_id": os.getenv("KAFKA_CLIENT_ID", "auth-service"),
    }
    sasl_mechanism = os.getenv("KAFKA_SASL_MECHANISM")
    sasl_username = os.getenv("KAFKA_SASL_USERNAME")
    sasl_password = os.getenv("KAFKA_SASL_PASSWORD")
    if sasl_mechanism:
        options["sasl_mechanism"] = sasl_mechanism
    if sasl_username:
        options["sasl_plain_username"] = sasl_username
    if sasl_password:
        options["sasl_plain_password"] = sasl_password

    ca_location = os.getenv("KAFKA_SSL_CA_LOCATION")
    if ca_location:
        options["ssl_context"] = ssl.create_default_context(cafile=ca_location)
    elif security_protocol in ("SSL", "SASL_SSL") and os.getenv(
        "KAFKA_SSL_INSECURE", "false"
    ).lower() not in ("false", "0", "no"):
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        options["ssl_context"] = ssl_context
    return options


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
        **_kafka_consumer_options(),
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
