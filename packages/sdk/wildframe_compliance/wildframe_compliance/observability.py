"""Observability utilities for compliance: metrics, logging, health checks."""

import logging
import time
from datetime import UTC, datetime
from typing import Any
from functools import wraps

from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry

from wildframe_compliance.events import ComplianceEventType
from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin
from wildframe_compliance.policy import CompliancePolicy

logger = logging.getLogger(__name__)

# Prometheus metrics
COMPLIANCE_EVALUATIONS = Counter(
    "compliance_evaluations_total",
    "Total number of compliance policy evaluations",
    ["operation", "jurisdiction", "result"],
)

COMPLIANCE_EVALUATION_DURATION = Histogram(
    "compliance_evaluation_duration_seconds",
    "Time spent evaluating compliance policies",
    ["operation", "jurisdiction"],
)

COMPLIANCE_POLICY_VERSION = Gauge(
    "compliance_policy_version",
    "Current version of compliance policy",
    ["jurisdiction"],
)

COMPLIANCE_VIOLATIONS = Counter(
    "compliance_violations_total",
    "Total number of compliance violations detected",
    ["jurisdiction", "violation_type"],
)

COMPLIANCE_EVENTS_PUBLISHED = Counter(
    "compliance_events_published_total",
    "Total number of compliance events published",
    ["event_type", "jurisdiction", "result"],
)

COMPLIANCE_EVENTS_CONSUMED = Counter(
    "compliance_events_consumed_total",
    "Total number of compliance events consumed",
    ["event_type", "jurisdiction", "result"],
)

# Health check results
COMPLIANCE_HEALTH = Gauge(
    "compliance_health",
    "Health status of compliance configuration (1=healthy, 0=unhealthy)",
    ["service"],
)


class ComplianceMetrics:
    """Metrics collector for compliance operations."""

    def __init__(self, registry: CollectorRegistry | None = None):
        self.registry = registry

    def record_evaluation(
        self,
        operation: str,
        jurisdiction: Jurisdiction,
        allowed: bool,
        duration_seconds: float,
    ) -> None:
        """Record a policy evaluation."""
        result = "allowed" if allowed else "denied"
        COMPLIANCE_EVALUATIONS.labels(
            operation=operation,
            jurisdiction=jurisdiction.value,
            result=result,
        ).inc()
        COMPLIANCE_EVALUATION_DURATION.labels(
            operation=operation,
            jurisdiction=jurisdiction.value,
        ).observe(duration_seconds)

    def record_violation(self, jurisdiction: Jurisdiction, violation_type: str) -> None:
        """Record a compliance violation."""
        COMPLIANCE_VIOLATIONS.labels(
            jurisdiction=jurisdiction.value,
            violation_type=violation_type,
        ).inc()

    def record_event_published(self, event_type: ComplianceEventType, jurisdiction: Jurisdiction, success: bool) -> None:
        """Record a published compliance event."""
        COMPLIANCE_EVENTS_PUBLISHED.labels(
            event_type=event_type.value,
            jurisdiction=jurisdiction.value,
            result="success" if success else "failed",
        ).inc()

    def record_event_consumed(self, event_type: ComplianceEventType, jurisdiction: Jurisdiction, success: bool) -> None:
        """Record a consumed compliance event."""
        COMPLIANCE_EVENTS_CONSUMED.labels(
            event_type=event_type.value,
            jurisdiction=jurisdiction.value,
            result="success" if success else "failed",
        ).inc()

    def set_policy_version(self, jurisdiction: Jurisdiction, version: str) -> None:
        """Set the current policy version for a jurisdiction."""
        # Extract numeric version for gauge
        try:
            version_num = float(version.split(".")[0])
        except (ValueError, IndexError):
            version_num = 1.0
        COMPLIANCE_POLICY_VERSION.labels(jurisdiction=jurisdiction.value).set(version_num)

    def set_health(self, service: str, healthy: bool) -> None:
        """Set health status for a service."""
        COMPLIANCE_HEALTH.labels(service=service).set(1 if healthy else 0)


class ComplianceLogger:
    """Structured logger for compliance decisions."""

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("wildframe.compliance")

    def log_evaluation(
        self,
        operation: str,
        jurisdiction: Jurisdiction,
        allowed: bool,
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Log a compliance evaluation decision."""
        self.logger.info(
            "Compliance evaluation",
            extra={
                "operation": operation,
                "jurisdiction": jurisdiction.value,
                "allowed": allowed,
                "details": details or {},
                "correlation_id": correlation_id,
            },
        )

    def log_violation(
        self,
        jurisdiction: Jurisdiction,
        violation_type: str,
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Log a compliance violation."""
        self.logger.warning(
            "Compliance violation detected",
            extra={
                "jurisdiction": jurisdiction.value,
                "violation_type": violation_type,
                "details": details or {},
                "correlation_id": correlation_id,
            },
        )

    def log_policy_change(
        self,
        event_type: str,
        jurisdiction: Jurisdiction,
        version: str,
        correlation_id: str | None = None,
    ) -> None:
        """Log a policy change event."""
        self.logger.info(
            "Compliance policy change",
            extra={
                "event_type": event_type,
                "jurisdiction": jurisdiction.value,
                "version": version,
                "correlation_id": correlation_id,
            },
        )

    def log_event_published(
        self,
        event_type: str,
        jurisdiction: Jurisdiction,
        success: bool,
        correlation_id: str | None = None,
    ) -> None:
        """Log an event publication."""
        self.logger.info(
            "Compliance event published",
            extra={
                "event_type": event_type,
                "jurisdiction": jurisdiction.value,
                "success": success,
                "correlation_id": correlation_id,
            },
        )

    def log_event_consumed(
        self,
        event_type: str,
        jurisdiction: Jurisdiction,
        success: bool,
        correlation_id: str | None = None,
    ) -> None:
        """Log an event consumption."""
        self.logger.info(
            "Compliance event consumed",
            extra={
                "event_type": event_type,
                "jurisdiction": jurisdiction.value,
                "success": success,
                "correlation_id": correlation_id,
            },
        )


def compliance_metrics(metrics: ComplianceMetrics, operation: str, jurisdiction: Jurisdiction):
    """Decorator to automatically record compliance evaluation metrics."""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start
                allowed = getattr(result, "allowed", True)
                metrics.record_evaluation(operation, jurisdiction, allowed, duration)
                return result
            except Exception as e:
                duration = time.time() - start
                metrics.record_evaluation(operation, jurisdiction, False, duration)
                raise
        return async_wrapper
    return decorator


async def compliance_health_check(
    settings: ComplianceSettingsMixin,
    metrics: ComplianceMetrics | None = None,
) -> dict[str, Any]:
    """Perform a compliance health check.

    Returns:
        Dictionary with health status and details
    """
    try:
        # Check if policy is valid for primary jurisdiction
        policy = settings.get_compliance_policy()

        # Check required configurations
        issues = []

        if policy.dpo_required and not settings.compliance_dpo_email:
            issues.append("DPO required but not configured")

        if hasattr(policy, "grievance_officer_required") and policy.grievance_officer_required:
            if not settings.compliance_grievance_officer_email:
                issues.append("Grievance officer required but not configured")

        if policy.data_residency_required and not settings.compliance_allowed_data_regions:
            issues.append("Data residency required but no allowed regions configured")

        healthy = len(issues) == 0

        if metrics:
            metrics.set_health(settings.SERVICE_NAME, healthy)
            metrics.set_policy_version(settings.compliance_jurisdiction, settings.get_compliance_policy().version)

        return {
            "healthy": healthy,
            "service": settings.SERVICE_NAME,
            "jurisdiction": settings.compliance_jurisdiction.value,
            "policy_version": settings.get_compliance_policy().version,
            "issues": issues,
            "checked_at": datetime.now(UTC).isoformat(),
        }

    except Exception as e:
        if metrics:
            metrics.set_health(settings.SERVICE_NAME, False)
        return {
            "healthy": False,
            "service": settings.SERVICE_NAME,
            "error": str(e),
            "checked_at": datetime.now(UTC).isoformat(),
        }