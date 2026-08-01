"""Content service tests."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.services.content import ContentService


@pytest.fixture
def genre_id():
    """Generate test genre ID."""
    return uuid4()


@pytest.fixture
def movie_id():
    """Generate test movie ID."""
    return uuid4()


@pytest.fixture
def show_id():
    """Generate test show ID."""
    return uuid4()


@pytest.fixture
def season_id():
    """Generate test season ID."""
    return uuid4()


@pytest.fixture
def episode_id():
    """Generate test episode ID."""
    return uuid4()


@pytest.fixture
def mock_db():
    """Create mock database session."""
    return AsyncMock()


@pytest.fixture
def mock_repositories():
    """Create mock repositories."""
    return {
        "genre_repo": AsyncMock(),
        "movie_repo": AsyncMock(),
        "show_repo": AsyncMock(),
        "season_repo": AsyncMock(),
        "episode_repo": AsyncMock(),
    }


@pytest.fixture
def content_service(mock_db, mock_repositories):
    """Create ContentService instance with mocks."""
    service = ContentService(mock_db)
    service.genre_repo = mock_repositories["genre_repo"]
    service.movie_repo = mock_repositories["movie_repo"]
    service.show_repo = mock_repositories["show_repo"]
    service.season_repo = mock_repositories["season_repo"]
    service.episode_repo = mock_repositories["episode_repo"]
    return service


class TestGenreManagement:
    """Test genre management."""
    
    @pytest.mark.asyncio
    async def test_create_genre(self, content_service, mock_repositories):
        """Test creating genre."""
        mock_repositories["genre_repo"].get_by_name.return_value = None
        mock_genre = MagicMock()
        mock_genre.name = "Action"
        mock_repositories["genre_repo"].create.return_value = mock_genre
        
        genre = await content_service.create_genre("Action", "Action movies")
        
        assert genre.name == "Action"
        mock_repositories["genre_repo"].create.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_create_genre_duplicate(self, content_service, mock_repositories):
        """Test creating duplicate genre."""
        mock_genre = MagicMock()
        mock_repositories["genre_repo"].get_by_name.return_value = mock_genre
        
        with pytest.raises(ValueError, match="already exists"):
            await content_service.create_genre("Action")
    
    @pytest.mark.asyncio
    async def test_list_genres(self, content_service, mock_repositories, genre_id):
        """Test listing genres."""
        mock_genre1 = MagicMock()
        mock_genre2 = MagicMock()
        mock_repositories["genre_repo"].list_all.return_value = [mock_genre1, mock_genre2]
        
        genres = await content_service.list_genres()
        
        assert len(genres) == 2


class TestMovieManagement:
    """Test movie management."""
    
    @pytest.mark.asyncio
    async def test_create_movie(self, content_service, mock_repositories):
        """Test creating movie."""
        mock_repositories["movie_repo"].get_by_media_key.return_value = None
        mock_movie = MagicMock()
        mock_movie.title = "Test Movie"
        mock_repositories["movie_repo"].create.return_value = mock_movie
        
        movie_data = {
            "title": "Test Movie",
            "description": "A test movie",
            "poster_url": "http://example.com/poster.jpg",
            "release_date": datetime.now(UTC),
            "duration_seconds": 7200,
            "genre_ids": [uuid4()],
            "director": "Test Director",
            "language": "en",
            "media_key": "test_movie_123"
        }
        
        movie = await content_service.create_movie(movie_data)
        
        assert movie.title == "Test Movie"
    
    @pytest.mark.asyncio
    async def test_get_movie(self, content_service, movie_id, mock_repositories):
        """Test getting movie."""
        mock_movie = MagicMock()
        mock_movie.id = movie_id
        mock_repositories["movie_repo"].get_by_id.return_value = mock_movie
        
        movie = await content_service.get_movie(movie_id)
        
        assert movie.id == movie_id
    
    @pytest.mark.asyncio
    async def test_search_movies(self, content_service, mock_repositories):
        """Test searching movies."""
        mock_movie = MagicMock()
        mock_repositories["movie_repo"].search.return_value = ([mock_movie], 1)
        
        movies, total = await content_service.search_movies("action", 20, 0)
        
        assert len(movies) == 1
        assert total == 1
    
    @pytest.mark.asyncio
    async def test_search_movies_too_short(self, content_service):
        """Test search query too short."""
        with pytest.raises(ValueError, match="at least 2 characters"):
            await content_service.search_movies("a", 20, 0)
    
    @pytest.mark.asyncio
    async def test_list_trending_movies(self, content_service, mock_repositories):
        """Test listing trending movies."""
        mock_movie = MagicMock()
        mock_repositories["movie_repo"].list_trending.return_value = ([mock_movie], 1)
        
        movies, _total = await content_service.list_trending_movies(20, 0)
        
        assert len(movies) == 1
    
    @pytest.mark.asyncio
    async def test_update_movie(self, content_service, movie_id, mock_repositories):
        """Test updating movie."""
        mock_movie = MagicMock()
        mock_movie.id = movie_id
        mock_repositories["movie_repo"].get_by_id.return_value = mock_movie
        mock_repositories["movie_repo"].update.return_value = mock_movie
        
        updated = await content_service.update_movie(movie_id, {"rating": 8.5})
        
        assert updated.id == movie_id


class TestShowManagement:
    """Test show management."""
    
    @pytest.mark.asyncio
    async def test_create_show(self, content_service, mock_repositories):
        """Test creating show."""
        mock_repositories["show_repo"].get_by_media_key.return_value = None
        mock_show = MagicMock()
        mock_show.title = "Test Show"
        mock_repositories["show_repo"].create.return_value = mock_show
        
        show_data = {
            "title": "Test Show",
            "description": "A test show",
            "poster_url": "http://example.com/poster.jpg",
            "first_air_date": datetime.now(UTC),
            "episode_runtime_seconds": 3600,
            "genre_ids": [uuid4()],
            "language": "en",
            "media_key": "test_show_123"
        }
        
        show = await content_service.create_show(show_data)
        
        assert show.title == "Test Show"
    
    @pytest.mark.asyncio
    async def test_get_show(self, content_service, show_id, mock_repositories):
        """Test getting show."""
        mock_show = MagicMock()
        mock_show.id = show_id
        mock_repositories["show_repo"].get_by_id.return_value = mock_show
        
        show = await content_service.get_show(show_id)
        
        assert show.id == show_id
    
    @pytest.mark.asyncio
    async def test_list_ongoing_shows(self, content_service, mock_repositories):
        """Test listing ongoing shows."""
        mock_show = MagicMock()
        mock_repositories["show_repo"].list_ongoing.return_value = ([mock_show], 1)
        
        shows, _total = await content_service.list_ongoing_shows(20, 0)
        
        assert len(shows) == 1
    
    @pytest.mark.asyncio
    async def test_search_shows(self, content_service, mock_repositories):
        """Test searching shows."""
        mock_show = MagicMock()
        mock_repositories["show_repo"].search.return_value = ([mock_show], 1)
        
        shows, total = await content_service.search_shows("drama", 20, 0)
        
        assert len(shows) == 1
        assert total == 1


class TestSeasonManagement:
    """Test season management."""
    
    @pytest.mark.asyncio
    async def test_create_season(self, content_service, show_id, mock_repositories):
        """Test creating season."""
        mock_show = MagicMock()
        mock_show.id = show_id
        mock_repositories["show_repo"].get_by_id.return_value = mock_show
        
        mock_season = MagicMock()
        mock_season.id = uuid4()
        mock_repositories["season_repo"].create.return_value = mock_season
        
        season = await content_service.create_season(show_id, 1, {})
        
        assert season.id is not None
    
    @pytest.mark.asyncio
    async def test_list_seasons(self, content_service, show_id, mock_repositories):
        """Test listing seasons."""
        mock_show = MagicMock()
        mock_show.id = show_id
        mock_repositories["show_repo"].get_by_id.return_value = mock_show
        
        mock_season = MagicMock()
        mock_repositories["season_repo"].list_by_show.return_value = [mock_season]
        
        seasons = await content_service.list_seasons(show_id)
        
        assert len(seasons) == 1


class TestEpisodeManagement:
    """Test episode management."""
    
    @pytest.mark.asyncio
    async def test_create_episode(self, content_service, show_id, season_id, mock_repositories):
        """Test creating episode."""
        mock_show = MagicMock()
        mock_show.id = show_id
        mock_repositories["show_repo"].get_by_id.return_value = mock_show
        
        mock_season = MagicMock()
        mock_season.id = season_id
        mock_repositories["season_repo"].get_by_id.return_value = mock_season
        
        mock_episode = MagicMock()
        mock_episode.id = uuid4()
        mock_repositories["episode_repo"].create.return_value = mock_episode
        
        episode = await content_service.create_episode(season_id, show_id, {})
        
        assert episode.id is not None
    
    @pytest.mark.asyncio
    async def test_list_episodes_by_season(self, content_service, season_id, mock_repositories):
        """Test listing episodes by season."""
        mock_season = MagicMock()
        mock_season.id = season_id
        mock_repositories["season_repo"].get_by_id.return_value = mock_season
        
        mock_episode = MagicMock()
        mock_repositories["episode_repo"].list_by_season.return_value = [mock_episode]
        
        episodes = await content_service.list_episodes_by_season(season_id)
        
        assert len(episodes) == 1
    
    @pytest.mark.asyncio
    async def test_get_episode(self, content_service, episode_id, mock_repositories):
        """Test getting episode."""
        mock_episode = MagicMock()
        mock_episode.id = episode_id
        mock_repositories["episode_repo"].get_by_id.return_value = mock_episode
        
        episode = await content_service.get_episode(episode_id)
        
        assert episode.id == episode_id
