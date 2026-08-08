"""Content Service tests — covers the actual ContentService API."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.services import ContentService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_db():
    """Create mock database session."""
    return AsyncMock()


@pytest.fixture
def mock_repositories():
    """Create mock repositories."""
    return {
        "genre_repo": AsyncMock(),
        "content_repo": AsyncMock(),
        "cast_repo": AsyncMock(),
        "season_repo": AsyncMock(),
        "episode_repo": AsyncMock(),
        "rating_repo": AsyncMock(),
        "recommendation_repo": AsyncMock(),
    }


@pytest.fixture
def content_service(mock_db, mock_repositories):
    """Create ContentService with mocked repositories."""
    service = ContentService(mock_db)
    service.content_repo = mock_repositories["content_repo"]
    service.genre_repo = mock_repositories["genre_repo"]
    service.cast_repo = mock_repositories["cast_repo"]
    service.season_repo = mock_repositories["season_repo"]
    service.episode_repo = mock_repositories["episode_repo"]
    service.rating_repo = mock_repositories["rating_repo"]
    service.recommendation_repo = mock_repositories["recommendation_repo"]
    return service


class TestGenreManagement:
    """Test genre operations."""

    async def test_create_genre(self, content_service, mock_repositories):
        """Test creating a genre."""
        from app.schemas import GenreCreateRequest

        mock_genre = MagicMock()
        mock_genre.id = uuid4()
        mock_genre.name = "Action"
        mock_repositories["genre_repo"].create.return_value = mock_genre

        result = await content_service.create_genre(
            GenreCreateRequest(name="Action", slug="action")
        )

        assert result is not None
        mock_repositories["genre_repo"].create.assert_called_once()

    async def test_list_genres(self, content_service, mock_repositories):
        """Test listing genres."""
        mock_repositories["genre_repo"].get_all.return_value = [MagicMock(), MagicMock()]

        result = await content_service.list_genres()

        assert len(result) == 2
        mock_repositories["genre_repo"].get_all.assert_called_once()

    async def test_get_genre(self, content_service, mock_repositories):
        """Test getting a genre."""
        genre_id = uuid4()
        mock_repositories["genre_repo"].get_by_id.return_value = MagicMock()

        result = await content_service.get_genre(genre_id)

        assert result is not None
        mock_repositories["genre_repo"].get_by_id.assert_called_once_with(genre_id)


class TestContentManagement:
    """Test content operations."""

    async def test_create_content(self, content_service, mock_repositories):
        """Test creating content."""
        from app.schemas import ContentCreateRequest

        mock_content = MagicMock()
        mock_content.id = uuid4()
        mock_repositories["content_repo"].create.return_value = mock_content

        request = ContentCreateRequest(
            title="Test Movie", content_type="movie", description="desc", slug="test-movie"
        )
        result = await content_service.create_content(request)

        assert result is not None
        mock_repositories["content_repo"].create.assert_called_once()

    async def test_get_content(self, content_service, mock_repositories):
        """Test getting content."""
        content_id = uuid4()
        mock_repositories["content_repo"].get_by_id.return_value = MagicMock()

        result = await content_service.get_content(content_id)

        assert result is not None
        mock_repositories["content_repo"].get_by_id.assert_called_once_with(content_id)

    async def test_get_content_not_found(self, content_service, mock_repositories):
        """Test getting missing content returns None."""
        content_id = uuid4()
        mock_repositories["content_repo"].get_by_id.return_value = None

        result = await content_service.get_content(content_id)

        assert result is None

    async def test_search_content(self, content_service, mock_repositories):
        """Test searching content."""
        mock_repositories["content_repo"].search.return_value = [MagicMock(), MagicMock()]

        result = await content_service.search_content("matrix")

        assert len(result) == 2
        mock_repositories["content_repo"].search.assert_called_once_with("matrix")

    async def test_update_content(self, content_service, mock_repositories):
        """Test updating content."""
        from app.schemas import ContentUpdateRequest

        content_id = uuid4()
        mock_content = MagicMock()
        mock_repositories["content_repo"].update.return_value = mock_content

        request = ContentUpdateRequest(title="Updated")
        result = await content_service.update_content(content_id, request)

        assert result is not None
        mock_repositories["content_repo"].update.assert_called_once()


class TestSeasonManagement:
    """Test season operations."""

    async def test_create_season(self, content_service, mock_repositories):
        """Test creating a season."""
        from app.schemas import SeasonCreateRequest

        content_id = uuid4()
        mock_repositories["season_repo"].create.return_value = MagicMock()

        request = SeasonCreateRequest(season_number=1, title="Season 1")
        result = await content_service.create_season(content_id, request)

        assert result is not None
        mock_repositories["season_repo"].create.assert_called_once()


class TestEpisodeManagement:
    """Test episode operations."""

    async def test_create_episode(self, content_service, mock_repositories):
        """Test creating an episode."""
        from app.schemas import EpisodeCreateRequest

        content_id = uuid4()
        season_id = uuid4()
        mock_repositories["episode_repo"].create.return_value = MagicMock()

        request = EpisodeCreateRequest(title="Pilot", episode_number=1, duration_minutes=45)
        result = await content_service.create_episode(content_id, season_id, request)

        assert result is not None
        mock_repositories["episode_repo"].create.assert_called_once()

    async def test_get_episode(self, content_service, mock_repositories):
        """Test getting an episode."""
        content_id = uuid4()
        season_id = uuid4()
        episode_id = uuid4()
        mock_season = MagicMock(content_id=content_id)
        mock_episode = MagicMock(season_id=season_id)
        mock_repositories["season_repo"].get_by_id.return_value = mock_season
        mock_repositories["episode_repo"].get_by_id.return_value = mock_episode

        result = await content_service.get_episode(content_id, season_id, episode_id)

        assert result is not None
        mock_repositories["episode_repo"].get_by_id.assert_called_once_with(episode_id)
