"""Domain Event base class.

A DomainEvent is a small, self-describing data object that captures a
state change in the system. It is the **integration surface** between
services — services never reach into another service's DB; they emit
and consume events.

Every event carries:
  - topic: which Kafka topic / event type this is
  - key: partitioning + idempotency key (entity ID)
  - payload: event-specific data (dict)
  - event_id: globally unique ID for dedup / tracing
  - occurred_at: ISO-8601 UTC timestamp
  - producer: which service emitted this
  - schema_version: contract version for forward compatibility
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any
from uuid import uuid4


# Current schema version — bump when breaking changes happen.
SCHEMA_VERSION = 1


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

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a Kafka-friendly dict (JSON-serializable)."""
        return {
            "event_id": self.event_id,
            "topic": self.topic,
            "key": self.key,
            "occurred_at": self.occurred_at,
            "producer": self.producer,
            "schema_version": self.schema_version,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        """Serialize to JSON string (for Kafka value_serializer)."""
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DomainEvent:
        """Deserialize from a dict (Kafka consumer output)."""
        return cls(
            topic=data["topic"],
            key=data["key"],
            payload=data.get("payload", {}),
            event_id=data.get("event_id", str(uuid4())),
            occurred_at=data.get("occurred_at", datetime.now(timezone.utc).isoformat()),
            producer=data.get("producer", ""),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )

    @classmethod
    def from_json(cls, json_str: str) -> DomainEvent:
        """Deserialize from a JSON string."""
        return cls.from_dict(json.loads(json_str))
