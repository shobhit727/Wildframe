"""Route-level tests for the Admin Service HTTP API.

Exercises the real FastAPI router via TestClient with ``get_db`` overridden
with a fake session and ``AdminService`` monkeypatched. Covers user
moderation, content moderation, alerts, config, audit logs and stats:
status codes, response models, 401/404 handling and route->service args.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.routes.admin import get_current_admin_id
from app.core.database import get_db
from app.main import app


@pytest.fixture
def fake_db():
    return MagicMock()


@pytest.fixture
def fake_service():
    return MagicMock()


@pytest.fixture(autouse=True)
def override_deps(fake_db, fake_service):
    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_current_admin_id] = lambda: "admin-1"
    with patch("app.api.routes.admin.AdminService", return_value=fake_service):
        yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def make_moderation():
    m = MagicMock()
    m.id = 1
    m.user_id = str(uuid4())
    m.status = "suspended"
    m.reason = "Spam"
    m.moderated_by = "admin-1"
    m.moderated_at = datetime(2026, 1, 1, tzinfo=UTC)
    m.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    m.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    return m


def make_content_moderation():
    m = MagicMock()
    m.id = 1
    m.content_id = str(uuid4())
    m.content_type = "movie"
    m.status = "flagged"
    m.reason = "Copyright"
    m.flagged_at = datetime(2026, 1, 1, tzinfo=UTC)
    m.resolved_at = None
    m.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    return m


def make_alert():
    a = MagicMock()
    a.id = 1
    a.alert_type = "high_error_rate"
    a.severity = "critical"
    a.message = "Errors rising"
    a.service = "content-service"
    a.acknowledged = False
    a.acknowledged_by = None
    a.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    return a


def make_config():
    c = MagicMock()
    c.id = 1
    c.key = "max_streams_per_user"
    c.value = "4"
    c.config_type = "integer"
    c.description = None
    c.updated_by = "admin-1"
    c.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    c.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    return c


def make_audit_log():
    a = MagicMock()
    a.id = 1
    a.admin_id = "admin-1"
    a.action = "moderate_user"
    a.resource_type = "user"
    a.resource_id = str(uuid4())
    a.changes = None
    a.ip_address = "0.0.0.0"
    a.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    return a


def make_stats():
    s = MagicMock()
    s.total_users = 1000
    s.active_users = 800
    s.suspended_users = 20
    s.flagged_content = 5
    s.active_alerts = 3
    s.system_uptime_hours = 72.5
    return s


class TestAuth:
    def test_moderate_user_requires_admin_token(self, client):
        app.dependency_overrides.pop(get_current_admin_id, None)
        try:
            response = client.post(
                "/api/v1/admin/users/moderate",
                json={"user_id": str(uuid4()), "status": "suspended"},
            )
        finally:
            app.dependency_overrides[get_current_admin_id] = lambda: "admin-1"

        assert response.status_code == 401

    def test_non_admin_role_rejected_403(self):
        from datetime import UTC, datetime, timedelta

        import jwt as pyjwt
        from app.core.settings import settings
        from fastapi import HTTPException

        token = pyjwt.encode(
            {
                "sub": str(uuid4()),
                "role": "user",
                "type": "access",
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        import asyncio

        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_current_admin_id(f"Bearer {token}"))

        assert exc.value.status_code == 403

    def test_admin_role_accepted(self):
        from datetime import UTC, datetime, timedelta

        import jwt as pyjwt
        from app.core.settings import settings

        token = pyjwt.encode(
            {
                "sub": str(uuid4()),
                "role": "admin",
                "type": "access",
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        import asyncio

        result = asyncio.run(get_current_admin_id(f"Bearer {token}"))
        assert result is not None

    def test_garbage_token_rejected_401(self):
        from fastapi import HTTPException

        import asyncio

        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_current_admin_id("Bearer garbage"))
        assert exc.value.status_code == 401

    def test_refresh_token_rejected_401(self):
        """Token-type separation (#221): refresh tokens must 401, never 500."""
        from datetime import UTC, datetime, timedelta

        import jwt as pyjwt
        from app.core.settings import settings
        from fastapi import HTTPException

        token = pyjwt.encode(
            {
                "sub": str(uuid4()),
                "role": "admin",
                "type": "refresh",
                "aud": settings.JWT_AUDIENCE,
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        import asyncio

        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_current_admin_id(f"Bearer {token}"))

        assert exc.value.status_code == 401

    def test_token_with_wrong_audience_rejected_401(self):
        """Tokens carrying a foreign audience must not decode."""
        from datetime import UTC, datetime, timedelta

        import jwt as pyjwt
        from app.core.settings import settings
        from fastapi import HTTPException

        token = pyjwt.encode(
            {
                "sub": str(uuid4()),
                "role": "admin",
                "type": "access",
                "aud": "some-other-api",
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        import asyncio

        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_current_admin_id(f"Bearer {token}"))

        assert exc.value.status_code == 401


class TestNoBulkSurface:
    """#225 finding 5: no bulk admin mutations exist.

    Every admin mutation request schema is single-item; there are no list
    payloads and no collection endpoints. Bulk operations, if ever added,
    need bounded batch sizes and atomic/partial-failure semantics.
    """

    def test_no_collection_bulk_routes(self):
        from app.main import app

        bulk_routes = [
            route.path
            for route in app.routes
            if hasattr(route, "path") and any(k in route.path.lower() for k in ("/bulk", "/batch"))
        ]
        assert not bulk_routes, f"bulk admin routes present: {bulk_routes}"

    def test_mutation_schemas_are_single_item(self):
        from app.schemas.admin import (
            ContentModerationRequest,
            SystemAlertRequest,
            SystemConfigRequest,
            UserModerationRequest,
        )

        for schema in (
            UserModerationRequest,
            ContentModerationRequest,
            SystemAlertRequest,
            SystemConfigRequest,
        ):
            list_fields = [
                name
                for name, field in schema.model_fields.items()
                if "list" in str(field.annotation).lower()
            ]
            assert not list_fields, f"{schema.__name__} has list fields: {list_fields}"


class TestUserModerationRoutes:
    def test_moderate_user(self, client, fake_service):
        mod = make_moderation()
        fake_service.moderate_user = AsyncMock(return_value=mod)

        response = client.post(
            "/api/v1/admin/users/moderate",
            json={"user_id": mod.user_id, "status": "suspended", "reason": "Spam"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "suspended"
        fake_service.moderate_user.assert_awaited_once()

    def test_moderate_user_rejects_bad_status(self, client):
        response = client.post(
            "/api/v1/admin/users/moderate",
            json={"user_id": str(uuid4()), "status": "ghosted"},
        )

        assert response.status_code == 422

    def test_get_user_moderation(self, client, fake_service):
        mod = make_moderation()
        fake_service.get_user_moderation_history = AsyncMock(return_value=mod)

        response = client.get(f"/api/v1/admin/users/moderation/{mod.user_id}")

        assert response.status_code == 200

    def test_get_user_moderation_missing_returns_404(self, client, fake_service):
        fake_service.get_user_moderation_history = AsyncMock(return_value=None)

        response = client.get(f"/api/v1/admin/users/moderation/{uuid4()}")

        assert response.status_code == 404

    def test_list_moderated_users(self, client, fake_service):
        fake_service.list_moderated_users = AsyncMock(return_value=[make_moderation()])

        response = client.get("/api/v1/admin/users/moderated")

        assert response.status_code == 200
        assert len(response.json()) == 1
        fake_service.list_moderated_users.assert_awaited_once_with(None, 50, 0)


class TestContentModerationRoutes:
    def test_flag_content(self, client, fake_service):
        mod = make_content_moderation()
        fake_service.flag_content = AsyncMock(return_value=mod)

        response = client.post(
            "/api/v1/admin/content/flag",
            json={
                "content_id": mod.content_id,
                "content_type": "movie",
                "status": "flagged",
                "reason": "Copyright",
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "flagged"
        fake_service.flag_content.assert_awaited_once()

    def test_flag_content_rejects_bad_type(self, client):
        response = client.post(
            "/api/v1/admin/content/flag",
            json={"content_id": str(uuid4()), "content_type": "gif"},
        )

        assert response.status_code == 422

    def test_resolve_content_flag(self, client, fake_service):
        mod = make_content_moderation()
        mod.status = "removed"
        fake_service.resolve_content_flag = AsyncMock(return_value=mod)

        response = client.post(
            f"/api/v1/admin/content/resolve?content_id={mod.content_id}&status=removed"
        )

        assert response.status_code == 200
        assert response.json()["status"] == "removed"
        fake_service.resolve_content_flag.assert_awaited_once()

    def test_resolve_content_flag_rejects_bad_status(self, client):
        response = client.post(
            f"/api/v1/admin/content/resolve?content_id={uuid4()}&status=quarantined"
        )

        assert response.status_code == 422

    def test_resolve_content_flag_missing_returns_404(self, client, fake_service):
        fake_service.resolve_content_flag = AsyncMock(return_value=None)

        response = client.post(f"/api/v1/admin/content/resolve?content_id={uuid4()}&status=removed")

        assert response.status_code == 404

    def test_list_flagged_content(self, client, fake_service):
        fake_service.list_flagged_content = AsyncMock(return_value=[make_content_moderation()])

        response = client.get("/api/v1/admin/content/flagged")

        assert response.status_code == 200
        fake_service.list_flagged_content.assert_awaited_once_with(50, 0)


class TestAlertRoutes:
    def test_create_alert(self, client, fake_service):
        alert = make_alert()
        fake_service.create_alert = AsyncMock(return_value=alert)

        response = client.post(
            "/api/v1/admin/alerts",
            json={
                "alert_type": "high_error_rate",
                "severity": "critical",
                "message": "High error rate",
                "service": "content-service",
            },
        )

        assert response.status_code == 200
        assert response.json()["severity"] == "critical"

    def test_create_alert_rejects_bad_severity(self, client):
        response = client.post(
            "/api/v1/admin/alerts",
            json={"alert_type": "x", "severity": "fatal", "message": "m", "service": "s"},
        )

        assert response.status_code == 422

    def test_get_alerts(self, client, fake_service):
        fake_service.get_system_alerts = AsyncMock(return_value=[make_alert()])

        response = client.get("/api/v1/admin/alerts")

        assert response.status_code == 200
        fake_service.get_system_alerts.assert_awaited_once_with(50)

    def test_get_critical_alerts(self, client, fake_service):
        fake_service.get_critical_alerts = AsyncMock(return_value=[make_alert()])

        response = client.get("/api/v1/admin/alerts/critical")

        assert response.status_code == 200
        fake_service.get_critical_alerts.assert_awaited_once_with()

    def test_acknowledge_alert(self, client, fake_service):
        alert = make_alert()
        alert.acknowledged = True
        alert.acknowledged_by = "admin-1"
        fake_service.acknowledge_alert = AsyncMock(return_value=alert)

        response = client.post("/api/v1/admin/alerts/1/acknowledge")

        assert response.status_code == 200
        fake_service.acknowledge_alert.assert_awaited_once_with(1, "admin-1", "testclient")

    def test_acknowledge_alert_missing_returns_404(self, client, fake_service):
        fake_service.acknowledge_alert = AsyncMock(return_value=None)

        response = client.post("/api/v1/admin/alerts/999/acknowledge")

        assert response.status_code == 404


class TestConfigRoutes:
    def test_set_config(self, client, fake_service):
        config = make_config()
        fake_service.set_config = AsyncMock(return_value=config)

        response = client.post(
            "/api/v1/admin/config",
            json={"key": "max_streams_per_user", "value": "4", "config_type": "integer"},
        )

        assert response.status_code == 200
        assert response.json()["key"] == "max_streams_per_user"

    def test_set_config_rejects_bad_type(self, client):
        response = client.post(
            "/api/v1/admin/config",
            json={"key": "k", "value": "v", "config_type": "yaml"},
        )

        assert response.status_code == 422

    def test_get_config(self, client, fake_service):
        config = make_config()
        fake_service.get_config = AsyncMock(return_value=config)

        response = client.get("/api/v1/admin/config/max_streams_per_user")

        assert response.status_code == 200
        assert response.json()["value"] == "4"

    def test_get_config_missing_returns_404(self, client, fake_service):
        fake_service.get_config = AsyncMock(return_value=None)

        response = client.get("/api/v1/admin/config/nope")

        assert response.status_code == 404

    def test_list_configs(self, client, fake_service):
        fake_service.list_configs = AsyncMock(return_value=[make_config()])

        response = client.get("/api/v1/admin/config")

        assert response.status_code == 200
        fake_service.list_configs.assert_awaited_once_with(100)


class TestAuditRoutes:
    def test_get_audit_by_admin(self, client, fake_service):
        fake_service.get_audit_logs_by_admin = AsyncMock(return_value=[make_audit_log()])

        response = client.get("/api/v1/admin/audit/admin/admin-1")

        assert response.status_code == 200
        fake_service.get_audit_logs_by_admin.assert_awaited_once_with("admin-1", 50)

    def test_get_audit_by_resource(self, client, fake_service):
        fake_service.get_audit_logs_by_resource = AsyncMock(return_value=[make_audit_log()])

        response = client.get(f"/api/v1/admin/audit/resource/user/{uuid4()}")

        assert response.status_code == 200
        fake_service.get_audit_logs_by_resource.assert_awaited_once()

    def test_no_audit_write_routes_exist(self):
        """[#168] The audit trail has no HTTP write surface: an admin can
        read, but there is no endpoint that creates, alters, or deletes
        audit rows. If one is ever added it must be guarded and reviewed."""
        from fastapi.routing import APIRoute

        audit_write_routes = [
            route.path
            for route in app.routes
            if isinstance(route, APIRoute)
            and "audit" in route.path
            and route.methods - {"GET", "HEAD"}
        ]
        assert not audit_write_routes, f"audit write routes found: {audit_write_routes}"

    def test_audit_reads_are_admin_only(self, client):
        from app.api.routes.admin import get_current_admin_id

        app.dependency_overrides.pop(get_current_admin_id, None)
        response = client.get("/api/v1/admin/audit/admin/admin-1")
        assert response.status_code == 401


class TestStatsRoutes:
    def test_get_system_stats(self, client, fake_service):
        fake_service.get_system_stats = AsyncMock(return_value=make_stats())

        response = client.get("/api/v1/admin/stats")

        assert response.status_code == 200
        assert response.json()["total_users"] == 1000
