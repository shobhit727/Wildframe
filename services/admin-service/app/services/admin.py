from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.admin import (
    AdminAuditLogRepository,
    ContentModerationRepository,
    SystemAlertRepository,
    SystemConfigRepository,
    UserModerationRepository,
)


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
        moderation = await self.user_repo.update_status(user_id, status, reason, moderated_by)
        if not moderation:
            moderation = await self.user_repo.create(user_id, status, reason, moderated_by)

        await self.audit_repo.create(
            moderated_by, f"user_moderation_{status}", "user", user_id, reason, ip_address
        )
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

    async def list_moderated_users(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        users = await self.user_repo.list_moderated_users(status, limit, offset)
        return [
            {
                "id": u.id,
                "user_id": u.user_id,
                "status": u.status,
                "reason": u.reason,
                "moderated_by": u.moderated_by,
                "moderated_at": u.moderated_at,
                "created_at": u.created_at,
                "updated_at": u.updated_at,
            }
            for u in users
        ]

    # Content Moderation
    async def flag_content(
        self,
        content_id: str,
        content_type: str,
        reason: str | None,
        flagged_by: str,
        ip_address: str,
    ) -> dict:
        moderation = await self.content_repo.create(
            content_id, content_type, "flagged", reason, flagged_by
        )
        await self.audit_repo.create(
            flagged_by, "content_flagged", "content", content_id, reason, ip_address
        )
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
        moderation = await self.content_repo.update_status(content_id, status, resolved_by)
        if moderation:
            await self.audit_repo.create(
                resolved_by, f"content_resolved_{status}", "content", content_id, None, ip_address
            )
        return (
            {
                "id": moderation.id,
                "content_id": moderation.content_id,
                "content_type": moderation.content_type,
                "status": moderation.status,
                "reason": moderation.reason,
                "flagged_at": moderation.flagged_at,
                "resolved_at": moderation.resolved_at,
                "created_at": moderation.created_at,
            }
            if moderation
            else None
        )

    async def list_flagged_content(self, limit: int = 50, offset: int = 0) -> list[dict]:
        content = await self.content_repo.list_by_status("flagged", limit, offset)
        return [
            {
                "id": c.id,
                "content_id": c.content_id,
                "content_type": c.content_type,
                "status": c.status,
                "reason": c.reason,
                "flagged_at": c.flagged_at,
                "resolved_at": c.resolved_at,
                "created_at": c.created_at,
            }
            for c in content
        ]

    # System Alerts
    async def create_alert(
        self, alert_type: str, severity: str, message: str, service: str
    ) -> dict:
        alert = await self.alert_repo.create(alert_type, severity, message, service)
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
        alerts = await self.alert_repo.list_unacknowledged(limit)
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

    async def acknowledge_alert(self, alert_id: int, admin_id: str) -> dict | None:
        alert = await self.alert_repo.acknowledge(alert_id, admin_id)
        if alert:
            return {
                "id": alert.id,
                "alert_type": alert.alert_type,
                "acknowledged": alert.acknowledged,
                "acknowledged_by": alert.acknowledged_by,
                "acknowledged_at": alert.acknowledged_at,
            }
        return None

    async def get_critical_alerts(self) -> list[dict]:
        alerts = await self.alert_repo.list_by_severity("critical", 50)
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
        config = await self.config_repo.get_by_key(key)
        if config:
            config = await self.config_repo.update(key, value, admin_id)
        else:
            config = await self.config_repo.create(key, value, config_type, description, admin_id)

        await self.audit_repo.create(
            admin_id, "config_updated", "config", key, f"value={value}", ip_address
        )
        return {
            "id": config.id,
            "key": config.key,
            "value": config.value,
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
        return {
            "id": config.id,
            "key": config.key,
            "value": config.value,
            "config_type": config.config_type,
            "description": config.description,
            "updated_by": config.updated_by,
            "created_at": config.created_at,
            "updated_at": config.updated_at,
        }

    async def list_configs(self, limit: int = 100) -> list[dict]:
        configs = await self.config_repo.list_all(limit)
        return [
            {
                "id": c.id,
                "key": c.key,
                "value": c.value,
                "config_type": c.config_type,
                "description": c.description,
                "updated_by": c.updated_by,
                "created_at": c.created_at,
                "updated_at": c.updated_at,
            }
            for c in configs
        ]

    # Audit Logs
    async def get_audit_logs_by_admin(self, admin_id: str, limit: int = 50) -> list[dict]:
        logs = await self.audit_repo.list_by_admin(admin_id, limit)
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
        logs = await self.audit_repo.list_by_resource(resource_type, resource_id, limit)
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
        flagged_content = await self.content_repo.list_by_status("flagged", 1000)
        alerts = await self.alert_repo.list_unacknowledged(1000)

        return {
            "total_users": total_users,
            "active_users": total_users - suspended_users,
            "suspended_users": suspended_users,
            "flagged_content": len(flagged_content),
            "active_alerts": len([a for a in alerts if not a.acknowledged]),
            "system_uptime_hours": 99.9,
        }
