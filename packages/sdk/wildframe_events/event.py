"""Domain Event base class.

A DomainEvent is a small, self-describing data object that captures a
state change in the system. It is the **integration surface** between
services — services never reach into another service's DB; they emit
and consume events.

Every event carries:
  - topic: which Kafka topic / event type this is
  - key: partitioning + business idempotency key (entity ID)
  - payload: event-specific data (dict)
  - event_id: globally unique ID for dedup / tracing
  - occurred_at: ISO-8601 UTC timestamp (client/advisory only — see below)
  - producer: which service emitted this
  - schema_version: contract version for forward compatibility
  - server_time: ISO-8601 UTC timestamp assigned by the broker at ingest.
    Ordering-sensitive consumers MUST use ``server_time`` (or Kafka
    offset order), never the client-controlled ``occurred_at``.
  - sequence: optional per-key ordering sequence assigned by the
    producer/ingest layer for events that need explicit ordering.
  - correlation_id: cross-service correlation ID propagated through
    asynchronous boundaries (inherited from the originating request).

Timestamp semantics
-------------------
``occurred_at`` is produced by the emitting service and is therefore
attacker-controllable; it is kept for diagnostics only. Consumers that
make ordering or security decisions must use the broker-assigned
``server_time`` (set from ``message.timestamp`` by the Kafka adapter)
or Kafka offset order within a partition.

Schema versioning
-----------------
``schema_version`` starts at 1. Producers MUST only make additive
changes to payload fields; ``from_dict`` rejects envelopes with a
version newer than the supported one (``SchemaVersionError``) instead
of silently misinterpreting them. Unknown fields are dropped (not
crashed on), which keeps older producers readable by newer consumers.
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4


# Current schema version — bump when breaking changes happen.
SCHEMA_VERSION = 1

#: Maximum length accepted for correlation_id / producer strings.
MAX_ID_LENGTH = 128


class PayloadValidationError(ValueError):
    """Raised when an event payload is not JSON-safe, carries secret-shaped
    keys, or the envelope is missing required fields."""


class SchemaVersionError(ValueError):
    """Raised when an event's ``schema_version`` is newer than the supported
    version — the consumer cannot safely interpret it."""


# Exact-match (case-insensitive, leading underscores ignored) key names that
# must never appear in an event payload: authorization headers, passwords,
# secret configuration, or tokens. Prevents accidental secret exfiltration
# through the event bus.
_SECRET_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "authorization",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "auth_token",
        "private_key",
        "client_secret",
        "x_api_key",
        "x_amz_security_token",
        "aws_secret_access_key",
    }
)


def validate_payload(
    payload: Any, *, forbid_secret_keys: bool = True, path: str = "payload"
) -> None:
    """Recursively verify a payload is deterministically JSON-safe.

    Rejects (raises :class:`PayloadValidationError`):
      - non-finite floats (NaN / Infinity) — they are not valid JSON
      - non-string dict keys
      - bytes, sets, and arbitrary objects (no silent ``str()`` coercion)
      - keys that match the secret-shaped allowlist when
        ``forbid_secret_keys`` is True

    Allowed: str, int, bool, None, finite float, list/tuple, dict with
    string keys, recursively.
    """
    if payload is None or isinstance(payload, (str, int, bool)):
        return
    if isinstance(payload, float):
        if not math.isfinite(payload):
            raise PayloadValidationError(f"non-finite float at {path}")
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            if not isinstance(key, str):
                raise PayloadValidationError(
                    f"non-string key {key!r} at {path} (must be JSON-safe)"
                )
            if forbid_secret_keys and key.lower().lstrip("_") in _SECRET_KEYS:
                raise PayloadValidationError(
                    f"secret-shaped key {key!r} at {path} is not allowed in event payloads"
                )
            validate_payload(value, forbid_secret_keys=forbid_secret_keys, path=f"{path}.{key}")
        return
    if isinstance(payload, (list, tuple)):
        for index, item in enumerate(payload):
            validate_payload(
                item, forbid_secret_keys=forbid_secret_keys, path=f"{path}[{index}]"
            )
        return
    raise PayloadValidationError(
        f"unsupported type {type(payload).__name__} at {path} "
        "(payloads must be JSON-safe: str/int/bool/float/None/list/dict)"
    )


@dataclass
class DomainEvent:
    """A domain event that crosses service boundaries.

    Usage::

        from wildframe_events import DomainEvent, Topic

        event = DomainEvent(
            topic=Topic.CONTENT_UPLOADED,
            key=str(upload_session_id),
            payload={
                "upload_session_id": str(upload_session_id),
                "content_id": str(content_id),
                "storage_key": "uploads/abc123.mp4",
            },
            producer="uploads-service",
        )
        await publisher.publish(event)
    """

    topic: str
    key: str
    payload: Dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    producer: str = ""
    schema_version: int = SCHEMA_VERSION
    # Additive fields (appended to keep positional construction stable):
    server_time: Optional[str] = None
    sequence: Optional[int] = None
    correlation_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a Kafka-friendly dict (JSON-serializable)."""
        return {
            "event_id": self.event_id,
            "topic": self.topic,
            "key": self.key,
            "occurred_at": self.occurred_at,
            "producer": self.producer,
            "schema_version": self.schema_version,
            "server_time": self.server_time,
            "sequence": self.sequence,
            "correlation_id": self.correlation_id,
            "payload": copy.deepcopy(self.payload),
        }

    def to_json(self) -> str:
        """Serialize to a JSON string (for Kafka value_serializer).

        Raises :class:`PayloadValidationError` if the payload is not
        JSON-safe — serialization never silently stringifies arbitrary
        objects or non-finite floats.
        """
        validate_payload(self.payload)
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        *,
        max_schema_version: Optional[int] = None,
    ) -> DomainEvent:
        """Deserialize from a dict (Kafka consumer output).

        Validates the envelope:
          - ``topic`` and ``key`` are required non-empty strings
          - ``schema_version`` must not exceed the supported version
            (``max_schema_version``, defaulting to :data:`SCHEMA_VERSION`)
          - ``occurred_at`` must be an ISO-8601 timestamp
          - ``server_time`` (if present) must be an ISO-8601 timestamp
          - ``sequence`` (if present) must be an int

        Unknown top-level fields are ignored (forward compatibility:
        a newer producer must remain readable by older consumers until
        the schema version bumps).
        """
        if not isinstance(data, dict):
            raise PayloadValidationError(
                f"event envelope must be a dict, got {type(data).__name__}"
            )
        topic = data.get("topic")
        key = data.get("key")
        if not isinstance(topic, str) or not topic:
            raise PayloadValidationError("event envelope missing required string field 'topic'")
        if not isinstance(key, str) or not key:
            raise PayloadValidationError("event envelope missing required string field 'key'")

        version = data.get("schema_version", SCHEMA_VERSION)
        supported = SCHEMA_VERSION if max_schema_version is None else max_schema_version
        if not isinstance(version, int) or isinstance(version, bool):
            raise PayloadValidationError(
                f"event schema_version must be an int, got {version!r} (topic={topic})"
            )
        if version > supported:
            raise SchemaVersionError(
                f"event schema_version {version} is newer than supported {supported} "
                f"(topic={topic}); refusing to misinterpret it"
            )

        occurred_at = data.get("occurred_at")
        if occurred_at is None:
            occurred_at = datetime.now(timezone.utc).isoformat()
        elif not isinstance(occurred_at, str) or not _parse_iso(occurred_at):
            raise PayloadValidationError(
                f"event occurred_at must be an ISO-8601 timestamp, got {occurred_at!r}"
            )

        server_time = data.get("server_time")
        if server_time is not None and (
            not isinstance(server_time, str) or not _parse_iso(server_time)
        ):
            raise PayloadValidationError(
                f"event server_time must be an ISO-8601 timestamp, got {server_time!r}"
            )

        sequence = data.get("sequence")
        if sequence is not None and (not isinstance(sequence, int) or isinstance(sequence, bool)):
            raise PayloadValidationError(
                f"event sequence must be an int, got {sequence!r}"
            )
        payload = copy.deepcopy(data.get("payload", {}))
        validate_payload(payload, forbid_secret_keys=True)
        return cls(
            topic=topic,
            key=key,
            payload=payload,
            event_id=data.get("event_id") or str(uuid4()),
            occurred_at=occurred_at,
            producer=data.get("producer", ""),
            schema_version=version,
            server_time=server_time,
            sequence=sequence,
            correlation_id=data.get("correlation_id"),
        )

    @classmethod
    def from_json(cls, json_str: str) -> DomainEvent:
        """Deserialize from a JSON string."""
        return cls.from_dict(json.loads(json_str))


def _parse_iso(value: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; return the datetime or None if invalid."""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
