"""wildframe-compliance: Jurisdiction-aware compliance and policy configuration."""

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.policy import (
    CompliancePolicy,
    GDPRPolicy,
    USPrivacyPolicy,
    IndiaDPDPPolicy,
    EUAVMSPolicy,
    GlobalBaselinePolicy,
    get_policy_for_jurisdiction,
)
from wildframe_compliance.settings import ComplianceSettingsMixin
from wildframe_compliance.engine import PolicyEngine, evaluate_policy
from wildframe_compliance.events import (
    ComplianceEventType,
    CompliancePolicyEvent,
    COMPLIANCE_POLICY_TOPIC,
    COMPLIANCE_POLICY_DLQ_TOPIC,
)
from wildframe_compliance.consumer import (
    PolicyChangeConsumer,
    create_policy_change_consumer,
)
from wildframe_compliance.producer import (
    PolicyChangeProducer,
    create_policy_change_producer,
)
from wildframe_compliance.observability import (
    ComplianceMetrics,
    ComplianceLogger,
    compliance_metrics,
    compliance_health_check,
    COMPLIANCE_EVALUATIONS,
    COMPLIANCE_EVALUATION_DURATION,
    COMPLIANCE_POLICY_VERSION,
    COMPLIANCE_VIOLATIONS,
    COMPLIANCE_EVENTS_PUBLISHED,
    COMPLIANCE_EVENTS_CONSUMED,
    COMPLIANCE_HEALTH,
)

__all__ = [
    "Jurisdiction",
    "CompliancePolicy",
    "GDPRPolicy",
    "USPrivacyPolicy",
    "IndiaDPDPPolicy",
    "EUAVMSPolicy",
    "GlobalBaselinePolicy",
    "get_policy_for_jurisdiction",
    "ComplianceSettingsMixin",
    "PolicyEngine",
    "evaluate_policy",
    "ComplianceEventType",
    "CompliancePolicyEvent",
    "COMPLIANCE_POLICY_TOPIC",
    "COMPLIANCE_POLICY_DLQ_TOPIC",
    "PolicyChangeConsumer",
    "create_policy_change_consumer",
    "PolicyChangeProducer",
    "create_policy_change_producer",
    "ComplianceMetrics",
    "ComplianceLogger",
    "compliance_metrics",
    "compliance_health_check",
    "COMPLIANCE_EVALUATIONS",
    "COMPLIANCE_EVALUATION_DURATION",
    "COMPLIANCE_POLICY_VERSION",
    "COMPLIANCE_VIOLATIONS",
    "COMPLIANCE_EVENTS_PUBLISHED",
    "COMPLIANCE_EVENTS_CONSUMED",
    "COMPLIANCE_HEALTH",
]

__version__ = "1.0.0"