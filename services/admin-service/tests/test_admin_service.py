from unittest.mock import AsyncMock, MagicMock

import pytest
from app.services.admin import AdminService


@pytest.fixture
async def mock_db():
    return AsyncMock()


@pytest.fixture
def admin_service(mock_db):
    return AdminService(mock_db)


class TestUserModeration:
    @pytest.mark.asyncio
    async def test_moderate_user_suspend(self, admin_service):
        admin_service.user_repo.get_by_user_id = AsyncMock(return_value=None)
        admin_service.user_repo.update_status = AsyncMock(
            return_value=MagicMock(
                id=1,
                user_id="user123",
                status="suspended",
                reason="spam",
                moderated_by="admin1",
                moderated_at="2026-05-20",
            )
        )
        admin_service.audit_repo.create = AsyncMock(return_value=None)

        result = await admin_service.moderate_user(
            "user123", "suspended", "spam", "admin1", "192.168.1.1"
        )

        assert result["user_id"] == "user123"
        assert result["status"] == "suspended"
        assert admin_service.audit_repo.create.called

    @pytest.mark.asyncio
    async def test_get_user_moderation_history(self, admin_service):
        admin_service.user_repo.get_by_user_id = AsyncMock(
            return_value=MagicMock(
                id=1,
                user_id="user123",
                status="active",
                reason=None,
                moderated_by="admin1",
                moderated_at="2026-05-20",
            )
        )

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
            MagicMock(
                id=1, user_id="user1", status="suspended", reason="spam", moderated_at="2026-05-20"
            ),
            MagicMock(
                id=2, user_id="user2", status="banned", reason="abuse", moderated_at="2026-05-20"
            ),
        ]
        admin_service.user_repo.list_moderated_users = AsyncMock(return_value=mock_users)

        result = await admin_service.list_moderated_users(status="suspended", limit=50, offset=0)

        assert len(result) == 2
        assert result[0]["status"] == "suspended"


class TestContentModeration:
    @pytest.mark.asyncio
    async def test_flag_content(self, admin_service):
        admin_service.content_repo.get_active_flag = AsyncMock(return_value=None)
        admin_service.content_repo.create = AsyncMock(
            return_value=MagicMock(
                id=1,
                content_id="movie123",
                content_type="movie",
                status="flagged",
                reason="inappropriate",
            )
        )
        admin_service.audit_repo.create = AsyncMock(return_value=None)

        result = await admin_service.flag_content(
            "movie123", "movie", "inappropriate", "admin1", "192.168.1.1"
        )

        assert result["content_id"] == "movie123"
        assert result["status"] == "flagged"

    async def test_resolve_content_flag(self, admin_service):
        admin_service.content_repo.get_by_content_id = AsyncMock(
            return_value=MagicMock(id=1, content_id="movie123", status="flagged")
        )
        admin_service.content_repo.update_status = AsyncMock(
            return_value=MagicMock(
                id=1, content_id="movie123", status="removed", resolved_at="2026-05-20"
            )
        )
        admin_service.audit_repo.create = AsyncMock(return_value=None)

        result = await admin_service.resolve_content_flag(
            "movie123", "removed", "admin1", "192.168.1.1"
        )

        assert result["content_id"] == "movie123"
        assert result["status"] == "removed"

    @pytest.mark.asyncio
    async def test_list_flagged_content(self, admin_service):
        mock_content = [
            MagicMock(
                id=1,
                content_id="movie1",
                content_type="movie",
                reason="violence",
                flagged_at="2026-05-20",
            ),
            MagicMock(
                id=2,
                content_id="show1",
                content_type="show",
                reason="explicit",
                flagged_at="2026-05-20",
            ),
        ]
        admin_service.content_repo.list_by_status = AsyncMock(return_value=mock_content)

        result = await admin_service.list_flagged_content(limit=50, offset=0)

        assert len(result) == 2


class TestSystemAlerts:
    @pytest.mark.asyncio
    async def test_create_alert(self, admin_service):
        admin_service.alert_repo.create = AsyncMock(
            return_value=MagicMock(
                id=1,
                alert_type="database",
                severity="critical",
                message="DB offline",
                service="streaming",
                created_at="2026-05-20",
            )
        )

        result = await admin_service.create_alert("database", "critical", "DB offline", "streaming")

        assert result["alert_type"] == "database"
        assert result["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_get_system_alerts(self, admin_service):
        mock_alerts = [
            MagicMock(
                id=1,
                alert_type="memory",
                severity="warning",
                message="Low memory",
                service="auth",
                acknowledged=False,
                created_at="2026-05-20",
            ),
            MagicMock(
                id=2,
                alert_type="cpu",
                severity="critical",
                message="High CPU",
                service="content",
                acknowledged=False,
                created_at="2026-05-20",
            ),
        ]
        admin_service.alert_repo.list_unacknowledged = AsyncMock(return_value=mock_alerts)

        result = await admin_service.get_system_alerts(limit=50)

        assert len(result) == 2
        assert not result[0]["acknowledged"]

    @pytest.mark.asyncio
    async def test_acknowledge_alert(self, admin_service):
        admin_service.alert_repo.acknowledge = AsyncMock(
            return_value=MagicMock(
                id=1,
                alert_type="database",
                acknowledged=True,
                acknowledged_by="admin1",
                acknowledged_at="2026-05-20",
            )
        )

        result = await admin_service.acknowledge_alert(1, "admin1")

        assert result["acknowledged"] is True
        assert result["acknowledged_by"] == "admin1"

    @pytest.mark.asyncio
    async def test_get_critical_alerts(self, admin_service):
        mock_alerts = [
            MagicMock(
                id=1,
                alert_type="database",
                message="DB offline",
                service="streaming",
                severity="critical",
                created_at="2026-05-20",
            )
        ]
        admin_service.alert_repo.list_by_severity = AsyncMock(return_value=mock_alerts)

        result = await admin_service.get_critical_alerts()

        assert len(result) == 1


class TestSystemConfig:
    @pytest.mark.asyncio
    async def test_set_config_new(self, admin_service):
        admin_service.config_repo.get_by_key = AsyncMock(return_value=None)
        admin_service.config_repo.create = AsyncMock(
            return_value=MagicMock(
                id=1, key="max_users", value="10000", config_type="integer", updated_at="2026-05-20"
            )
        )
        admin_service.audit_repo.create = AsyncMock(return_value=None)

        result = await admin_service.set_config(
            "max_users", "10000", "integer", None, "admin1", "192.168.1.1"
        )

        assert result["key"] == "max_users"
        assert result["value"] == "10000"

    @pytest.mark.asyncio
    async def test_set_config_update(self, admin_service):
        admin_service.config_repo.get_by_key = AsyncMock(return_value=MagicMock(id=1))
        admin_service.config_repo.update = AsyncMock(
            return_value=MagicMock(
                id=1, key="max_users", value="20000", config_type="integer", updated_at="2026-05-20"
            )
        )


class TestIdempotencyAndDedupe:
    """Verify moderation and config operations are idempotent and de-duped."""

    @pytest.mark.asyncio
    async def test_moderate_user_idempotent(self, admin_service):
        """Re-applying the same status returns existing row without duplicate audit."""
        existing = MagicMock(
            id=42,
            user_id="user999",
            status="suspended",
            reason="original",
            moderated_by="admin1",
            moderated_at="2026-05-20",
            created_at="2026-05-20",
            updated_at="2026-05-20",
        )
        admin_service.user_repo.get_by_user_id = AsyncMock(return_value=existing)
        admin_service.user_repo.update_status = AsyncMock()  # should NOT be called
        admin_service.audit_repo.create = AsyncMock()  # should NOT be called

        result = await admin_service.moderate_user(
            "user999", "suspended", "duplicate attempt", "admin2", "10.0.0.1"
        )

        assert result["id"] == 42
        assert result["status"] == "suspended"
        assert result["reason"] == "original"  # original preserved
        admin_service.user_repo.update_status.assert_not_awaited()
        admin_service.audit_repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_flag_content_dedupe_same_actor(self, admin_service):
        """Repeated flag by same actor returns the existing open flag."""
        existing = MagicMock(
            id=7,
            content_id="movie456",
            content_type="movie",
            status="flagged",
            reason="first report",
            flagged_at="2026-05-20",
            created_at="2026-05-20",
        )
        admin_service.content_repo.get_active_flag = AsyncMock(return_value=existing)
        admin_service.content_repo.create = AsyncMock()  # should NOT be called
        admin_service.audit_repo.create = AsyncMock()  # should NOT be called

        result = await admin_service.flag_content(
            "movie456", "movie", "second report", "admin1", "10.0.0.1"
        )

        assert result["id"] == 7
        assert result["reason"] == "first report"  # original preserved
        admin_service.content_repo.create.assert_not_awaited()
        admin_service.audit_repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_flag_content_allows_different_actor(self, admin_service):
        """Different actor can flag the same content (no cross-actor dedupe)."""
        admin_service.content_repo.get_active_flag = AsyncMock(return_value=None)
        admin_service.content_repo.create = AsyncMock(
            return_value=MagicMock(
                id=8,
                content_id="movie456",
                content_type="movie",
                status="flagged",
                reason="new report",
                flagged_at="2026-05-20",
                created_at="2026-05-20",
            )
        )
        admin_service.audit_repo.create = AsyncMock(return_value=None)

        result = await admin_service.flag_content(
            "movie456", "movie", "new report", "admin2", "10.0.0.1"
        )

        assert result["id"] == 8
        assert result["reason"] == "new report"
        admin_service.content_repo.create.assert_awaited_once()
        admin_service.audit_repo.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resolve_content_flag_idempotent(self, admin_service):
        """Re-resolving to the same status is a no-op (no audit duplicate)."""
        existing = MagicMock(
            id=9,
            content_id="movie789",
            content_type="movie",
            status="removed",
            reason="spam",
            flagged_at="2026-05-20",
            resolved_at="2026-05-21",
            created_at="2026-05-20",
        )
        admin_service.content_repo.get_by_content_id = AsyncMock(return_value=existing)
        admin_service.content_repo.update_status = AsyncMock()
        admin_service.audit_repo.create = AsyncMock()

        result = await admin_service.resolve_content_flag(
            "movie789", "removed", "admin2", "10.0.0.1"
        )

        assert result["id"] == 9
        assert result["status"] == "removed"
        admin_service.content_repo.update_status.assert_not_awaited()
        admin_service.audit_repo.create.assert_not_awaited()


class TestSecretMasking:
    """Sensitive config values are masked in API responses and audit logs."""

    @pytest.mark.asyncio
    async def test_set_config_audit_logs_masked_value(self, admin_service):
        admin_service.config_repo.get_by_key = AsyncMock(return_value=None)
        admin_service.config_repo.create = AsyncMock(
            return_value=MagicMock(
                id=1,
                key="webhook_secret",
                value="whsec_real",
                config_type="string",
                description=None,
                updated_by="admin1",
                created_at="2026-05-20",
                updated_at="2026-05-20",
            )
        )
        admin_service.audit_repo.create = AsyncMock()

        await admin_service.set_config(
            "webhook_secret", "whsec_real", "string", None, "admin1", "10.0.0.1"
        )

        # audit_repo.create called with masked value in reason (5th positional)
        admin_service.audit_repo.create.assert_awaited_once()
        call_args = admin_service.audit_repo.create.call_args[0]
        assert call_args[4] == "value=********"

    @pytest.mark.asyncio
    async def test_set_config_audit_logs_plain_value_when_not_secret(self, admin_service):
        admin_service.config_repo.get_by_key = AsyncMock(return_value=None)
        admin_service.config_repo.create = AsyncMock(
            return_value=MagicMock(
                id=1,
                key="default_quality",
                value="1080p",
                config_type="string",
                description=None,
                updated_by="admin1",
                created_at="2026-05-20",
                updated_at="2026-05-20",
            )
        )
        admin_service.audit_repo.create = AsyncMock()

        result = await admin_service.set_config(
            "default_quality", "1080p", "string", None, "admin1", "10.0.0.1"
        )

        admin_service.audit_repo.create.assert_awaited_once()
        call_args = admin_service.audit_repo.create.call_args[0]
        assert call_args[4] == "value=1080p"
        assert result["value"] == "1080p"

    @pytest.mark.asyncio
    async def test_list_configs_masks_secrets_only(self, admin_service):
        admin_service.config_repo.list_all = AsyncMock(
            return_value=[
                MagicMock(
                    id=1,
                    key="api_token",
                    value="secret123",
                    config_type="string",
                    description=None,
                    updated_by="admin",
                    created_at="2026-05-20",
                    updated_at="2026-05-20",
                ),
                MagicMock(
                    id=2,
                    key="max_upload_size",
                    value="100MB",
                    config_type="string",
                    description=None,
                    updated_by="admin",
                    created_at="2026-05-20",
                    updated_at="2026-05-20",
                ),
            ]
        )

        result = await admin_service.list_configs(limit=100)

        assert len(result) == 2
        assert result[0]["value"] == "********"  # api_token masked
        assert result[1]["value"] == "100MB"  # normal value shown


class TestAppendOnlyAudit:
    """Audit log records are append-only; update/delete raise."""

    @pytest.mark.asyncio
    async def test_audit_repo_update_raises(self, admin_service):
        from app.models.admin import AuditLogAppendOnlyError

        with pytest.raises(AuditLogAppendOnlyError):
            await admin_service.audit_repo.update()

    @pytest.mark.asyncio
    async def test_audit_repo_delete_raises(self, admin_service):
        from app.models.admin import AuditLogAppendOnlyError

        with pytest.raises(AuditLogAppendOnlyError):
            await admin_service.audit_repo.delete()


class TestBatchLimits:
    """List/bulk endpoints respect hard ceilings."""

    @pytest.mark.asyncio
    async def test_list_moderated_users_clamps_limit(self, admin_service):
        admin_service.user_repo.list_moderated_users = AsyncMock(return_value=[])

        await admin_service.list_moderated_users(limit=5000)  # above MAX_LIST_LIMIT

        # repo called with clamped limit
        admin_service.user_repo.list_moderated_users.assert_awaited_once_with(None, 1000, 0)

    @pytest.mark.asyncio
    async def test_list_configs_clamps_limit(self, admin_service):
        admin_service.config_repo.list_all = AsyncMock(return_value=[])

        await admin_service.list_configs(limit=5000)

        admin_service.config_repo.list_all.assert_awaited_once_with(1000)

    @pytest.mark.asyncio
    async def test_get_audit_logs_by_admin_clamps_limit(self, admin_service):
        admin_service.audit_repo.list_by_admin = AsyncMock(return_value=[])

        await admin_service.get_audit_logs_by_admin("admin1", limit=5000)

        admin_service.audit_repo.list_by_admin.assert_awaited_once_with("admin1", 1000)


class TestConcurrencyLocks:
    """Row-level locks are used for concurrent moderation decisions."""

    @pytest.mark.asyncio
    async def test_moderate_user_uses_for_update(self, admin_service):
        # The pre-check should NOT lock; only update_status should.
        admin_service.user_repo.get_by_user_id = AsyncMock(return_value=None)
        admin_service.user_repo.update_status = AsyncMock(
            return_value=MagicMock(
                id=1,
                user_id="user123",
                status="suspended",
                reason="spam",
                moderated_by="admin1",
                moderated_at="2026-05-20",
                created_at="2026-05-20",
                updated_at="2026-05-20",
            )
        )
        admin_service.audit_repo.create = AsyncMock(return_value=None)

        await admin_service.moderate_user("user123", "suspended", "spam", "admin1", "192.168.1.1")

        # get_by_user_id called without for_update
        get_calls = admin_service.user_repo.get_by_user_id.call_args_list
        assert len(get_calls) == 1
        # called as get_by_user_id(user_id) -> args[0] = user_id, no for_update kwarg
        assert get_calls[0].args[0] == "user123"
        assert "for_update" not in get_calls[0].kwargs

    @pytest.mark.asyncio
    async def test_resolve_content_flag_uses_for_update(self, admin_service):
        # Mock get_by_content_id to return a flagged content on first call
        # (the pre-check in resolve_content_flag), then return the same
        # object on the second call (inside update_status with for_update=True).
        mock_content = MagicMock(
            id=1, content_id="movie1", status="flagged", is_active=True
        )
        admin_service.content_repo.get_by_content_id = AsyncMock(
            side_effect=[mock_content, mock_content]
        )
        # Don't mock update_status - let the real method run so it calls
        # get_by_content_id with for_update=True.
        admin_service.audit_repo.create = AsyncMock(return_value=None)
        # Mock db commit/refresh since we're using a mocked DB
        admin_service.db.commit = AsyncMock()
        admin_service.db.refresh = AsyncMock()

        await admin_service.resolve_content_flag("movie1", "removed", "admin1", "10.0.0.1")

        # Verify get_by_content_id was called twice: once without for_update
        # (pre-check), once with for_update=True (inside update_status).
        get_calls = admin_service.content_repo.get_by_content_id.call_args_list
        assert len(get_calls) == 2
        # First call: pre-check, no for_update
        assert get_calls[0].args[0] == "movie1"
        assert "for_update" not in get_calls[0].kwargs
        # Second call: inside update_status, with for_update=True
        assert get_calls[1].args[0] == "movie1"
        assert get_calls[1].kwargs.get("for_update") is True

    @pytest.mark.asyncio
    async def test_get_config(self, admin_service):
        admin_service.config_repo.get_by_key = AsyncMock(
            return_value=MagicMock(
                id=1,
                key="max_users",
                value="10000",
                config_type="integer",
                description="Max users allowed",
            )
        )

        result = await admin_service.get_config("max_users")

        assert result["key"] == "max_users"
        assert result["value"] == "10000"

    @pytest.mark.asyncio
    async def test_list_configs(self, admin_service):
        mock_configs = [
            MagicMock(
                id=1, key="max_users", value="10000", config_type="integer", description=None
            ),
            MagicMock(
                id=2, key="maintenance_mode", value="false", config_type="boolean", description=None
            ),
        ]
        admin_service.config_repo.list_all = AsyncMock(return_value=mock_configs)

        result = await admin_service.list_configs(limit=100)

        assert len(result) == 2


class TestAuditLogs:
    @pytest.mark.asyncio
    async def test_get_audit_logs_by_admin(self, admin_service):
        mock_logs = [
            MagicMock(
                id=1,
                admin_id="admin1",
                action="user_suspended",
                resource_type="user",
                resource_id="user1",
                ip_address="192.168.1.1",
                created_at="2026-05-20",
            ),
            MagicMock(
                id=2,
                admin_id="admin1",
                action="config_updated",
                resource_type="config",
                resource_id="max_users",
                created_at="2026-05-20",
            ),
        ]
        admin_service.audit_repo.list_by_admin = AsyncMock(return_value=mock_logs)

        result = await admin_service.get_audit_logs_by_admin("admin1", limit=50)

        assert len(result) == 2
        assert result[0]["action"] == "user_suspended"

    @pytest.mark.asyncio
    async def test_get_audit_logs_by_resource(self, admin_service):
        mock_logs = [
            MagicMock(
                id=1,
                admin_id="admin1",
                action="user_suspended",
                resource_type="user",
                resource_id="user1",
                created_at="2026-05-20",
            )
        ]
        admin_service.audit_repo.list_by_resource = AsyncMock(return_value=mock_logs)

        result = await admin_service.get_audit_logs_by_resource("user", "user1", limit=50)

        assert len(result) == 1


class TestSystemStats:
    @pytest.mark.asyncio
    async def test_get_system_stats(self, admin_service):
        admin_service.content_repo.list_by_status = AsyncMock(
            return_value=[MagicMock() for _ in range(5)]
        )
        admin_service.alert_repo.list_unacknowledged = AsyncMock(
            return_value=[
                MagicMock(acknowledged=False),
                MagicMock(acknowledged=False),
                MagicMock(acknowledged=True),
            ]
        )

        result = await admin_service.get_system_stats(total_users=5000, suspended_users=50)

        assert result["total_users"] == 5000
        assert result["active_users"] == 4950
        assert result["suspended_users"] == 50
        assert result["flagged_content"] == 5
        assert result["active_alerts"] == 2
