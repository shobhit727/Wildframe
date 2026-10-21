"""Exports for admin-service repositories.

These are plain re-exports of ``app/repositories/admin.py``. They used to be
loaded through ``importlib.util.spec_from_file_location`` + ``exec_module``
because the module was expected to work "both as a package import and when
loaded via ``importlib.util`` in the test suite", but ``exec_module`` does not
register the module in ``sys.modules``, so each name below used to be a
*second* class object distinct from the one ``app.services.admin`` imports.
That duplicated the repository classes, broke ``isinstance`` across the two
import paths, and made ``mock.patch("app.repositories.<Name>")`` a silent
no-op against production code.

The test-suite reason does not hold: a normal absolute import inside this file
works identically when the file is exec'd standalone under another name.
"""

from app.repositories.admin import (
    AdminAuditLogRepository,
    ContentModerationRepository,
    SystemAlertRepository,
    SystemConfigRepository,
    UserModerationRepository,
)

__all__ = [
    "UserModerationRepository",
    "ContentModerationRepository",
    "SystemAlertRepository",
    "SystemConfigRepository",
    "AdminAuditLogRepository",
]
