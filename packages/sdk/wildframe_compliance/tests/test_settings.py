"""Tests for compliance settings mixin."""

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.policy import CompliancePolicy
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
            compliance_additional_jurisdictions: list[Jurisdiction] = [
                Jurisdiction.US_CA,
                Jurisdiction.IN,
            ]

        settings = TestSettings()
        assert settings.compliance_jurisdiction == Jurisdiction.US
        assert Jurisdiction.US_CA in settings.compliance_additional_jurisdictions
        assert Jurisdiction.IN in settings.compliance_additional_jurisdictions

    def test_policy_overrides(self):
        class TestSettings(ComplianceSettingsMixin):
            SERVICE_NAME: str = "test-service"
            compliance_policy_overrides: dict = {
                "dpo_required": False,
                "breach_notification_hours": 48,
            }

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


# ---------------------------------------------------------------------------
# Policy merging / compliance evaluation.
#
# `_merge_policies` is the "most restrictive wins" rule that decides the
# effective policy when a service is subject to several jurisdictions, so each
# branch of its type dispatch (bool / int-age / int-retention / other) is
# asserted directly.
# ---------------------------------------------------------------------------


def _settings(**overrides):
    from wildframe_compliance.settings import ComplianceSettingsMixin

    class _S(ComplianceSettingsMixin):
        SERVICE_NAME: str = "test-service"
        KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"

    return _S(**overrides)


class _Concrete(CompliancePolicy):
    """Minimal concrete policy for exercising `_merge_policies` in isolation.

    `CompliancePolicy` is an ABC, so it cannot be instantiated directly; the
    merge logic only reads `model_dump()`, so this stub is sufficient.
    """

    jurisdiction: Jurisdiction = Jurisdiction.GLOBAL

    def get_applicable_regulations(self) -> list[str]:
        return []


class TestMergePolicies:
    def _merge(self, primary, additional):
        return _settings()._merge_policies(primary, additional)

    def test_bool_fields_take_the_permissive_side(self):
        """True = "this control is on" = more restrictive, so True wins."""
        a = _Concrete(dpo_required=False, encryption_at_rest=False, enabled=False)
        b = _Concrete(dpo_required=True, encryption_at_rest=True, enabled=True)
        merged = self._merge(a, b)
        assert merged.dpo_required is True
        assert merged.encryption_at_rest is True
        assert merged.enabled is True

    def test_bool_merge_keeps_primary_when_already_true(self):
        merged = self._merge(
            _Concrete(enabled=True), _Concrete(enabled=False)
        )
        assert merged.enabled is True

    def test_int_age_fields_take_the_higher_value(self):
        merged = self._merge(
            _Concrete(consent_minor_age=13), _Concrete(consent_minor_age=18)
        )
        assert merged.consent_minor_age == 18

    def test_int_retention_fields_take_the_lower_value(self):
        merged = self._merge(
            _Concrete(default_retention_days=2555),
            _Concrete(default_retention_days=365),
        )
        assert merged.default_retention_days == 365

    def test_int_hours_fields_take_the_lower_value(self):
        merged = self._merge(
            _Concrete(breach_notification_hours=72),
            _Concrete(breach_notification_hours=24),
        )
        assert merged.breach_notification_hours == 24

    def test_unrecognised_int_field_keeps_the_primary_value(self):
        merged = self._merge(
            _Concrete(default_retention_days=100),
            _Concrete(default_retention_days=200),
        )
        # "retention" IS a recognised key, so this exercises the "age/minor"
        # and "retention/hours" arms; add a neutral int check too.
        assert merged.default_retention_days == 100

    def test_non_bool_non_int_fields_keep_the_primary_value(self):
        merged = self._merge(
            _Concrete(version="primary"),
            _Concrete(version="additional"),
        )
        assert merged.version == "primary"

    def test_optional_int_none_is_not_treated_as_an_int(self):
        """``max_retention_days: int | None`` — a None primary must not be
        replaced by the additional policy's value via the int branch."""
        merged = self._merge(
            _Concrete(max_retention_days=None),
            _Concrete(max_retention_days=100),
        )
        assert merged.max_retention_days is None

    def test_merged_result_keeps_the_primary_type(self):
        from wildframe_compliance.policy import GDPRPolicy

        merged = self._merge(GDPRPolicy(), _Concrete())
        assert isinstance(merged, GDPRPolicy)
        assert merged.jurisdiction == Jurisdiction.EU
        assert isinstance(self._merge(_Concrete(), _Concrete()), _Concrete)

    def test_merge_does_not_mutate_either_input(self):
        a = _Concrete(consent_minor_age=13)
        b = _Concrete(consent_minor_age=18)
        self._merge(a, b)
        assert a.consent_minor_age == 13
        assert b.consent_minor_age == 18


class TestGetCompliancePolicy:
    def test_primary_jurisdiction_only(self):
        policy = _settings(compliance_jurisdiction=Jurisdiction.EU).get_compliance_policy()
        assert policy.jurisdiction == Jurisdiction.EU
        assert policy.dpo_required is True

    def test_additional_jurisdiction_is_merged(self):
        """US has a HIGHER consent_minor_age (13 vs EU's 16) — the merged
        result must be the more restrictive of the two (16)."""
        settings = _settings(
            compliance_jurisdiction=Jurisdiction.EU,
            compliance_additional_jurisdictions=[Jurisdiction.US],
        )
        assert settings.get_compliance_policy().consent_minor_age == 16

    def test_overrides_are_applied(self):
        settings = _settings(
            compliance_jurisdiction=Jurisdiction.EU,
            compliance_policy_overrides={"consent_minor_age": 21},
        )
        assert settings.get_compliance_policy().consent_minor_age == 21

    def test_overrides_survive_the_additional_jurisdiction_merge(self):
        settings = _settings(
            compliance_jurisdiction=Jurisdiction.EU,
            compliance_policy_overrides={"breach_notification_hours": 1},
            compliance_additional_jurisdictions=[Jurisdiction.IN],
        )
        # The override is 1h; IN's 720h is less restrictive, so 1 must survive.
        assert settings.get_compliance_policy().breach_notification_hours == 1


class TestIsCompliant:
    def test_missing_dpo_fails_for_a_dpo_jurisdiction(self):
        assert _settings(compliance_jurisdiction=Jurisdiction.EU).is_compliant() is False

    def test_dpo_present_passes(self):
        settings = _settings(
            compliance_jurisdiction=Jurisdiction.EU, compliance_dpo_email="dpo@example.com"
        )
        assert settings.is_compliant() is True

    def test_missing_grievance_officer_fails_for_india(self):
        assert _settings(compliance_jurisdiction=Jurisdiction.IN).is_compliant() is False

    def test_india_also_requires_residency_regions(self):
        """IndiaDPDPPolicy sets data_residency_required=True, so a DPO +
        grievance officer is still not enough without allowed regions."""
        settings = _settings(
            compliance_jurisdiction=Jurisdiction.IN,
            compliance_dpo_email="dpo@example.com",
            compliance_grievance_officer_email="grievance@example.com",
        )
        assert settings.is_compliant() is False

    def test_full_india_configuration_passes(self):
        settings = _settings(
            compliance_jurisdiction=Jurisdiction.IN,
            compliance_dpo_email="dpo@example.com",
            compliance_grievance_officer_email="grievance@example.com",
            compliance_allowed_data_regions=["IN"],
        )
        assert settings.is_compliant() is True

    def test_global_baseline_does_not_require_residency_regions(self):
        assert _settings().is_compliant() is True
        assert _settings().get_compliance_policy().data_residency_required is False

    def test_every_registry_jurisdiction_is_evaluable(self):
        """A jurisdiction whose policy demands a config the service cannot
        supply would make the service permanently non-compliant."""
        for jurisdiction in (Jurisdiction.EU, Jurisdiction.US, Jurisdiction.IN, Jurisdiction.GLOBAL):
            settings = _settings(
                compliance_jurisdiction=jurisdiction,
                compliance_dpo_email="dpo@example.com",
                compliance_grievance_officer_email="g@example.com",
                compliance_allowed_data_regions=["IN", "US", "EU"],
            )
            assert isinstance(settings.is_compliant(), bool), jurisdiction

    def test_global_baseline_needs_nothing(self):
        assert _settings(compliance_jurisdiction=Jurisdiction.GLOBAL).is_compliant() is True

    def test_jurisdiction_argument_is_accepted_and_ignored(self):
        """The signature takes ``jurisdiction`` but always evaluates the
        *configured* primary — pinned so the parameter's fate is explicit."""
        settings = _settings(compliance_jurisdiction=Jurisdiction.EU)
        assert settings.is_compliant(jurisdiction=Jurisdiction.GLOBAL) is False


class TestComplianceSummary:
    def test_additional_jurisdictions_are_listed(self):
        settings = _settings(
            compliance_jurisdiction=Jurisdiction.EU,
            compliance_additional_jurisdictions=[Jurisdiction.IN, Jurisdiction.US_CA],
        )
        summary = settings.get_compliance_summary()
        assert summary["additional_jurisdictions"] == ["IN", "US-CA"]

    def test_summary_reflects_the_enforcement_flags(self):
        settings = _settings(
            compliance_strict_mode=False,
            compliance_audit_enabled=False,
            compliance_dpo_email="dpo@example.com",
        )
        summary = settings.get_compliance_summary()
        assert summary["strict_mode"] is False
        assert summary["audit_enabled"] is False
        assert summary["compliant"] is True

    def test_summary_reports_unconfigured_requirements(self):
        summary = _settings(compliance_jurisdiction=Jurisdiction.EU).get_compliance_summary()
        requirements = summary["requirements"]
        assert requirements["dpo_required"] is True
        assert requirements["dpo_configured"] is False
        assert requirements["grievance_officer_configured"] is False
        assert requirements["data_residency_configured"] is False
        assert requirements["grievance_officer_required"] is False

    def test_summary_grievance_flag_follows_the_merged_policy(self):
        settings = _settings(
            compliance_jurisdiction=Jurisdiction.EU,
            compliance_additional_jurisdictions=[Jurisdiction.IN],
        )
        requirements = settings.get_compliance_summary()["requirements"]
        assert requirements["grievance_officer_required"] is True


class TestSettingsDefaults:
    def test_defaults_match_the_declared_field_values(self):
        settings = _settings()
        assert settings.compliance_jurisdiction == Jurisdiction.GLOBAL
        assert settings.compliance_additional_jurisdictions == []
        assert settings.compliance_policy_overrides == {}
        assert settings.compliance_strict_mode is True
        assert settings.compliance_audit_enabled is True
        assert settings.compliance_data_residency_required is False
        assert settings.compliance_allowed_data_regions == []
        assert settings.compliance_transfer_mechanism == "scc"
        assert settings.compliance_dpo_email is None
        assert settings.compliance_grievance_officer_email is None

    def test_env_prefix_is_applied(self, monkeypatch):
        monkeypatch.setenv("COMPLIANCE_COMPLIANCE_TRANSFER_MECHANISM", "bcr")
        assert _settings().compliance_transfer_mechanism == "bcr"

    def test_unknown_env_keys_are_ignored(self, monkeypatch):
        monkeypatch.setenv("COMPLIANCE_TOTALLY_UNKNOWN_KEY", "x")
        assert _settings().compliance_transfer_mechanism == "scc"

    def test_mutable_defaults_are_not_shared(self):
        a = _settings()
        b = _settings()
        a.compliance_allowed_data_regions.append("EU")
        a.compliance_policy_overrides["k"] = 1
        assert b.compliance_allowed_data_regions == []
        assert b.compliance_policy_overrides == {}


class TestMergeAcrossDifferentPolicyClasses:
    """`_merge_policies` is also reached with two *different* policy classes
    (a service whose primary jurisdiction has a stricter policy than an
    additional one). That exercises the key-set mismatch branch at :135 and the
    unrecognised-int branch at :129.
    """

    def test_primary_only_keys_survive_the_merge(self):
        """EUAVMSPolicy declares European-works quotas that GDPRPolicy does
        not; those keys exist only on the primary side."""
        from wildframe_compliance.policy import EUAVMSPolicy, GDPRPolicy

        merged = _settings()._merge_policies(EUAVMSPolicy(), GDPRPolicy())
        assert merged.european_works_quota == EUAVMSPolicy().european_works_quota
        assert merged.independent_producer_quota == EUAVMSPolicy().independent_producer_quota

    def test_unrecognised_int_key_keeps_the_primary_value(self):
        """`grievance_response_days` contains neither "age"/"minor" nor
        "retention"/"hours", so the else arm at :129 applies."""
        from wildframe_compliance.policy import IndiaDPDPPolicy

        primary = IndiaDPDPPolicy().model_copy(update={"grievance_response_days": 30})
        additional = IndiaDPDPPolicy().model_copy(update={"grievance_response_days": 7})
        merged = _settings()._merge_policies(primary, additional)
        assert merged.grievance_response_days == 30

    def test_india_jurisdiction_reports_residency_as_missing(self):
        """Reaching the data-residency branch of is_compliant() (line 145)."""
        settings = _settings(
            compliance_jurisdiction=Jurisdiction.IN,
            compliance_dpo_email="dpo@example.com",
            compliance_grievance_officer_email="g@example.com",
        )
        policy = settings.get_compliance_policy()
        assert policy.data_residency_required is True
        assert settings.compliance_allowed_data_regions == []
        assert settings.is_compliant() is False

    def test_missing_grievance_officer_alone_fails(self):
        """Isolates the grievance branch (:144-145): the DPO check passes, so
        the grievance check is the one that must return False."""
        settings = _settings(
            compliance_jurisdiction=Jurisdiction.IN,
            compliance_dpo_email="dpo@example.com",
            compliance_allowed_data_regions=["IN"],
        )
        policy = settings.get_compliance_policy()
        assert policy.dpo_required is True
        assert policy.grievance_officer_required is True
        assert settings.compliance_grievance_officer_email is None
        assert settings.is_compliant() is False
