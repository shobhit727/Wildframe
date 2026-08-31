"""Tests for compliance policies."""

import pytest

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.policy import (
    GDPRPolicy,
    EUAVMSPolicy,
    USPrivacyPolicy,
    CCPA_CPRAPolicy,
    IndiaDPDPPolicy,
    GlobalBaselinePolicy,
    get_policy_for_jurisdiction,
    get_all_policies,
)


class TestGDPRPolicy:
    """Tests for GDPR policy."""

    def test_gdpr_policy_defaults(self):
        policy = GDPRPolicy()
        assert policy.jurisdiction == Jurisdiction.EU
        assert policy.enabled is True
        assert policy.dpo_required is True
        assert policy.data_protection_impact_assessment is True
        assert policy.records_of_processing is True
        assert policy.adequacy_decision_required is True
        assert policy.scc_required is True
        assert policy.breach_notification_hours == 72
        assert policy.consent_minor_age == 16

    def test_gdpr_ott_requirements(self):
        policy = GDPRPolicy()
        assert policy.content_rating_required is True
        assert policy.age_verification_required is True
        assert policy.parental_controls_required is True
        assert policy.accessibility_required is True
        assert policy.advertising_restrictions is True

    def test_gdpr_regulations(self):
        policy = GDPRPolicy()
        regs = policy.get_applicable_regulations()
        assert "GDPR" in regs
        assert "ePrivacy" in regs
        assert "AVMS Directive" in regs
        assert "DSA" in regs
        assert "DMA" in regs


class TestEUAVMSPolicy:
    """Tests for EU AVMS policy."""

    def test_avms_policy_defaults(self):
        policy = EUAVMSPolicy()
        assert policy.jurisdiction == Jurisdiction.EU
        assert policy.content_rating_required is True
        assert policy.age_verification_required is True
        assert policy.parental_controls_required is True
        assert policy.accessibility_required is True
        assert policy.advertising_restrictions is True

    def test_avms_quotas(self):
        policy = EUAVMSPolicy()
        assert policy.european_works_quota == 0.30
        assert policy.independent_producer_quota == 0.10


class TestUSPrivacyPolicy:
    """Tests for US Federal privacy policy."""

    def test_us_policy_defaults(self):
        policy = USPrivacyPolicy()
        assert policy.jurisdiction == Jurisdiction.US
        assert policy.dpo_required is False
        assert policy.data_protection_impact_assessment is False
        assert policy.consent_minor_age == 13  # COPPA
        assert policy.breach_notification_hours == 720  # 30 days
        assert policy.adequacy_decision_required is False
        assert policy.scc_required is False


class TestCCPA_CPRAPolicy:
    """Tests for California CCPA/CPRA policy."""

    def test_ccpa_policy_defaults(self):
        policy = CCPA_CPRAPolicy()
        assert policy.jurisdiction == Jurisdiction.US_CA
        assert policy.data_subject_objection is True  # Right to opt-out of sale
        assert policy.sensitive_personal_information is True
        assert policy.limit_sensitive_pi_use is True
        assert policy.data_minimization is True
        assert policy.purpose_limitation is True
        assert policy.retention_disclosure_required is True
        assert policy.vendor_contracts_required is True


class TestIndiaDPDPPolicy:
    """Tests for India DPDP policy."""

    def test_dpdp_policy_defaults(self):
        policy = IndiaDPDPPolicy()
        assert policy.jurisdiction == Jurisdiction.IN
        assert policy.consent_manager_required is True
        assert policy.consent_minor_age == 18
        assert policy.verifiable_parental_consent is True
        assert policy.dpo_required is True
        assert policy.breach_notification_hours == 72
        assert policy.data_localization_required is True
        assert policy.grievance_officer_required is True
        assert policy.grievance_response_days == 30
        assert policy.central_govt_approval_required is True

    def test_dpdp_ott_requirements(self):
        policy = IndiaDPDPPolicy()
        assert policy.content_self_classification is True
        assert policy.grievance_mechanism_3_tier is True


class TestGlobalBaselinePolicy:
    """Tests for Global Baseline policy."""

    def test_global_policy_defaults(self):
        policy = GlobalBaselinePolicy()
        assert policy.jurisdiction == Jurisdiction.GLOBAL
        assert policy.encryption_at_rest is True
        assert policy.encryption_in_transit is True
        assert policy.breach_notification_hours == 72
        assert policy.records_of_processing is True
        assert policy.vendor_assessment is True
        assert policy.audit_log_required is True
        assert policy.default_retention_days == 2555
        assert policy.audit_log_retention_days == 2555


class TestPolicyRegistry:
    """Tests for policy registry functions."""

    def test_get_policy_for_jurisdiction(self):
        policy = get_policy_for_jurisdiction(Jurisdiction.EU)
        assert isinstance(policy, GDPRPolicy)

        policy = get_policy_for_jurisdiction(Jurisdiction.IN)
        assert isinstance(policy, IndiaDPDPPolicy)

        policy = get_policy_for_jurisdiction(Jurisdiction.GLOBAL)
        assert isinstance(policy, GlobalBaselinePolicy)

    def test_get_policy_with_overrides(self):
        policy = get_policy_for_jurisdiction(Jurisdiction.EU, dpo_required=False)
        assert policy.dpo_required is False

    def test_get_policy_hierarchical_merge(self):
        # US-CA should merge with US federal
        policy = get_policy_for_jurisdiction(Jurisdiction.US_CA)
        assert isinstance(policy, CCPA_CPRAPolicy)
        # Should have both CCPA and US federal characteristics
        assert policy.data_subject_objection is True  # CCPA
        assert policy.consent_minor_age == 13  # US federal COPPA (lower)

    def test_get_all_policies(self):
        policies = get_all_policies()
        assert Jurisdiction.EU in policies
        assert Jurisdiction.IN in policies
        assert Jurisdiction.GLOBAL in policies
        assert Jurisdiction.US in policies
        assert Jurisdiction.US_CA in policies

    def test_unknown_jurisdiction_fallbacks_to_global(self):
        # Create a mock unknown jurisdiction - using one not in registry
        policy = get_policy_for_jurisdiction(Jurisdiction.US_VA)
        assert isinstance(policy, USPrivacyPolicy)  # Falls back to US federal