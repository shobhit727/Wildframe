"""Tests for compliance events module."""

from uuid import uuid4

from wildframe_compliance.events import (
    ComplianceEventType,
    CompliancePolicyEvent,
    COMPLIANCE_POLICY_TOPIC,
    COMPLIANCE_POLICY_DLQ_TOPIC,
)
from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.policy import GDPRPolicy


class TestComplianceEventType:
    """Tests for ComplianceEventType enum."""

    def test_event_types(self):
        assert ComplianceEventType.POLICY_CREATED.value == "compliance.policy.created"
        assert ComplianceEventType.POLICY_UPDATED.value == "compliance.policy.updated"
        assert ComplianceEventType.POLICY_DELETED.value == "compliance.policy.deleted"
        assert ComplianceEventType.POLICY_ACTIVATED.value == "compliance.policy.activated"
        assert ComplianceEventType.POLICY_DEACTIVATED.value == "compliance.policy.deactivated"


class TestCompliancePolicyEvent:
    """Tests for CompliancePolicyEvent."""

    def test_create_from_policy(self):
        policy = GDPRPolicy()
        event_id = uuid4()
        event = CompliancePolicyEvent.from_policy(
            event_type=ComplianceEventType.POLICY_CREATED,
            policy=policy,
            event_id=event_id,
            changed_fields=["dpo_required"],
            changed_by="admin",
            correlation_id="corr-123",
        )

        assert event.event_type == ComplianceEventType.POLICY_CREATED
        assert event.event_id == event_id
        assert event.jurisdiction == Jurisdiction.EU
        assert event.policy_version == "1.0.0"
        assert event.changed_fields == ["dpo_required"]
        assert event.changed_by == "admin"
        assert event.correlation_id == "corr-123"

    def test_to_dict(self):
        policy = GDPRPolicy()
        event_id = uuid4()
        event = CompliancePolicyEvent.from_policy(
            event_type=ComplianceEventType.POLICY_UPDATED,
            policy=policy,
            event_id=event_id,
        )

        event_dict = event.to_dict()

        assert event_dict["event_type"] == "compliance.policy.updated"
        assert event_dict["event_id"] == str(event_id)
        assert event_dict["jurisdiction"] == "EU"
        assert event_dict["policy_version"] == "1.0.0"
        assert "policy_data" in event_dict
        assert event_dict["policy_data"]["jurisdiction"] == "EU"

    def test_from_dict(self):
        policy = GDPRPolicy()
        event_id = uuid4()
        original_event = CompliancePolicyEvent.from_policy(
            event_type=ComplianceEventType.POLICY_DELETED,
            policy=policy,
            event_id=event_id,
            changed_by="admin",
            correlation_id="corr-456",
        )

        event_dict = original_event.to_dict()
        restored_event = CompliancePolicyEvent.from_dict(event_dict)

        assert restored_event.event_type == ComplianceEventType.POLICY_DELETED
        assert restored_event.event_id == event_id
        assert restored_event.jurisdiction == Jurisdiction.EU
        assert restored_event.changed_by == "admin"
        assert restored_event.correlation_id == "corr-456"

    def test_event_serialization_roundtrip(self):
        policy = GDPRPolicy()
        event_id = uuid4()
        original = CompliancePolicyEvent.from_policy(
            event_type=ComplianceEventType.POLICY_ACTIVATED,
            policy=policy,
            event_id=event_id,
            changed_fields=["enabled"],
            changed_by="system",
        )

        # Serialize to dict
        event_dict = original.to_dict()

        # Deserialize back
        restored = CompliancePolicyEvent.from_dict(event_dict)

        assert restored.event_type == original.event_type
        assert restored.event_id == original.event_id
        assert restored.jurisdiction == original.jurisdiction
        assert restored.policy_version == original.policy_version
        assert restored.changed_fields == original.changed_fields
        assert restored.changed_by == original.changed_by


class TestEventTopics:
    """Tests for event topic constants."""

    def test_topic_names(self):
        assert COMPLIANCE_POLICY_TOPIC == "compliance.policy.changed"
        assert COMPLIANCE_POLICY_DLQ_TOPIC == "compliance.policy.changed.dlq"
