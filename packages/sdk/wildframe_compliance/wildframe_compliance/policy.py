"""Compliance policy definitions for each jurisdiction."""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from wildframe_compliance.jurisdiction import Jurisdiction


class CompliancePolicy(BaseModel, ABC):
    """Base compliance policy model.

    Each jurisdiction implements its specific requirements by extending this base.
    """

    jurisdiction: Jurisdiction
    version: str = "1.0.0"
    enabled: bool = True

    # Data subject rights
    data_subject_access: bool = True
    data_subject_rectification: bool = True
    data_subject_erasure: bool = True
    data_subject_portability: bool = True
    data_subject_restriction: bool = True
    data_subject_objection: bool = True
    automated_decision_opt_out: bool = True

    # Consent management
    consent_required: bool = True
    consent_granular: bool = True
    consent_withdrawable: bool = True
    consent_minor_age: int = 16

    # Data processing
    lawful_basis_required: bool = True
    purpose_limitation: bool = True
    data_minimization: bool = True
    storage_limitation: bool = True
    accuracy_required: bool = True
    integrity_confidentiality: bool = True

    # Security
    encryption_at_rest: bool = True
    encryption_in_transit: bool = True
    pseudonymization: bool = False
    breach_notification_hours: int = 72

    # Accountability
    dpo_required: bool = False
    grievance_officer_required: bool = False
    dpi_required: bool = False
    records_of_processing: bool = True
    data_protection_impact_assessment: bool = False
    vendor_assessment: bool = True

    # International transfers
    adequacy_decision_required: bool = False
    scc_required: bool = False
    binding_corporate_rules: bool = False

    # Data residency
    data_residency_required: bool = False

    # OTT/Content specific
    content_rating_required: bool = False
    age_verification_required: bool = False
    parental_controls_required: bool = False
    accessibility_required: bool = False
    advertising_restrictions: bool = False

    # Retention
    default_retention_days: int = 2555  # 7 years
    max_retention_days: int | None = None

    # Audit
    audit_log_required: bool = True
    audit_log_retention_days: int = 2555

    class Config:
        use_enum_values = True

    @abstractmethod
    def get_applicable_regulations(self) -> list[str]:
        """Return list of regulations this policy implements."""
        ...


class GDPRPolicy(CompliancePolicy):
    """GDPR (EU 2016/679) compliance policy."""

    jurisdiction: Jurisdiction = Jurisdiction.EU

    # GDPR-specific: DPO required for public authorities or large-scale processing
    dpo_required: bool = True

    # GDPR-specific: DPIA required for high-risk processing
    data_protection_impact_assessment: bool = True

    # GDPR-specific: Records of processing activities (Article 30)
    records_of_processing: bool = True

    # GDPR-specific: International transfers
    adequacy_decision_required: bool = True
    scc_required: bool = True

    # GDPR-specific: 72-hour breach notification
    breach_notification_hours: int = 72

    # GDPR-specific: Right to be forgotten
    data_subject_erasure: bool = True

    # GDPR-specific: Data portability (Article 20)
    data_subject_portability: bool = True

    # GDPR-specific: Age of consent (can be lowered to 13 by member states)
    consent_minor_age: int = 16

    # OTT-specific: AVMS Directive requirements
    content_rating_required: bool = True
    age_verification_required: bool = True
    parental_controls_required: bool = True
    accessibility_required: bool = True
    advertising_restrictions: bool = True

    def get_applicable_regulations(self) -> list[str]:
        return self.jurisdiction.regulations


class EUAVMSPolicy(CompliancePolicy):
    """EU Audiovisual Media Services Directive compliance policy."""

    jurisdiction: Jurisdiction = Jurisdiction.EU

    # AVMS-specific: Content regulation
    content_rating_required: bool = True
    age_verification_required: bool = True
    parental_controls_required: bool = True

    # AVMS-specific: Accessibility (European Accessibility Act)
    accessibility_required: bool = True

    # AVMS-specific: Advertising restrictions (protection of minors)
    advertising_restrictions: bool = True

    # AVMS-specific: European works promotion
    european_works_quota: float = 0.30
    independent_producer_quota: float = 0.10

    def get_applicable_regulations(self) -> list[str]:
        return ["AVMS Directive", "European Accessibility Act"]


class USPrivacyPolicy(CompliancePolicy):
    """US Federal privacy baseline (FTC, COPPA, HIPAA, GLBA)."""

    jurisdiction: Jurisdiction = Jurisdiction.US

    # US-specific: Sectoral approach, no comprehensive federal law
    dpo_required: bool = False
    data_protection_impact_assessment: bool = False

    # COPPA: Children under 13
    consent_minor_age: int = 13

    # Breach notification varies by state (typically 30-60 days)
    breach_notification_hours: int = 720  # 30 days

    # No adequacy decisions / SCCs at federal level
    adequacy_decision_required: bool = False
    scc_required: bool = False

    def get_applicable_regulations(self) -> list[str]:
        return self.jurisdiction.regulations


class CCPA_CPRAPolicy(CompliancePolicy):
    """California CCPA/CPRA compliance policy."""

    jurisdiction: Jurisdiction = Jurisdiction.US_CA

    # CCPA/CPRA: Sale opt-out, sensitive personal information
    data_subject_objection: bool = True  # Right to opt-out of sale
    sensitive_personal_information: bool = True

    # CPRA: Right to limit use of sensitive PI
    limit_sensitive_pi_use: bool = True

    # CPRA: Data minimization and purpose limitation
    data_minimization: bool = True
    purpose_limitation: bool = True

    # CPRA: Retention disclosure
    retention_disclosure_required: bool = True

    # CPRA: Contractual requirements for service providers
    vendor_contracts_required: bool = True

    def get_applicable_regulations(self) -> list[str]:
        return self.jurisdiction.regulations


class IndiaDPDPPolicy(CompliancePolicy):
    """India Digital Personal Data Protection Act 2023 compliance policy."""

    jurisdiction: Jurisdiction = Jurisdiction.IN

    # DPDP: Consent manager framework
    consent_manager_required: bool = True

    # DPDP: Significant Data Fiduciary obligations
    significant_fiduciary: bool = False  # Set based on volume/sensitivity

    # DPDP: Child data (under 18) - verifiable parental consent
    consent_minor_age: int = 18
    verifiable_parental_consent: bool = True

    # DPDP: Data Protection Officer
    dpo_required: bool = True  # For significant fiduciaries

    # DPDP: Data breach notification
    breach_notification_hours: int = 72

    # DPDP: Data localization - certain data must stay in India
    data_localization_required: bool = True
    data_residency_required: bool = True

    # DPDP: Grievance redressal mechanism
    grievance_officer_required: bool = True
    grievance_response_days: int = 30

    # DPDP: Cross-border transfers
    central_govt_approval_required: bool = True

    # OTT-specific: India OTT publisher compliance
    content_self_classification: bool = True
    grievance_mechanism_3_tier: bool = True

    def get_applicable_regulations(self) -> list[str]:
        return self.jurisdiction.regulations


class GlobalBaselinePolicy(CompliancePolicy):
    """Global baseline policy - minimum standards applied everywhere."""

    jurisdiction: Jurisdiction = Jurisdiction.GLOBAL

    # Baseline security standards
    encryption_at_rest: bool = True
    encryption_in_transit: bool = True
    breach_notification_hours: int = 72

    # Baseline accountability
    records_of_processing: bool = True
    vendor_assessment: bool = True
    audit_log_required: bool = True

    # Baseline retention
    default_retention_days: int = 2555
    audit_log_retention_days: int = 2555

    def get_applicable_regulations(self) -> list[str]:
        return self.jurisdiction.regulations


# Policy registry
_POLICY_REGISTRY: dict[Jurisdiction, type[CompliancePolicy]] = {
    Jurisdiction.EU: GDPRPolicy,
    Jurisdiction.IN: IndiaDPDPPolicy,
    Jurisdiction.GLOBAL: GlobalBaselinePolicy,
    Jurisdiction.US: USPrivacyPolicy,
    Jurisdiction.US_CA: CCPA_CPRAPolicy,
    # Add more as needed
}


def get_policy_for_jurisdiction(jurisdiction: Jurisdiction, **overrides: Any) -> CompliancePolicy:
    """Get the appropriate policy for a jurisdiction.

    Args:
        jurisdiction: The jurisdiction to get policy for
        **overrides: Optional field overrides

    Returns:
        CompliancePolicy instance for the jurisdiction
    """
    # Check if jurisdiction or its parent is in registry
    policy_class = _POLICY_REGISTRY.get(jurisdiction)
    if policy_class is None:
        parent = jurisdiction.parent
        if parent and parent in _POLICY_REGISTRY:
            policy_class = _POLICY_REGISTRY[parent]
        else:
            policy_class = GlobalBaselinePolicy

    policy = policy_class(**overrides)

    # Apply parent jurisdiction policies if applicable (hierarchical)
    parent = jurisdiction.parent
    if parent and parent in _POLICY_REGISTRY:
        parent_policy = _POLICY_REGISTRY[parent]()
        # Merge: child overrides parent
        parent_dict = parent_policy.model_dump()
        parent_dict.update(policy.model_dump(exclude_unset=True))
        policy = policy_class(**parent_dict)

    return policy


def get_all_policies(**overrides: Any) -> dict[Jurisdiction, CompliancePolicy]:
    """Get all registered policies with optional overrides."""
    return {j: get_policy_for_jurisdiction(j, **overrides) for j in _POLICY_REGISTRY}
