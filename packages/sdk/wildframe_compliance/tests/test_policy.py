"""Tests for compliance policies."""

import json
from uuid import uuid4

import pytest

from wildframe_compliance.events import ComplianceEventType, CompliancePolicyEvent
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

    def test_resolved_policies_with_a_registered_parent_report_regulations(self):
        """ISSUE #846 (was: `test_known_bug_...`).

        The parent-merge rebuilds the policy from
        ``parent_policy.model_dump()``, so the child's ``jurisdiction`` is
        re-validated as an explicit argument. With ``use_enum_values = True`` on
        ``CompliancePolicy.Config`` that argument was stored as a plain ``str``,
        and ``get_applicable_regulations()`` — which does
        ``self.jurisdiction.regulations`` — raised AttributeError for every
        jurisdiction whose parent is registered (all 16 US states).

        Full table, including the expected regulation names, lives in
        ``TestJurisdictionIdentityIsPreserved``.
        """
        affected = [
            j for j in Jurisdiction if j.parent is not None and j.parent in _POLICY_REGISTRY
        ]
        assert len(affected) == 16  # the 16 US states; CA-QC's parent (CA) is unregistered
        for jurisdiction in affected:
            policy = get_policy_for_jurisdiction(jurisdiction)
            assert isinstance(policy.jurisdiction, Jurisdiction), jurisdiction
            assert policy.jurisdiction is jurisdiction
            assert policy.get_applicable_regulations() == jurisdiction.regulations

    def test_resolved_policy_keeps_the_requested_jurisdiction(self):
        """ISSUE #846, second symptom (was: `test_known_bug_...`).

        ``get_policy_for_jurisdiction(US_CA)`` used to yield a CCPA_CPRAPolicy
        whose ``jurisdiction`` was 'US', because the child's jurisdiction is a
        class default and therefore absent from ``exclude_unset=True()``, so the
        parent's serialised value won — and with it the parent's regulations.
        """
        assert get_policy_for_jurisdiction(Jurisdiction.US_CA).jurisdiction is Jurisdiction.US_CA
        assert get_policy_for_jurisdiction(Jurisdiction.US_VA).jurisdiction is Jurisdiction.US_VA
        # Jurisdictions WITHOUT a registered parent keep their own value.
        assert get_policy_for_jurisdiction(Jurisdiction.EU).jurisdiction == Jurisdiction.EU
        assert get_policy_for_jurisdiction(Jurisdiction.IN).jurisdiction == Jurisdiction.IN

    def test_str_enum_is_recoerced_on_reconstruction(self):
        """The mechanism behind the crash, isolated (was: `test_known_bug_...`)."""
        policy = USPrivacyPolicy()
        assert policy.jurisdiction is Jurisdiction.US
        round_tripped = USPrivacyPolicy(**policy.model_dump())
        assert round_tripped.jurisdiction is Jurisdiction.US
        assert isinstance(round_tripped.jurisdiction, Jurisdiction)

    def test_jurisdictions_without_a_registered_parent_are_unaffected(self):
        """The safe subset: registry hits whose parent is absent (or None) keep
        the enum, so get_applicable_regulations() works."""
        for jurisdiction in (
            Jurisdiction.EU,
            Jurisdiction.IN,
            Jurisdiction.US,
            Jurisdiction.GLOBAL,
            Jurisdiction.JP,
            Jurisdiction.BR,
            Jurisdiction.SG,
            Jurisdiction.KR,
            Jurisdiction.AU,
            Jurisdiction.CA,
        ):
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
        # Issue #846: the policy identifies the jurisdiction that was ASKED for,
        # not the one it inherited its field defaults from.
        assert policy.jurisdiction == Jurisdiction.US_VA

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
        policy = get_policy_for_jurisdiction(Jurisdiction.US_VA, breach_notification_hours=1)
        assert policy.breach_notification_hours == 1

    def test_parent_merge_preserves_inherited_defaults(self):
        """A child policy inherits unset fields from its parent's defaults."""
        child = get_policy_for_jurisdiction(Jurisdiction.US_VA)
        assert child.audit_log_required is USPrivacyPolicy().audit_log_required

    def test_get_all_policies_covers_every_registry_entry(self):
        from wildframe_compliance.policy import _POLICY_REGISTRY

        policies = get_all_policies()
        assert set(policies) == set(_POLICY_REGISTRY)


# ---------------------------------------------------------------------------
# REGRESSION (issue #846): a resolved policy must carry its own `Jurisdiction`
# enum member, and must therefore answer `get_applicable_regulations()`.
#
# Root cause: `CompliancePolicy.Config.use_enum_values = True` made pydantic
# store the RAW VALUE, so every policy that was re-constructed with an explicit
# `jurisdiction=` argument (which is what the hierarchical parent-merge does)
# ended up holding a plain `str`. `self.jurisdiction.regulations` then raised
# AttributeError for all 16 US variants, and the requested jurisdiction was
# silently replaced by its parent's value ('US-CA' reported as 'US').
#
# The expected regulations below are the ones written in
# `Jurisdiction.regulations`; the expected classes are the registry's.
# ---------------------------------------------------------------------------

# The 16 US variants, each with the regulations its jurisdiction declares.
US_VARIANT_EXPECTATIONS = [
    (Jurisdiction.US_CA, CCPA_CPRAPolicy, ["CCPA", "CPRA"]),
    (Jurisdiction.US_VA, USPrivacyPolicy, ["VCDPA"]),
    (Jurisdiction.US_CO, USPrivacyPolicy, ["CPA"]),
    (Jurisdiction.US_CT, USPrivacyPolicy, ["CTDPA"]),
    (Jurisdiction.US_UT, USPrivacyPolicy, ["UCPA"]),
    (Jurisdiction.US_TX, USPrivacyPolicy, ["TDPSA"]),
    (Jurisdiction.US_OR, USPrivacyPolicy, ["OCPA"]),
    (Jurisdiction.US_MT, USPrivacyPolicy, ["MTCDPA"]),
    (Jurisdiction.US_DE, USPrivacyPolicy, ["DPDPA"]),
    (Jurisdiction.US_NH, USPrivacyPolicy, ["NHPPA"]),
    (Jurisdiction.US_NJ, USPrivacyPolicy, ["NJDPA"]),
    (Jurisdiction.US_MN, USPrivacyPolicy, ["MCDPA"]),
    (Jurisdiction.US_MD, USPrivacyPolicy, ["MODPA"]),
    (Jurisdiction.US_NE, USPrivacyPolicy, ["NEDPA"]),
    (Jurisdiction.US_RI, USPrivacyPolicy, ["RIDTPPA"]),
    (Jurisdiction.US_KY, USPrivacyPolicy, ["KCDPA"]),
]


class TestJurisdictionIdentityIsPreserved:
    @pytest.mark.parametrize("jurisdiction,expected_class,expected_regs", US_VARIANT_EXPECTATIONS)
    def test_us_variant_reports_its_own_jurisdiction_and_regulations(
        self, jurisdiction, expected_class, expected_regs
    ):
        """The whole affected set, in one table: stored type, stored value, regs.

        `type(policy.jurisdiction) is Jurisdiction` and the exact `regs` list are
        both asserted, so this fails on a plain-str jurisdiction even though a
        str-enum compares equal to its own value.
        """
        policy = get_policy_for_jurisdiction(jurisdiction)

        assert type(policy) is expected_class
        assert type(policy.jurisdiction) is Jurisdiction
        assert policy.jurisdiction is jurisdiction
        assert policy.get_applicable_regulations() == expected_regs

    @pytest.mark.parametrize("jurisdiction,expected_class,expected_regs", US_VARIANT_EXPECTATIONS)
    def test_us_variant_consent_gate_uses_the_coppa_boundary(
        self, jurisdiction, expected_class, expected_regs
    ):
        """No US variant overrides `consent_minor_age`, so all 16 inherit COPPA's
        13 from USPrivacyPolicy. auth-service derives the minor flag as
        `declared_age < policy.consent_minor_age` (app/api/routes/age.py), so the
        boundary is pinned at 12/13 and the EU threshold at 16 is asserted to
        NOT apply to a US variant.
        """
        policy = get_policy_for_jurisdiction(jurisdiction)

        assert policy.consent_minor_age == 13  # COPPA, inherited from US federal
        assert (12 < policy.consent_minor_age) is True  # 12 is a minor
        assert (13 < policy.consent_minor_age) is False  # 13 is the boundary
        assert (16 < policy.consent_minor_age) is False  # EU's 16 does not apply

    @pytest.mark.parametrize("jurisdiction", [j for j, _, _ in US_VARIANT_EXPECTATIONS])
    def test_us_variant_inherits_the_us_federal_parent_defaults(self, jurisdiction):
        """The merge still has to do its job: parent fields, not just identity."""
        policy = get_policy_for_jurisdiction(jurisdiction)
        federal = USPrivacyPolicy()
        assert policy.breach_notification_hours == federal.breach_notification_hours == 720
        assert policy.dpo_required is federal.dpo_required is False
        assert policy.audit_log_required is federal.audit_log_required is True

    def test_explicit_jurisdiction_argument_is_stored_as_the_enum(self):
        """The coercion itself, isolated from the registry: passing a
        `Jurisdiction` must store a `Jurisdiction`, not its `.value`."""
        policy = USPrivacyPolicy(jurisdiction=Jurisdiction.US_CA)
        assert type(policy.jurisdiction) is Jurisdiction
        assert policy.jurisdiction is Jurisdiction.US_CA

    def test_reconstruction_from_model_dump_keeps_the_enum(self):
        """`model_dump()` returns the enum member in python mode, so the
        parent-merge's re-construction re-validates it back into the enum."""
        round_tripped = USPrivacyPolicy(**USPrivacyPolicy().model_dump())
        assert type(round_tripped.jurisdiction) is Jurisdiction
        assert round_tripped.jurisdiction is Jurisdiction.US

    def test_requested_jurisdiction_survives_the_parent_merge(self):
        """The requested jurisdiction is the child's identity; it is not an
        inherited field and must not be replaced by the parent's value."""
        assert get_policy_for_jurisdiction(Jurisdiction.US_CA).jurisdiction is (Jurisdiction.US_CA)
        assert get_policy_for_jurisdiction(Jurisdiction.US_VA).jurisdiction is Jurisdiction.US_VA
        # Jurisdictions without a registered parent keep their own value too.
        assert get_policy_for_jurisdiction(Jurisdiction.EU).jurisdiction is Jurisdiction.EU
        assert get_policy_for_jurisdiction(Jurisdiction.IN).jurisdiction is Jurisdiction.IN
        assert get_policy_for_jurisdiction(Jurisdiction.GLOBAL).jurisdiction is Jurisdiction.GLOBAL

    def test_event_payload_still_serialises_the_jurisdiction_as_a_plain_string(self):
        """Blast-radius guard: the Kafka payload is built from
        `policy.model_dump()`, so removing the coercion changes the in-memory
        type inside `policy_data`. The serialised wire form must not change.
        `wildframe_events.publisher` emits it with `json.dumps(..., default=str)`.
        """
        event = CompliancePolicyEvent.from_policy(
            event_type=ComplianceEventType.POLICY_UPDATED,
            policy=get_policy_for_jurisdiction(Jurisdiction.US_CA),
            event_id=uuid4(),
        )

        wire = json.loads(json.dumps(event.to_dict(), default=str))
        assert wire["jurisdiction"] == "US-CA"
        assert wire["policy_data"]["jurisdiction"] == "US-CA"
