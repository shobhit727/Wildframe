from datetime import datetime
from sqlalchemy import Column, String, Integer, Boolean, DateTime, Float, Index, Enum, Text
from sqlalchemy.ext.declarative import declarative_base
import enum

Base = declarative_base()


class UserModeration(Base):
    __tablename__ = "user_moderations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(255), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="active", index=True)
    reason = Column(Text, nullable=True)
    moderated_by = Column(String(255), nullable=False)
    moderated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("idx_user_id", "user_id"),
        Index("idx_status", "status"),
        Index("idx_moderated_at", "moderated_at"),
    )


class ContentModeration(Base):
    __tablename__ = "content_moderations"

    id = Column(Integer, primary_key=True, index=True)
    content_id = Column(String(255), nullable=False, index=True)
    content_type = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False, default="active", index=True)
    reason = Column(Text, nullable=True)
    flagged_by = Column(String(255), nullable=True)
    resolved_by = Column(String(255), nullable=True)
    flagged_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("idx_content_id", "content_id"),
        Index("idx_status", "status"),
        Index("idx_flagged_at", "flagged_at"),
    )


class SystemAlert(Base):
    __tablename__ = "system_alerts"

    id = Column(Integer, primary_key=True, index=True)
    alert_type = Column(String(50), nullable=False, index=True)
    severity = Column(String(50), nullable=False)
    message = Column(Text, nullable=False)
    service = Column(String(255), nullable=False)
    acknowledged = Column(Boolean, nullable=False, default=False, index=True)
    acknowledged_by = Column(String(255), nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("idx_alert_type", "alert_type"),
        Index("idx_severity", "severity"),
        Index("idx_acknowledged", "acknowledged"),
    )


class SystemConfig(Base):
    __tablename__ = "system_configs"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(255), nullable=False, unique=True, index=True)
    value = Column(Text, nullable=False)
    config_type = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    updated_by = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("idx_key", "key"),
    )


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(String(255), nullable=False, index=True)
    action = Column(String(255), nullable=False)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(String(255), nullable=False)
    changes = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("idx_admin_id", "admin_id"),
        Index("idx_action", "action"),
        Index("idx_created_at", "created_at"),
    )
