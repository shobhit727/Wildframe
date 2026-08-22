from datetime import UTC, datetime

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secrets import redact_secrets
from app.models.admin import (
    AdminAuditLog,
    AuditLogAppendOnlyError,
    ContentModeration,
    SystemAlert,
    SystemConfig,
    UserModeration,
)


class UserModerationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self, user_id: str, status: str, reason: str | None, moderated_by: str
    ) -> UserModeration:
        moderation = UserModeration(
            user_id=user_id, status=status, reason=reason, moderated_by=moderated_by
        )
        self.db.add(moderation)
        await self.db.commit()
        await self.db.refresh(moderation)
        return moderation

    async def get_by_user_id(self, user_id: str, for_update: bool = False) -> UserModeration | None:
        query = (
            select(UserModeration)
            .where(UserModeration.user_id == user_id)
            .order_by(desc(UserModeration.created_at), UserModeration.id.desc())
        )
        if for_update:
            query = query.with_for_update()
        result = await self.db.execute(query)
        return result.scalars().first()

    async def list_moderated_users(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[UserModeration]:
        query = select(UserModeration).where(UserModeration.is_active == True)
        if status:
            query = query.where(UserModeration.status == status)
        query = (
            query.order_by(desc(UserModeration.created_at), desc(UserModeration.id))
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def update_status(
        self, user_id: str, status: str, reason: str | None, moderated_by: str
    ) -> UserModeration | None:
        # Row lock makes the read-modify-write of a moderation decision atomic
        # under concurrency: concurrent decisions serialize instead of racing.
        moderation = await self.get_by_user_id(user_id, for_update=True)
        if moderation:
            moderation.status = status
            moderation.reason = reason
            moderation.moderated_by = moderated_by
            await self.db.refresh(moderation)
        return moderation


class ContentModerationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        content_id: str,
        content_type: str,
        status: str,
        reason: str | None,
        flagged_by: str | None = None,
    ) -> ContentModeration:
        moderation = ContentModeration(
            content_id=content_id,
            content_type=content_type,
            status=status,
            reason=reason,
            flagged_by=flagged_by,
        )
        self.db.add(moderation)
        await self.db.commit()
        await self.db.refresh(moderation)
        return moderation

    async def get_by_id(self, moderation_id: int) -> ContentModeration | None:
        return await self.db.get(ContentModeration, moderation_id)

    async def get_by_content_id(
        self, content_id: str, for_update: bool = False
    ) -> ContentModeration | None:
        query = (
            select(ContentModeration)
            .where(ContentModeration.content_id == content_id)
            .order_by(desc(ContentModeration.created_at), ContentModeration.id.desc())
        )
        if for_update:
            query = query.with_for_update()
        result = await self.db.execute(query)
        return result.scalars().first()

    async def get_active_flag(self, content_id: str, flagged_by: str) -> ContentModeration | None:
        """Latest still-open flag of ``content_id`` raised by ``flagged_by``."""
        result = await self.db.execute(
            select(ContentModeration)
            .where(
                and_(
                    ContentModeration.content_id == content_id,
                    ContentModeration.flagged_by == flagged_by,
                    ContentModeration.is_active == True,
                    ContentModeration.status == "flagged",
                )
            )
            .order_by(desc(ContentModeration.created_at), ContentModeration.id.desc())
        )
        return result.scalars().first()

    async def list_by_status(
        self, status: str, limit: int = 50, offset: int = 0
    ) -> list[ContentModeration]:
        query = (
            select(ContentModeration)
            .where(and_(ContentModeration.is_active == True, ContentModeration.status == status))
            .order_by(desc(ContentModeration.flagged_at), desc(ContentModeration.id))
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def update_status(
        self, content_id: str, status: str, resolved_by: str
    ) -> ContentModeration | None:
        moderation = await self.get_by_content_id(content_id, for_update=True)
        if moderation:
            moderation.status = status
            moderation.resolved_by = resolved_by
            moderation.resolved_at = datetime.now(UTC) if status == "removed" else None
            await self.db.refresh(moderation)
        return moderation


class SystemAlertRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self, alert_type: str, severity: str, message: str, service: str
    ) -> SystemAlert:
        alert = SystemAlert(
            alert_type=alert_type, severity=severity, message=message, service=service
        )
        self.db.add(alert)
        await self.db.commit()
        await self.db.refresh(alert)
        return alert

    async def get_by_id(self, alert_id: int) -> SystemAlert | None:
        return await self.db.get(SystemAlert, alert_id)

    async def list_unacknowledged(self, limit: int = 50) -> list[SystemAlert]:
        query = (
            select(SystemAlert)
            .where(and_(SystemAlert.is_active == True, SystemAlert.acknowledged == False))
            .order_by(desc(SystemAlert.created_at), desc(SystemAlert.id))
            .limit(limit)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_by_severity(self, severity: str, limit: int = 50) -> list[SystemAlert]:
        query = (
            select(SystemAlert)
            .where(and_(SystemAlert.is_active == True, SystemAlert.severity == severity))
            .order_by(desc(SystemAlert.created_at), desc(SystemAlert.id))
            .limit(limit)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def acknowledge(self, alert_id: int, admin_id: str) -> SystemAlert | None:
        alert = await self.get_by_id(alert_id)
        if alert and not alert.acknowledged:
            alert.acknowledged = True
            alert.acknowledged_by = admin_id
            await self.db.refresh(alert)
        return alert


class SystemConfigRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self, key: str, value: str, config_type: str, description: str | None, updated_by: str
    ) -> SystemConfig:
        config = SystemConfig(
            key=key,
            value=value,
            config_type=config_type,
            description=description,
            updated_by=updated_by,
        )
        self.db.add(config)
        await self.db.commit()
        await self.db.refresh(config)
        return config

    async def get_by_key(self, key: str) -> SystemConfig | None:
        result = await self.db.execute(select(SystemConfig).where(SystemConfig.key == key))
        return result.scalars().first()

    async def list_all(self, limit: int = 100) -> list[SystemConfig]:
        query = (
            select(SystemConfig)
            .where(SystemConfig.is_active == True)
            .order_by(SystemConfig.key)
            .limit(limit)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def update(self, key: str, value: str, updated_by: str) -> SystemConfig | None:
        config = await self.get_by_key(key)
        if config:
            config.value = value
            config.updated_by = updated_by
            await self.db.refresh(config)
        return config


class AdminAuditLogRepository:
    """Append-only audit persistence.

    Records can only be created and read. Any update or delete attempt is
    rejected (see also the ORM-level guards on ``AdminAuditLog``); corrections
    must be recorded as new events.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        admin_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        changes: str | None,
        ip_address: str,
    ) -> AdminAuditLog:
        log = AdminAuditLog(
            admin_id=admin_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            changes=redact_secrets(changes),
            ip_address=ip_address,
        )
        self.db.add(log)
        await self.db.commit()
        await self.db.refresh(log)
        return log

    async def update(self, *args, **kwargs) -> AdminAuditLog:
        raise AuditLogAppendOnlyError(
            "admin audit logs are append-only: corrections must be recorded as new events"
        )

    async def delete(self, *args, **kwargs) -> None:
        raise AuditLogAppendOnlyError("admin audit logs are append-only: deletion is not permitted")

    async def list_by_admin(self, admin_id: str, limit: int = 50) -> list[AdminAuditLog]:
        query = (
            select(AdminAuditLog)
            .where(AdminAuditLog.admin_id == admin_id)
            .order_by(desc(AdminAuditLog.created_at), desc(AdminAuditLog.id))
            .limit(limit)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_by_resource(
        self, resource_type: str, resource_id: str, limit: int = 50
    ) -> list[AdminAuditLog]:
        query = (
            select(AdminAuditLog)
            .where(
                and_(
                    AdminAuditLog.resource_type == resource_type,
                    AdminAuditLog.resource_id == resource_id,
                )
            )
            .order_by(desc(AdminAuditLog.created_at), desc(AdminAuditLog.id))
            .limit(limit)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())
