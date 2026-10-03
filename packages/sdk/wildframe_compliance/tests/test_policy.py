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

from wildframe_compliance.policy import _POLICY_REGISTRY


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


# ---------------------------------------------------------------------------
# `get_applicable_regulations` per subclass + the registry's parent/global
# fallback. Both are the resolution path a service takes at startup, so a
# jurisdiction that silently resolves to the wrong framework is a compliance
# incident.
# ---------------------------------------------------------------------------


class TestApplicableRegulations:
    def test_policies_constructed_directly_report_regulations(self):
        """Every concrete policy class must satisfy the ABC contract when
        constructed the ordinary way."""
        for cls in (
            GDPRPolicy,
            EUAVMSPolicy,
            USPrivacyPolicy,
            CCPA_CPRAPolicy,
            IndiaDPDPPolicy,
            GlobalBaselinePolicy,
        ):
            regs = cls().get_applicable_regulations()
            assert isinstance(regs, list) and regs, cls
            assert all(isinstance(r, str) and r for r in regs), cls

    def test_known_bug_resolved_policies_with_a_registered_parent_crash(self):
        """BUG (policy.py:299-307): the parent-merge rebuilds the policy from
        ``parent_policy.model_dump()``. In python mode ``model_dump()``
        serialises the ``Jurisdiction`` str-Enum to a plain ``str``, and
        pydantic does NOT re-coerce it back to the enum on re-construction.
        The result is that for EVERY jurisdiction whose parent is registered
        (all 16 US states + CA-QC) the resolved policy's ``jurisdiction`` is a
        plain ``str``, so ``get_applicable_regulations()`` — which does
        ``self.jurisdiction.regulations`` — raises AttributeError.

        Pinned as-is; this is a production bug, not a test bug.
        """
        affected = [
            j
            for j in Jurisdiction
            if j.parent is not None and j.parent in _POLICY_REGISTRY
        ]
        assert len(affected) == 16  # the 16 US states; CA-QC's parent (CA) is unregistered
        for jurisdiction in affected:
            policy = get_policy_for_jurisdiction(jurisdiction)
            assert not isinstance(policy.jurisdiction, Jurisdiction), jurisdiction
            with pytest.raises(AttributeError, match="has no attribute 'regulations'"):
                policy.get_applicable_regulations()

    def test_known_bug_resolved_policy_reports_the_parent_jurisdiction(self):
        """Same root cause, second symptom: the requested jurisdiction is lost.

        ``get_policy_for_jurisdiction(US_CA)`` yields a CCPA_CPRAPolicy whose
        ``jurisdiction`` is 'US', not 'US-CA', because the child's jurisdiction
        is a class default and therefore absent from ``exclude_unset=True()``,
        so the parent's serialised value wins.
        """
        assert get_policy_for_jurisdiction(Jurisdiction.US_CA).jurisdiction == "US"
        assert get_policy_for_jurisdiction(Jurisdiction.US_VA).jurisdiction == "US"
        # Jurisdictions WITHOUT a registered parent keep their own value.
        assert get_policy_for_jurisdiction(Jurisdiction.EU).jurisdiction == Jurisdiction.EU
        assert get_policy_for_jurisdiction(Jurisdiction.IN).jurisdiction == Jurisdiction.IN

    def test_known_bug_str_enum_is_not_recoerced_on_reconstruction(self):
        """The mechanism behind the crash, isolated."""
        policy = USPrivacyPolicy()
        assert policy.jurisdiction is Jurisdiction.US
        round_tripped = USPrivacyPolicy(**policy.model_dump())
        assert round_tripped.jurisdiction == "US"
        assert not isinstance(round_tripped.jurisdiction, Jurisdiction)

    def test_jurisdictions_without_a_registered_parent_are_unaffected(self):
        """The safe subset: registry hits whose parent is absent (or None) keep
        the enum, so get_applicable_regulations() works."""
        for jurisdiction in (Jurisdiction.EU, Jurisdiction.IN, Jurisdiction.US,
                            Jurisdiction.GLOBAL, Jurisdiction.JP, Jurisdiction.BR,
                            Jurisdiction.SG, Jurisdiction.KR, Jurisdiction.AU,
                            Jurisdiction.CA):
            policy = get_policy_for_jurisdiction(jurisdiction)
            assert isinstance(policy.jurisdiction, Jurisdiction), jurisdiction
            assert policy.get_applicable_regulations()

    def test_gdpr_lists_gdpr(self):
        assert "GDPR" in GDPRPolicy().get_applicable_regulations()

    def test_eu_avms_lists_the_avms_directive(self):
        regs = EUAVMSPolicy().get_applicable_regulations()
        assert "AVMS Directive" in regs
        assert "European Accessibility Act" in regs

    def test_us_lists_its_statutes(self):
        assert USPrivacyPolicy().get_applicable_regulations() == Jurisdiction.US.regulations

    def test_ccpa_cpra_lists_ccpa(self):
        assert "CCPA" in CCPA_CPRAPolicy().get_applicable_regulations()

    def test_ccpa_cpra_requires_vendor_contracts(self):
        assert CCPA_CPRAPolicy().vendor_contracts_required is True
        assert CCPA_CPRAPolicy().retention_disclosure_required is True

    def test_india_dpp_lists_its_act(self):
        assert "DPDP Act" in IndiaDPDPPolicy().get_applicable_regulations()

    def test_india_requires_ott_self_classification(self):
        policy = IndiaDPDPPolicy()
        assert policy.content_self_classification is True
        assert policy.grievance_mechanism_3_tier is True
        assert policy.data_localization_required is True
        assert policy.max_retention_days == 100

    def test_us_federal_needs_no_adequacy_or_scc(self):
        policy = USPrivacyPolicy()
        assert policy.adequacy_decision_required is False
        assert policy.scc_required is False

    def test_global_baseline_is_the_minimum_everywhere(self):
        policy = GlobalBaselinePolicy()
        assert policy.encryption_at_rest is True
        assert policy.encryption_in_transit is True
        assert policy.audit_log_required is True
        assert policy.default_retention_days == 2555


class TestPolicyRegistryResolution:
    def test_exact_registry_hits(self):
        assert type(get_policy_for_jurisdiction(Jurisdiction.EU)) is GDPRPolicy
        assert type(get_policy_for_jurisdiction(Jurisdiction.IN)) is IndiaDPDPPolicy
        assert type(get_policy_for_jurisdiction(Jurisdiction.US)) is USPrivacyPolicy
        assert type(get_policy_for_jurisdiction(Jurisdiction.US_CA)) is CCPA_CPRAPolicy
        assert type(get_policy_for_jurisdiction(Jurisdiction.GLOBAL)) is GlobalBaselinePolicy

    def test_unregistered_child_state_falls_back_to_its_parent_policy(self):
        """US-VA is not in the registry; it must inherit USPrivacyPolicy."""
        assert Jurisdiction.US_VA.parent is Jurisdiction.US
        policy = get_policy_for_jurisdiction(Jurisdiction.US_VA)
        assert type(policy) is USPrivacyPolicy
        # NB: the jurisdiction VALUE is wrong here (see
        # test_known_bug_resolved_policy_reports_the_parent_jurisdiction).
        assert policy.jurisdiction == Jurisdiction.US

    def test_unregistered_child_states_resolve_to_their_parent_class(self):
        """Every US state that is not itself registered resolves to the same
        policy CLASS as its parent."""
        from wildframe_compliance.policy import _POLICY_REGISTRY

        for jurisdiction in Jurisdiction:
            parent = jurisdiction.parent
            if parent is None or jurisdiction in _POLICY_REGISTRY:
                continue
            if parent not in _POLICY_REGISTRY:
                continue  # CA-QC -> CA is not registered; falls back to GLOBAL
            resolved = get_policy_for_jurisdiction(jurisdiction)
            assert type(resolved) is _POLICY_REGISTRY[parent], jurisdiction

    def test_jurisdiction_without_a_parent_falls_back_to_the_global_baseline(self):
        """CA-QC's parent (CA) is NOT in the registry, and neither is CA-QC, so
        the global baseline is used."""
        assert Jurisdiction.CA_QC.parent is Jurisdiction.CA
        policy = get_policy_for_jurisdiction(Jurisdiction.CA_QC)
        assert type(policy) is GlobalBaselinePolicy

    def test_orphan_jurisdiction_without_registry_or_parent_uses_global(self):
        assert Jurisdiction.JP.parent is None
        policy = get_policy_for_jurisdiction(Jurisdiction.JP)
        assert type(policy) is GlobalBaselinePolicy
        assert policy.encryption_at_rest is True

    def test_overrides_are_applied_to_the_resolved_policy(self):
        policy = get_policy_for_jurisdiction(Jurisdiction.EU, consent_minor_age=21)
        assert policy.consent_minor_age == 21

    def test_overrides_reach_a_parent_fallback_policy(self):
        policy = get_policy_for_jurisdiction(
            Jurisdiction.US_VA, breach_notification_hours=1
        )
        assert policy.breach_notification_hours == 1

    def test_parent_merge_preserves_inherited_defaults(self):
        """A child policy inherits unset fields from its parent's defaults."""
        child = get_policy_for_jurisdiction(Jurisdiction.US_VA)
        assert child.audit_log_required is USPrivacyPolicy().audit_log_required

    def test_get_all_policies_covers_every_registry_entry(self):
        from wildframe_compliance.policy import _POLICY_REGISTRY

        policies = get_all_policies()
        assert set(policies) == set(_POLICY_REGISTRY)
