"""Public model exports for admin-service.

Normal package imports preserve one class identity per SQLAlchemy model.
"""

from .admin import (
    AdminAuditLog,
    AuditLogAppendOnlyError,
    Base,
    ContentModeration,
    SystemAlert,
    SystemConfig,
    UserModeration,
)

__all__ = [
    "Base",
    "UserModeration",
    "ContentModeration",
    "SystemAlert",
    "SystemConfig",
    "AdminAuditLog",
    "AuditLogAppendOnlyError",
]