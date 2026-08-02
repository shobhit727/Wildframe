"""Integration tests for Content Service."""
from datetime import UTC

import pytest_asyncio
from httpx import AsyncClient

from app.main import app
from app.models import ContentStatus
from app.services import ContentService


@pytest_asyncio.fixture
async def client():
    """Async HTTP client for testing."""
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def content_service(db_session):
    """ContentService instance with test DB."""
    return ContentService(db_session)


class TestGenreIntegration:
    """Integration tests for genres."""

    async def test_create_genre(self, content_service, db_session):
        """Test creating a genre."""
        from app.schemas import GenreCreateRequest
        
        request = GenreCreateRequest(
            name="Action",
            slug="action",
            description="Action movies and shows",
        )
        
        genre = await content_service.create_genre(request)
        
        assert genre.name == "Action"
        assert genre.slug == "action"
        
        # Verify in DB
        from app.repositories import GenreRepository
        repo = GenreRepository(db_session)
        db_genre = await repo.get_by_id(genre.id)
        assert db_genre is not None
        assert db_genre.name == "Action"

    async def test_list_genres(self, content_service, db_session):
        """Test listing genres."""
        from app.schemas import GenreCreateRequest
        
        # Create a few genres
        for name in ["Action", "Comedy", "Drama"]:
            request = GenreCreateRequest(name=name, slug=name.lower())
            await content_service.create_genre(request)
        
        genres = await content_service.list_genres()
        
        assert len(genres) >= 3


class TestContentIntegration:
    """Integration tests for content."""

    async def test_create_content(self, content_service, db_session):
        """Test creating content."""
        from datetime import datetime

        from app.models import ContentType
        from app.schemas import ContentCreateRequest
        
        request = ContentCreateRequest(
            title="Test Movie",
            slug="test-movie",
            description="A test movie",
            content_type="movie",
            release_date=datetime.now(UTC),
            duration_minutes=120,
            original_language="en",
            country="US",
        )
        
        content = await content_service.create_content(request)
        
        assert content.title == "Test Movie"
        assert content.content_type == ContentType.MOVIE
        assert content.status == ContentStatus.DRAFT
        
        # Verify in DB
        from app.repositories import ContentRepository
        repo = ContentRepository(db_session)
        db_content = await repo.get_by_id(content.id)
        assert db_content is not None
        assert db_content.title == "Test Movie"

    async def test_create_content_with_genres(self, content_service, db_session):
        """Test creating content with genres."""
        from datetime import datetime

        from app.schemas import ContentCreateRequest
        
        # Create genres first
        genre1 = await content_service.create_genre(
            type('obj', (object,), {'name': 'Action', 'slug': 'action', 'description': None, 'icon_url': None})()
        )
        genre2 = await content_service.create_genre(
            type('obj', (object,), {'name': 'Adventure', 'slug': 'adventure', 'description': None, 'icon_url': None})()
        )
        
        request = ContentCreateRequest(
            title="Adventure Movie",
            slug="adventure-movie",
            description="An adventure movie",
            content_type="movie",
            release_date=datetime.now(UTC),
            duration_minutes=150,
            genre_ids=[genre1.id, genre2.id],
        )
        
        content = await content_service.create_content(request)
        
        assert len(content.genres) == 2

    async def test_update_content(self, content_service, db_session):
        """Test updating content."""
        from app.schemas import ContentCreateRequest, ContentUpdateRequest
        
        # Create content
        request = ContentCreateRequest(
            title="Original Title",
            slug="original-title",
            description="Original description",
            content_type="movie",
        )
        content = await content_service.create_content(request)
        
        # Update content
        update_request = ContentUpdateRequest(
            title="Updated Title",
            description="Updated description",
        )
        
        updated = await content_service.update_content(content.id, update_request)
        
        assert updated.title == "Updated Title"
        assert updated.description == "Updated description"

    async def test_publish_content(self, content_service, db_session):
        """Test publishing content."""
        from app.models import ContentStatus
        from app.schemas import ContentCreateRequest, ContentPublishRequest
        
        request = ContentCreateRequest(
            title="To Publish",
            slug="to-publish",
            description="Will be published",
            content_type="movie",
        )
        content = await content_service.create_content(request)
        
        assert content.status == ContentStatus.DRAFT
        
        # Publish
        publish_request = ContentPublishRequest(status="published")
        published = await content_service.publish_content(content.id, publish_request)
        
        assert published.status == ContentStatus.PUBLISHED
        assert published.published_at is not None


class TestSeasonIntegration:
    """Integration tests for seasons."""

    async def test_create_season(self, content_service, db_session):
        """Test creating a season."""
        from datetime import datetime

        from app.schemas import ContentCreateRequest, SeasonCreateRequest
        
        # Create content first
        content_request = ContentCreateRequest(
            title="Test Show",
            slug="test-show",
            description="A test show",
            content_type="series",
        )
        content = await content_service.create_content(content_request)
        
        # Create season
        season_request = SeasonCreateRequest(
            season_number=1,
            title="Season 1",
            description="First season",
            release_date=datetime.now(UTC),
        )
        
        season = await content_service.create_season(content.id, season_request)
        
        assert season.season_number == 1
        assert season.title == "Season 1"
        assert season.content_id == content.id


class TestEpisodeIntegration:
    """Integration tests for episodes."""

    async def test_create_episode(self, content_service, db_session):
        """Test creating an episode."""
        from app.schemas import (
            ContentCreateRequest,
            EpisodeCreateRequest,
            SeasonCreateRequest,
        )
        
        # Create content
        content_request = ContentCreateRequest(
            title="Show with Episodes",
            slug="show-with-episodes",
            description="A show",
            content_type="series",
        )
        content = await content_service.create_content(content_request)
        
        # Create season
        season_request = SeasonCreateRequest(
            season_number=1,
            title="Season 1",
        )
        season = await content_service.create_season(content.id, season_request)
        
        # Create episode
        episode_request = EpisodeCreateRequest(
            episode_number=1,
            title="Pilot",
            duration_minutes=45,
            description="First episode",
        )
        
        episode = await content_service.create_episode(content.id, season.id, episode_request)
        
        assert episode.episode_number == 1
        assert episode.title == "Pilot"
        assert episode.duration_minutes == 45


class TestRatingIntegration:
    """Integration tests for ratings."""

    async def test_rate_content(self, content_service, db_session):
        """Test rating content."""
        from uuid import uuid4

        from app.schemas import ContentCreateRequest, ContentRatingCreateRequest
        
        request = ContentCreateRequest(
            title="Rated Content",
            slug="rated-content",
            description="Content to rate",
            content_type="movie",
        )
        content = await content_service.create_content(request)
        
        user_id = uuid4()
        rating_request = ContentRatingCreateRequest(
            rating=8.5,
            review="Great movie!",
        )
        
        rating = await content_service.rate_content(content.id, user_id, rating_request)
        
        assert rating.rating == 8.5
        assert rating.review == "Great movie!"
        assert rating.content_id == content.id
        assert rating.user_id == user_id


class TestRecommendationIntegration:
    """Integration tests for recommendations."""

    async def test_add_recommendation(self, content_service, db_session):
        """Test adding a recommendation."""
        from app.schemas import ContentCreateRequest, ContentRecommendationCreateRequest
        
        # Create two content items
        content1 = ContentCreateRequest(
            title="Content 1",
            slug="content-1",
            description="First",
            content_type="movie",
        )
        c1 = await content_service.create_content(content1)
        
        content2 = ContentCreateRequest(
            title="Content 2",
            slug="content-2",
            description="Second",
            content_type="movie",
        )
        c2 = await content_service.create_content(content2)
        
        # Add recommendation
        rec_request = ContentRecommendationCreateRequest(
            recommended_content_id=c2.id,
            similarity_score=0.95,
            recommendation_type="similar",
        )
        
        recommendation = await content_service.add_recommendation(c1.id, rec_request)
        
        assert recommendation.recommended_content_id == c2.id
        assert recommendation.similarity_score == 0.95