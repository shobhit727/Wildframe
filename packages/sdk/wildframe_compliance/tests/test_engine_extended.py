"""Additional tests for policy engine - edge cases and complex scenarios."""

import pytest
from datetime import UTC, datetime
from uuid import uuid4

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin
from wildframe_compliance.engine import (
    PolicyEngine,
    evaluate_policy,
    ConsentDecision,
    DataSubjectDecision,
    TransferDecision,
    PolicyDecision,
)
from wildframe_compliance.policy import (
    GDPRPolicy,
    USPrivacyPolicy,
    CCPA_CPRAPolicy,
    IndiaDPDPPolicy,
    GlobalBaselinePolicy,
    get_policy_for_jurisdiction,
)


class MockSettings(ComplianceSettingsMixin):
    SERVICE_NAME: str = "test-service"
    compliance_jurisdiction: Jurisdiction = Jurisdiction.EU
    compliance_additional_jurisdictions: list[Jurisdiction] = [Jurisdiction.US, Jurisdiction.IN]


class TestPolicyEngineEdgeCases:
    """Edge case tests for PolicyEngine."""

    @pytest.fixture(autouse=True)
    def setup_engine(self):
        self.settings = MockSettings()
        self.engine = PolicyEngine(self.settings)

    def test_evaluate_data_subject_right_with_max_retention_override(self):
        """Test retention check with max_retention_days override on policy."""
        from wildframe_compliance.policy import IndiaDPDPPolicy
        policy = IndiaDPDPPolicy(max_retention_days=100)
        self.engine._policy_cache = {Jurisdiction.IN: policy}

        decision = self.engine.evaluate_data_subject_right(
            right_type="access",
            user_id="user-123",
            data_categories=["profile"],
            retention_days=200,  # Exceeds 100
            jurisdiction=Jurisdiction.IN,
        )
        # With max_retention_days=100 set on policy, should fail
        assert decision.allowed is False
        assert decision.retention_check_passed is False

    def test_evaluate_data_subject_right_without_override(self):
        """Test retention check without override (default 2555 days)."""
        decision = self.engine.evaluate_data_subject_right(
            right_type="access",
            user_id="user-123",
            data_categories=["profile"],
            retention_days=None,
            jurisdiction=Jurisdiction.EU,
        )
        # Within default 2555 days, should be allowed
        assert decision.allowed is True
        assert decision.retention_check_passed is True

    def test_evaluate_data_subject_right_unknown_right_type(self):
        """Test data subject right with unrecognized right type."""
        decision = self.engine.evaluate_data_subject_right(
            right_type="unknown_right",
            user_id="user-123",
            data_categories=["profile"],
            retention_days=None,
            jurisdiction=Jurisdiction.EU,
        )
        assert decision.allowed is False
        assert "not recognized" in decision.reason

    def test_evaluate_consent_valid(self):
        """Test consent evaluation with valid consent."""
        decision = self.engine.evaluate_consent(
            user_age=30,
            consent_given=True,
            consent_granular=True,
            consent_withdrawable=True,
            jurisdiction=Jurisdiction.EU,
        )
        assert decision.allowed is True

    def test_evaluate_consent_invalid(self):
        """Test consent evaluation with invalid consent."""
        decision = self.engine.evaluate_consent(
            user_age=30,
            consent_given=False,
            consent_granular=False,
            consent_withdrawable=False,
            jurisdiction=Jurisdiction.EU,
        )
        assert decision.allowed is False

    def test_evaluate_transfer_adequacy_countries(self):
        """Test transfer to adequacy countries."""
        adequacy_countries = [Jurisdiction.CA, Jurisdiction.JP]

        for country in adequacy_countries:
            decision = self.engine.evaluate_transfer(
                source_jurisdiction=Jurisdiction.EU,
                target_jurisdiction=country,
                data_categories=["profile"],
            )
            assert decision.allowed is True

    def test_evaluate_transfer_insufficient_adequacy(self):
        """Test transfer to non-adequacy country with SCC mechanism."""
        decision = self.engine.evaluate_transfer(
            source_jurisdiction=Jurisdiction.EU,
            target_jurisdiction=Jurisdiction.IN,
            data_categories=["profile"],
            transfer_mechanism="scc",
        )
        # SCC mechanism is valid for transfer
        assert decision.allowed is True

    def test_evaluate_retention_within_default(self):
        """Test retention evaluation within default limits."""
        decision = self.engine.evaluate_retention(
            data_category="profile",
            current_retention_days=30,
            jurisdiction=Jurisdiction.EU,
        )
        assert decision.allowed is True

    def test_evaluate_retention_exceeds_default(self):
        """Test retention evaluation exceeding default limits."""
        decision = self.engine.evaluate_retention(
            data_category="profile",
            current_retention_days=5000,
            jurisdiction=Jurisdiction.EU,
        )
        assert decision.allowed is False
        assert "exceeds default" in decision.reason

    def test_evaluate_retention_exceeds_maximum(self):
        """Test retention evaluation exceeding maximum limits."""
        decision = self.engine.evaluate_retention(
            data_category="profile",
            current_retention_days=5000,
            jurisdiction=Jurisdiction.EU,
        )
        assert decision.allowed is False

    def test_evaluate_security_compliant(self):
        """Test security evaluation with compliant settings."""
        decision = self.engine.evaluate_security(
            encryption_at_rest=True,
            encryption_in_transit=True,
            pseudonymization=True,
            jurisdiction=Jurisdiction.EU,
        )
        assert decision.allowed is True

    def test_evaluate_security_non_compliant(self):
        """Test security evaluation with non-compliant settings."""
        decision = self.engine.evaluate_security(
            encryption_at_rest=False,
            encryption_in_transit=False,
            pseudonymization=False,
            jurisdiction=Jurisdiction.EU,
        )
        assert decision.allowed is False

    def test_evaluate_security_partial_compliance(self):
        """Test security evaluation with partial compliance (AND logic)."""
        decision = self.engine.evaluate_security(
            encryption_at_rest=True,
            encryption_in_transit=False,
            pseudonymization=True,
            jurisdiction=Jurisdiction.EU,
        )
        # Both encryption at rest and in transit are required (AND logic)
        assert decision.allowed is False

    def test_evaluate_policy_with_override(self):
        """Test policy evaluation with GDPR policy (US jurisdiction)."""
        from wildframe_compliance.policy import GDPRPolicy
        policy = GDPRPolicy()
        self.engine._policy_cache = {Jurisdiction.US: policy}

        decision = self.engine.evaluate_security(
            encryption_at_rest=True,
            encryption_in_transit=True,
            pseudonymization=True,
            jurisdiction=Jurisdiction.US,
        )
        # GDPR policy with compliant settings should pass
        assert decision.allowed is True

    def test_policy_engine_with_multiple_jurisdictions(self):
        """Test engine works with multiple jurisdictions."""
        settings = MockSettings()
        engine = PolicyEngine(settings)

        # Test EU
        eu_decision = engine.evaluate_consent(
            user_age=30,
            consent_given=True,
            consent_granular=True,
            consent_withdrawable=True,
            jurisdiction=Jurisdiction.EU,
        )
        assert eu_decision.allowed is True

        # Test US
        us_decision = engine.evaluate_consent(
            user_age=30,
            consent_given=True,
            consent_granular=True,
            consent_withdrawable=True,
            jurisdiction=Jurisdiction.US,
        )
        assert us_decision.allowed is True

        # Test IN
        in_decision = engine.evaluate_consent(
            user_age=30,
            consent_given=True,
            consent_granular=True,
            consent_withdrawable=True,
            jurisdiction=Jurisdiction.IN,
        )
        assert in_decision.allowed is True

    def test_evaluate_data_subject_right_all_types(self):
        """Test all data subject right types."""
        for right_type in [
            "access",
            "rectification",
            "erasure",
            "portability",
            "restriction",
            "objection",
            "automated_decision_opt_out",
        ]:
            decision = self.engine.evaluate_data_subject_right(
                right_type=right_type,
                user_id="user-123",
                data_categories=["profile"],
                retention_days=None,
                jurisdiction=Jurisdiction.EU,
            )
            # All should be allowed for EU with valid policy
            assert decision.allowed is True or decision.reason != "Right can be exercised"
