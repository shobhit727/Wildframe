"""Comprehensive tests for User Service."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.services import UserService


@pytest.fixture
def user_id():
    """Generate test user ID."""
    return uuid4()


@pytest.fixture
def device_id():
    """Generate test device ID."""
    return uuid4()


@pytest.fixture
def content_id():
    """Generate test content ID."""
    return uuid4()


@pytest.fixture
def mock_repositories():
    """Create mock repositories."""
    return {
        "profile_repo": AsyncMock(),
        "device_repo": AsyncMock(),
        "session_repo": AsyncMock(),
        "history_repo": AsyncMock(),
        "preference_repo": AsyncMock(),
        "subscription_repo": AsyncMock(),
    }


@pytest.fixture
def user_service(mock_repositories):
    """Create UserService instance with mocks."""
    service = UserService(
        profile_repo=mock_repositories["profile_repo"],
        device_repo=mock_repositories["device_repo"],
        preference_repo=mock_repositories["preference_repo"],
        subscription_repo=mock_repositories["subscription_repo"],
    )
    return service


class TestProfileManagement:
    """Test user profile management."""

    @pytest.mark.asyncio
    async def test_create_profile(self, user_service, user_id, mock_repositories):
        """Test creating user profile."""
        from datetime import UTC, datetime

        mock_profile = MagicMock()
        mock_profile.id = uuid4()
        mock_profile.user_id = user_id
        mock_profile.avatar_url = None
        mock_profile.bio = None
        mock_profile.phone_number = None
        mock_profile.date_of_birth = None
        mock_profile.country = None
        mock_profile.language = "en"
        mock_profile.timezone = "UTC"
        mock_profile.public_profile = True
        mock_profile.newsletter_subscribed = False
        mock_profile.marketing_emails = False
        mock_profile.completed_onboarding = False
        mock_profile.profile_completeness = 0
        mock_profile.created_at = datetime.now(UTC)
        mock_profile.updated_at = datetime.now(UTC)
        mock_repositories["profile_repo"].create.return_value = mock_profile

        profile = await user_service.create_user_profile(user_id)

        assert profile.user_id == user_id
        mock_repositories["profile_repo"].create.assert_called_once_with(user_id=user_id)

    @pytest.mark.asyncio
    async def test_get_profile(self, user_service, user_id, mock_repositories):
        """Test retrieving user profile."""
        from datetime import UTC, datetime

        mock_profile = MagicMock()
        mock_profile.id = uuid4()
        mock_profile.user_id = user_id
        mock_profile.avatar_url = None
        mock_profile.bio = None
        mock_profile.phone_number = None
        mock_profile.date_of_birth = None
        mock_profile.country = None
        mock_profile.language = "en"
        mock_profile.timezone = "UTC"
        mock_profile.public_profile = True
        mock_profile.newsletter_subscribed = False
        mock_profile.marketing_emails = False
        mock_profile.completed_onboarding = False
        mock_profile.profile_completeness = 0
        mock_profile.created_at = datetime.now(UTC)
        mock_profile.updated_at = datetime.now(UTC)
        mock_repositories["profile_repo"].get_by_user_id.return_value = mock_profile

        profile = await user_service.get_user_profile(user_id)

        assert profile.user_id == user_id
        mock_repositories["profile_repo"].get_by_user_id.assert_called_once_with(user_id)

    @pytest.mark.asyncio
    async def test_get_profile_not_found(self, user_service, user_id, mock_repositories):
        """Test profile not found."""
        mock_repositories["profile_repo"].get_by_user_id.return_value = None

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await user_service.get_user_profile(user_id)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_profile(self, user_service, user_id, mock_repositories):
        """Test updating user profile."""
        from datetime import UTC, datetime

        mock_profile = MagicMock()
        mock_profile.id = uuid4()
        mock_profile.user_id = user_id
        mock_profile.avatar_url = None
        mock_profile.bio = None
        mock_profile.phone_number = None
        mock_profile.date_of_birth = None
        mock_profile.country = None
        mock_profile.language = "en"
        mock_profile.timezone = "UTC"
        mock_profile.public_profile = True
        mock_profile.newsletter_subscribed = False
        mock_profile.marketing_emails = False
        mock_profile.completed_onboarding = False
        mock_profile.profile_completeness = 0
        mock_profile.created_at = datetime.now(UTC)
        mock_profile.updated_at = datetime.now(UTC)
        mock_repositories["profile_repo"].get_by_user_id.return_value = mock_profile
        mock_repositories["profile_repo"].update.return_value = mock_profile

        from app.schemas import UserProfileUpdateRequest

        profile_data = UserProfileUpdateRequest(first_name="John", last_name="Doe")
        profile = await user_service.update_user_profile(user_id, profile_data)

        assert profile.user_id == user_id
        mock_repositories["profile_repo"].update.assert_called_once()


class TestDeviceManagement:
    """Test device management."""

    @pytest.mark.asyncio
    async def test_register_device(self, user_service, user_id, mock_repositories):
        """Test registering device."""
        from datetime import UTC, datetime

        mock_device = MagicMock()
        mock_device.id = uuid4()
        mock_device.user_id = user_id
        mock_device.device_id = "device123"
        mock_device.device_name = "Test Device"
        mock_device.device_type = "web"
        mock_device.os_name = "Linux"
        mock_device.os_version = "20.04"
        mock_device.browser_name = "Chrome"
        mock_device.browser_version = "120.0"
        mock_device.ip_address = "192.168.1.1"
        mock_device.is_active = True
        mock_device.is_trusted = False
        mock_device.can_stream = True
        mock_device.can_download = True
        mock_device.last_active_at = datetime.now(UTC)
        mock_device.registration_date = datetime.now(UTC)
        mock_device.created_at = datetime.now(UTC)
        mock_device.updated_at = datetime.now(UTC)
        mock_repositories["device_repo"].create.return_value = mock_device

        from app.schemas import UserDeviceRegisterRequest

        device_data = UserDeviceRegisterRequest(
            device_id="device123",
            device_name="Test Device",
            device_type="web",
            os_name="Linux",
            os_version="20.04",
            browser_name="Chrome",
            browser_version="120.0",
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
        )
        device = await user_service.register_device(user_id, device_data, "192.168.1.1")

        assert device.device_id == "device123"
        mock_repositories["device_repo"].create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_user_devices(self, user_service, user_id, mock_repositories):
        """Test getting user devices."""
        from datetime import UTC, datetime

        mock_device1 = MagicMock()
        mock_device1.id = uuid4()
        mock_device1.device_id = "device1"
        mock_device1.device_name = "Device 1"
        mock_device1.device_type = "web"
        mock_device1.os_name = "Linux"
        mock_device1.os_version = "20.04"
        mock_device1.browser_name = "Chrome"
        mock_device1.browser_version = "120.0"
        mock_device1.ip_address = "192.168.1.1"
        mock_device1.is_active = True
        mock_device1.is_trusted = False
        mock_device1.can_stream = True
        mock_device1.can_download = True
        mock_device1.last_active_at = datetime.now(UTC)
        mock_device1.registration_date = datetime.now(UTC)
        mock_device1.created_at = datetime.now(UTC)
        mock_device1.updated_at = datetime.now(UTC)

        mock_device2 = MagicMock()
        mock_device2.id = uuid4()
        mock_device2.device_id = "device2"
        mock_device2.device_name = "Device 2"
        mock_device2.device_type = "ios"
        mock_device2.os_name = "iOS"
        mock_device2.os_version = "17.0"
        mock_device2.browser_name = "Safari"
        mock_device2.browser_version = "17.0"
        mock_device2.ip_address = "192.168.1.2"
        mock_device2.is_active = True
        mock_device2.is_trusted = True
        mock_device2.can_stream = True
        mock_device2.can_download = True
        mock_device2.last_active_at = datetime.now(UTC)
        mock_device2.registration_date = datetime.now(UTC)
        mock_device2.created_at = datetime.now(UTC)
        mock_device2.updated_at = datetime.now(UTC)

        mock_repositories["device_repo"].get_user_devices.return_value = [
            mock_device1,
            mock_device2,
        ]

        devices = await user_service.get_user_devices(user_id)

        assert len(devices) == 2
        mock_repositories["device_repo"].get_user_devices.assert_called_once_with(user_id)

    @pytest.mark.asyncio
    async def test_deactivate_device(self, user_service, user_id, device_id, mock_repositories):
        """Test deactivating device."""
        from datetime import UTC, datetime

        mock_device = MagicMock()
        mock_device.id = uuid4()
        mock_device.device_id = "device123"
        mock_device.device_name = "Test Device"
        mock_device.device_type = "web"
        mock_device.os_name = "Linux"
        mock_device.os_version = "20.04"
        mock_device.browser_name = "Chrome"
        mock_device.browser_version = "120.0"
        mock_device.ip_address = "192.168.1.1"
        mock_device.is_active = False
        mock_device.is_trusted = False
        mock_device.can_stream = True
        mock_device.can_download = True
        mock_device.last_active_at = datetime.now(UTC)
        mock_device.registration_date = datetime.now(UTC)
        mock_device.created_at = datetime.now(UTC)
        mock_device.updated_at = datetime.now(UTC)
        mock_repositories["device_repo"].mark_device_inactive.return_value = mock_device

        device = await user_service.deactivate_device(device_id)

        assert device.is_active is False
        mock_repositories["device_repo"].mark_device_inactive.assert_called_once_with(device_id)


class TestSessionManagement:
    """Test session management."""

    @pytest.mark.asyncio
    async def test_create_session(self, user_service, user_id, device_id, mock_repositories):
        """Test creating session."""
        mock_session = MagicMock()
        mock_session.user_id = user_id
        mock_session.device_id = device_id
        mock_repositories["session_repo"].create.return_value = mock_session

        session = await user_service.create_session(
            user_id=user_id,
            device_id=device_id,
            session_token="token123",
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
        )

        assert session.user_id == user_id
        mock_repositories["session_repo"].create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_active_sessions(self, user_service, user_id, mock_repositories):
        """Test getting active sessions."""
        mock_session1 = MagicMock()
        mock_session2 = MagicMock()
        mock_repositories["session_repo"].list_active_sessions.return_value = [
            mock_session1,
            mock_session2,
        ]

        sessions = await user_service.get_active_sessions(user_id)

        assert len(sessions) == 2
        mock_repositories["session_repo"].list_active_sessions.assert_called_once_with(user_id)

    @pytest.mark.asyncio
    async def test_end_session(self, user_service, user_id, mock_repositories):
        """Test ending session."""
        session_id = uuid4()
        mock_session = MagicMock()
        mock_session.user_id = user_id
        mock_repositories["session_repo"].get_by_id.return_value = mock_session

        await user_service.end_session(user_id, session_id)

        mock_repositories["session_repo"].end_session.assert_called_once_with(session_id)

    @pytest.mark.asyncio
    async def test_end_all_sessions(self, user_service, user_id, mock_repositories):
        """Test ending all sessions."""
        await user_service.end_all_sessions(user_id)

        mock_repositories["session_repo"].end_all_sessions.assert_called_once_with(user_id)


class TestWatchHistory:
    """Test watch history tracking."""

    @pytest.mark.asyncio
    async def test_record_watch(self, user_service, user_id, content_id, mock_repositories):
        """Test recording watch history."""
        mock_entry = MagicMock()
        mock_entry.user_id = user_id
        mock_entry.content_id = content_id
        mock_repositories["history_repo"].record_watch.return_value = mock_entry

        entry = await user_service.record_watch(
            user_id=user_id,
            content_id=content_id,
            content_type="movie",
            progress_seconds=1800,
            progress_percentage=50,
        )

        assert entry.user_id == user_id
        mock_repositories["history_repo"].record_watch.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_watch_history(self, user_service, user_id, mock_repositories):
        """Test getting watch history."""
        mock_entry1 = MagicMock()
        mock_entry2 = MagicMock()
        mock_repositories["history_repo"].get_watch_history.return_value = (
            [mock_entry1, mock_entry2],
            2,
        )

        entries, total = await user_service.get_watch_history(user_id)

        assert len(entries) == 2
        assert total == 2
        mock_repositories["history_repo"].get_watch_history.assert_called_once()


class TestPreferences:
    """Test user preferences management."""

    @pytest.mark.asyncio
    async def test_get_preferences(self, user_service, user_id, mock_repositories):
        """Test getting preferences."""
        mock_pref = MagicMock()
        mock_pref.user_id = user_id
        mock_repositories["preference_repo"].get_by_user_id.return_value = mock_pref

        prefs = await user_service.get_preferences(user_id)

        assert prefs.user_id == user_id
        mock_repositories["preference_repo"].get_by_user_id.assert_called_once_with(user_id)

    @pytest.mark.asyncio
    async def test_update_preferences(self, user_service, user_id, mock_repositories):
        """Test updating preferences."""
        mock_pref = MagicMock()
        mock_pref.user_id = user_id
        mock_repositories["preference_repo"].update.return_value = mock_pref

        from app.schemas import UserPreferenceUpdateRequest

        pref_data = UserPreferenceUpdateRequest(
            preferred_quality="1080p", autoplay_next_episode=True
        )
        prefs = await user_service.update_preferences(user_id, pref_data)

        assert prefs.user_id == user_id
        mock_repositories["preference_repo"].update.assert_called_once()


class TestSubscription:
    """Test subscription management."""

    @pytest.mark.asyncio
    async def test_get_subscription(self, user_service, user_id, mock_repositories):
        """Test getting subscription."""
        mock_sub = MagicMock()
        mock_sub.user_id = user_id
        mock_repositories["subscription_repo"].get_by_user_id.return_value = mock_sub

        sub = await user_service.get_subscription(user_id)

        assert sub.user_id == user_id
        mock_repositories["subscription_repo"].get_by_user_id.assert_called_once_with(user_id)

    @pytest.mark.asyncio
    async def test_upgrade_subscription(self, user_service, user_id, mock_repositories):
        """Test upgrading subscription."""
        mock_sub = MagicMock()
        mock_sub.user_id = user_id
        mock_repositories["subscription_repo"].update_tier.return_value = mock_sub

        sub = await user_service.upgrade_subscription(user_id, "premium")

        assert sub.user_id == user_id
        mock_repositories["subscription_repo"].update_tier.assert_called_once_with(
            user_id, "premium"
        )


class TestOnboarding:
    """Test onboarding completion."""

    @pytest.mark.asyncio
    async def test_mark_onboarding_complete(self, user_service, user_id, mock_repositories):
        """Test marking onboarding complete."""
        mock_profile = MagicMock()
        mock_profile.user_id = user_id
        mock_repositories["profile_repo"].mark_onboarding_complete.return_value = mock_profile

        profile = await user_service.mark_onboarding_complete(user_id)

        assert profile.user_id == user_id
        mock_repositories["profile_repo"].mark_onboarding_complete.assert_called_once_with(user_id)
