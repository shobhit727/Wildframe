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
from typing import Optional

logger = logging.getLogger(__name__)

DLQ_RETENTION_MS = int(os.getenv("DLQ_RETENTION_MS", str(7 * 24 * 60 * 60 * 1000)))
DLQ_SEGMENT_MS = int(os.getenv("DLQ_SEGMENT_MS", str(24 * 60 * 60 * 1000)))


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
        existing = set((await admin.list_topics()).topics)
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

        from aiokafka.admin.config_resource import ConfigResource  # type: ignore[import-untyped]

        for t in dlq:
            if t in missing:
                continue
            resource = ConfigResource(ConfigResource.Type.TOPIC, t)
            resource.set_config("retention.ms", str(DLQ_RETENTION_MS))
            resource.set_config("segment.ms", str(DLQ_SEGMENT_MS))
            try:
                await admin.alter_configs(resource)
                configured += 1
            except Exception:  # noqa: BLE001 - per-topic best effort
                logger.warning("could not set retention on %s", t)
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
