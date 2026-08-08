"""Route-level tests for the User Service HTTP API.

Exercises the real FastAPI router -> service call chain via TestClient with
``get_user_service`` and the auth dependencies overridden. Covers the HTTP
contract (status codes, response models, 401/403/404 handling) for profiles,
devices, preferences and subscriptions.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_current_user_id, get_user_service, require_self
from app.main import app


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def device_id():
    return uuid4()


@pytest.fixture
def fake_service():
    return MagicMock()


@pytest.fixture(autouse=True)
def override_deps(fake_service, user_id):
    app.dependency_overrides[get_user_service] = lambda: fake_service
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[require_self] = lambda jwt_user_id=user_id, request=None: jwt_user_id
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    # Not used as a context manager: the lifespan runs a DB health check that
    # raises RuntimeError without a database, and CI has no postgres.
    yield TestClient(app, base_url="http://localhost")


def make_profile(id_value=None):
    p = MagicMock()
    p.id = id_value or uuid4()
    p.user_id = uuid4()
    p.avatar_url = None
    p.bio = "Movie fan"
    p.phone_number = None
    p.date_of_birth = None
    p.country = "US"
    p.language = "en"
    p.timezone = "UTC"
    p.public_profile = True
    p.newsletter_subscribed = False
    p.marketing_emails = False
    p.completed_onboarding = False
    p.profile_completeness = 50
    p.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    p.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    return p


def make_device(id_value=None):
    d = MagicMock()
    d.id = id_value or uuid4()
    d.device_id = "device-123"
    d.device_name = "Chrome on MacBook"
    d.device_type = "web"
    d.os_name = "macOS"
    d.os_version = None
    d.browser_name = "Chrome"
    d.browser_version = None
    d.ip_address = "127.0.0.1"
    d.is_active = True
    d.is_trusted = False
    d.can_stream = True
    d.can_download = True
    d.last_active_at = None
    d.registration_date = datetime(2026, 1, 1, tzinfo=UTC)
    d.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    d.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    return d


def make_preferences(id_value=None):
    pr = MagicMock()
    pr.id = id_value or uuid4()
    pr.user_id = uuid4()
    pr.theme = "dark"
    pr.language = "en"
    pr.subtitle_language = "en"
    pr.subtitle_size = "medium"
    pr.closed_captions = True
    pr.autoplay = True
    pr.autoplay_next_episode = True
    pr.default_video_quality = "adaptive"
    pr.default_audio_language = "en"
    pr.content_rating = "PG-13"
    pr.allow_explicit_content = False
    pr.share_viewing_activity = True
    pr.allow_recommendations = True
    pr.data_collection = True
    pr.email_new_content = False
    pr.email_recommendations = False
    pr.push_notifications = True
    pr.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    pr.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    return pr


def make_subscription(id_value=None):
    s = MagicMock()
    s.id = id_value or uuid4()
    s.user_id = uuid4()
    s.subscription_tier = "basic"
    s.subscription_status = "active"
    s.max_concurrent_streams = 2
    s.can_download = True
    s.can_use_4k = False
    s.ad_free = False
    s.current_period_start = None
    s.current_period_end = None
    s.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    s.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    return s


class TestProfileRoutes:
    def test_create_profile_requires_auth(self, client):
        app.dependency_overrides.pop(get_current_user_id, None)
        try:
            response = client.post("/api/v1/profiles")
        finally:
            app.dependency_overrides[get_current_user_id] = lambda: uuid4()

        assert response.status_code == 401

    def test_create_profile_returns_201(self, client, fake_service, user_id):
        profile = make_profile()
        profile.user_id = user_id
        fake_service.create_user_profile = AsyncMock(return_value=profile)

        response = client.post("/api/v1/profiles")

        assert response.status_code == 201
        assert response.json()["user_id"] == str(user_id)
        fake_service.create_user_profile.assert_awaited_once_with(user_id)

    def test_get_profile(self, client, fake_service, user_id):
        profile = make_profile()
        profile.user_id = user_id
        fake_service.get_user_profile = AsyncMock(return_value=profile)

        response = client.get(f"/api/v1/profiles/{user_id}")

        assert response.status_code == 200
        assert response.json()["id"] == str(profile.id)

    def test_get_profile_returns_404(self, client, fake_service, user_id):
        from fastapi import HTTPException

        fake_service.get_user_profile = AsyncMock(
            side_effect=HTTPException(status_code=404, detail="User profile not found")
        )

        response = client.get(f"/api/v1/profiles/{user_id}")

        assert response.status_code == 404

    def test_get_complete_profile(self, client, fake_service, user_id):
        complete = MagicMock()
        complete.profile = make_profile()
        complete.devices = []
        complete.preferences = make_preferences()
        complete.subscription = make_subscription()
        fake_service.get_complete_profile = AsyncMock(return_value=complete)

        response = client.get(f"/api/v1/profiles/{user_id}/complete")

        assert response.status_code == 200
        assert "profile" in response.json()
        assert "devices" in response.json()
        assert "preferences" in response.json()
        assert "subscription" in response.json()

    def test_update_profile(self, client, fake_service, user_id):
        profile = make_profile()
        profile.user_id = user_id
        fake_service.update_user_profile = AsyncMock(return_value=profile)

        response = client.patch(
            f"/api/v1/profiles/{user_id}", json={"bio": "New bio", "country": "FR"}
        )

        assert response.status_code == 200
        call_args = fake_service.update_user_profile.await_args
        assert call_args.args[0] == user_id

    def test_update_profile_rejects_bad_country(self, client, user_id):
        response = client.patch(f"/api/v1/profiles/{user_id}", json={"country": "USAFR"})

        assert response.status_code == 422

    def test_mark_onboarding_complete(self, client, fake_service, user_id):
        profile = make_profile()
        profile.user_id = user_id
        profile.completed_onboarding = True
        fake_service.mark_onboarding_complete = AsyncMock(return_value=profile)

        response = client.post(f"/api/v1/profiles/{user_id}/onboarding/complete")

        assert response.status_code == 200
        assert response.json()["completed_onboarding"] is True
        fake_service.mark_onboarding_complete.assert_awaited_once_with(user_id)


class TestDeviceRoutes:
    def test_register_device_returns_201(self, client, fake_service, user_id):
        device = make_device()
        fake_service.register_device = AsyncMock(return_value=device)

        response = client.post(
            "/api/v1/devices",
            json={
                "device_id": "device-123",
                "device_name": "Chrome on MacBook",
                "device_type": "web",
            },
        )

        assert response.status_code == 201
        call_args = fake_service.register_device.await_args
        assert call_args.args[0] == user_id

    def test_register_device_rejects_bad_type(self, client):
        response = client.post(
            "/api/v1/devices",
            json={"device_id": "d", "device_name": "n", "device_type": "console"},
        )

        assert response.status_code == 422

    def test_get_devices(self, client, fake_service, user_id):
        fake_service.get_user_devices = AsyncMock(return_value=[make_device(), make_device()])

        response = client.get(f"/api/v1/devices/{user_id}")

        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_update_device(self, client, fake_service, device_id):
        device = make_device(device_id)
        fake_service.update_device = AsyncMock(return_value=device)

        response = client.patch(f"/api/v1/devices/{device_id}", json={"is_trusted": True})

        assert response.status_code == 200
        call_args = fake_service.update_device.await_args
        assert call_args.args[0] == device_id

    def test_deactivate_device(self, client, fake_service, device_id):
        device = make_device(device_id)
        device.is_active = False
        fake_service.deactivate_device = AsyncMock(return_value=device)

        response = client.post(f"/api/v1/devices/{device_id}/deactivate")

        assert response.status_code == 200
        assert response.json()["is_active"] is False

    def test_remove_device_returns_204(self, client, fake_service, device_id):
        fake_service.remove_device = AsyncMock(return_value=None)

        response = client.delete(f"/api/v1/devices/{device_id}")

        assert response.status_code == 204
        fake_service.remove_device.assert_awaited_once_with(device_id)


class TestPreferenceRoutes:
    def test_get_preferences(self, client, fake_service, user_id):
        fake_service.get_preferences = AsyncMock(return_value=make_preferences())

        response = client.get(f"/api/v1/preferences/{user_id}")

        assert response.status_code == 200
        assert response.json()["theme"] == "dark"

    def test_update_preferences(self, client, fake_service, user_id):
        prefs = make_preferences()
        prefs.theme = "light"
        fake_service.update_preferences = AsyncMock(return_value=prefs)

        response = client.patch(
            f"/api/v1/preferences/{user_id}", json={"theme": "light", "autoplay": False}
        )

        assert response.status_code == 200
        assert response.json()["theme"] == "light"
        call_args = fake_service.update_preferences.await_args
        assert call_args.args[0] == user_id

    def test_update_preferences_rejects_bad_theme(self, client, user_id):
        response = client.patch(f"/api/v1/preferences/{user_id}", json={"theme": "neon"})

        assert response.status_code == 422


class TestSubscriptionRoutes:
    def test_get_subscription(self, client, fake_service, user_id):
        sub = make_subscription()
        sub.user_id = user_id
        fake_service.get_subscription = AsyncMock(return_value=sub)

        response = client.get(f"/api/v1/subscriptions/{user_id}")

        assert response.status_code == 200
        assert response.json()["subscription_tier"] == "basic"

    def test_get_subscription_returns_404(self, client, fake_service, user_id):
        from fastapi import HTTPException

        fake_service.get_subscription = AsyncMock(
            side_effect=HTTPException(status_code=404, detail="Subscription not found")
        )

        response = client.get(f"/api/v1/subscriptions/{user_id}")

        assert response.status_code == 404

    def test_upgrade_subscription(self, client, fake_service, user_id):
        sub = make_subscription()
        sub.user_id = user_id
        sub.subscription_tier = "premium"
        fake_service.upgrade_subscription = AsyncMock(return_value=sub)

        response = client.post(f"/api/v1/subscriptions/{user_id}/upgrade?new_tier=premium")

        assert response.status_code == 200
        assert response.json()["subscription_tier"] == "premium"
        fake_service.upgrade_subscription.assert_awaited_once_with(user_id, "premium")
