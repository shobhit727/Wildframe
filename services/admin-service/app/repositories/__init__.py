"""Public repository exports for admin-service.

Use normal Python imports so every caller receives the same repository class
objects; dynamic file execution created duplicate classes and broke patching.
"""

from .admin import (
    AdminAuditLogRepository,
    ContentModerationRepository,
    SystemAlertRepository,
    SystemConfigRepository,
    UserModerationRepository,
)

__all__ = [
    "AdminAuditLogRepository",
    "ContentModerationRepository",
    "SystemAlertRepository",
    "SystemConfigRepository",
    "UserModerationRepository",
]