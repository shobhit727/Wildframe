"""
Service layer for Content Service business logic.
Orchestrates repositories and business rules.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from typing import List, Optional
import logging

from app.models import ContentType, ContentStatus, AnimationStyle, ContentCreator, ContentSeries, SeriesStatus
from app.repositories import (
    ContentRepository, GenreRepository, CastMemberRepository,
    SeasonRepository, EpisodeRepository, ContentRatingRepository,
    ContentRecommendationRepository
)
from app.schemas import (
    ContentCreateRequest, ContentUpdateRequest, ContentPublishRequest,
    SeasonCreateRequest, SeasonUpdateRequest, EpisodeCreateRequest, EpisodeUpdateRequest,
    GenreCreateRequest, CastMemberCreateRequest, ContentRatingCreateRequest,
    ContentRecommendationCreateRequest
)

logger = logging.getLogger(__name__)


class ContentService:
    """Service for content management."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.content_repo = ContentRepository(session)
        self.genre_repo = GenreRepository(session)
        self.cast_repo = CastMemberRepository(session)
        self.season_repo = SeasonRepository(session)
        self.episode_repo = EpisodeRepository(session)
        self.rating_repo = ContentRatingRepository(session)
        self.recommendation_repo = ContentRecommendationRepository(session)
    
    # Genre operations
    
    async def create_genre(self, request: GenreCreateRequest):
        """Create a new genre."""
        try:
            genre = await self.genre_repo.create(
                name=request.name,
                slug=request.slug,
                description=request.description,
                icon_url=request.icon_url
            )
            await self.content_repo.commit()
            return genre
        except Exception as e:
            await self.content_repo.rollback()
            logger.error(f"Failed to create genre: {e}")
            raise
    
    async def get_genre(self, genre_id: UUID):
        """Get genre by ID."""
        return await self.genre_repo.get_by_id(genre_id)
    
    async def list_genres(self):
        """List all genres."""
        return await self.genre_repo.get_all()
    
    async def delete_genre(self, genre_id: UUID):
        """Delete genre."""
        try:
            success = await self.genre_repo.delete(genre_id)
            await self.content_repo.commit()
            return success
        except Exception as e:
            await self.content_repo.rollback()
            logger.error(f"Failed to delete genre: {e}")
            raise
    
    # Cast member operations
    
    async def create_cast_member(self, request: CastMemberCreateRequest):
        """Create a new cast member."""
        try:
            member = await self.cast_repo.create(
                name=request.name,
                slug=request.slug,
                bio=request.bio,
                birth_date=request.birth_date,
                image_url=request.image_url
            )
            await self.content_repo.commit()
            return member
        except Exception as e:
            await self.content_repo.rollback()
            logger.error(f"Failed to create cast member: {e}")
            raise
    
    async def get_cast_member(self, member_id: UUID):
        """Get cast member by ID."""
        return await self.cast_repo.get_by_id(member_id)
    
    async def search_cast_members(self, name: str):
        """Search cast members by name."""
        return await self.cast_repo.search(name)
    
    # Content operations
    
    async def create_content(self, request: ContentCreateRequest):
        """Create new content."""
        try:
            content = await self.content_repo.create(
                title=request.title,
                slug=request.slug,
                description=request.description,
                content_type=ContentType(request.content_type),
                release_date=request.release_date,
                duration_minutes=request.duration_minutes,
                original_language=request.original_language,
                country=request.country,
                poster_url=request.poster_url,
                backdrop_url=request.backdrop_url,
                trailer_url=request.trailer_url,
                imdb_rating=request.imdb_rating,
                content_rating=request.content_rating,
                is_premium=request.is_premium,
                can_download=request.can_download,
                can_stream=request.can_stream
            )
            
            # Add genres if provided
            if request.genre_ids:
                for genre_id in request.genre_ids:
                    genre = await self.genre_repo.get_by_id(genre_id)
                    if genre:
                        content.genres.append(genre)
            
            await self.content_repo.commit()
            return content
        except Exception as e:
            await self.content_repo.rollback()
            logger.error(f"Failed to create content: {e}")
            raise
    
    async def get_content(self, content_id: UUID):
        """Get content by ID."""
        return await self.content_repo.get_by_id(content_id)
    
    async def get_content_by_slug(self, slug: str):
        """Get content by slug."""
        return await self.content_repo.get_by_slug(slug)
    
    async def list_content_by_type(self, content_type: str):
        """List content by type."""
        return await self.content_repo.get_by_type(ContentType(content_type))
    
    async def list_content_by_genre(self, genre_id: UUID):
        """List content by genre."""
        return await self.content_repo.get_by_genre(genre_id)
    
    async def search_content(self, query: str):
        """Search content."""
        return await self.content_repo.search(query)
    
    async def get_trending_content(self, limit: int = 10):
        """Get trending content."""
        return await self.content_repo.get_trending(limit)
    
    async def get_premium_content(self):
        """Get premium content."""
        return await self.content_repo.get_premium()
    
    async def update_content(self, content_id: UUID, request: ContentUpdateRequest):
        """Update content."""
        try:
            update_data = request.model_dump(exclude_unset=True, exclude={'genre_ids'})
            content = await self.content_repo.update(content_id, **update_data)
            
            # Update genres if provided
            if request.genre_ids is not None:
                content.genres.clear()
                for genre_id in request.genre_ids:
                    genre = await self.genre_repo.get_by_id(genre_id)
                    if genre:
                        content.genres.append(genre)
            
            await self.content_repo.commit()
            return content
        except Exception as e:
            await self.content_repo.rollback()
            logger.error(f"Failed to update content: {e}")
            raise
    
    async def publish_content(self, content_id: UUID, request: ContentPublishRequest):
        """Publish or archive content."""
        try:
            from datetime import datetime
            update_data = {'status': ContentStatus(request.status)}
            if request.status == 'published':
                update_data['published_at'] = datetime.utcnow()
            
            content = await self.content_repo.update(content_id, **update_data)
            await self.content_repo.commit()
            return content
        except Exception as e:
            await self.content_repo.rollback()
            logger.error(f"Failed to publish content: {e}")
            raise
    
    async def delete_content(self, content_id: UUID):
        """Delete content."""
        try:
            success = await self.content_repo.delete(content_id)
            await self.content_repo.commit()
            return success
        except Exception as e:
            await self.content_repo.rollback()
            logger.error(f"Failed to delete content: {e}")
            raise
    
    # Season operations
    
    async def create_season(self, content_id: UUID, request: SeasonCreateRequest):
        """Create a new season."""
        try:
            season = await self.season_repo.create(
                content_id=content_id,
                season_number=request.season_number,
                title=request.title,
                description=request.description,
                poster_url=request.poster_url,
                release_date=request.release_date
            )
            await self.content_repo.commit()
            return season
        except Exception as e:
            await self.content_repo.rollback()
            logger.error(f"Failed to create season: {e}")
            raise
    
    async def get_season(self, season_id: UUID):
        """Get season by ID."""
        return await self.season_repo.get_by_id(season_id)
    
    async def list_content_seasons(self, content_id: UUID):
        """List all seasons for content."""
        return await self.season_repo.get_content_seasons(content_id)
    
    async def update_season(self, season_id: UUID, request: SeasonUpdateRequest):
        """Update season."""
        try:
            update_data = request.model_dump(exclude_unset=True)
            season = await self.season_repo.update(season_id, **update_data)
            await self.content_repo.commit()
            return season
        except Exception as e:
            await self.content_repo.rollback()
            logger.error(f"Failed to update season: {e}")
            raise
    
    # Episode operations
    
    async def create_episode(self, content_id: UUID, season_id: UUID, request: EpisodeCreateRequest):
        """Create a new episode."""
        try:
            episode = await self.episode_repo.create(
                content_id=content_id,
                season_id=season_id,
                episode_number=request.episode_number,
                title=request.title,
                duration_minutes=request.duration_minutes,
                description=request.description,
                thumbnail_url=request.thumbnail_url,
                release_date=request.release_date,
                is_available=request.is_available
            )
            
            # Update season episode count
            season = await self.season_repo.get_by_id(season_id)
            if season:
                episodes = await self.episode_repo.get_season_episodes(season_id)
                season.episode_count = len(episodes)
            
            await self.content_repo.commit()
            return episode
        except Exception as e:
            await self.content_repo.rollback()
            logger.error(f"Failed to create episode: {e}")
            raise
    
    async def get_episode(self, episode_id: UUID):
        """Get episode by ID."""
        return await self.episode_repo.get_by_id(episode_id)
    
    async def list_season_episodes(self, season_id: UUID):
        """List all episodes in season."""
        return await self.episode_repo.get_season_episodes(season_id)
    
    async def update_episode(self, episode_id: UUID, request: EpisodeUpdateRequest):
        """Update episode."""
        try:
            update_data = request.model_dump(exclude_unset=True)
            episode = await self.episode_repo.update(episode_id, **update_data)
            await self.content_repo.commit()
            return episode
        except Exception as e:
            await self.content_repo.rollback()
            logger.error(f"Failed to update episode: {e}")
            raise
    
    # Rating operations
    
    async def rate_content(self, content_id: UUID, user_id: UUID, request: ContentRatingCreateRequest):
        """Rate content."""
        try:
            rating = await self.rating_repo.create(
                content_id=content_id,
                user_id=user_id,
                rating=request.rating,
                review=request.review
            )
            
            # Update content average score
            ratings = await self.rating_repo.get_content_ratings(content_id)
            if ratings:
                avg_score = sum(r.rating for r in ratings) / len(ratings)
                content = await self.content_repo.get_by_id(content_id)
                if content:
                    content.audience_score = avg_score
                    content.total_votes = len(ratings)
            
            await self.content_repo.commit()
            return rating
        except Exception as e:
            await self.content_repo.rollback()
            logger.error(f"Failed to rate content: {e}")
            raise
    
    async def get_content_ratings(self, content_id: UUID):
        """Get all ratings for content."""
        return await self.rating_repo.get_content_ratings(content_id)
    
    # Recommendation operations
    
    async def add_recommendation(self, content_id: UUID, request: ContentRecommendationCreateRequest):
        """Add content recommendation."""
        try:
            recommendation = await self.recommendation_repo.create(
                content_id=content_id,
                recommended_content_id=request.recommended_content_id,
                similarity_score=request.similarity_score,
                recommendation_type=request.recommendation_type
            )
            await self.content_repo.commit()
            return recommendation
        except Exception as e:
            await self.content_repo.rollback()
            logger.error(f"Failed to add recommendation: {e}")
            raise
    
    async def get_recommendations(self, content_id: UUID, limit: int = 10):
        """Get content recommendations."""
        return await self.recommendation_repo.get_recommendations(content_id, limit)
    
    async def remove_recommendation(self, recommendation_id: UUID):
        """Remove content recommendation."""
        try:
            success = await self.recommendation_repo.delete(recommendation_id)
            await self.content_repo.commit()
            return success
        except Exception as e:
            await self.content_repo.rollback()
            logger.error(f"Failed to remove recommendation: {e}")
            raise

    # Animation-specific queries

    async def get_by_animation_style(self, animation_style: AnimationStyle, limit: int = 50, offset: int = 0):
        """List content by animation style."""
        return await self.content_repo.get_by_animation_style(animation_style, limit, offset)

    async def get_series_episodes(self, series_id: UUID, limit: int = 50, offset: int = 0):
        """List episodes belonging to a series."""
        return await self.content_repo.get_series_episodes(series_id, limit, offset)

    async def get_creator_filmography(self, creator_id: UUID, limit: int = 50, offset: int = 0):
        """List content credited to a creator."""
        return await self.content_repo.get_creator_filmography(creator_id, limit, offset)
