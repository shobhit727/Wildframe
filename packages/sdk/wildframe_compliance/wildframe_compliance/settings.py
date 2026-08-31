"""Compliance settings mixin for service configurations."""

from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.policy import CompliancePolicy, get_policy_for_jurisdiction


class ComplianceSettingsMixin(BaseSettings):
    """Mixin to add compliance configuration to service settings.

    Usage:
        class Settings(ComplianceSettingsMixin):
            SERVICE_NAME: str = "my-service"
            # ... other settings
    """

    model_config = SettingsConfigDict(
        env_prefix="COMPLIANCE_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # Jurisdiction configuration
    compliance_jurisdiction: Jurisdiction = Field(
        default=Jurisdiction.GLOBAL,
        description="Primary jurisdiction for this service deployment",
    )

    # Additional jurisdictions this service must comply with
    compliance_additional_jurisdictions: list[Jurisdiction] = Field(
        default_factory=list,
        description="Additional jurisdictions to enforce",
    )

    # Policy overrides (environment-specific)
    compliance_policy_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="Runtime policy field overrides",
    )

    # Enforcement settings
    compliance_strict_mode: bool = Field(
        default=True,
        description="Fail requests that violate policy (vs. log only)",
    )

    compliance_audit_enabled: bool = Field(
        default=True,
        description="Enable compliance audit logging",
    )

    # Data residency
    compliance_data_residency_required: bool = Field(
        default=False,
        description="Require data to stay within jurisdiction",
    )

    compliance_allowed_data_regions: list[str] = Field(
        default_factory=list,
        description="Allowed data storage regions",
    )

    # Cross-border transfers
    compliance_transfer_mechanism: str = Field(
        default="scc",
        description="Transfer mechanism: scc, bcr, adequacy, consent",
    )

    # DPO contact (for GDPR)
    compliance_dpo_email: str | None = Field(
        default=None,
        description="Data Protection Officer contact email",
    )

    # Grievance officer (for India DPDP)
    compliance_grievance_officer_email: str | None = Field(
        default=None,
        description="Grievance officer contact email",
    )

    def get_compliance_policy(self) -> CompliancePolicy:
        """Get the effective compliance policy for this service.

        Merges primary jurisdiction policy with additional jurisdictions
        and applies any runtime overrides.
        """
        policy = get_policy_for_jurisdiction(
            self.compliance_jurisdiction,
            **self.compliance_policy_overrides,
        )

        # Merge additional jurisdictions (most restrictive wins)
        for additional in self.compliance_additional_jurisdictions:
            additional_policy = get_policy_for_jurisdiction(additional)
            policy = self._merge_policies(policy, additional_policy)

        return policy

    def _merge_policies(
        self, primary: CompliancePolicy, additional: CompliancePolicy
    ) -> CompliancePolicy:
        """Merge two policies, taking the most restrictive settings."""
        primary_dict = primary.model_dump()
        additional_dict = additional.model_dump()

        # For boolean fields, True is more restrictive
        # For numeric fields, lower/higher depends on context
        merged = {}
        for key in primary_dict:
            if key in additional_dict:
                primary_val = primary_dict[key]
                additional_val = additional_dict[key]

                if isinstance(primary_val, bool) and isinstance(additional_val, bool):
                    merged[key] = primary_val or additional_val  # True = more restrictive
                elif isinstance(primary_val, int) and isinstance(additional_val, int):
                    # For age, retention, etc. - more restrictive varies
                    if "age" in key.lower() or "minor" in key.lower():
                        merged[key] = max(primary_val, additional_val)  # Higher age = more restrictive
                    elif "retention" in key.lower() or "hours" in key.lower():
                        merged[key] = min(primary_val, additional_val)  # Shorter = more restrictive
                    else:
                        merged[key] = primary_val
                else:
                    merged[key] = primary_val
            else:
                merged[key] = primary_dict[key]

        return type(primary)(**merged)

    def is_compliant(self, jurisdiction: Jurisdiction | None = None) -> bool:
        """Check if current configuration meets policy requirements."""
        policy = self.get_compliance_policy()

        # Check required settings are present
        if policy.dpo_required and not self.compliance_dpo_email:
            return False
        if policy.grievance_officer_required and not self.compliance_grievance_officer_email:
            return False
        if policy.data_residency_required and not self.compliance_allowed_data_regions:
            return False
            return False

        return True

    def get_compliance_summary(self) -> dict[str, Any]:
        """Get a summary of compliance configuration for health checks."""
        policy = self.get_compliance_policy()
        return {
            "primary_jurisdiction": self.compliance_jurisdiction.value,
            "additional_jurisdictions": [j.value for j in self.compliance_additional_jurisdictions],
            "policy_version": policy.version,
            "enabled": policy.enabled,
            "strict_mode": self.compliance_strict_mode,
            "audit_enabled": self.compliance_audit_enabled,
            "compliant": self.is_compliant(),
            "requirements": {
                "dpo_required": policy.dpo_required,
                "dpo_configured": bool(self.compliance_dpo_email),
                "grievance_officer_required": getattr(policy, "grievance_officer_required", False),
                "grievance_officer_configured": bool(self.compliance_grievance_officer_email),
                "data_residency_required": policy.data_residency_required,
                "data_residency_configured": bool(self.compliance_allowed_data_regions),
                "breach_notification_hours": policy.breach_notification_hours,
                "consent_minor_age": policy.consent_minor_age,
            },
        }