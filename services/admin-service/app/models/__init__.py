"""Exports for admin-service models.
Loads definitions from ``admin.py`` using ``importlib`` so the module works both when
imported as a package and when loaded via ``importlib.util`` in the test suite.
"""

import importlib.util
import pathlib

_admin_path = pathlib.Path(__file__).parent / "admin.py"
_spec = importlib.util.spec_from_file_location("admin_models_impl", _admin_path)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)

# Re‑export public symbols expected by the tests.
Base = _mod.Base
UserModeration = _mod.UserModeration
ContentModeration = _mod.ContentModeration
SystemAlert = _mod.SystemAlert
SystemConfig = _mod.SystemConfig
AdminAuditLog = _mod.AdminAuditLog
AuditLogAppendOnlyError = _mod.AuditLogAppendOnlyError
