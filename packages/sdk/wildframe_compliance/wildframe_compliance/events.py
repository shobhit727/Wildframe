"""Event schemas for compliance policy changes."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.policy import CompliancePolicy


class ComplianceEventType(str, Enum):
    """Types of compliance events."""

    POLICY_CREATED = "compliance.policy.created"
    POLICY_UPDATED = "compliance.policy.updated"
    POLICY_DELETED = "compliance.policy.deleted"
    POLICY_ACTIVATED = "compliance.policy.activated"
    POLICY_DEACTIVATED = "compliance.policy.deactivated"


@dataclass(frozen=True)
class CompliancePolicyEvent:
    """Event emitted when a compliance policy changes."""

    event_type: ComplianceEventType
    event_id: UUID
    timestamp: datetime
    jurisdiction: Jurisdiction
    policy_version: str
    policy_data: dict[str, Any]
    changed_fields: list[str] | None = None
    changed_by: str | None = None
    correlation_id: str | None = None

    @classmethod
    def from_policy(
        cls,
        event_type: ComplianceEventType,
        policy: CompliancePolicy,
        event_id: UUID,
        changed_fields: list[str] | None = None,
        changed_by: str | None = None,
        correlation_id: str | None = None,
    ) -> "CompliancePolicyEvent":
        """Create event from a policy instance."""
        return cls(
            event_type=event_type,
            event_id=event_id,
            timestamp=datetime.now(UTC),
            jurisdiction=policy.jurisdiction,
            policy_version=policy.version,
            policy_data=policy.model_dump(),
            changed_fields=changed_fields,
            changed_by=changed_by,
            correlation_id=correlation_id,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "event_type": self.event_type.value,
            "event_id": str(self.event_id),
            "timestamp": self.timestamp.isoformat(),
            "jurisdiction": self.jurisdiction.value,
            "policy_version": self.policy_version,
            "policy_data": self.policy_data,
            "changed_fields": self.changed_fields,
            "changed_by": self.changed_by,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CompliancePolicyEvent":
        """Create from dictionary."""
        return cls(
            event_type=ComplianceEventType(data["event_type"]),
            event_id=UUID(data["event_id"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            jurisdiction=Jurisdiction(data["jurisdiction"]),
            policy_version=data["policy_version"],
            policy_data=data["policy_data"],
            changed_fields=data.get("changed_fields"),
            changed_by=data.get("changed_by"),
            correlation_id=data.get("correlation_id"),
        )


# Event topic names
COMPLIANCE_POLICY_TOPIC = "compliance.policy.changed"
COMPLIANCE_POLICY_DLQ_TOPIC = "compliance.policy.changed.dlq"