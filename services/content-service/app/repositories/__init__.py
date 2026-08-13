"""
Repository layer for Content Service data access.
Provides abstraction over database operations with transaction management.
"""

import logging
from typing import Sequence
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    AnimationStyle,
    CastMember,
    Content,
    ContentRating,
    ContentRecommendation,
    ContentStatus,
    ContentType,
    Episode,
    Genre,
    Season,
)

logger = logging.getLogger(__name__)


class BaseRepository:
    """Base repository with common transaction management methods."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def commit(self) -> None:
        """Commit current transaction."""
        await self.session.commit()

    async def rollback(self) -> None:
        """Rollback current transaction."""
        await self.session.rollback()

    async def flush(self) -> None:
        """Flush pending changes."""
        await self.session.flush()


class GenreRepository(BaseRepository):
    """Repository for genre operations."""

    async def create(
        self, name: str, slug: str, description: str | None = None, icon_url: str | None = None
    ) -> Genre:
        """Create a new genre."""
        genre = Genre(name=name, slug=slug, description=description, icon_url=icon_url)
        self.session.add(genre)
        await self.flush()
        return genre

    async def get_by_id(self, genre_id: UUID) -> Genre | None:
        """Get genre by ID."""
        return await self.session.get(Genre, genre_id)

    async def get_by_slug(self, slug: str) -> Genre | None:
        """Get genre by slug."""
        result = await self.session.execute(select(Genre).where(Genre.slug == slug))
        return result.scalars().first()

    async def get_all(self) -> list[Genre]:
        """Get all genres."""
        result = await self.session.execute(select(Genre))
        return list(result.scalars().all())

    async def update(self, genre_id: UUID, **kwargs) -> Genre | None:
        """Update a genre."""
        genre = await self.get_by_id(genre_id)
        if not genre:
            return None

        for key, value in kwargs.items():
            if hasattr(genre, key) and value is not None:
                setattr(genre, key, value)

        await self.flush()
        return genre

    async def delete(self, genre_id: UUID) -> bool:
        """Delete a genre."""
        genre = await self.get_by_id(genre_id)
        if genre:
            await self.session.delete(genre)
            await self.flush()
            return True
        return False


class CastMemberRepository(BaseRepository):
    """Repository for cast member operations."""

    async def create(
        self,
        name: str,
        slug: str,
        bio: str | None = None,
        birth_date=None,
        image_url: str | None = None,
    ) -> CastMember:
        """Create a new cast member."""
        member = CastMember(
            name=name, slug=slug, bio=bio, birth_date=birth_date, image_url=image_url
        )
        self.session.add(member)
        await self.flush()
        return member

    async def get_by_id(self, member_id: UUID) -> CastMember | None:
        """Get cast member by ID."""
        return await self.session.get(CastMember, member_id)

    async def get_by_slug(self, slug: str) -> CastMember | None:
        """Get cast member by slug."""
        result = await self.session.execute(select(CastMember).where(CastMember.slug == slug))
        return result.scalars().first()

    async def search(self, name: str) -> list[CastMember]:
        """Search cast members by name."""
        result = await self.session.execute(
            select(CastMember).where(CastMember.name.ilike(f"%{name}%")).limit(20)
        )
        return list(result.scalars().all())


class ContentRepository(BaseRepository):
    """Repository for content operations."""

    async def create(
        self,
        title: str,
        slug: str,
        description: str,
        content_type: ContentType,
        release_date=None,
        duration_minutes: int | None = None,
        original_language: str = "en",
        country: str | None = None,
        poster_url: str | None = None,
        backdrop_url: str | None = None,
        trailer_url: str | None = None,
        imdb_rating: float | None = None,
        content_rating: str | None = None,
        is_premium: bool = False,
        can_download: bool = True,
        can_stream: bool = True,
        genres: list[Genre] | None = None,
    ) -> Content:
        """Create a new content."""
        content = Content(
            title=title,
            slug=slug,
            description=description,
            content_type=content_type,
            release_date=release_date,
            duration_minutes=duration_minutes,
            original_language=original_language,
            country=country,
            poster_url=poster_url,
            backdrop_url=backdrop_url,
            trailer_url=trailer_url,
            imdb_rating=imdb_rating,
            content_rating=content_rating,
            is_premium=is_premium,
            can_download=can_download,
            can_stream=can_stream,
            genres=genres or [],
        )
        self.session.add(content)
        await self.flush()
        return content

    async def get_by_id(self, content_id: UUID) -> Content | None:
        """Get content by ID with all relationships."""
        result = await self.session.execute(
            select(Content)
            .where(Content.id == content_id)
            .options(
                selectinload(Content.genres),
                selectinload(Content.cast_members),
                selectinload(Content.seasons).selectinload(Season.episodes),
                selectinload(Content.episodes),
            )
        )
        return result.scalars().unique().first()

    async def get_by_slug(self, slug: str) -> Content | None:
        """Get content by slug."""
        result = await self.session.execute(select(Content).where(Content.slug == slug))
        return result.scalars().first()

    async def get_published(self) -> Sequence[Content]:
        """Get all published content."""
        result = await self.session.execute(
            select(Content)
            .where(Content.status == ContentStatus.PUBLISHED)
            .options(selectinload(Content.genres), selectinload(Content.cast_members))
        )
        return result.scalars().unique().all()

    async def list_filtered(
        self,
        page: int = 1,
        page_size: int = 20,
        content_type: str | None = None,
        status: str | None = None,
        genre_id: UUID | None = None,
    ) -> Sequence[Content]:
        """Get paginated content with optional type/status/genre filters."""
        stmt = select(Content).options(selectinload(Content.genres))

        conditions = []
        if content_type:
            conditions.append(Content.content_type == ContentType(content_type))
        if status:
            conditions.append(Content.status == ContentStatus(status))
        if conditions:
            stmt = stmt.where(and_(*conditions))

        if genre_id:
            stmt = stmt.join(Content.genres).where(Genre.id == genre_id)

        stmt = (
            stmt.order_by(Content.created_at.desc(), Content.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )

        result = await self.session.execute(stmt)
        return result.scalars().unique().all()

    async def get_by_type(self, content_type: ContentType) -> Sequence[Content]:
        """Get content by type."""
        result = await self.session.execute(
            select(Content)
            .where(
                and_(
                    Content.content_type == content_type, Content.status == ContentStatus.PUBLISHED
                )
            )
            .options(selectinload(Content.genres))
        )
        return result.scalars().unique().all()

    async def get_by_genre(self, genre_id: UUID) -> Sequence[Content]:
        """Get content by genre."""
        result = await self.session.execute(
            select(Content)
            .join(Content.genres)
            .where(and_(Genre.id == genre_id, Content.status == ContentStatus.PUBLISHED))
            .options(selectinload(Content.genres))
        )
        return result.scalars().unique().all()

    async def search(self, query: str) -> Sequence[Content]:
        """Search content by title and description."""
        result = await self.session.execute(
            select(Content)
            .where(
                and_(
                    Content.status == ContentStatus.PUBLISHED,
                    or_(Content.title.ilike(f"%{query}%"), Content.description.ilike(f"%{query}%")),
                )
            )
            .limit(50)
        )
        return list(result.scalars().all())

    async def get_trending(self, limit: int = 10) -> Sequence[Content]:
        """Get trending content by audience score and votes."""
        result = await self.session.execute(
            select(Content)
            .options(selectinload(Content.genres))
            .where(Content.status == ContentStatus.PUBLISHED)
            .order_by(Content.audience_score.desc(), Content.total_votes.desc(), Content.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_premium(self) -> Sequence[Content]:
        """Get premium content."""
        result = await self.session.execute(
            select(Content).where(
                and_(Content.is_premium == True, Content.status == ContentStatus.PUBLISHED)
            )
        )
        return list(result.scalars().all())

    async def update(self, content_id: UUID, **kwargs) -> Content | None:
        """Update content."""
        content = await self.get_by_id(content_id)
        if not content:
            return None

        for key, value in kwargs.items():
            if hasattr(content, key) and value is not None:
                setattr(content, key, value)

        await self.flush()
        return content

    async def delete(self, content_id: UUID) -> bool:
        """Delete content."""
        content = await self.get_by_id(content_id)
        if content:
            await self.session.delete(content)
            await self.flush()
            return True
        return False

    # Animation-specific queries

    async def get_by_animation_style(
        self, animation_style: AnimationStyle, limit: int = 50, offset: int = 0
    ) -> Sequence[Content]:
        """Get content by animation style."""
        result = await self.session.execute(
            select(Content)
            .where(
                and_(
                    Content.animation_style == animation_style,
                    Content.status == ContentStatus.PUBLISHED,
                )
            )
            .order_by(Content.created_at.desc(), Content.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().unique().all()

    async def get_series_episodes(
        self, series_id: UUID, limit: int = 50, offset: int = 0
    ) -> Sequence[Content]:
        """Get episodes belonging to a series."""
        result = await self.session.execute(
            select(Content)
            .where(
                and_(Content.series_id == series_id, Content.content_type == ContentType.EPISODE)
            )
            .order_by(Content.season_number.asc(), Content.episode_number.asc(), Content.id.asc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().unique().all()

    async def get_creator_filmography(
        self, creator_id: UUID, limit: int = 50, offset: int = 0
    ) -> Sequence[Content]:
        """Get content credited to a creator."""
        result = await self.session.execute(
            select(Content)
            .where(
                and_(Content.creator_id == creator_id, Content.status == ContentStatus.PUBLISHED)
            )
            .order_by(Content.created_at.desc(), Content.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().unique().all()


class SeasonRepository(BaseRepository):
    """Repository for season operations."""

    async def create(
        self,
        content_id: UUID,
        season_number: int,
        title: str,
        description: str | None = None,
        poster_url: str | None = None,
        release_date=None,
    ) -> Season:
        """Create a new season."""
        season = Season(
            content_id=content_id,
            season_number=season_number,
            title=title,
            description=description,
            poster_url=poster_url,
            release_date=release_date,
        )
        self.session.add(season)
        await self.flush()
        return season

    async def get_by_id(self, season_id: UUID) -> Season | None:
        """Get season by ID."""
        result = await self.session.execute(
            select(Season).where(Season.id == season_id).options(selectinload(Season.episodes))
        )
        return result.scalars().unique().first()

    async def get_by_content_and_number(
        self, content_id: UUID, season_number: int
    ) -> Season | None:
        """Get season by content ID and season number."""
        result = await self.session.execute(
            select(Season)
            .where(and_(Season.content_id == content_id, Season.season_number == season_number))
            .options(selectinload(Season.episodes))
        )
        return result.scalars().unique().first()

    async def get_content_seasons(self, content_id: UUID) -> Sequence[Season]:
        """Get all seasons for content."""
        result = await self.session.execute(
            select(Season)
            .where(Season.content_id == content_id)
            .order_by(Season.season_number)
            .options(selectinload(Season.episodes))
        )
        return result.scalars().unique().all()

    async def update(self, season_id: UUID, **kwargs) -> Season | None:
        """Update season."""
        season = await self.get_by_id(season_id)
        if not season:
            return None

        for key, value in kwargs.items():
            if hasattr(season, key) and value is not None:
                setattr(season, key, value)

        await self.flush()
        return season

    async def delete(self, season_id: UUID) -> bool:
        """Delete a season."""
        season = await self.get_by_id(season_id)
        if season:
            await self.session.delete(season)
            await self.flush()
            return True
        return False


class EpisodeRepository(BaseRepository):
    """Repository for episode operations."""

    async def create(
        self,
        content_id: UUID,
        season_id: UUID,
        episode_number: int,
        title: str,
        duration_minutes: int,
        description: str | None = None,
        thumbnail_url: str | None = None,
        release_date=None,
        is_available: bool = True,
    ) -> Episode:
        """Create a new episode."""
        episode = Episode(
            content_id=content_id,
            season_id=season_id,
            episode_number=episode_number,
            title=title,
            duration_minutes=duration_minutes,
            description=description,
            thumbnail_url=thumbnail_url,
            release_date=release_date,
            is_available=is_available,
        )
        self.session.add(episode)
        await self.flush()
        return episode

    async def get_by_id(self, episode_id: UUID) -> Episode | None:
        """Get episode by ID."""
        return await self.session.get(Episode, episode_id)

    async def get_season_episodes(self, season_id: UUID) -> Sequence[Episode]:
        """Get all episodes in a season."""
        result = await self.session.execute(
            select(Episode).where(Episode.season_id == season_id).order_by(Episode.episode_number)
        )
        return list(result.scalars().all())

    async def update(self, episode_id: UUID, **kwargs) -> Episode | None:
        """Update episode."""
        episode = await self.get_by_id(episode_id)
        if not episode:
            return None

        for key, value in kwargs.items():
            if hasattr(episode, key) and value is not None:
                setattr(episode, key, value)

        await self.flush()
        return episode

    async def delete(self, episode_id: UUID) -> bool:
        """Delete an episode."""
        episode = await self.get_by_id(episode_id)
        if episode:
            await self.session.delete(episode)
            await self.flush()
            return True
        return False


class ContentRatingRepository(BaseRepository):
    """Repository for content rating operations."""

    async def create(
        self, content_id: UUID, user_id: UUID, rating: float, review: str | None = None
    ) -> ContentRating:
        """Create or update a content rating."""
        existing = await self.get_user_rating(content_id, user_id)
        if existing:
            existing.rating = rating  # type: ignore[assignment]
            existing.review = review  # type: ignore[assignment]
            await self.flush()
            return existing

        rating_obj = ContentRating(
            content_id=content_id, user_id=user_id, rating=rating, review=review
        )
        self.session.add(rating_obj)
        await self.flush()
        return rating_obj

    async def get_user_rating(self, content_id: UUID, user_id: UUID) -> ContentRating | None:
        """Get user's rating for content."""
        result = await self.session.execute(
            select(ContentRating).where(
                and_(ContentRating.content_id == content_id, ContentRating.user_id == user_id)
            )
        )
        return result.scalars().first()

    async def get_content_ratings(self, content_id: UUID) -> list[ContentRating]:
        """Get all ratings for content."""
        result = await self.session.execute(
            select(ContentRating)
            .where(ContentRating.content_id == content_id)
            .order_by(ContentRating.created_at.desc())
        )
        return list(result.scalars().all())


class ContentRecommendationRepository(BaseRepository):
    """Repository for content recommendation operations."""

    async def create(
        self,
        content_id: UUID,
        recommended_content_id: UUID,
        similarity_score: float,
        recommendation_type: str,
    ) -> ContentRecommendation:
        """Create a content recommendation."""
        recommendation = ContentRecommendation(
            content_id=content_id,
            recommended_content_id=recommended_content_id,
            similarity_score=similarity_score,
            recommendation_type=recommendation_type,
        )
        self.session.add(recommendation)
        await self.flush()
        return recommendation

    async def get_recommendations(
        self, content_id: UUID, limit: int = 10
    ) -> list[ContentRecommendation]:
        """Get recommendations for content."""
        result = await self.session.execute(
            select(ContentRecommendation)
            .where(ContentRecommendation.content_id == content_id)
            .order_by(ContentRecommendation.similarity_score.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def delete(self, recommendation_id: UUID) -> bool:
        """Delete a recommendation."""
        recommendation = await self.session.get(ContentRecommendation, recommendation_id)
        if recommendation:
            await self.session.delete(recommendation)
            await self.flush()
            return True
        return False
