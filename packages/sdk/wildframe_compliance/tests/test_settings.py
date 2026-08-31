"""Tests for compliance settings mixin."""

import pytest

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin


class TestSettingsMixin:
    """Tests for ComplianceSettingsMixin."""

    def test_default_settings(self):
        class TestSettings(ComplianceSettingsMixin):
            SERVICE_NAME: str = "test-service"

        settings = TestSettings()
        assert settings.compliance_jurisdiction == Jurisdiction.GLOBAL
        assert settings.compliance_additional_jurisdictions == []
        assert settings.compliance_policy_overrides == {}
        assert settings.compliance_strict_mode is True
        assert settings.compliance_audit_enabled is True
        assert settings.compliance_data_residency_required is False

    def test_custom_jurisdiction(self):
        class TestSettings(ComplianceSettingsMixin):
            SERVICE_NAME: str = "test-service"
            compliance_jurisdiction: Jurisdiction = Jurisdiction.EU

        settings = TestSettings()
        assert settings.compliance_jurisdiction == Jurisdiction.EU

    def test_additional_jurisdictions(self):
        class TestSettings(ComplianceSettingsMixin):
            SERVICE_NAME: str = "test-service"
            compliance_jurisdiction: Jurisdiction = Jurisdiction.US
            compliance_additional_jurisdictions: list[Jurisdiction] = [Jurisdiction.US_CA, Jurisdiction.IN]

        settings = TestSettings()
        assert settings.compliance_jurisdiction == Jurisdiction.US
        assert Jurisdiction.US_CA in settings.compliance_additional_jurisdictions
        assert Jurisdiction.IN in settings.compliance_additional_jurisdictions

    def test_policy_overrides(self):
        class TestSettings(ComplianceSettingsMixin):
            SERVICE_NAME: str = "test-service"
            compliance_policy_overrides: dict = {"dpo_required": False, "breach_notification_hours": 48}

        settings = TestSettings()
        assert settings.compliance_policy_overrides["dpo_required"] is False
        assert settings.compliance_policy_overrides["breach_notification_hours"] == 48

    def test_get_compliance_policy(self):
        class TestSettings(ComplianceSettingsMixin):
            SERVICE_NAME: str = "test-service"
            compliance_jurisdiction: Jurisdiction = Jurisdiction.EU

        settings = TestSettings()
        policy = settings.get_compliance_policy()
        assert policy.jurisdiction == Jurisdiction.EU
        assert policy.dpo_required is True

    def test_get_compliance_policy_with_overrides(self):
        class TestSettings(ComplianceSettingsMixin):
            SERVICE_NAME: str = "test-service"
            compliance_jurisdiction: Jurisdiction = Jurisdiction.EU
            compliance_policy_overrides: dict = {"dpo_required": False}

        settings = TestSettings()
        policy = settings.get_compliance_policy()
        assert policy.dpo_required is False  # Override applied

    def test_get_compliance_policy_with_additional_jurisdictions(self):
        class TestSettings(ComplianceSettingsMixin):
            SERVICE_NAME: str = "test-service"
            compliance_jurisdiction: Jurisdiction = Jurisdiction.US
            compliance_additional_jurisdictions: list[Jurisdiction] = [Jurisdiction.US_CA]

        settings = TestSettings()
        policy = settings.get_compliance_policy()
        # Should merge US federal with CCPA (most restrictive)
        assert policy.jurisdiction == Jurisdiction.US
        # CCPA requires data_subject_objection (opt-out of sale)
        assert policy.data_subject_objection is True

    def test_is_compliant_dpo_required(self):
        class TestSettings(ComplianceSettingsMixin):
            SERVICE_NAME: str = "test-service"
            compliance_jurisdiction: Jurisdiction = Jurisdiction.EU
            compliance_dpo_email: str | None = None

        settings = TestSettings()
        assert settings.is_compliant() is False  # DPO required but not configured

    def test_is_compliant_dpo_configured(self):
        class TestSettings(ComplianceSettingsMixin):
            SERVICE_NAME: str = "test-service"
            compliance_jurisdiction: Jurisdiction = Jurisdiction.EU
            compliance_dpo_email: str | None = "dpo@example.com"

        settings = TestSettings()
        assert settings.is_compliant() is True

    def test_is_compliant_grievance_officer_required(self):
        class TestSettings(ComplianceSettingsMixin):
            SERVICE_NAME: str = "test-service"
            compliance_jurisdiction: Jurisdiction = Jurisdiction.IN
            compliance_grievance_officer_email: str | None = None

        settings = TestSettings()
        assert settings.is_compliant() is False  # Grievance officer required but not configured

    def test_is_compliant_grievance_officer_configured(self):
        class TestSettings(ComplianceSettingsMixin):
            SERVICE_NAME: str = "test-service"
            compliance_jurisdiction: Jurisdiction = Jurisdiction.IN
            compliance_grievance_officer_email: str | None = "grievance@example.com"
            compliance_allowed_data_regions: list[str] = ["IN", "SG"]
            compliance_dpo_email: str = "dpo@example.com"

        settings = TestSettings()
        assert settings.is_compliant() is True

    def test_get_compliance_summary(self):
        class TestSettings(ComplianceSettingsMixin):
            SERVICE_NAME: str = "test-service"
            compliance_jurisdiction: Jurisdiction = Jurisdiction.EU
            compliance_dpo_email: str | None = "dpo@example.com"

        settings = TestSettings()
        summary = settings.get_compliance_summary()

        assert summary["primary_jurisdiction"] == "EU"
        assert summary["policy_version"] == "1.0.0"
        assert summary["enabled"] is True
        assert summary["strict_mode"] is True
        assert summary["audit_enabled"] is True
        assert summary["compliant"] is True
        assert summary["requirements"]["dpo_required"] is True
        assert summary["requirements"]["dpo_configured"] is True
        assert summary["requirements"]["breach_notification_hours"] == 72
        assert summary["requirements"]["consent_minor_age"] == 16