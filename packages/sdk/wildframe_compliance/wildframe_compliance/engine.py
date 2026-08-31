"""Policy evaluation engine for runtime compliance checks."""

from dataclasses import dataclass
from typing import Any

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.policy import CompliancePolicy, get_policy_for_jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin


@dataclass(frozen=True)
class PolicyDecision:
    """Result of a policy evaluation."""

    allowed: bool
    jurisdiction: Jurisdiction
    policy_version: str
    reason: str | None = None
    required_actions: list[str] = None
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.required_actions is None:
            object.__setattr__(self, "required_actions", [])
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})


@dataclass(frozen=True)
class ConsentDecision(PolicyDecision):
    """Decision for consent-related operations."""

    consent_valid: bool = False
    consent_requirements: list[str] = None

    def __post_init__(self):
        super().__post_init__()
        if self.consent_requirements is None:
            object.__setattr__(self, "consent_requirements", [])


@dataclass(frozen=True)
class DataSubjectDecision(PolicyDecision):
    """Decision for data subject rights operations."""

    right_type: str = ""
    can_execute: bool = False
    retention_check_passed: bool = True

    def __post_init__(self):
        super().__post_init__()


@dataclass(frozen=True)
class TransferDecision(PolicyDecision):
    """Decision for cross-border data transfers."""

    transfer_mechanism: str = ""
    adequacy_status: bool = False

    def __post_init__(self):
        super().__post_init__()


class PolicyEngine:
    """Engine for evaluating compliance policies at runtime.

    Provides methods to check if operations are compliant with
    the configured jurisdiction policies.
    """

    def __init__(self, settings: ComplianceSettingsMixin):
        self.settings = settings
        self._policy_cache: dict[Jurisdiction, CompliancePolicy] = {}

    def _get_policy(self, jurisdiction: Jurisdiction) -> CompliancePolicy:
        """Get cached policy or fetch and cache."""
        if jurisdiction not in self._policy_cache:
            self._policy_cache[jurisdiction] = get_policy_for_jurisdiction(jurisdiction)
        return self._policy_cache[jurisdiction]

    def evaluate_consent(
        self,
        user_age: int | None,
        consent_given: bool,
        consent_granular: bool,
        consent_withdrawable: bool,
        sensitive_data: bool = False,
        jurisdiction: Jurisdiction | None = None,
    ) -> ConsentDecision:
        """Evaluate if consent is valid for data processing."""
        jurisdiction = jurisdiction or self.settings.compliance_jurisdiction
        policy = self._get_policy(jurisdiction)

        required_actions = []
        reasons = []

        # Check consent given
        if not consent_given:
            if policy.consent_required:
                return ConsentDecision(
                    allowed=False,
                    jurisdiction=jurisdiction,
                    policy_version=policy.version,
                    reason="Consent required but not given",
                    required_actions=["obtain_consent"],
                    consent_valid=False,
                    consent_requirements=["Consent must be freely given"],
                )

        # Check age
        if user_age is not None and user_age < policy.consent_minor_age:
            if (
                policy.verifiable_parental_consent
                if hasattr(policy, "verifiable_parental_consent")
                else False
            ):
                required_actions.append("obtain_verifiable_parental_consent")
                reasons.append(
                    f"User is {user_age}, below age of consent ({policy.consent_minor_age})"
                )
            else:
                required_actions.append("obtain_parental_consent")
                reasons.append(
                    f"User is {user_age}, below age of consent ({policy.consent_minor_age})"
                )

        # Check granularity
        if policy.consent_granular and not consent_granular:
            required_actions.append("provide_granular_consent_options")
            reasons.append("Granular consent required")

        # Check withdrawability
        if policy.consent_withdrawable and not consent_withdrawable:
            required_actions.append("implement_consent_withdrawal")
            reasons.append("Consent must be withdrawable")

        # Sensitive data extra checks
        if sensitive_data:
            if jurisdiction == Jurisdiction.US_CA:
                required_actions.append("provide_sensitive_pi_opt_out")
                reasons.append("CCPA/CPRA requires opt-out for sensitive PI")
            if jurisdiction == Jurisdiction.IN:
                required_actions.append("explicit_consent_for_sensitive")
                reasons.append("DPDP requires explicit consent for sensitive data")

        allowed = len(required_actions) == 0

        return ConsentDecision(
            allowed=allowed,
            jurisdiction=jurisdiction,
            policy_version=policy.version,
            reason="; ".join(reasons) if reasons else "Consent valid",
            required_actions=required_actions,
            consent_valid=allowed,
            consent_requirements=required_actions if not allowed else [],
            metadata={
                "user_age": user_age,
                "consent_given": consent_given,
                "consent_granular": consent_granular,
                "consent_withdrawable": consent_withdrawable,
                "sensitive_data": sensitive_data,
            },
        )

    def evaluate_data_subject_right(
        self,
        right_type: str,
        user_id: str,
        data_categories: list[str],
        retention_days: int | None,
        jurisdiction: Jurisdiction | None = None,
    ) -> DataSubjectDecision:
        """Evaluate if a data subject right request can be executed."""
        jurisdiction = jurisdiction or self.settings.compliance_jurisdiction
        policy = self._get_policy(jurisdiction)

        required_actions = []
        reasons = []

        # Map right types to policy fields
        right_mapping = {
            "access": "data_subject_access",
            "rectification": "data_subject_rectification",
            "erasure": "data_subject_erasure",
            "portability": "data_subject_portability",
            "restriction": "data_subject_restriction",
            "objection": "data_subject_objection",
            "automated_decision_opt_out": "automated_decision_opt_out",
        }

        policy_field = right_mapping.get(right_type)
        if policy_field is None:
            return DataSubjectDecision(
                allowed=False,
                jurisdiction=jurisdiction,
                policy_version=policy.version,
                reason=f"Right '{right_type}' not recognized in {jurisdiction.value}",
                required_actions=[f"implement_{right_type}_right"],
                right_type=right_type,
                can_execute=False,
            )

        if not getattr(policy, policy_field, False):
            return DataSubjectDecision(
                allowed=False,
                jurisdiction=jurisdiction,
                policy_version=policy.version,
                reason=f"Right '{right_type}' not enabled in {jurisdiction.value} policy",
                required_actions=[f"enable_{right_type}_right"],
                right_type=right_type,
                can_execute=False,
            )

        # Check retention - use max_retention_days if set, otherwise default_retention_days
        retention_check_passed = True
        max_retention = policy.max_retention_days or policy.default_retention_days
        if retention_days is not None and retention_days > max_retention:
            retention_check_passed = False
            required_actions.append("reduce_retention_period")
            reasons.append(
                f"Retention ({retention_days} days) exceeds maximum ({max_retention} days)"
            )

        can_execute = len(required_actions) == 0 and retention_check_passed

        return DataSubjectDecision(
            allowed=can_execute,
            jurisdiction=jurisdiction,
            policy_version=policy.version,
            reason="; ".join(reasons) if reasons else "Right can be exercised",
            required_actions=required_actions,
            right_type=right_type,
            can_execute=can_execute,
            retention_check_passed=retention_check_passed,
            metadata={
                "user_id": user_id,
                "data_categories": data_categories,
                "retention_days": retention_days,
            },
        )

    def evaluate_transfer(
        self,
        source_jurisdiction: Jurisdiction | str,
        target_jurisdiction: Jurisdiction | str,
        transfer_mechanism: str | None = None,
        data_categories: list[str] | None = None,
    ) -> TransferDecision:
        """Evaluate if cross-border data transfer is compliant."""
        if isinstance(source_jurisdiction, str):
            source_jurisdiction = Jurisdiction(source_jurisdiction)
        if isinstance(target_jurisdiction, str):
            target_jurisdiction = Jurisdiction(target_jurisdiction)
        source_policy = self._get_policy(source_jurisdiction)
        _ = self._get_policy(target_jurisdiction)  # validated but not used in current logic

        required_actions = []
        reasons = []

        # Determine mechanism
        mechanism = transfer_mechanism or self.settings.compliance_transfer_mechanism

        # Check adequacy decision
        adequacy_status = False
        if source_policy.adequacy_decision_required:
            # Simplified: would check actual adequacy decisions in production
            adequacy_status = target_jurisdiction in [
                Jurisdiction.CA,  # Canada has adequacy
                Jurisdiction.JP,  # Japan has adequacy
                # Add more as needed
            ]
            if not adequacy_status:
                # Only require SCC/BCR if mechanism doesn't provide one
                if mechanism not in ("scc", "bcr"):
                    required_actions.append("implement_scc_or_bcr")
                    reasons.append(f"No adequacy decision for {target_jurisdiction.value}")

        # Check SCC requirement (only if no adequacy decision and mechanism is not SCC)
        if source_policy.scc_required and mechanism != "scc" and not adequacy_status:
            required_actions.append("implement_standard_contractual_clauses")
            reasons.append("Standard Contractual Clauses required")

        # Check BCR
        if source_policy.binding_corporate_rules and mechanism != "bcr":
            required_actions.append("implement_binding_corporate_rules")
            reasons.append("Binding Corporate Rules required")

        # India DPDP: Central government approval
        if source_jurisdiction == Jurisdiction.IN:
            if (
                hasattr(source_policy, "central_govt_approval_required")
                and source_policy.central_govt_approval_required
            ):
                required_actions.append("obtain_central_govt_approval")
                reasons.append(
                    "DPDP requires central government approval for cross-border transfers"
                )

        allowed = len(required_actions) == 0

        return TransferDecision(
            allowed=allowed,
            jurisdiction=source_jurisdiction,
            policy_version=source_policy.version,
            reason="; ".join(reasons) if reasons else "Transfer compliant",
            required_actions=required_actions,
            transfer_mechanism=mechanism,
            adequacy_status=adequacy_status,
            metadata={
                "source_jurisdiction": source_jurisdiction.value,
                "target_jurisdiction": target_jurisdiction.value,
                "data_categories": data_categories,
            },
        )

    def evaluate_retention(
        self,
        data_category: str,
        current_retention_days: int,
        jurisdiction: Jurisdiction | None = None,
    ) -> PolicyDecision:
        """Evaluate if data retention period is compliant."""
        jurisdiction = jurisdiction or self.settings.compliance_jurisdiction
        policy = self.settings.get_compliance_policy()

        required_actions = []
        reasons = []

        if current_retention_days > policy.default_retention_days:
            required_actions.append("reduce_retention_period")
            reasons.append(
                f"Retention ({current_retention_days} days) exceeds default ({policy.default_retention_days} days)"
            )

        if policy.max_retention_days and current_retention_days > policy.max_retention_days:
            required_actions.append("reduce_retention_to_maximum")
            reasons.append(
                f"Retention ({current_retention_days} days) exceeds maximum ({policy.max_retention_days} days)"
            )

        allowed = len(required_actions) == 0

        return PolicyDecision(
            allowed=allowed,
            jurisdiction=jurisdiction,
            policy_version=policy.version,
            reason="; ".join(reasons) if reasons else "Retention compliant",
            required_actions=required_actions,
            metadata={
                "data_category": data_category,
                "current_retention_days": current_retention_days,
                "default_retention_days": policy.default_retention_days,
                "max_retention_days": policy.max_retention_days,
            },
        )

    def evaluate_security(
        self,
        encryption_at_rest: bool,
        encryption_in_transit: bool,
        pseudonymization: bool,
        jurisdiction: Jurisdiction | None = None,
    ) -> PolicyDecision:
        """Evaluate if security measures meet policy requirements."""
        jurisdiction = jurisdiction or self.settings.compliance_jurisdiction
        policy = self.settings.get_compliance_policy()

        required_actions = []
        reasons = []

        if policy.encryption_at_rest and not encryption_at_rest:
            required_actions.append("enable_encryption_at_rest")
            reasons.append("Encryption at rest required")

        if policy.encryption_in_transit and not encryption_in_transit:
            required_actions.append("enable_encryption_in_transit")
            reasons.append("Encryption in transit required")

        if policy.pseudonymization and not pseudonymization:
            required_actions.append("implement_pseudonymization")
            reasons.append("Pseudonymization required")

        allowed = len(required_actions) == 0

        return PolicyDecision(
            allowed=allowed,
            jurisdiction=jurisdiction,
            policy_version=policy.version,
            reason="; ".join(reasons) if reasons else "Security compliant",
            required_actions=required_actions,
            metadata={
                "encryption_at_rest": encryption_at_rest,
                "encryption_in_transit": encryption_in_transit,
                "pseudonymization": pseudonymization,
            },
        )

    def get_applicable_requirements(
        self, jurisdiction: Jurisdiction | None = None
    ) -> dict[str, Any]:
        """Get all applicable compliance requirements for a jurisdiction."""
        jurisdiction = jurisdiction or self.settings.compliance_jurisdiction
        policy = self._get_policy(jurisdiction)

        return {
            "jurisdiction": jurisdiction.value,
            "regulations": policy.get_applicable_regulations(),
            "data_subject_rights": {
                "access": policy.data_subject_access,
                "rectification": policy.data_subject_rectification,
                "erasure": policy.data_subject_erasure,
                "portability": policy.data_subject_portability,
                "restriction": policy.data_subject_restriction,
                "objection": policy.data_subject_objection,
                "automated_decision_opt_out": policy.automated_decision_opt_out,
            },
            "consent": {
                "required": policy.consent_required,
                "granular": policy.consent_granular,
                "withdrawable": policy.consent_withdrawable,
                "minor_age": policy.consent_minor_age,
            },
            "security": {
                "encryption_at_rest": policy.encryption_at_rest,
                "encryption_in_transit": policy.encryption_in_transit,
                "pseudonymization": policy.pseudonymization,
                "breach_notification_hours": policy.breach_notification_hours,
            },
            "accountability": {
                "dpo_required": policy.dpo_required,
                "dpi_required": policy.data_protection_impact_assessment,
                "records_of_processing": policy.records_of_processing,
                "vendor_assessment": policy.vendor_assessment,
            },
            "transfers": {
                "adequacy_required": policy.adequacy_decision_required,
                "scc_required": policy.scc_required,
                "bcr_available": policy.binding_corporate_rules,
            },
            "retention": {
                "default_days": policy.default_retention_days,
                "max_days": policy.max_retention_days,
            },
            "audit": {
                "required": policy.audit_log_required,
                "retention_days": policy.audit_log_retention_days,
            },
        }


def evaluate_policy(
    settings: ComplianceSettingsMixin,
    operation: str,
    **kwargs: Any,
) -> PolicyDecision:
    """Convenience function to evaluate a policy operation.

    Args:
        settings: Service compliance settings
        operation: Operation type (consent, data_subject_right, transfer, retention, security)
        **kwargs: Operation-specific parameters

    Returns:
        PolicyDecision with allowed/denied status and details
    """
    engine = PolicyEngine(settings)

    operations = {
        "consent": engine.evaluate_consent,
        "data_subject_right": engine.evaluate_data_subject_right,
        "transfer": engine.evaluate_transfer,
        "retention": engine.evaluate_retention,
        "security": engine.evaluate_security,
    }

    if operation not in operations:
        raise ValueError(f"Unknown operation: {operation}")

    return operations[operation](**kwargs)
