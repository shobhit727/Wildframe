from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_, or_
from typing import List, Optional
from app.models.admin import UserModeration, ContentModeration, SystemAlert, SystemConfig, AdminAuditLog


class UserModerationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, user_id: str, status: str, reason: Optional[str], moderated_by: str) -> UserModeration:
        moderation = UserModeration(user_id=user_id, status=status, reason=reason, moderated_by=moderated_by)
        self.db.add(moderation)
        await self.db.commit()
        await self.db.refresh(moderation)
        return moderation

    async def get_by_user_id(self, user_id: str) -> Optional[UserModeration]:
        result = await self.db.execute(
            select(UserModeration).where(UserModeration.user_id == user_id).order_by(desc(UserModeration.created_at))
        )
        return result.scalars().first()

    async def list_moderated_users(self, status: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[UserModeration]:
        query = select(UserModeration).where(UserModeration.is_active == True)
        if status:
            query = query.where(UserModeration.status == status)
        query = query.order_by(desc(UserModeration.created_at)).limit(limit).offset(offset)
        result = await self.db.execute(query)
        return result.scalars().all()

    async def update_status(self, user_id: str, status: str, reason: Optional[str], moderated_by: str) -> Optional[UserModeration]:
        moderation = await self.get_by_user_id(user_id)
        if moderation:
            moderation.status = status
            moderation.reason = reason
            moderation.moderated_by = moderated_by
            await self.db.commit()
            await self.db.refresh(moderation)
        return moderation


class ContentModerationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, content_id: str, content_type: str, status: str, reason: Optional[str], flagged_by: Optional[str] = None) -> ContentModeration:
        moderation = ContentModeration(content_id=content_id, content_type=content_type, status=status, reason=reason, flagged_by=flagged_by)
        self.db.add(moderation)
        await self.db.commit()
        await self.db.refresh(moderation)
        return moderation

    async def get_by_id(self, moderation_id: int) -> Optional[ContentModeration]:
        return await self.db.get(ContentModeration, moderation_id)

    async def get_by_content_id(self, content_id: str) -> Optional[ContentModeration]:
        result = await self.db.execute(
            select(ContentModeration).where(ContentModeration.content_id == content_id).order_by(desc(ContentModeration.created_at))
        )
        return result.scalars().first()

    async def list_by_status(self, status: str, limit: int = 50, offset: int = 0) -> List[ContentModeration]:
        query = select(ContentModeration).where(
            and_(ContentModeration.is_active == True, ContentModeration.status == status)
        ).order_by(desc(ContentModeration.flagged_at)).limit(limit).offset(offset)
        result = await self.db.execute(query)
        return result.scalars().all()

    async def update_status(self, content_id: str, status: str, resolved_by: str) -> Optional[ContentModeration]:
        moderation = await self.get_by_content_id(content_id)
        if moderation:
            moderation.status = status
            moderation.resolved_by = resolved_by
            await self.db.commit()
            await self.db.refresh(moderation)
        return moderation


class SystemAlertRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, alert_type: str, severity: str, message: str, service: str) -> SystemAlert:
        alert = SystemAlert(alert_type=alert_type, severity=severity, message=message, service=service)
        self.db.add(alert)
        await self.db.commit()
        await self.db.refresh(alert)
        return alert

    async def get_by_id(self, alert_id: int) -> Optional[SystemAlert]:
        return await self.db.get(SystemAlert, alert_id)

    async def list_unacknowledged(self, limit: int = 50) -> List[SystemAlert]:
        query = select(SystemAlert).where(
            and_(SystemAlert.is_active == True, SystemAlert.acknowledged == False)
        ).order_by(desc(SystemAlert.created_at)).limit(limit)
        result = await self.db.execute(query)
        return result.scalars().all()

    async def list_by_severity(self, severity: str, limit: int = 50) -> List[SystemAlert]:
        query = select(SystemAlert).where(
            and_(SystemAlert.is_active == True, SystemAlert.severity == severity)
        ).order_by(desc(SystemAlert.created_at)).limit(limit)
        result = await self.db.execute(query)
        return result.scalars().all()

    async def acknowledge(self, alert_id: int, admin_id: str) -> Optional[SystemAlert]:
        alert = await self.get_by_id(alert_id)
        if alert:
            alert.acknowledged = True
            alert.acknowledged_by = admin_id
            await self.db.commit()
            await self.db.refresh(alert)
        return alert


class SystemConfigRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, key: str, value: str, config_type: str, description: Optional[str], updated_by: str) -> SystemConfig:
        config = SystemConfig(key=key, value=value, config_type=config_type, description=description, updated_by=updated_by)
        self.db.add(config)
        await self.db.commit()
        await self.db.refresh(config)
        return config

    async def get_by_key(self, key: str) -> Optional[SystemConfig]:
        result = await self.db.execute(select(SystemConfig).where(SystemConfig.key == key))
        return result.scalars().first()

    async def list_all(self, limit: int = 100) -> List[SystemConfig]:
        query = select(SystemConfig).where(SystemConfig.is_active == True).order_by(SystemConfig.key).limit(limit)
        result = await self.db.execute(query)
        return result.scalars().all()

    async def update(self, key: str, value: str, updated_by: str) -> Optional[SystemConfig]:
        config = await self.get_by_key(key)
        if config:
            config.value = value
            config.updated_by = updated_by
            await self.db.commit()
            await self.db.refresh(config)
        return config


class AdminAuditLogRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, admin_id: str, action: str, resource_type: str, resource_id: str, changes: Optional[str], ip_address: str) -> AdminAuditLog:
        log = AdminAuditLog(admin_id=admin_id, action=action, resource_type=resource_type, resource_id=resource_id, changes=changes, ip_address=ip_address)
        self.db.add(log)
        await self.db.commit()
        await self.db.refresh(log)
        return log

    async def list_by_admin(self, admin_id: str, limit: int = 50) -> List[AdminAuditLog]:
        query = select(AdminAuditLog).where(AdminAuditLog.admin_id == admin_id).order_by(desc(AdminAuditLog.created_at)).limit(limit)
        result = await self.db.execute(query)
        return result.scalars().all()

    async def list_by_resource(self, resource_type: str, resource_id: str, limit: int = 50) -> List[AdminAuditLog]:
        query = select(AdminAuditLog).where(
            and_(AdminAuditLog.resource_type == resource_type, AdminAuditLog.resource_id == resource_id)
        ).order_by(desc(AdminAuditLog.created_at)).limit(limit)
        result = await self.db.execute(query)
        return result.scalars().all()
