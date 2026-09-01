"""Tests for compliance observability module."""

import pytest
from datetime import UTC, datetime
from unittest.mock import Mock, patch, MagicMock

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin
from wildframe_compliance.observability import (
    ComplianceMetrics,
    ComplianceLogger,
    compliance_metrics,
    compliance_health_check,
    COMPLIANCE_EVALUATIONS,
    COMPLIANCE_EVALUATION_DURATION,
    COMPLIANCE_POLICY_VERSION,
    COMPLIANCE_VIOLATIONS,
    COMPLIANCE_EVENTS_PUBLISHED,
    COMPLIANCE_EVENTS_CONSUMED,
    COMPLIANCE_HEALTH,
)


class MockSettingsEU(ComplianceSettingsMixin):
    """EU settings for testing."""
    SERVICE_NAME: str = "test-service-eu"
    compliance_jurisdiction: Jurisdiction = Jurisdiction.EU
    compliance_additional_jurisdictions: list[Jurisdiction] = []
    compliance_dpo_email: str = "dpo@example.com"
    compliance_grievance_officer_email: str = "grievance@example.com"
    compliance_allowed_data_regions: list[str] = ["EU", "US"]
    compliance_data_residency_required: bool = True
    compliance_strict_mode: bool = True
    compliance_audit_enabled: bool = True
    compliance_policy_overrides: dict = {}


class MockSettingsIN(ComplianceSettingsMixin):
    """India settings for testing."""
    SERVICE_NAME: str = "test-service-in"
    compliance_jurisdiction: Jurisdiction = Jurisdiction.IN
    compliance_additional_jurisdictions: list[Jurisdiction] = []
    compliance_dpo_email: str = ""
    compliance_grievance_officer_email: str = ""
    compliance_allowed_data_regions: list[str] = []
    compliance_data_residency_required: bool = True
    compliance_strict_mode: bool = True
    compliance_audit_enabled: bool = True
    compliance_policy_overrides: dict = {}


class TestComplianceMetrics:
    """Tests for ComplianceMetrics class."""

    def setup_method(self):
        self.metrics = ComplianceMetrics()

    def test_record_evaluation_allowed(self):
        self.metrics.record_evaluation("consent", Jurisdiction.EU, True, 0.1)
        # Just verify no exception

    def test_record_evaluation_denied(self):
        self.metrics.record_evaluation("consent", Jurisdiction.US, False, 0.2)

    def test_record_violation(self):
        self.metrics.record_violation(Jurisdiction.EU, "missing_dpo")

    def test_record_event_published(self):
        from wildframe_compliance.events import ComplianceEventType
        self.metrics.record_event_published(ComplianceEventType.POLICY_UPDATED, Jurisdiction.EU, True)

    def test_record_event_consumed(self):
        from wildframe_compliance.events import ComplianceEventType
        self.metrics.record_event_consumed(ComplianceEventType.POLICY_UPDATED, Jurisdiction.EU, True)

    def test_set_policy_version(self):
        self.metrics.set_policy_version(Jurisdiction.EU, "2.1.0")

    def test_set_health(self):
        self.metrics.set_health("test-service", True)
        self.metrics.set_health("test-service", False)


class TestComplianceLogger:
    """Tests for ComplianceLogger class."""

    def setup_method(self):
        self.logger = ComplianceLogger()

    def test_log_evaluation(self):
        self.logger.log_evaluation("consent", Jurisdiction.EU, True, {"user_age": 25}, "corr-123")

    def test_log_violation(self):
        self.logger.log_violation(Jurisdiction.EU, "missing_dpo", {"service": "auth"}, "corr-123")

    def test_log_policy_change(self):
        self.logger.log_policy_change("updated", Jurisdiction.EU, "2.0.0", "corr-123")

    def test_log_event_published(self):
        self.logger.log_event_published("policy_updated", Jurisdiction.EU, True, "corr-123")

    def test_log_event_consumed(self):
        self.logger.log_event_consumed("policy_updated", Jurisdiction.EU, True, "corr-123")


class TestComplianceMetricsDecorator:
    """Tests for compliance_metrics decorator."""

    @pytest.mark.asyncio
    async def test_decorator_records_metrics(self):
        metrics = ComplianceMetrics()
        settings = MockSettingsEU()

        @compliance_metrics(metrics, "consent", Jurisdiction.EU)
        async def mock_evaluation():
            from wildframe_compliance.engine import ConsentDecision
            return ConsentDecision(
                allowed=True,
                jurisdiction=Jurisdiction.EU,
                policy_version="1.0.0",
                reason="Consent valid",
                required_actions=[],
                consent_valid=True,
                consent_requirements=[],
            )

        result = await mock_evaluation()
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_decorator_records_failed_metrics(self):
        metrics = ComplianceMetrics()
        settings = MockSettingsEU()

        @compliance_metrics(metrics, "consent", Jurisdiction.EU)
        async def mock_failing_evaluation():
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            await mock_failing_evaluation()


class TestComplianceHealthCheckEU:
    """Tests for compliance_health_check with EU settings."""

    @pytest.mark.asyncio
    async def test_health_check_healthy(self):
        """Test healthy EU settings."""
        settings = MockSettingsEU()
        health = await compliance_health_check(settings)

        assert health["healthy"] is True
        assert health["service"] == "test-service-eu"
        assert health["jurisdiction"] == "EU"
        assert health["issues"] == []

    @pytest.mark.asyncio
    async def test_health_check_missing_dpo(self):
        """Test health check fails when DPO email is missing."""
        class SettingsNoDPO(MockSettingsEU):
            compliance_dpo_email: str = ""

        settings = SettingsNoDPO()
        health = await compliance_health_check(settings)

        assert health["healthy"] is False
        assert "DPO required but not configured" in health["issues"]

    @pytest.mark.asyncio
    async def test_health_check_with_metrics(self):
        from wildframe_compliance.observability import ComplianceMetrics
        settings = MockSettingsEU()
        metrics = ComplianceMetrics()

        health = await compliance_health_check(settings, metrics)

        assert health["healthy"] is True


class TestComplianceHealthCheckIN:
    """Tests for compliance_health_check with India settings."""

    @pytest.mark.asyncio
    async def test_health_check_unhealthy_missing_dpo(self):
        """Test health check fails when DPO email is missing (India)."""
        settings = MockSettingsIN()
        health = await compliance_health_check(settings)

        assert health["healthy"] is False
        assert "DPO required but not configured" in health["issues"]

    @pytest.mark.asyncio
    async def test_health_check_unhealthy_missing_grievance_officer(self):
        """Test health check fails when grievance officer email is missing (India)."""
        settings = MockSettingsIN()
        health = await compliance_health_check(settings)

        assert health["healthy"] is False
        assert "Grievance officer required but not configured" in health["issues"]

    @pytest.mark.asyncio
    async def test_health_check_unhealthy_missing_data_regions(self):
        """Test health check fails when data regions are missing (India)."""
        settings = MockSettingsIN()
        health = await compliance_health_check(settings)

        assert health["healthy"] is False
        assert "Data residency required but no allowed regions configured" in health["issues"]


class TestPrometheusMetrics:
    """Tests that Prometheus metrics are properly defined."""

    def test_metrics_exist(self):
        # Just verify metrics are defined
        assert COMPLIANCE_EVALUATIONS is not None
        assert COMPLIANCE_EVALUATION_DURATION is not None
        assert COMPLIANCE_POLICY_VERSION is not None
        assert COMPLIANCE_VIOLATIONS is not None
        assert COMPLIANCE_EVENTS_PUBLISHED is not None
        assert COMPLIANCE_EVENTS_CONSUMED is not None
        assert COMPLIANCE_HEALTH is not None