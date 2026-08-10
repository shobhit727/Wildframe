"""Integration tests for Admin Service."""

from uuid import uuid4

import pytest_asyncio

from app.schemas.admin import (
    ContentModerationRequest,
    SystemAlertRequest,
    SystemConfigRequest,
    UserModerationRequest,
)
from app.services.admin import AdminService


@pytest_asyncio.fixture
async def admin_service(db_session):
    """AdminService instance with test DB."""
    return AdminService(db_session)


class TestUserModerationIntegration:
    """Integration tests for user moderation."""

    async def test_moderate_user(self, admin_service, db_session):
        """Test moderating a user."""
        user_id = str(uuid4())

        request = UserModerationRequest(
            user_id=user_id,
            status="suspended",
            reason="Violated terms of service",
        )

        result = await admin_service.moderate_user(
            request.user_id, request.status, request.reason, "admin-1", "127.0.0.1"
        )

        assert result["user_id"] == user_id
        assert result["status"] == "suspended"
        assert result["reason"] == "Violated terms of service"
        assert result["moderated_by"] == "admin-1"

    async def test_get_user_moderation_history(self, admin_service, db_session):
        """Test getting user moderation history."""
        user_id = str(uuid4())

        request = UserModerationRequest(
            user_id=user_id,
            status="suspended",
            reason="First violation",
        )
        await admin_service.moderate_user(
            request.user_id, request.status, request.reason, "admin-1", "127.0.0.1"
        )

        request2 = UserModerationRequest(
            user_id=user_id,
            status="active",
            reason="Appeal approved",
        )
        await admin_service.moderate_user(
            request2.user_id, request2.status, request2.reason, "admin-2", "127.0.0.1"
        )

        history = await admin_service.get_user_moderation_history(user_id)

        assert history is not None
        assert history["user_id"] == user_id

    async def test_list_moderated_users(self, admin_service, db_session):
        """Test listing moderated users."""
        for i in range(3):
            user_id = str(uuid4())
            request = UserModerationRequest(
                user_id=user_id,
                status="suspended" if i % 2 == 0 else "active",
                reason=f"Reason {i}",
            )
            await admin_service.moderate_user(
                request.user_id, request.status, request.reason, f"admin-{i}", "127.0.0.1"
            )

        users = await admin_service.list_moderated_users(status="suspended", limit=10, offset=0)

        assert len(users) >= 2


class TestContentModerationIntegration:
    """Integration tests for content moderation."""

    async def test_flag_content(self, admin_service, db_session):
        """Test flagging content."""
        content_id = str(uuid4())

        request = ContentModerationRequest(
            content_id=content_id,
            content_type="movie",
            status="flagged",
            reason="Inappropriate content",
        )

        result = await admin_service.flag_content(
            request.content_id, request.content_type, request.reason, "moderator-1", "127.0.0.1"
        )

        assert result["content_id"] == content_id
        assert result["content_type"] == "movie"
        assert result["reason"] == "Inappropriate content"
        assert result["status"] == "flagged"

    async def test_resolve_content_flag(self, admin_service, db_session):
        """Test resolving flagged content."""
        content_id = str(uuid4())

        request = ContentModerationRequest(
            content_id=content_id,
            content_type="show",
            status="flagged",
            reason="Flagged for review",
        )
        await admin_service.flag_content(
            request.content_id, request.content_type, request.reason, "moderator-1", "127.0.0.1"
        )

        result = await admin_service.resolve_content_flag(
            content_id, "removed", "moderator-2", "127.0.0.1"
        )

        assert result["content_id"] == content_id
        assert result["status"] == "removed"

    async def test_list_flagged_content(self, admin_service, db_session):
        """Test listing flagged content."""
        for i in range(3):
            content_id = str(uuid4())
            request = ContentModerationRequest(
                content_id=content_id,
                content_type="movie",
            status="flagged",
                reason=f"Reason {i}",
            )
            await admin_service.flag_content(
                request.content_id, request.content_type, request.reason, f"mod-{i}", "127.0.0.1"
            )

        flagged = await admin_service.list_flagged_content(limit=10, offset=0)

        assert len(flagged) >= 3


class TestSystemAlertIntegration:
    """Integration tests for system alerts."""

    async def test_create_alert(self, admin_service, db_session):
        """Test creating a system alert."""
        request = SystemAlertRequest(
            alert_type="high_cpu",
            severity="critical",
            message="CPU usage above 90%",
            service="streaming-service",
        )

        alert = await admin_service.create_alert(
            request.alert_type, request.severity, request.message, request.service
        )

        assert alert["alert_type"] == "high_cpu"
        assert alert["severity"] == "critical"
        assert alert["message"] == "CPU usage above 90%"
        assert alert["service"] == "streaming-service"
        assert alert["acknowledged"] is False

    async def test_get_system_alerts(self, admin_service, db_session):
        """Test getting system alerts."""
        for i in range(5):
            request = SystemAlertRequest(
                alert_type=f"alert_{i}",
                severity="warning",
                message=f"Alert {i}",
                service="test-service",
            )
            await admin_service.create_alert(
                request.alert_type, request.severity, request.message, request.service
            )

        alerts = await admin_service.get_system_alerts(limit=10)

        assert len(alerts) >= 5

    async def test_acknowledge_alert(self, admin_service, db_session):
        """Test acknowledging an alert."""
        request = SystemAlertRequest(
            alert_type="test_ack",
            severity="critical",
            message="Test acknowledgment",
            service="test",
        )
        alert = await admin_service.create_alert(
            request.alert_type, request.severity, request.message, request.service
        )

        acknowledged = await admin_service.acknowledge_alert(alert["id"], "admin-user")

        assert acknowledged["id"] == alert["id"]
        assert acknowledged["acknowledged"] is True
        assert acknowledged["acknowledged_by"] == "admin-user"
        assert acknowledged["acknowledged_at"] is not None


class TestSystemConfigIntegration:
    """Integration tests for system configuration."""

    async def test_set_config(self, admin_service, db_session):
        """Test setting system configuration."""
        request = SystemConfigRequest(
            key="max_upload_size",
            value="100MB",
            config_type="string",
            description="Maximum upload size",
        )

        config = await admin_service.set_config(
            request.key,
            request.value,
            request.config_type,
            request.description,
            "admin",
            "127.0.0.1",
        )

        assert config["key"] == "max_upload_size"
        assert config["value"] == "100MB"
        assert config["config_type"] == "string"

    async def test_get_config(self, admin_service, db_session):
        """Test getting system configuration."""
        request = SystemConfigRequest(
            key="stream_quality_default",
            value="1080p",
            config_type="string",
            description="Default stream quality",
        )
        await admin_service.set_config(
            request.key,
            request.value,
            request.config_type,
            request.description,
            "admin",
            "127.0.0.1",
        )

        config = await admin_service.get_config("stream_quality_default")

        assert config["key"] == "stream_quality_default"
        assert config["value"] == "1080p"

    async def test_list_configs(self, admin_service, db_session):
        """Test listing all configurations."""
        for i in range(3):
            request = SystemConfigRequest(
                key=f"config_{i}",
                value=f"value_{i}",
                config_type="string",
                description=f"Config {i}",
            )
            await admin_service.set_config(
                request.key,
                request.value,
                request.config_type,
                request.description,
                "admin",
                "127.0.0.1",
            )

        configs = await admin_service.list_configs(limit=10)

        assert len(configs) >= 3


class TestAuditLogIntegration:
    """Integration tests for audit logs."""

    async def test_get_audit_logs_by_admin(self, admin_service, db_session):
        """Test getting audit logs by admin."""
        admin_id = "audit-admin"

        # Generate some audit logs via moderation
        for i in range(3):
            user_id = str(uuid4())
            request = UserModerationRequest(
                user_id=user_id,
                status="suspended",
                reason=f"Audit test {i}",
            )
            await admin_service.moderate_user(
                request.user_id, request.status, request.reason, admin_id, "127.0.0.1"
            )

        logs = await admin_service.get_audit_logs_by_admin(admin_id, limit=10)

        assert len(logs) >= 3

    async def test_get_audit_logs_by_resource(self, admin_service, db_session):
        """Test getting audit logs by resource."""
        content_id = str(uuid4())

        request = ContentModerationRequest(
            content_id=content_id,
            content_type="movie",
            status="flagged",
            reason="Resource audit test",
        )
        await admin_service.flag_content(
            request.content_id, request.content_type, request.reason, "mod-1", "127.0.0.1"
        )

        logs = await admin_service.get_audit_logs_by_resource("content", content_id, limit=10)

        assert len(logs) >= 1


class TestSystemStatsIntegration:
    """Integration tests for system statistics."""

    async def test_get_system_stats(self, admin_service, db_session):
        """Test getting system statistics."""
        stats = await admin_service.get_system_stats(total_users=1000, suspended_users=50)

        assert stats["total_users"] == 1000
        assert stats["suspended_users"] == 50
        assert stats["active_users"] == 950
        assert "flagged_content" in stats
        assert "active_alerts" in stats
        assert "system_uptime_hours" in stats
