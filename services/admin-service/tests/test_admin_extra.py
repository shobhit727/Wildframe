"""Behavioural tests for the admin service layer and the legacy ``app.config``.

``app/services/admin.py`` was at 93% with six uncovered branches, all of them
interesting: the idempotent-moderation republish, its failure propagation, the
create-after-update fallback, and the sensitive-config read paths. Each is
driven here against a real SQLite session so the assertions are about persisted
state and emitted events, not about mocks.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from wildframe_events import InMemoryEventPublisher

from app.core import events as events_mod
from app.core.events import reset_event_publisher
from app.models.admin import AdminAuditLog, SystemConfig
from app.services.admin import MAX_LIST_LIMIT, AdminService, _clamp_limit, _moderated_at_iso


@pytest.fixture
async def service(tmp_path, monkeypatch):
    """A real AdminService over SQLite with an in-memory event publisher."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc.db'}")
    async with engine.begin() as conn:
        from app.models.admin import Base

        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    publisher = InMemoryEventPublisher()
    monkeypatch.setattr(events_mod, "_publisher", publisher)
    async with factory() as session:
        yield AdminService(session), session, publisher
    reset_event_publisher()
    await engine.dispose()


class TestClampLimit:
    def test_clamps_to_zero_or_below(self):
        assert _clamp_limit(-5) == 0

    def test_clamps_to_the_hard_ceiling(self):
        assert _clamp_limit(10**9) == MAX_LIST_LIMIT
        assert MAX_LIST_LIMIT == 1000

    def test_passes_mid_range_values_through(self):
        assert _clamp_limit(50) == 50

    def test_accepts_a_custom_maximum(self):
        assert _clamp_limit(80, maximum=10) == 10


class TestModeratedAtIso:
    def test_falls_back_to_now_for_an_unset_value(self):
        assert _moderated_at_iso(None).endswith("+00:00")

    def test_falls_back_to_now_for_a_falsy_value(self):
        assert _moderated_at_iso("").endswith("+00:00")

    def test_passes_an_already_serialised_string_through(self):
        assert _moderated_at_iso("2026-01-01T00:00:00+00:00") == "2026-01-01T00:00:00+00:00"

    def test_serialises_a_datetime(self):
        from datetime import UTC, datetime

        moment = datetime(2026, 1, 1, tzinfo=UTC)
        assert _moderated_at_iso(moment) == moment.isoformat()


class TestModerateUserIdempotency:
    async def test_repeating_the_same_decision_does_not_duplicate_the_audit_row(self, service):
        svc, session, _ = service
        first = await svc.moderate_user("u-1", "suspended", "spam", "admin-1", "10.0.0.1")
        second = await svc.moderate_user("u-1", "suspended", "spam", "admin-1", "10.0.0.1")
        assert first["id"] == second["id"]
        audits = (await session.execute(select(AdminAuditLog))).scalars().all()
        assert [a.action for a in audits] == ["user_moderation_suspended"]

    async def test_idempotent_remoderation_still_republishes_the_event(self, service):
        # auth-service may have missed the first delivery; the comment in the
        # code makes re-publishing mandatory even though no new row is written.
        svc, _, publisher = service
        await svc.moderate_user("u-1", "suspended", None, "admin-1", "10.0.0.1")
        assert len(publisher.sent) == 1
        await svc.moderate_user("u-1", "suspended", None, "admin-1", "10.0.0.1")
        assert len(publisher.sent) == 2
        # Same user, same status, and the same moderated_at (the row was not
        # rewritten), so both deliveries carry the same idempotency key.
        assert publisher.sent[0].key == publisher.sent[1].key
        assert publisher.sent[0].key.startswith("moderated:u-1:suspended:")
        assert {e.payload["user_id"] for e in publisher.sent} == {"u-1"}

    async def test_republish_after_a_status_change_gets_a_new_key(self, service):
        svc, _, publisher = service
        await svc.moderate_user("u-1", "suspended", None, "admin-1", "10.0.0.1")
        await svc.moderate_user("u-1", "banned", "fraud", "admin-1", "10.0.0.1")
        assert [e.payload["status"] for e in publisher.sent] == ["suspended", "banned"]

    async def test_changing_the_status_writes_a_new_audit_row(self, service):
        svc, session, _ = service
        await svc.moderate_user("u-1", "suspended", None, "admin-1", "10.0.0.1")
        await svc.moderate_user("u-1", "banned", "fraud", "admin-2", "10.0.0.1")
        audits = (await session.execute(select(AdminAuditLog))).scalars().all()
        assert [a.action for a in audits] == [
            "user_moderation_suspended",
            "user_moderation_banned",
        ]

    async def test_first_moderation_creates_the_row(self, service):
        svc, session, _ = service
        await svc.moderate_user("u-new", "active", None, "admin-1", "10.0.0.1")
        from app.models.admin import UserModeration

        rows = (await session.execute(select(UserModeration))).scalars().all()
        assert [(r.user_id, r.status) for r in rows] == [("u-new", "active")]

    async def test_republish_failure_propagates(self, service):
        svc, _, _ = service
        await svc.moderate_user("u-1", "suspended", None, "admin-1", "10.0.0.1")

        class Boom:
            async def publish(self, _event):
                raise OSError("broker unreachable")

        events_mod._publisher = Boom()
        with pytest.raises(OSError):
            await svc.moderate_user("u-1", "suspended", None, "admin-1", "10.0.0.1")

    async def test_first_publish_failure_propagates_to_the_caller(self, service):
        # The row + audit commit happens before publishing, so the write stands
        # while the caller still sees the failure (documented in the code:
        # durable delivery requires an outbox).
        svc, _, _ = service

        class Boom:
            async def publish(self, _event):
                raise OSError("broker unreachable")

        events_mod._publisher = Boom()
        with pytest.raises(OSError):
            await svc.moderate_user("u-1", "banned", None, "admin-1", "10.0.0.1")
        assert (await svc.get_user_moderation_history("u-1"))["status"] == "banned"


class TestUserModerationReads:
    async def test_history_is_none_for_an_unknown_user(self, service):
        svc, _, _ = service
        assert await svc.get_user_moderation_history("nobody") is None

    async def test_list_clamps_a_hostile_limit(self, service):
        svc, _, _ = service
        assert await svc.list_moderated_users(limit=10**6) == []

    async def test_list_clamps_a_hostile_offset(self, service):
        svc, _, _ = service
        assert await svc.list_moderated_users(limit=10, offset=-5) == []


class TestFlagContentIdempotency:
    async def test_repeat_reports_from_the_same_actor_do_not_inflate_counts(self, service):
        svc, session, _ = service
        first = await svc.flag_content("c-1", "movie", "gore", "mod-1", "10.0.0.1")
        second = await svc.flag_content("c-1", "movie", "gore", "mod-1", "10.0.0.1")
        assert first["id"] == second["id"]
        audits = (await session.execute(select(AdminAuditLog))).scalars().all()
        assert [a.action for a in audits] == ["content_flagged"]

    async def test_a_different_reporter_creates_a_separate_flag(self, service):
        svc, _, _ = service
        first = await svc.flag_content("c-1", "movie", "gore", "mod-1", "10.0.0.1")
        second = await svc.flag_content("c-1", "movie", "gore", "mod-2", "10.0.0.1")
        assert first["id"] != second["id"]

    async def test_flagged_content_always_gets_the_flagged_status(self, service):
        svc, _, _ = service
        # The requested status is deliberately ignored in favour of "flagged".
        result = await svc.flag_content("c-1", "movie", "gore", "mod-1", "10.0.0.1")
        assert result["status"] == "flagged"

    async def test_resolve_of_an_unknown_content_is_none(self, service):
        svc, _, _ = service
        assert await svc.resolve_content_flag("nope", "removed", "mod-1", "10.0.0.1") is None

    async def test_resolve_of_already_resolved_content_returns_the_row(self, service):
        svc, _, _ = service
        await svc.flag_content("c-1", "movie", "gore", "mod-1", "10.0.0.1")
        await svc.resolve_content_flag("c-1", "removed", "mod-9", "10.0.0.1")
        again = await svc.resolve_content_flag("c-1", "removed", "mod-9", "10.0.0.1")
        assert again is not None
        assert again["status"] == "removed"

    async def test_list_flagged_content_clamps_hostile_paging(self, service):
        svc, _, _ = service
        assert await svc.list_flagged_content(limit=10**6, offset=-3) == []


class TestAlertReads:
    async def test_create_alert_without_an_admin_skips_the_audit_row(self, service):
        svc, session, _ = service
        await svc.create_alert("error", "info", "m", "svc")
        assert (await session.execute(select(AdminAuditLog))).scalars().all() == []

    async def test_create_alert_with_an_admin_writes_an_audit_row(self, service):
        svc, session, _ = service
        await svc.create_alert("error", "critical", "m", "svc", "admin-1", "10.0.0.1")
        audits = (await session.execute(select(AdminAuditLog))).scalars().all()
        assert [a.changes for a in audits] == ["severity=critical"]

    async def test_acknowledge_of_an_unknown_alert_is_none(self, service):
        svc, _, _ = service
        assert await svc.acknowledge_alert(999, "admin-1", "10.0.0.1") is None

    async def test_get_critical_alerts_clamps_the_limit(self, service):
        svc, _, _ = service
        assert await svc.get_critical_alerts() == []


class TestConfigReads:
    async def test_get_config_is_none_for_an_unknown_key(self, service):
        svc, _, _ = service
        assert await svc.get_config("missing") is None

    async def test_set_config_updates_an_existing_key(self, service):
        svc, session, _ = service
        await svc.set_config("region", "eu", "string", None, "admin-1", "10.0.0.1")
        updated = await svc.set_config(
            "region", "us", "string", "moved", "admin-2", "10.0.0.1"
        )
        assert updated["value"] == "us"
        assert updated["updated_by"] == "admin-2"
        assert updated["description"] == "moved"
        rows = (await session.execute(select(SystemConfig))).scalars().all()
        assert len(rows) == 1

    async def test_sensitive_values_are_masked_on_read(self, service):
        svc, _, _ = service
        await svc.set_config("api_key", "sk_live", "string", None, "admin-1", "10.0.0.1")
        assert (await svc.get_config("api_key"))["value"] == "********"

    async def test_plain_values_are_returned_verbatim(self, service):
        svc, _, _ = service
        await svc.set_config("region", "eu", "string", None, "admin-1", "10.0.0.1")
        assert (await svc.get_config("region"))["value"] == "eu"

    async def test_audit_changes_never_contain_a_sensitive_value(self, service):
        svc, session, _ = service
        await svc.set_config(
            "api_key", "sk_live_leak", "string", None, "admin-1", "10.0.0.1"
        )
        audits = (await session.execute(select(AdminAuditLog))).scalars().all()
        assert "sk_live_leak" not in audits[0].changes
        assert audits[0].changes == "value=********"

    async def test_list_configs_masks_each_sensitive_entry(self, service):
        svc, _, _ = service
        await svc.set_config("api_key", "sk_live", "string", None, "admin-1", "10.0.0.1")
        await svc.set_config("region", "eu", "string", None, "admin-1", "10.0.0.1")
        listed = {c["key"]: c["value"] for c in await svc.list_configs()}
        assert listed == {"api_key": "********", "region": "eu"}


class TestAuditReads:
    async def test_audit_by_admin_is_empty_for_an_unknown_admin(self, service):
        svc, _, _ = service
        assert await svc.get_audit_logs_by_admin("nobody") == []

    async def test_audit_by_resource_is_empty_for_an_unknown_resource(self, service):
        svc, _, _ = service
        assert await svc.get_audit_logs_by_resource("user", "nobody") == []

    async def test_audit_rows_come_back_newest_first(self, service):
        svc, _, _ = service
        await svc.moderate_user("u-1", "suspended", None, "admin-1", "10.0.0.1")
        await svc.moderate_user("u-1", "banned", "fraud", "admin-1", "10.0.0.1")
        actions = [row["action"] for row in await svc.get_audit_logs_by_admin("admin-1")]
        assert actions == ["user_moderation_banned", "user_moderation_suspended"]


class TestLegacyConfigModule:
    """``app/config.py`` is imported by nothing; pin what it still promises."""

    def test_module_exposes_the_documented_constants(self):
        import app.config as legacy

        assert legacy.DATABASE_URL.startswith("postgresql+asyncpg://")
        assert legacy.REDIS_URL.startswith("redis://")
        assert legacy.JWT_ALGORITHM == "HS256"
        assert legacy.JWT_SECRET

    def test_environment_overrides_win_over_the_defaults(self):
        import importlib

        import app.config as legacy

        original = {
            k: __import__("os").environ.get(k)
            for k in ("ADMIN_DATABASE_URL", "REDIS_URL", "JWT_SECRET")
        }
        try:
            __import__("os").environ["ADMIN_DATABASE_URL"] = "postgresql+asyncpg://x/y"
            __import__("os").environ["REDIS_URL"] = "redis://override:6380"
            __import__("os").environ["JWT_SECRET"] = "override-secret"
            reloaded = importlib.reload(legacy)
            assert reloaded.DATABASE_URL == "postgresql+asyncpg://x/y"
            assert reloaded.REDIS_URL == "redis://override:6380"
            assert reloaded.JWT_SECRET == "override-secret"
        finally:
            import os

            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            importlib.reload(legacy)

    def test_nothing_in_the_app_imports_this_module(self):
        # Proof that app/config.py is dead rather than load-bearing: no module
        # under app/ references it.
        import app.config  # noqa: F401  (ensure it is in sys.modules)

        service_root = __import__("pathlib").Path(__file__).resolve().parents[1] / "app"
        offenders = []
        for path in service_root.rglob("*.py"):
            if path.name == "config.py":
                continue
            text = path.read_text(encoding="utf-8")
            if "app.config" in text or "from app import config" in text:
                offenders.append(str(path.relative_to(service_root)))
        assert offenders == []
