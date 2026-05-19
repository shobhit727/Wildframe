import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.admin import AdminService
from app.repositories.admin import (
    UserModerationRepository,
    ContentModerationRepository,
    SystemAlertRepository,
    SystemConfigRepository,
    AdminAuditLogRepository
)


@pytest.fixture
async def mock_db():
    return AsyncMock()


@pytest.fixture
def admin_service(mock_db):
    return AdminService(mock_db)


class TestUserModeration:
    @pytest.mark.asyncio
    async def test_moderate_user_suspend(self, admin_service):
        admin_service.user_repo.update_status = AsyncMock(return_value=MagicMock(
            id=1, user_id="user123", status="suspended", reason="spam", moderated_by="admin1", moderated_at="2026-05-20"
        ))
        admin_service.audit_repo.create = AsyncMock(return_value=None)
        
        result = await admin_service.moderate_user("user123", "suspended", "spam", "admin1", "192.168.1.1")
        
        assert result["user_id"] == "user123"
        assert result["status"] == "suspended"
        assert admin_service.audit_repo.create.called

    @pytest.mark.asyncio
    async def test_get_user_moderation_history(self, admin_service):
        admin_service.user_repo.get_by_user_id = AsyncMock(return_value=MagicMock(
            id=1, user_id="user123", status="active", reason=None, moderated_by="admin1", moderated_at="2026-05-20"
        ))
        
        result = await admin_service.get_user_moderation_history("user123")
        
        assert result["user_id"] == "user123"
        assert result["status"] == "active"

    @pytest.mark.asyncio
    async def test_get_user_moderation_not_found(self, admin_service):
        admin_service.user_repo.get_by_user_id = AsyncMock(return_value=None)
        
        result = await admin_service.get_user_moderation_history("nonexistent")
        
        assert result is None

    @pytest.mark.asyncio
    async def test_list_moderated_users(self, admin_service):
        mock_users = [
            MagicMock(id=1, user_id="user1", status="suspended", reason="spam", moderated_at="2026-05-20"),
            MagicMock(id=2, user_id="user2", status="banned", reason="abuse", moderated_at="2026-05-20")
        ]
        admin_service.user_repo.list_moderated_users = AsyncMock(return_value=mock_users)
        
        result = await admin_service.list_moderated_users(status="suspended", limit=50, offset=0)
        
        assert len(result) == 2
        assert result[0]["status"] == "suspended"


class TestContentModeration:
    @pytest.mark.asyncio
    async def test_flag_content(self, admin_service):
        admin_service.content_repo.create = AsyncMock(return_value=MagicMock(
            id=1, content_id="movie123", content_type="movie", status="flagged", reason="inappropriate"
        ))
        admin_service.audit_repo.create = AsyncMock(return_value=None)
        
        result = await admin_service.flag_content("movie123", "movie", "inappropriate", "admin1", "192.168.1.1")
        
        assert result["content_id"] == "movie123"
        assert result["status"] == "flagged"

    @pytest.mark.asyncio
    async def test_resolve_content_flag(self, admin_service):
        admin_service.content_repo.update_status = AsyncMock(return_value=MagicMock(
            id=1, content_id="movie123", status="removed", resolved_at="2026-05-20"
        ))
        admin_service.audit_repo.create = AsyncMock(return_value=None)
        
        result = await admin_service.resolve_content_flag("movie123", "removed", "admin1", "192.168.1.1")
        
        assert result["content_id"] == "movie123"
        assert result["status"] == "removed"

    @pytest.mark.asyncio
    async def test_list_flagged_content(self, admin_service):
        mock_content = [
            MagicMock(id=1, content_id="movie1", content_type="movie", reason="violence", flagged_at="2026-05-20"),
            MagicMock(id=2, content_id="show1", content_type="show", reason="explicit", flagged_at="2026-05-20")
        ]
        admin_service.content_repo.list_by_status = AsyncMock(return_value=mock_content)
        
        result = await admin_service.list_flagged_content(limit=50, offset=0)
        
        assert len(result) == 2


class TestSystemAlerts:
    @pytest.mark.asyncio
    async def test_create_alert(self, admin_service):
        admin_service.alert_repo.create = AsyncMock(return_value=MagicMock(
            id=1, alert_type="database", severity="critical", message="DB offline", service="streaming", created_at="2026-05-20"
        ))
        
        result = await admin_service.create_alert("database", "critical", "DB offline", "streaming")
        
        assert result["alert_type"] == "database"
        assert result["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_get_system_alerts(self, admin_service):
        mock_alerts = [
            MagicMock(id=1, alert_type="memory", severity="warning", message="Low memory", service="auth", acknowledged=False, created_at="2026-05-20"),
            MagicMock(id=2, alert_type="cpu", severity="critical", message="High CPU", service="content", acknowledged=False, created_at="2026-05-20")
        ]
        admin_service.alert_repo.list_unacknowledged = AsyncMock(return_value=mock_alerts)
        
        result = await admin_service.get_system_alerts(limit=50)
        
        assert len(result) == 2
        assert not result[0]["acknowledged"]

    @pytest.mark.asyncio
    async def test_acknowledge_alert(self, admin_service):
        admin_service.alert_repo.acknowledge = AsyncMock(return_value=MagicMock(
            id=1, alert_type="database", acknowledged=True, acknowledged_by="admin1", acknowledged_at="2026-05-20"
        ))
        
        result = await admin_service.acknowledge_alert(1, "admin1")
        
        assert result["acknowledged"] is True
        assert result["acknowledged_by"] == "admin1"

    @pytest.mark.asyncio
    async def test_get_critical_alerts(self, admin_service):
        mock_alerts = [
            MagicMock(id=1, alert_type="database", message="DB offline", service="streaming", severity="critical", created_at="2026-05-20")
        ]
        admin_service.alert_repo.list_by_severity = AsyncMock(return_value=mock_alerts)
        
        result = await admin_service.get_critical_alerts()
        
        assert len(result) == 1


class TestSystemConfig:
    @pytest.mark.asyncio
    async def test_set_config_new(self, admin_service):
        admin_service.config_repo.get_by_key = AsyncMock(return_value=None)
        admin_service.config_repo.create = AsyncMock(return_value=MagicMock(
            id=1, key="max_users", value="10000", config_type="integer", updated_at="2026-05-20"
        ))
        admin_service.audit_repo.create = AsyncMock(return_value=None)
        
        result = await admin_service.set_config("max_users", "10000", "integer", None, "admin1", "192.168.1.1")
        
        assert result["key"] == "max_users"
        assert result["value"] == "10000"

    @pytest.mark.asyncio
    async def test_set_config_update(self, admin_service):
        admin_service.config_repo.get_by_key = AsyncMock(return_value=MagicMock(id=1))
        admin_service.config_repo.update = AsyncMock(return_value=MagicMock(
            id=1, key="max_users", value="20000", config_type="integer", updated_at="2026-05-20"
        ))
        admin_service.audit_repo.create = AsyncMock(return_value=None)
        
        result = await admin_service.set_config("max_users", "20000", "integer", None, "admin1", "192.168.1.1")
        
        assert result["value"] == "20000"

    @pytest.mark.asyncio
    async def test_get_config(self, admin_service):
        admin_service.config_repo.get_by_key = AsyncMock(return_value=MagicMock(
            id=1, key="max_users", value="10000", config_type="integer", description="Max users allowed"
        ))
        
        result = await admin_service.get_config("max_users")
        
        assert result["key"] == "max_users"
        assert result["value"] == "10000"

    @pytest.mark.asyncio
    async def test_list_configs(self, admin_service):
        mock_configs = [
            MagicMock(id=1, key="max_users", value="10000", config_type="integer", description=None),
            MagicMock(id=2, key="maintenance_mode", value="false", config_type="boolean", description=None)
        ]
        admin_service.config_repo.list_all = AsyncMock(return_value=mock_configs)
        
        result = await admin_service.list_configs(limit=100)
        
        assert len(result) == 2


class TestAuditLogs:
    @pytest.mark.asyncio
    async def test_get_audit_logs_by_admin(self, admin_service):
        mock_logs = [
            MagicMock(id=1, admin_id="admin1", action="user_suspended", resource_type="user", resource_id="user1", ip_address="192.168.1.1", created_at="2026-05-20"),
            MagicMock(id=2, admin_id="admin1", action="config_updated", resource_type="config", resource_id="max_users", created_at="2026-05-20")
        ]
        admin_service.audit_repo.list_by_admin = AsyncMock(return_value=mock_logs)
        
        result = await admin_service.get_audit_logs_by_admin("admin1", limit=50)
        
        assert len(result) == 2
        assert result[0]["action"] == "user_suspended"

    @pytest.mark.asyncio
    async def test_get_audit_logs_by_resource(self, admin_service):
        mock_logs = [
            MagicMock(id=1, admin_id="admin1", action="user_suspended", resource_type="user", resource_id="user1", created_at="2026-05-20")
        ]
        admin_service.audit_repo.list_by_resource = AsyncMock(return_value=mock_logs)
        
        result = await admin_service.get_audit_logs_by_resource("user", "user1", limit=50)
        
        assert len(result) == 1


class TestSystemStats:
    @pytest.mark.asyncio
    async def test_get_system_stats(self, admin_service):
        admin_service.content_repo.list_by_status = AsyncMock(return_value=[MagicMock() for _ in range(5)])
        admin_service.alert_repo.list_unacknowledged = AsyncMock(return_value=[
            MagicMock(acknowledged=False),
            MagicMock(acknowledged=False),
            MagicMock(acknowledged=True)
        ])
        
        result = await admin_service.get_system_stats(total_users=5000, suspended_users=50)
        
        assert result["total_users"] == 5000
        assert result["active_users"] == 4950
        assert result["suspended_users"] == 50
        assert result["flagged_content"] == 5
        assert result["active_alerts"] == 2
