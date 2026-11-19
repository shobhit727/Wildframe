"""Kafka consumer for user.moderated events.

admin-service owns moderation decisions; auth-service owns the login
boundary. This consumer applies moderation status to the local user row
(``is_active``) so suspended/banned accounts are rejected at login and
refresh without a cross-service call on the hot path.

At-least-once delivery: applying ``status == "active" -> is_active=True``
is idempotent, so redelivery is harmless. The offset is committed only
after the decision landed (or the message was rejected as malformed);
a failed apply is never committed (#1115).
"""

import asyncio
import json
import logging
import os
import ssl
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from aiokafka import AIOKafkaConsumer
from aiokafka.structs import TopicPartition

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "auth-service"
USER_MODERATED_TOPIC = "user.moderated"

# Bounded retry for a failed apply (transient DB outage, pool exhaustion).
# Attempt N waits APPLY_RETRY_BACKOFF_SECONDS * 2**(N-1) before retrying.
APPLY_MAX_ATTEMPTS = 3
APPLY_RETRY_BACKOFF_SECONDS = 0.5


class MalformedModerationEvent(ValueError):
    """The message can never be applied, so redelivery cannot help."""


def _parse_moderation(raw: bytes) -> tuple[str, str]:
    """Return ``(user_id, status)`` or raise :class:`MalformedModerationEvent`.

    ``user_id`` is validated as a UUID here: on Postgres a non-UUID string
    fails the UPDATE with the same error on every redelivery, which would
    otherwise turn one bad event into a permanently stalled partition.
    """
    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
        raise MalformedModerationEvent(f"unparseable: {exc}") from exc
    if not isinstance(event, dict):
        raise MalformedModerationEvent("event is not a JSON object")
    # SDK envelope: {event_id, topic, payload: {...}, ...}
    payload = event.get("payload", event)
    if not isinstance(payload, dict):
        raise MalformedModerationEvent("payload is not a JSON object")
    status = payload.get("status")
    if not isinstance(status, str) or not status:
        raise MalformedModerationEvent("missing status")
    try:
        user_id = str(UUID(str(payload["user_id"])))
    except (KeyError, ValueError) as exc:
        raise MalformedModerationEvent("missing or invalid user_id") from exc
    return user_id, status


async def _apply_with_retry(session_factory, user_id: str, status: str) -> None:
    """Apply the decision, retrying up to APPLY_MAX_ATTEMPTS; re-raise on exhaustion."""
    for attempt in range(1, APPLY_MAX_ATTEMPTS + 1):
        try:
            await _apply_moderation(session_factory, user_id, status)
            return
        except Exception:
            if attempt == APPLY_MAX_ATTEMPTS:
                raise
            logger.warning(
                "user.moderated apply failed (attempt %d/%d); retrying",
                attempt,
                APPLY_MAX_ATTEMPTS,
                exc_info=True,
            )
            await asyncio.sleep(APPLY_RETRY_BACKOFF_SECONDS * 2 ** (attempt - 1))


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
    options: dict[str, Any] = {
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
            position = {TopicPartition(msg.topic, msg.partition): msg.offset + 1}
            try:
                user_id, status = _parse_moderation(msg.value)
            except MalformedModerationEvent as exc:
                # Redelivery can never apply this message: skip it explicitly
                # (commit) so it does not block the partition.
                logger.error(
                    "skipping malformed user.moderated topic=%s partition=%s offset=%s: %s",
                    msg.topic,
                    msg.partition,
                    msg.offset,
                    exc,
                )
                await consumer.commit(position)
                continue

            try:
                await _apply_with_retry(session_factory, user_id, status)
            except Exception:
                # Do NOT commit and do NOT continue: committing a later
                # offset would skip this unapplied decision. Stopping leaves
                # the committed offset before this message, so it replays on
                # the next start (#1115).
                logger.exception(
                    "user.moderated apply failed after %d attempts; stopping without commit "
                    "topic=%s partition=%s offset=%s",
                    APPLY_MAX_ATTEMPTS,
                    msg.topic,
                    msg.partition,
                    msg.offset,
                )
                raise
            await consumer.commit(position)
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
