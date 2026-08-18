from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secrets import is_sensitive_config_key, mask_value
from app.repositories.admin import (
    AdminAuditLogRepository,
    ContentModerationRepository,
    SystemAlertRepository,
    SystemConfigRepository,
    UserModerationRepository,
)

# Hard ceiling for any list/bulk read, independent of route-level limits.
MAX_LIST_LIMIT = 1000


def _clamp_limit(limit: int, maximum: int = MAX_LIST_LIMIT) -> int:
    return max(0, min(limit, maximum))


class AdminService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.user_repo = UserModerationRepository(db)
        self.content_repo = ContentModerationRepository(db)
        self.alert_repo = SystemAlertRepository(db)
        self.config_repo = SystemConfigRepository(db)
        self.audit_repo = AdminAuditLogRepository(db)

    # User Moderation
    async def moderate_user(
        self, user_id: str, status: str, reason: str | None, moderated_by: str, ip_address: str
    ) -> dict:
        existing = await self.user_repo.get_by_user_id(user_id)
        if existing is not None and existing.status == status:
            # Idempotent: the same decision already stands — do not rewrite the
            # row or duplicate the audit trail.
            return self._serialize_user_moderation(existing)

        moderation = await self.user_repo.update_status(user_id, status, reason, moderated_by)
        if not moderation:
            moderation = await self.user_repo.create(user_id, status, reason, moderated_by)

        await self.audit_repo.create(
            moderated_by, f"user_moderation_{status}", "user", user_id, reason, ip_address
        )
        return self._serialize_user_moderation(moderation)

    @staticmethod
    def _serialize_user_moderation(moderation) -> dict:
        return {
            "id": moderation.id,
            "user_id": moderation.user_id,
            "status": moderation.status,
            "reason": moderation.reason,
            "moderated_by": moderation.moderated_by,
            "moderated_at": moderation.moderated_at,
            "created_at": moderation.created_at,
            "updated_at": moderation.updated_at,
        }

    async def get_user_moderation_history(self, user_id: str) -> dict | None:
        moderation = await self.user_repo.get_by_user_id(user_id)
        if not moderation:
            return None
        return self._serialize_user_moderation(moderation)

    async def list_moderated_users(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        limit = _clamp_limit(limit)
        offset = max(0, offset)
        users = await self.user_repo.list_moderated_users(status, limit, offset)
        return [self._serialize_user_moderation(u) for u in users]

    # Content Moderation
    async def flag_content(
        self,
        content_id: str,
        content_type: str,
        reason: str | None,
        flagged_by: str,
        ip_address: str,
    ) -> dict:
        existing = await self.content_repo.get_active_flag(content_id, flagged_by)
        if existing is not None and existing.status == "flagged":
            # Repeated reports from the same actor must not inflate flag
            # counts; return the existing open flag without a duplicate audit.
            return self._serialize_content_moderation(existing)

        moderation = await self.content_repo.create(
            content_id, content_type, "flagged", reason, flagged_by
        )
        await self.audit_repo.create(
            flagged_by, "content_flagged", "content", content_id, reason, ip_address
        )
        return self._serialize_content_moderation(moderation)

    @staticmethod
    def _serialize_content_moderation(moderation) -> dict:
        return {
            "id": moderation.id,
            "content_id": moderation.content_id,
            "content_type": moderation.content_type,
            "status": moderation.status,
            "reason": moderation.reason,
            "flagged_at": moderation.flagged_at,
            "resolved_at": moderation.resolved_at,
            "created_at": moderation.created_at,
        }

    async def resolve_content_flag(
        self, content_id: str, status: str, resolved_by: str, ip_address: str
    ) -> dict | None:
        existing = await self.content_repo.get_by_content_id(content_id)
        if existing is None:
            return None
        if existing.status == status:
            # Idempotent re-resolution: no row rewrite, no duplicate audit.
            return self._serialize_content_moderation(existing)

        moderation = await self.content_repo.update_status(content_id, status, resolved_by)
        await self.audit_repo.create(
            resolved_by, f"content_resolved_{status}", "content", content_id, None, ip_address
        )
        return self._serialize_content_moderation(moderation)

    async def list_flagged_content(self, limit: int = 50, offset: int = 0) -> list[dict]:
        limit = _clamp_limit(limit)
        offset = max(0, offset)
        content = await self.content_repo.list_by_status("flagged", limit, offset)
        return [self._serialize_content_moderation(c) for c in content]

    # System Alerts
    async def create_alert(
        self,
        alert_type: str,
        severity: str,
        message: str,
        service: str,
        admin_id: str | None = None,
        ip_address: str = "0.0.0.0",
    ) -> dict:
        alert = await self.alert_repo.create(alert_type, severity, message, service)
        if admin_id:
            await self.audit_repo.create(
                admin_id=admin_id,
                action="alert_created",
                resource_type="alert",
                resource_id=str(alert.id),
                changes=f"severity={severity}",
                ip_address=ip_address,
            )
        else:
            # No audit row to commit for: persist the mutation alone.
            await self.db.commit()
        return {
            "id": alert.id,
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "message": alert.message,
            "service": alert.service,
            "acknowledged": alert.acknowledged,
            "acknowledged_by": alert.acknowledged_by,
            "created_at": alert.created_at,
        }

    async def get_system_alerts(self, limit: int = 50) -> list[dict]:
        alerts = await self.alert_repo.list_unacknowledged(_clamp_limit(limit))
        return [
            {
                "id": a.id,
                "alert_type": a.alert_type,
                "severity": a.severity,
                "message": a.message,
                "service": a.service,
                "acknowledged": a.acknowledged,
                "acknowledged_by": a.acknowledged_by,
                "created_at": a.created_at,
            }
            for a in alerts
        ]

    async def acknowledge_alert(self, alert_id: int, admin_id: str, ip_address: str) -> dict | None:
        alert = await self.alert_repo.acknowledge(alert_id, admin_id)
        if alert:
            await self.audit_repo.create(
                admin_id=admin_id,
                action="alert_acknowledged",
                resource_type="alert",
                resource_id=str(alert.id),
                changes=None,
                ip_address=ip_address,
            )
            return {
                "id": alert.id,
                "alert_type": alert.alert_type,
                "acknowledged": alert.acknowledged,
                "acknowledged_by": alert.acknowledged_by,
                "acknowledged_at": alert.acknowledged_at,
            }
        return None

    async def get_critical_alerts(self) -> list[dict]:
        alerts = await self.alert_repo.list_by_severity("critical", _clamp_limit(50))
        return [
            {
                "id": a.id,
                "alert_type": a.alert_type,
                "severity": a.severity,
                "message": a.message,
                "service": a.service,
                "acknowledged": a.acknowledged,
                "acknowledged_by": a.acknowledged_by,
                "created_at": a.created_at,
            }
            for a in alerts
        ]

    # System Configuration
    async def set_config(
        self,
        key: str,
        value: str,
        config_type: str,
        description: str | None,
        admin_id: str,
        ip_address: str,
    ) -> dict:
        sensitive = is_sensitive_config_key(key)
        config = await self.config_repo.get_by_key(key)
        if config:
            config = await self.config_repo.update(key, value, admin_id)
        else:
            config = await self.config_repo.create(key, value, config_type, description, admin_id)

        assert config is not None
        logged_value = mask_value(value) if sensitive else value
        await self.audit_repo.create(
            admin_id, "config_updated", "config", key, f"value={logged_value}", ip_address
        )
        return self._serialize_config(config, sensitive)

    @staticmethod
    def _serialize_config(config, sensitive: bool) -> dict:
        return {
            "id": config.id,
            "key": config.key,
            "value": mask_value(config.value) if sensitive else config.value,
            "config_type": config.config_type,
            "description": config.description,
            "updated_by": config.updated_by,
            "created_at": config.created_at,
            "updated_at": config.updated_at,
        }

    async def get_config(self, key: str) -> dict | None:
        config = await self.config_repo.get_by_key(key)
        if not config:
            return None
        return self._serialize_config(config, is_sensitive_config_key(config.key))

    async def list_configs(self, limit: int = 100) -> list[dict]:
        configs = await self.config_repo.list_all(_clamp_limit(limit))
        return [self._serialize_config(c, is_sensitive_config_key(c.key)) for c in configs]

    # Audit Logs
    async def get_audit_logs_by_admin(self, admin_id: str, limit: int = 50) -> list[dict]:
        logs = await self.audit_repo.list_by_admin(admin_id, _clamp_limit(limit))
        return [
            {
                "id": log.id,
                "admin_id": log.admin_id,
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "changes": log.changes,
                "ip_address": log.ip_address,
                "created_at": log.created_at,
            }
            for log in logs
        ]

    async def get_audit_logs_by_resource(
        self, resource_type: str, resource_id: str, limit: int = 50
    ) -> list[dict]:
        logs = await self.audit_repo.list_by_resource(
            resource_type, resource_id, _clamp_limit(limit)
        )
        return [
            {
                "id": log.id,
                "admin_id": log.admin_id,
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "changes": log.changes,
                "ip_address": log.ip_address,
                "created_at": log.created_at,
            }
            for log in logs
        ]

    # System Stats
    async def get_system_stats(self, total_users: int = 0, suspended_users: int = 0) -> dict:
        flagged_content = await self.content_repo.list_by_status("flagged", _clamp_limit(1000))
        alerts = await self.alert_repo.list_unacknowledged(_clamp_limit(1000))

        return {
            "total_users": total_users,
            "active_users": total_users - suspended_users,
            "suspended_users": suspended_users,
            "flagged_content": len(flagged_content),
            "active_alerts": len([a for a in alerts if not a.acknowledged]),
            "system_uptime_hours": 99.9,
        }
