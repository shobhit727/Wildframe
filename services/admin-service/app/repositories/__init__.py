"""Exports for admin-service repositories.
Loads concrete repository classes from ``admin.py`` using ``importlib`` so the
module works both as a package import and when loaded via ``importlib.util``
in the test suite.
"""

import importlib.util
import pathlib

_admin_path = pathlib.Path(__file__).parent / "admin.py"
_spec = importlib.util.spec_from_file_location("admin_repos_impl", _admin_path)
assert _spec is not None and _spec.loader is not None, f"no import spec for {_admin_path}"
_loader = _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_loader.exec_module(_mod)

UserModerationRepository = _mod.UserModerationRepository
ContentModerationRepository = _mod.ContentModerationRepository
SystemAlertRepository = _mod.SystemAlertRepository
SystemConfigRepository = _mod.SystemConfigRepository
AdminAuditLogRepository = _mod.AdminAuditLogRepository
