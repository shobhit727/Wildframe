"""Bounded retention for dead-letter topics (#553).

DLQ topics are created lazily by the broker with infinite retention by
default — dead letters accumulate forever. This helper applies a bounded
``retention.ms`` (default 7 days) plus a segment cap to every ``*.dlq``
topic at service startup. Failures are logged, never raised: retention
enforcement must not block the service from serving.
"""

import logging
import os
import ssl
from typing import Any, Optional

logger = logging.getLogger(__name__)

DLQ_RETENTION_MS = int(os.getenv("DLQ_RETENTION_MS", str(7 * 24 * 60 * 60 * 1000)))
DLQ_SEGMENT_MS = int(os.getenv("DLQ_SEGMENT_MS", str(24 * 60 * 60 * 1000)))


def _alter_configs_refusal(response: Any) -> Optional[str]:
    """Describe the first per-resource error carried by an alter_configs reply.

    ``AIOKafkaAdminClient.alter_configs`` does **not** raise when the broker
    answers with an error code in the body — it returns the ``AlterConfigsResponse``
    with the failure recorded per resource (aiokafka 0.14.0:
    ``-> list[Response]``, each with ``resources`` of
    ``(error_code, error_message, resource_type, resource_name)``). Treating a
    non-raising call as success reports a topic as configured when the broker
    actually refused it — an ACL denial, an unknown topic, a read-only broker.

    Returns a diagnostic string for the first refusal found, or ``None`` when
    every resource in the reply succeeded.
    """
    if response is None:
        return None
    replies = response if isinstance(response, (list, tuple)) else [response]
    for reply in replies:
        for resource in getattr(reply, "resources", None) or ():
            if not isinstance(resource, (tuple, list)) or not resource:
                continue
            error_code = resource[0]
            if not error_code:
                continue  # 0 == NO_ERROR
            error_message = resource[1] if len(resource) > 1 else None
            return f"error_code={error_code} error_message={error_message!r}"
    return None


async def apply_dlq_retention(
    bootstrap_servers: str,
    client_id: str,
    security_protocol: str | None = None,
    sasl_mechanism: str | None = None,
    sasl_username: str | None = None,
    sasl_password: str | None = None,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> int:
    """Set bounded retention on all known DLQ topics.

    Creates the topics if absent (so the retention config sticks before the
    first dead letter arrives) and returns how many topics were configured.
    """
    from aiokafka.admin import AIOKafkaAdminClient, NewTopic  # type: ignore[import-untyped]

    from wildframe_events.topics import all_dlq_topics

    dlq = sorted(all_dlq_topics())
    security_protocol = security_protocol or os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
    sasl_mechanism = sasl_mechanism or os.getenv("KAFKA_SASL_MECHANISM")
    sasl_username = sasl_username or os.getenv("KAFKA_SASL_USERNAME")
    sasl_password = sasl_password or os.getenv("KAFKA_SASL_PASSWORD")
    if ssl_context is None:
        env_ca = os.getenv("KAFKA_SSL_CA_LOCATION")
        if env_ca:
            ctx = ssl.create_default_context(cafile=env_ca)
            ssl_context = ctx
        elif security_protocol in ("SSL", "SASL_SSL"):
            insecure = os.getenv("KAFKA_SSL_INSECURE", "false").lower() not in ("false", "0", "no")
            if insecure:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                ssl_context = ctx
    admin_kwargs: dict = {
        "bootstrap_servers": bootstrap_servers,
        "client_id": f"{client_id}-dlq-admin",
        "security_protocol": security_protocol,
    }
    if ssl_context is not None:
        admin_kwargs["ssl_context"] = ssl_context
    if sasl_mechanism:
        admin_kwargs["sasl_mechanism"] = sasl_mechanism
    if sasl_username:
        admin_kwargs["sasl_plain_username"] = sasl_username
    if sasl_password:
        admin_kwargs["sasl_plain_password"] = sasl_password
    admin = AIOKafkaAdminClient(**admin_kwargs)
    configured = 0
    try:
        await admin.start()
        existing = set(await admin.list_topics())
        missing = [t for t in dlq if t not in existing]
        if missing:
            import inspect

            try:
                _nt_params = inspect.signature(NewTopic.__init__).parameters
            except (TypeError, ValueError):
                _nt_params = {}
            _topic_config_key = "topic_configs" if "topic_configs" in _nt_params else "topic_config"
            await admin.create_topics(
                [
                    NewTopic(
                        name=t,
                        num_partitions=1,
                        replication_factor=1,
                        **{
                            _topic_config_key: {
                                "retention.ms": str(DLQ_RETENTION_MS),
                                "segment.ms": str(DLQ_SEGMENT_MS),
                            }
                        },
                    )
                    for t in missing
                ]
            )
            configured += len(missing)

        from aiokafka.admin.config_resource import (  # type: ignore[import-untyped]
            ConfigResource,
            ConfigResourceType,
        )

        for t in dlq:
            if t in missing:
                continue
            # aiokafka 0.14.0 has no nested ``ConfigResource.Type`` enum and no
            # ``set_config`` method: the topic scope is the module-level
            # ``ConfigResourceType`` IntEnum, and the config map is passed to
            # the constructor.
            resource = ConfigResource(
                ConfigResourceType.TOPIC,
                t,
                {
                    "retention.ms": str(DLQ_RETENTION_MS),
                    "segment.ms": str(DLQ_SEGMENT_MS),
                },
            )
            try:
                # ``alter_configs`` iterates its argument, so it takes a list.
                response = await admin.alter_configs([resource])
            except Exception:  # noqa: BLE001 - per-topic best effort
                # exc_info matters here: a broker ACL denial and a network
                # timeout are both "could not set retention", and without the
                # traceback the two are indistinguishable in the log.
                logger.warning("could not set retention on %s", t, exc_info=True)
                continue
            refusal = _alter_configs_refusal(response)
            if refusal is not None:
                # No exception was raised, so there is no traceback to attach;
                # the broker's own error code and message are the diagnostic.
                logger.warning("broker refused retention on %s: %s", t, refusal)
                continue
            configured += 1
        logger.info(
            "DLQ retention applied: %d topics at %d ms (%s)",
            configured,
            DLQ_RETENTION_MS,
            bootstrap_servers,
        )
        return configured
    except Exception:  # noqa: BLE001 - never block startup
        logger.exception("DLQ retention enforcement skipped")
        return configured
    finally:
        try:
            await admin.close()
        except Exception:  # noqa: BLE001
            pass
