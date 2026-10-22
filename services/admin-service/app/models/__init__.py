"""Exports for admin-service models.

These are plain re-exports of ``app/models/admin.py``. They used to be loaded
through ``importlib.util.spec_from_file_location`` + ``exec_module`` so the
package would "work when loaded via ``importlib.util`` in the test suite", but
``exec_module`` does not register the module in ``sys.modules``, so every name
below used to be a *second* class object distinct from the one production
imports. That duplicated the classes, broke ``isinstance`` across the two
import paths, made ``mock.patch`` on the package path a no-op, and -- because
``admin.py`` declares its own ``Base`` -- created a second SQLAlchemy
declarative registry, so ``from app.models import Base`` returned a ``MetaData``
the production models were not mapped in.

The test-suite reason does not hold: a normal absolute import inside this file
works identically when the file is exec'd standalone under another name.
"""

from app.models.admin import (
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
