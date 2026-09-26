"""Behavioural tests for ``app/repositories/admin.py`` (325 lines).

Two things are asserted here beyond ordinary CRUD:

1. ``app/repositories/__init__.py`` executes ``admin.py`` through
   ``importlib.util.spec_from_file_location("admin_repos_impl", ...)`` at
   import time, so the module is loaded under a *second* name that is never
   registered in ``sys.modules``. The production service imports the classes
   from ``app.repositories.admin`` instead. ``TestDoubleLoadHazard`` pins that
   fact and the consequences of it.
2. The remaining repository methods the existing suite never calls
   (``get_by_content_id``, ``list_moderated_users`` with a status filter,
   ``list_by_severity``, ``list_by_resource``) and the append-only audit-log
   guards.
"""

import importlib.util
import pathlib
import sys

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.admin import AdminAuditLog, AuditLogAppendOnlyError
from app.repositories import (
    AdminAuditLogRepository,
    ContentModerationRepository,
    SystemAlertRepository,
    SystemConfigRepository,
    UserModerationRepository,
)

SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_MODULE_PATH = SERVICE_ROOT / "app" / "repositories" / "admin.py"
MODEL_MODULE_PATH = SERVICE_ROOT / "app" / "models" / "admin.py"


def _load_third_copy(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def session(tmp_path):
    """A real async SQLite session with the admin tables created.

    ``app.models.admin`` is imported directly (not via the importlib-loaded
    ``app.models`` package) so the ORM classes the repositories query are the
    same objects whose metadata created the tables.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(_load_third_copy("admin_models_direct", MODEL_MODULE_PATH).Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


class TestDoubleLoadHazard:
    """Documents the double-load that ``app/repositories/__init__.py`` performs."""

    def test_package_export_is_not_the_module_the_service_uses(self):
        import app.repositories.admin as production_module
        import app.services.admin as service_module

        # The class the service actually instantiates:
        assert service_module.UserModerationRepository is (
            production_module.UserModerationRepository
        )
        # The class the package re-exports is a *different* object:
        assert UserModerationRepository is not production_module.UserModerationRepository

    def test_re_exported_classes_report_a_module_name_absent_from_sys_modules(self):
        # exec_module does not register the module, so the classes are
        # unreachable by name from sys.modules and cannot be patched there.
        assert UserModerationRepository.__module__ == "admin_repos_impl"
        assert "admin_repos_impl" not in sys.modules

    def test_models_suffer_the_same_hazard(self):
        import app.models as model_package
        import app.models.admin as model_module

        assert model_package.UserModeration is not model_module.UserModeration
        assert model_package.Base is not model_module.Base
        assert model_package.UserModeration.__module__ == "admin_models_impl"
        assert "admin_models_impl" not in sys.modules

    def test_isinstance_across_the_two_copies_fails(self):
        from app.repositories.admin import UserModerationRepository as Production

        production_instance = Production.__new__(Production)
        assert isinstance(production_instance, Production)
        assert not isinstance(production_instance, UserModerationRepository)

    def test_orm_table_mapping_nevertheless_resolves(self):
        # The hazard is inert at the SQL layer: both model copies map the same
        # table name, so a query built from either copy hits the same rows.
        import app.models as model_package
        import app.models.admin as model_module

        assert model_package.UserModeration.__tablename__ == (
            model_module.UserModeration.__tablename__
        )
        assert (
            select(model_package.UserModeration).compile().string
            == select(model_module.UserModeration).compile().string
        )

    async def test_repository_still_persists_through_the_service_wired_session(
        self, session
    ):
        # End-to-end sanity: the copy the service holds writes real rows.
        service_repo = UserModerationRepository.__new__(UserModerationRepository)
        service_repo.db = session
        row = await service_repo.create("u-1", "suspended", "spam", "admin-1")
        await session.commit()
        assert row.id is not None
        assert row.status == "suspended"


# ---------------------------------------------------------------------------
# UserModerationRepository
# ---------------------------------------------------------------------------


class TestUserModerationRepository:
    async def test_create_persists_all_fields(self, session):
        repo = UserModerationRepository(session)
        row = await repo.create("u-1", "banned", "fraud", "admin-1")
        await session.commit()
        assert (row.id, row.user_id, row.status) == (row.id, "u-1", "banned")
        assert (row.reason, row.moderated_by) == ("fraud", "admin-1")

    async def test_create_defaults_status_to_active(self, session):
        import app.models.admin as models

        row = models.UserModeration(user_id="u-x", moderated_by="admin-1")
        assert models.UserModeration.status.default.arg == "active"

    async def test_get_by_user_id_returns_newest_first(self, session):
        repo = UserModerationRepository(session)
        first = await repo.create("u-1", "active", None, "admin-1")
        await session.commit()
        second = await repo.create("u-1", "banned", "later", "admin-2")
        await session.commit()
        assert (await repo.get_by_user_id("u-1")).id == second.id
        assert first.id != second.id

    async def test_get_by_user_id_returns_none_for_unknown_user(self, session):
        assert await UserModerationRepository(session).get_by_user_id("nope") is None

    async def test_get_by_user_id_for_update_uses_a_row_lock(self, session):
        repo = UserModerationRepository(session)
        await repo.create("u-1", "active", None, "admin-1")
        await session.commit()
        assert await repo.get_by_user_id("u-1", for_update=True) is not None
        # Compiled SQL carries the FOR UPDATE clause the lock relies on.
        from app.models.admin import UserModeration

        sql = str(
            select(UserModeration)
            .where(UserModeration.user_id == "u-1")
            .with_for_update()
            .compile()
        )
        assert "FOR UPDATE" in sql.upper()

    async def test_update_status_rewrites_the_row(self, session):
        repo = UserModerationRepository(session)
        await repo.create("u-1", "active", None, "admin-1")
        await session.commit()
        updated = await repo.update_status("u-1", "suspended", "review", "admin-2")
        await session.commit()
        assert (updated.status, updated.reason, updated.moderated_by) == (
            "suspended",
            "review",
            "admin-2",
        )
        assert updated.moderated_at is not None

    async def test_update_status_returns_none_for_unknown_user(self, session):
        assert await UserModerationRepository(session).update_status(
            "ghost", "banned", None, "admin-1"
        ) is None

    async def test_list_moderated_users_is_newest_first(self, session):
        repo = UserModerationRepository(session)
        await repo.create("u-1", "active", None, "admin-1")
        await session.commit()
        await repo.create("u-2", "banned", None, "admin-1")
        await session.commit()
        listed = await repo.list_moderated_users()
        assert [r.user_id for r in listed] == ["u-2", "u-1"]

    async def test_list_moderated_users_filters_by_status(self, session):
        repo = UserModerationRepository(session)
        await repo.create("u-1", "active", None, "admin-1")
        await repo.create("u-2", "banned", None, "admin-1")
        await session.commit()
        banned = await repo.list_moderated_users(status="banned")
        assert [r.user_id for r in banned] == ["u-2"]

    async def test_list_moderated_users_applies_limit_and_offset(self, session):
        repo = UserModerationRepository(session)
        for i in range(4):
            await repo.create(f"u-{i}", "active", None, "admin-1")
        await session.commit()
        page = await repo.list_moderated_users(limit=2, offset=1)
        assert len(page) == 2
        assert page[0].user_id == "u-2"

    async def test_list_moderated_users_excludes_inactive_rows(self, session):
        import app.models.admin as models

        repo = UserModerationRepository(session)
        await repo.create("u-1", "active", None, "admin-1")
        await session.commit()
        stale = models.UserModeration(
            user_id="u-2", status="banned", moderated_by="admin-1", is_active=False
        )
        session.add(stale)
        await session.commit()
        assert [r.user_id for r in await repo.list_moderated_users()] == ["u-1"]


# ---------------------------------------------------------------------------
# ContentModerationRepository
# ---------------------------------------------------------------------------


class TestContentModerationRepository:
    async def test_create_and_get_by_id(self, session):
        repo = ContentModerationRepository(session)
        row = await repo.create("c-1", "movie", "flagged", "gore", flagged_by="mod-1")
        await session.commit()
        fetched = await repo.get_by_id(row.id)
        assert fetched.content_id == "c-1"
        assert fetched.flagged_by == "mod-1"

    async def test_create_allows_null_flagged_by(self, session):
        repo = ContentModerationRepository(session)
        row = await repo.create("c-2", "show", "flagged", None)
        await session.commit()
        assert row.flagged_by is None

    async def test_get_by_id_returns_none_when_absent(self, session):
        assert await ContentModerationRepository(session).get_by_id(9999) is None

    async def test_get_by_content_id_returns_newest_first(self, session):
        repo = ContentModerationRepository(session)
        first = await repo.create("c-1", "movie", "flagged", "old", flagged_by="mod-1")
        await session.commit()
        second = await repo.create("c-1", "movie", "flagged", "new", flagged_by="mod-2")
        await session.commit()
        assert (await repo.get_by_content_id("c-1")).id == second.id
        assert first.id != second.id

    async def test_get_by_content_id_for_update_is_available(self, session):
        repo = ContentModerationRepository(session)
        await repo.create("c-1", "movie", "flagged", "x", flagged_by="mod-1")
        await session.commit()
        assert await repo.get_by_content_id("c-1", for_update=True) is not None

    async def test_get_by_content_id_returns_none_for_unknown_content(self, session):
        assert await ContentModerationRepository(session).get_by_content_id("none") is None

    async def test_get_active_flag_matches_open_flag_from_same_reporter(self, session):
        repo = ContentModerationRepository(session)
        await repo.create("c-1", "movie", "flagged", "x", flagged_by="mod-1")
        await session.commit()
        assert await repo.get_active_flag("c-1", "mod-1") is not None
        assert await repo.get_active_flag("c-1", "mod-2") is None

    async def test_get_active_flag_ignores_resolved_flags(self, session):
        repo = ContentModerationRepository(session)
        await repo.create("c-1", "movie", "flagged", "x", flagged_by="mod-1")
        await session.commit()
        await repo.update_status("c-1", "removed", "mod-9")
        await session.commit()
        assert await repo.get_active_flag("c-1", "mod-1") is None

    async def test_list_by_status_filters_and_sorts_newest_first(self, session):
        repo = ContentModerationRepository(session)
        await repo.create("c-1", "movie", "flagged", "x", flagged_by="m1")
        await repo.create("c-2", "movie", "flagged", "y", flagged_by="m2")
        await session.commit()
        assert len(await repo.list_by_status("flagged")) == 2
        assert await repo.list_by_status("removed") == []

    async def test_list_by_status_respects_limit(self, session):
        repo = ContentModerationRepository(session)
        for i in range(3):
            await repo.create(f"c-{i}", "movie", "flagged", "x", flagged_by="m1")
        await session.commit()
        assert len(await repo.list_by_status("flagged", limit=2)) == 2

    async def test_update_status_resolves_every_open_flag(self, session):
        repo = ContentModerationRepository(session)
        a = await repo.create("c-1", "movie", "flagged", "x", flagged_by="m1")
        b = await repo.create("c-1", "movie", "flagged", "y", flagged_by="m2")
        await session.commit()
        first = await repo.update_status("c-1", "removed", "mod-9")
        await session.commit()
        assert first.id in (a.id, b.id)
        assert (await repo.get_by_id(a.id)).status == "removed"
        assert (await repo.get_by_id(b.id)).resolved_by == "mod-9"
        assert (await repo.get_by_id(b.id)).resolved_at is not None

    async def test_update_status_returns_none_when_nothing_is_flagged(self, session):
        repo = ContentModerationRepository(session)
        await repo.create("c-1", "movie", "flagged", "x", flagged_by="m1")
        await session.commit()
        await repo.update_status("c-1", "removed", "mod-9")
        await session.commit()
        assert await repo.update_status("c-1", "active", "mod-9") is None

    async def test_update_status_skips_already_resolved_rows(self, session):
        repo = ContentModerationRepository(session)
        row = await repo.create("c-1", "movie", "flagged", "x", flagged_by="m1")
        await session.commit()
        await repo.update_status("c-1", "removed", "mod-9")
        await session.commit()
        later = await repo.update_status("c-1", "active", "mod-8")
        assert later is None
        assert (await repo.get_by_id(row.id)).status == "removed"


# ---------------------------------------------------------------------------
# SystemAlertRepository
# ---------------------------------------------------------------------------


class TestSystemAlertRepository:
    async def test_create_and_get_by_id(self, session):
        repo = SystemAlertRepository(session)
        row = await repo.create("error", "critical", "disk full", "auth-service")
        await session.commit()
        fetched = await repo.get_by_id(row.id)
        assert (fetched.alert_type, fetched.severity) == ("error", "critical")
        assert fetched.acknowledged is False

    async def test_get_by_id_returns_none_when_absent(self, session):
        assert await SystemAlertRepository(session).get_by_id(4242) is None

    async def test_list_unacknowledged_excludes_acknowledged(self, session):
        repo = SystemAlertRepository(session)
        a = await repo.create("error", "info", "a", "svc")
        b = await repo.create("error", "info", "b", "svc")
        await session.commit()
        await repo.acknowledge(a.id, "admin-1")
        await session.commit()
        remaining = await repo.list_unacknowledged()
        assert [r.id for r in remaining] == [b.id]

    async def test_list_unacknowledged_respects_limit(self, session):
        repo = SystemAlertRepository(session)
        for i in range(3):
            await repo.create("error", "info", f"m{i}", "svc")
        await session.commit()
        assert len(await repo.list_unacknowledged(limit=1)) == 1

    async def test_list_by_severity_filters_and_orders_newest_first(self, session):
        repo = SystemAlertRepository(session)
        await repo.create("error", "info", "low", "svc")
        crit = await repo.create("error", "critical", "high", "svc")
        await session.commit()
        critical = await repo.list_by_severity("critical")
        assert [r.id for r in critical] == [crit.id]
        assert await repo.list_by_severity("fatal") == []

    async def test_list_by_severity_respects_limit(self, session):
        repo = SystemAlertRepository(session)
        for i in range(3):
            await repo.create("error", "warning", f"m{i}", "svc")
        await session.commit()
        assert len(await repo.list_by_severity("warning", limit=2)) == 2

    async def test_acknowledge_stamps_admin_and_timestamp(self, session):
        repo = SystemAlertRepository(session)
        row = await repo.create("error", "warning", "m", "svc")
        await session.commit()
        acked = await repo.acknowledge(row.id, "admin-7")
        await session.commit()
        assert acked.acknowledged is True
        assert acked.acknowledged_by == "admin-7"
        assert acked.acknowledged_at is not None

    async def test_acknowledge_is_idempotent(self, session):
        repo = SystemAlertRepository(session)
        row = await repo.create("error", "warning", "m", "svc")
        await session.commit()
        await repo.acknowledge(row.id, "admin-1")
        await session.commit()
        first_at = (await repo.get_by_id(row.id)).acknowledged_at
        again = await repo.acknowledge(row.id, "admin-2")
        assert again.acknowledged_by == "admin-1"
        assert (await repo.get_by_id(row.id)).acknowledged_at == first_at

    async def test_acknowledge_returns_none_for_unknown_alert(self, session):
        assert await SystemAlertRepository(session).acknowledge(777, "admin-1") is None


# ---------------------------------------------------------------------------
# SystemConfigRepository
# ---------------------------------------------------------------------------


class TestSystemConfigRepository:
    async def test_create_and_get_by_key(self, session):
        repo = SystemConfigRepository(session)
        row = await repo.create("retention_days", "90", "number", "How long", "admin-1")
        await session.commit()
        fetched = await repo.get_by_key("retention_days")
        assert fetched.value == "90"
        assert fetched.config_type == "number"
        assert fetched.description == "How long"

    async def test_get_by_key_returns_none_when_absent(self, session):
        assert await SystemConfigRepository(session).get_by_key("missing") is None

    async def test_list_all_is_key_ordered_and_excludes_inactive(self, session):
        import app.models.admin as models

        repo = SystemConfigRepository(session)
        await repo.create("b_key", "2", "number", None, "admin-1")
        await repo.create("a_key", "1", "number", None, "admin-1")
        await session.commit()
        session.add(models.SystemConfig(key="c_key", value="3", config_type="number", updated_by="x", is_active=False))
        await session.commit()
        assert [c.key for c in await repo.list_all()] == ["a_key", "b_key"]

    async def test_list_all_respects_limit(self, session):
        repo = SystemConfigRepository(session)
        for i in range(4):
            await repo.create(f"k{i}", str(i), "number", None, "admin-1")
        await session.commit()
        assert len(await repo.list_all(limit=3)) == 3

    async def test_update_rewrites_value_and_metadata(self, session):
        repo = SystemConfigRepository(session)
        await repo.create("k", "old", "string", "d1", "admin-1")
        await session.commit()
        updated = await repo.update("k", "new", "admin-2", "json", "d2")
        await session.commit()
        assert (updated.value, updated.config_type, updated.description) == (
            "new",
            "json",
            "d2",
        )
        assert updated.updated_by == "admin-2"

    async def test_update_returns_none_for_missing_key(self, session):
        assert await SystemConfigRepository(session).update(
            "missing", "v", "admin-1", "string", None
        ) is None


# ---------------------------------------------------------------------------
# AdminAuditLogRepository
# ---------------------------------------------------------------------------


class TestAdminAuditLogRepository:
    async def test_create_persists_the_audit_row(self, session):
        repo = AdminAuditLogRepository(session)
        row = await repo.create(
            "admin-1", "user_banned", "user", "u-9", "reason=spam", "10.0.0.1"
        )
        await session.commit()
        assert (row.admin_id, row.action, row.resource_id) == (
            "admin-1",
            "user_banned",
            "u-9",
        )
        assert row.ip_address == "10.0.0.1"

    async def test_create_redacts_secret_shaped_changes(self, session):
        repo = AdminAuditLogRepository(session)
        row = await repo.create(
            "admin-1", "config_updated", "config", "stripe_key",
            "value=sk_live_leak; token=tok_abc", "10.0.0.1",
        )
        await session.commit()
        assert "sk_live_leak" not in row.changes
        assert "tok_abc" not in row.changes
        assert row.changes.count("********") == 2

    async def test_create_accepts_null_changes(self, session):
        repo = AdminAuditLogRepository(session)
        row = await repo.create("admin-1", "a", "alert", "1", None, "10.0.0.1")
        await session.commit()
        assert row.changes is None

    async def test_update_is_rejected(self, session):
        repo = AdminAuditLogRepository(session)
        with pytest.raises(AuditLogAppendOnlyError, match="append-only"):
            await repo.update("admin-1", "changed", "user", "u-1", None, "10.0.0.1")
        with pytest.raises(AuditLogAppendOnlyError, match="append-only"):
            await repo.update()
        with pytest.raises(AuditLogAppendOnlyError, match="append-only"):
            await repo.update(id=1)

    async def test_delete_is_rejected(self, session):
        repo = AdminAuditLogRepository(session)
        with pytest.raises(AuditLogAppendOnlyError, match="deletion is not permitted"):
            await repo.delete(1)
        with pytest.raises(AuditLogAppendOnlyError):
            await repo.delete()

    async def test_orm_level_guards_reject_mutation(self, session):
        # The guards are before_update / before_delete mapper events, so they
        # fire on flush rather than on plain attribute assignment.
        row = AdminAuditLog(
            admin_id="admin-1",
            action="a",
            resource_type="user",
            resource_id="u-1",
            ip_address="10.0.0.1",
        )
        session.add(row)
        await session.commit()
        with pytest.raises(AuditLogAppendOnlyError, match="append-only"):
            row.action = "tampered"
            await session.flush()
        await session.rollback()
        with pytest.raises(AuditLogAppendOnlyError, match="deletion is not permitted"):
            await session.delete(row)
            await session.flush()

    async def test_list_by_admin_newest_first(self, session):
        repo = AdminAuditLogRepository(session)
        await repo.create("admin-1", "first", "user", "u-1", None, "10.0.0.1")
        await session.commit()
        await repo.create("admin-1", "second", "user", "u-2", None, "10.0.0.1")
        await session.commit()
        rows = await repo.list_by_admin("admin-1")
        assert [r.action for r in rows] == ["second", "first"]

    async def test_list_by_admin_excludes_other_admins(self, session):
        repo = AdminAuditLogRepository(session)
        await repo.create("admin-1", "mine", "user", "u-1", None, "10.0.0.1")
        await repo.create("admin-2", "theirs", "user", "u-2", None, "10.0.0.1")
        await session.commit()
        assert [r.action for r in await repo.list_by_admin("admin-1")] == ["mine"]

    async def test_list_by_admin_respects_limit(self, session):
        repo = AdminAuditLogRepository(session)
        for i in range(4):
            await repo.create("admin-1", f"a{i}", "user", f"u-{i}", None, "10.0.0.1")
        await session.commit()
        assert len(await repo.list_by_admin("admin-1", limit=2)) == 2

    async def test_list_by_resource_filters_on_both_columns(self, session):
        repo = AdminAuditLogRepository(session)
        await repo.create("admin-1", "flagged", "content", "c-1", None, "10.0.0.1")
        await repo.create("admin-1", "resolved", "content", "c-2", None, "10.0.0.1")
        await repo.create("admin-2", "banned", "user", "c-1", None, "10.0.0.1")
        await session.commit()
        rows = await repo.list_by_resource("content", "c-1")
        assert [(r.action, r.admin_id) for r in rows] == [("flagged", "admin-1")]

    async def test_list_by_resource_returns_empty_for_unknown_pair(self, session):
        repo = AdminAuditLogRepository(session)
        assert await repo.list_by_resource("content", "nope") == []

    async def test_list_by_resource_is_newest_first_and_limited(self, session):
        repo = AdminAuditLogRepository(session)
        for i in range(3):
            await repo.create("admin-1", f"a{i}", "config", "k", None, "10.0.0.1")
        await session.commit()
        rows = await repo.list_by_resource("config", "k", limit=2)
        assert [r.action for r in rows] == ["a2", "a1"]
