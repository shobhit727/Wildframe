"""Comprehensive tests for User Service."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.services.user import UserService


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
def mock_db():
    """Create mock database session."""
    return AsyncMock()


@pytest.fixture
def mock_repositories():
    """Create mock repositories."""
    return {
        "profile_repo": AsyncMock(),
        "device_repo": AsyncMock(),
        "session_repo": AsyncMock(),
        "history_repo": AsyncMock(),
        "preference_repo": AsyncMock(),
    }


@pytest.fixture
def user_service(mock_db, mock_repositories):
    """Create UserService instance with mocks."""
    service = UserService(mock_db)
    service.profile_repo = mock_repositories["profile_repo"]
    service.device_repo = mock_repositories["device_repo"]
    service.session_repo = mock_repositories["session_repo"]
    service.history_repo = mock_repositories["history_repo"]
    service.preference_repo = mock_repositories["preference_repo"]
    return service


class TestProfileManagement:
    """Test user profile management."""

    @pytest.mark.asyncio
    async def test_create_profile(self, user_service, user_id, mock_repositories):
        """Test creating user profile."""
        mock_profile = MagicMock()
        mock_profile.user_id = user_id
        mock_repositories["profile_repo"].create.return_value = mock_profile

        profile = await user_service.create_profile(user_id)

        assert profile.user_id == user_id
        mock_repositories["profile_repo"].create.assert_called_once_with(user_id)

    @pytest.mark.asyncio
    async def test_get_profile(self, user_service, user_id, mock_repositories):
        """Test retrieving user profile."""
        mock_profile = MagicMock()
        mock_profile.user_id = user_id
        mock_repositories["profile_repo"].get_by_user_id.return_value = mock_profile

        profile = await user_service.get_profile(user_id)

        assert profile.user_id == user_id
        mock_repositories["profile_repo"].get_by_user_id.assert_called_once_with(user_id)

    @pytest.mark.asyncio
    async def test_get_profile_not_found(self, user_service, user_id, mock_repositories):
        """Test profile not found."""
        mock_repositories["profile_repo"].get_by_user_id.return_value = None

        with pytest.raises(ValueError, match="Profile not found"):
            await user_service.get_profile(user_id)

    @pytest.mark.asyncio
    async def test_update_profile(self, user_service, user_id, mock_repositories):
        """Test updating user profile."""
        mock_profile = MagicMock()
        mock_profile.user_id = user_id
        mock_repositories["profile_repo"].get_by_user_id.return_value = mock_profile
        mock_repositories["profile_repo"].update.return_value = mock_profile

        profile_data = {"first_name": "John", "last_name": "Doe"}
        profile = await user_service.update_profile(user_id, profile_data)

        assert profile.user_id == user_id
        mock_repositories["profile_repo"].update.assert_called_once()


class TestDeviceManagement:
    """Test device management."""

    @pytest.mark.asyncio
    async def test_register_device(self, user_service, user_id, mock_repositories):
        """Test registering device."""
        mock_device = MagicMock()
        mock_device.user_id = user_id
        mock_device.device_id = "device123"
        mock_repositories["device_repo"].create.return_value = mock_device

        device_data = {"device_id": "device123", "device_type": "web"}
        device = await user_service.register_device(user_id, device_data)

        assert device.user_id == user_id
        mock_repositories["device_repo"].create.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_devices(self, user_service, user_id, mock_repositories):
        """Test listing user devices."""
        mock_device1 = MagicMock()
        mock_device2 = MagicMock()
        mock_repositories["device_repo"].list_by_user.return_value = [mock_device1, mock_device2]

        devices = await user_service.list_devices(user_id)

        assert len(devices) == 2
        mock_repositories["device_repo"].list_by_user.assert_called_once_with(user_id, True)

    @pytest.mark.asyncio
    async def test_deactivate_device(self, user_service, user_id, device_id, mock_repositories):
        """Test deactivating device."""
        mock_device = MagicMock()
        mock_device.user_id = user_id
        mock_repositories["device_repo"].get_by_id.return_value = mock_device

        await user_service.deactivate_device(device_id, user_id)

        mock_repositories["device_repo"].deactivate.assert_called_once_with(device_id)


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

        mock_repositories["session_repo"].end_all_sessions.assert_called_once_with(user_id, None)


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
        mock_repositories["preference_repo"].get_or_create.return_value = mock_pref

        prefs = await user_service.get_preferences(user_id)

        assert prefs.user_id == user_id
        mock_repositories["preference_repo"].get_or_create.assert_called_once_with(user_id)

    @pytest.mark.asyncio
    async def test_update_preferences(self, user_service, user_id, mock_repositories):
        """Test updating preferences."""
        mock_pref = MagicMock()
        mock_pref.user_id = user_id
        mock_repositories["preference_repo"].update.return_value = mock_pref

        pref_data = {"preferred_quality": "1080p", "autoplay_next_episode": True}
        prefs = await user_service.update_preferences(user_id, pref_data)

        assert prefs.user_id == user_id
        mock_repositories["preference_repo"].update.assert_called_once()
