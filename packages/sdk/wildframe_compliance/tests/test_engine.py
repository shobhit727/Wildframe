"""Tests for policy evaluation engine."""

import pytest

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin
from wildframe_compliance.engine import PolicyEngine, evaluate_policy


class MockSettings(ComplianceSettingsMixin):
    SERVICE_NAME: str = "test-service"
    compliance_jurisdiction: Jurisdiction = Jurisdiction.EU


class TestPolicyEngine:
    """Tests for PolicyEngine."""

    def setup_method(self):
        self.settings = MockSettings()
        self.engine = PolicyEngine(self.settings)

    def test_evaluate_consent_valid_adult(self):
        decision = self.engine.evaluate_consent(
            user_age=25,
            consent_given=True,
            consent_granular=True,
            consent_withdrawable=True,
        )
        assert decision.allowed is True
        assert decision.consent_valid is True
        assert len(decision.required_actions) == 0

    def test_evaluate_consent_minor_eu(self):
        decision = self.engine.evaluate_consent(
            user_age=15,
            consent_given=True,
            consent_granular=True,
            consent_withdrawable=True,
        )
        # EU consent age is 16, so 15-year-old needs parental consent
        assert decision.allowed is False
        assert "obtain_parental_consent" in decision.required_actions

    def test_evaluate_consent_minor_us(self):
        us_settings = MockSettings()
        us_settings.compliance_jurisdiction = Jurisdiction.US
        us_engine = PolicyEngine(us_settings)

        decision = us_engine.evaluate_consent(
            user_age=12,
            consent_given=True,
            consent_granular=True,
            consent_withdrawable=True,
        )
        # US COPPA age is 13, so 12-year-old needs parental consent
        assert decision.allowed is False
        assert "obtain_parental_consent" in decision.required_actions

    def test_evaluate_consent_no_consent(self):
        decision = self.engine.evaluate_consent(
            user_age=25,
            consent_given=False,
            consent_granular=True,
            consent_withdrawable=True,
        )
        assert decision.allowed is False
        assert "obtain_consent" in decision.required_actions

    def test_evaluate_consent_not_granular(self):
        decision = self.engine.evaluate_consent(
            user_age=25,
            consent_given=True,
            consent_granular=False,
            consent_withdrawable=True,
        )
        assert decision.allowed is False
        assert "provide_granular_consent_options" in decision.required_actions

    def test_evaluate_consent_not_withdrawable(self):
        decision = self.engine.evaluate_consent(
            user_age=25,
            consent_given=True,
            consent_granular=True,
            consent_withdrawable=False,
        )
        assert decision.allowed is False
        assert "implement_consent_withdrawal" in decision.required_actions

    def test_evaluate_consent_sensitive_data_ccpa(self):
        ca_settings = MockSettings()
        ca_settings.compliance_jurisdiction = Jurisdiction.US_CA
        ca_engine = PolicyEngine(ca_settings)

        decision = ca_engine.evaluate_consent(
            user_age=25,
            consent_given=True,
            consent_granular=True,
            consent_withdrawable=True,
            sensitive_data=True,
        )
        # CCPA requires opt-out for sensitive PI
        assert decision.allowed is False
        assert "provide_sensitive_pi_opt_out" in decision.required_actions

    def test_evaluate_consent_sensitive_data_dpdp(self):
        in_settings = MockSettings()
        in_settings.compliance_jurisdiction = Jurisdiction.IN
        in_engine = PolicyEngine(in_settings)

        decision = in_engine.evaluate_consent(
            user_age=25,
            consent_given=True,
            consent_granular=True,
            consent_withdrawable=True,
            sensitive_data=True,
        )
        # DPDP requires explicit consent for sensitive data
        assert decision.allowed is False
        assert "explicit_consent_for_sensitive" in decision.required_actions

    def test_evaluate_data_subject_right_access(self):
        decision = self.engine.evaluate_data_subject_right(
            right_type="access",
            user_id="user-123",
            data_categories=["profile", "viewing_history"],
            retention_days=365,
        )
        assert decision.allowed is True
        assert decision.can_execute is True
        assert decision.right_type == "access"

    def test_evaluate_data_subject_right_erasure(self):
        decision = self.engine.evaluate_data_subject_right(
            right_type="erasure",
            user_id="user-123",
            data_categories=["profile"],
            retention_days=365,
        )
        assert decision.allowed is True
        assert decision.can_execute is True
        assert decision.right_type == "erasure"

    def test_evaluate_data_subject_right_unknown(self):
        decision = self.engine.evaluate_data_subject_right(
            right_type="unknown_right",
            user_id="user-123",
            data_categories=["profile"],
            retention_days=365,
        )
        assert decision.allowed is False
        assert "implement_unknown_right_right" in decision.required_actions

    def test_evaluate_data_subject_right_retention_exceeded(self):
        decision = self.engine.evaluate_data_subject_right(
            right_type="access",
            user_id="user-123",
            data_categories=["profile"],
            retention_days=3000,  # Exceeds EU default of 2555
        )
        assert decision.allowed is False
        assert decision.retention_check_passed is False
        assert "reduce_retention_period" in decision.required_actions

    def test_evaluate_transfer_eu_to_adequate(self):
        decision = self.engine.evaluate_transfer(
            source_jurisdiction=Jurisdiction.EU,
            target_jurisdiction=Jurisdiction.CA,  # Canada has adequacy
            transfer_mechanism="adequacy",
        )
        assert decision.allowed is True
        assert decision.adequacy_status is True

    def test_evaluate_transfer_eu_to_non_adequate(self):
        decision = self.engine.evaluate_transfer(
            source_jurisdiction=Jurisdiction.EU,
            target_jurisdiction=Jurisdiction.US,  # No adequacy
            transfer_mechanism="scc",
        )
        assert decision.allowed is True  # SCC is valid
        assert decision.adequacy_status is False
        assert decision.transfer_mechanism == "scc"

    def test_evaluate_transfer_eu_no_mechanism(self):
        decision = self.engine.evaluate_transfer(
            source_jurisdiction=Jurisdiction.EU,
            target_jurisdiction=Jurisdiction.US,
            transfer_mechanism="none",
        )
        assert decision.allowed is False
        assert "implement_standard_contractual_clauses" in decision.required_actions

    def test_evaluate_transfer_india_dpdp(self):
        decision = self.engine.evaluate_transfer(
            source_jurisdiction=Jurisdiction.IN,
            target_jurisdiction=Jurisdiction.US,
            transfer_mechanism="scc",
        )
        # DPDP requires central government approval
        assert decision.allowed is False
        assert "obtain_central_govt_approval" in decision.required_actions

    def test_evaluate_retention_within_limits(self):
        decision = self.engine.evaluate_retention(
            data_category="profile",
            current_retention_days=365,
        )
        assert decision.allowed is True

    def test_evaluate_retention_exceeds_default(self):
        decision = self.engine.evaluate_retention(
            data_category="profile",
            current_retention_days=3000,  # Exceeds 2555 default
        )
        assert decision.allowed is False
        assert "reduce_retention_period" in decision.required_actions

    def test_evaluate_retention_exceeds_max(self):
        # Create policy with max retention
        max_settings = MockSettings()
        max_settings.compliance_policy_overrides = {"max_retention_days": 1000}
        max_engine = PolicyEngine(max_settings)

        decision = max_engine.evaluate_retention(
            data_category="profile",
            current_retention_days=1500,
        )
        assert decision.allowed is False
        assert "reduce_retention_to_maximum" in decision.required_actions

    def test_evaluate_security_compliant(self):
        decision = self.engine.evaluate_security(
            encryption_at_rest=True,
            encryption_in_transit=True,
            pseudonymization=False,
        )
        assert decision.allowed is True

    def test_evaluate_security_missing_encryption_at_rest(self):
        decision = self.engine.evaluate_security(
            encryption_at_rest=False,
            encryption_in_transit=True,
            pseudonymization=False,
        )
        assert decision.allowed is False
        assert "enable_encryption_at_rest" in decision.required_actions

    def test_evaluate_security_missing_encryption_in_transit(self):
        decision = self.engine.evaluate_security(
            encryption_at_rest=True,
            encryption_in_transit=False,
            pseudonymization=False,
        )
        assert decision.allowed is False
        assert "enable_encryption_in_transit" in decision.required_actions

    def test_evaluate_security_pseudonymization_required(self):
        # Create settings with pseudonymization required
        pseudo_settings = MockSettings()
        pseudo_settings.compliance_policy_overrides = {"pseudonymization": True}
        pseudo_engine = PolicyEngine(pseudo_settings)

        decision = pseudo_engine.evaluate_security(
            encryption_at_rest=True,
            encryption_in_transit=True,
            pseudonymization=False,
        )
        assert decision.allowed is False
        assert "implement_pseudonymization" in decision.required_actions


class TestEvaluatePolicyFunction:
    """Tests for the evaluate_policy convenience function."""

    def test_evaluate_consent_via_function(self):
        settings = MockSettings()
        decision = evaluate_policy(
            settings,
            "consent",
            user_age=25,
            consent_given=True,
            consent_granular=True,
            consent_withdrawable=True,
        )
        assert decision.allowed is True

    def test_evaluate_data_subject_right_via_function(self):
        settings = MockSettings()
        decision = evaluate_policy(
            settings,
            "data_subject_right",
            right_type="access",
            user_id="user-123",
            data_categories=["profile"],
            retention_days=365,
        )
        assert decision.allowed is True
        assert decision.right_type == "access"

    def test_evaluate_transfer_via_function(self):
        settings = MockSettings()
        decision = evaluate_policy(
            settings,
            "transfer",
            source_jurisdiction=Jurisdiction.EU,
            target_jurisdiction=Jurisdiction.CA,
        )
        assert decision.allowed is True

    def test_evaluate_unknown_operation(self):
        settings = MockSettings()
        with pytest.raises(ValueError, match="Unknown operation"):
            evaluate_policy(settings, "unknown_operation")


class TestGetApplicableRequirements:
    """Tests for getting applicable requirements."""

    def test_get_requirements_eu(self):
        settings = MockSettings()
        engine = PolicyEngine(settings)
        requirements = engine.get_applicable_requirements(Jurisdiction.EU)

        assert requirements["jurisdiction"] == "EU"
        assert "GDPR" in requirements["regulations"]
        assert requirements["data_subject_rights"]["access"] is True
        assert requirements["data_subject_rights"]["erasure"] is True
        assert requirements["consent"]["minor_age"] == 16
        assert requirements["security"]["encryption_at_rest"] is True
        assert requirements["accountability"]["dpo_required"] is True

    def test_get_requirements_us(self):
        settings = MockSettings()
        engine = PolicyEngine(settings)
        requirements = engine.get_applicable_requirements(Jurisdiction.US)

        assert requirements["jurisdiction"] == "US"
        assert requirements["consent"]["minor_age"] == 13
        assert requirements["accountability"]["dpo_required"] is False

    def test_get_requirements_india(self):
        settings = MockSettings()
        engine = PolicyEngine(settings)
        requirements = engine.get_applicable_requirements(Jurisdiction.IN)

        assert requirements["jurisdiction"] == "IN"
        assert requirements["consent"]["minor_age"] == 18
        assert requirements["accountability"]["dpo_required"] is True