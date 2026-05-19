"""Content service repositories."""

import logging
from uuid import UUID
from typing import Optional, List, Tuple
from datetime import datetime, timezone

from sqlalchemy import select, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import Genre, Movie, Show, Season, Episode

logger = logging.getLogger(__name__)


class GenreRepository:
    """Repository for genre data access."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create(self, name: str, description: Optional[str] = None) -> Genre:
        """Create new genre."""
        genre = Genre(name=name, description=description, is_active=True)
        self.db.add(genre)
        await self.db.flush()
        return genre
    
    async def get_by_id(self, genre_id: UUID) -> Optional[Genre]:
        """Get genre by ID."""
        result = await self.db.execute(
            select(Genre).where(Genre.id == genre_id).where(Genre.is_active)
        )
        return result.scalar_one_or_none()
    
    async def get_by_name(self, name: str) -> Optional[Genre]:
        """Get genre by name."""
        result = await self.db.execute(
            select(Genre).where(Genre.name == name).where(Genre.is_active)
        )
        return result.scalar_one_or_none()
    
    async def list_all(self) -> List[Genre]:
        """List all genres."""
        result = await self.db.execute(
            select(Genre).where(Genre.is_active).order_by(Genre.name)
        )
        return result.scalars().all()


class MovieRepository:
    """Repository for movie data access."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create(self, **kwargs) -> Movie:
        """Create new movie."""
        movie = Movie(**kwargs, is_active=True)
        self.db.add(movie)
        await self.db.flush()
        return movie
    
    async def get_by_id(self, movie_id: UUID) -> Optional[Movie]:
        """Get movie by ID."""
        result = await self.db.execute(
            select(Movie).where(Movie.id == movie_id).where(Movie.is_active)
        )
        return result.scalar_one_or_none()
    
    async def get_by_media_key(self, media_key: str) -> Optional[Movie]:
        """Get movie by media key."""
        result = await self.db.execute(
            select(Movie).where(Movie.media_key == media_key).where(Movie.is_active)
        )
        return result.scalar_one_or_none()
    
    async def list_by_genre(self, genre_id: UUID, limit: int = 50, offset: int = 0) -> Tuple[List[Movie], int]:
        """List movies by genre."""
        query = select(Movie).where(
            Movie.is_active
        ).where(
            Movie.genre_ids.contains([genre_id])
        )
        total_result = await self.db.execute(select(Movie).where(Movie.genre_ids.contains([genre_id])).where(Movie.is_active))
        total = len(total_result.scalars().all())
        
        result = await self.db.execute(
            query.order_by(desc(Movie.rating)).limit(limit).offset(offset)
        )
        return result.scalars().all(), total
    
    async def list_recent(self, limit: int = 50, offset: int = 0) -> Tuple[List[Movie], int]:
        """List recent movies."""
        query = select(Movie).where(Movie.is_active)
        total_result = await self.db.execute(query)
        total = len(total_result.scalars().all())
        
        result = await self.db.execute(
            query.order_by(desc(Movie.release_date)).limit(limit).offset(offset)
        )
        return result.scalars().all(), total
    
    async def list_trending(self, limit: int = 50, offset: int = 0) -> Tuple[List[Movie], int]:
        """List trending movies by views."""
        query = select(Movie).where(Movie.is_active)
        total_result = await self.db.execute(query)
        total = len(total_result.scalars().all())
        
        result = await self.db.execute(
            query.order_by(desc(Movie.views_count)).limit(limit).offset(offset)
        )
        return result.scalars().all(), total
    
    async def search(self, query: str, limit: int = 50, offset: int = 0) -> Tuple[List[Movie], int]:
        """Search movies by title."""
        search_query = f"%{query}%"
        sql_query = select(Movie).where(Movie.is_active).where(Movie.title.ilike(search_query))
        total_result = await self.db.execute(sql_query)
        total = len(total_result.scalars().all())
        
        result = await self.db.execute(
            sql_query.order_by(Movie.rating.desc()).limit(limit).offset(offset)
        )
        return result.scalars().all(), total
    
    async def update(self, movie: Movie, **kwargs) -> Movie:
        """Update movie."""
        for key, value in kwargs.items():
            if value is not None:
                setattr(movie, key, value)
        movie.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return movie
    
    async def increment_views(self, movie_id: UUID) -> None:
        """Increment movie view count."""
        movie = await self.get_by_id(movie_id)
        if movie:
            movie.views_count += 1
            await self.db.flush()


class ShowRepository:
    """Repository for show data access."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create(self, **kwargs) -> Show:
        """Create new show."""
        show = Show(**kwargs, is_active=True)
        self.db.add(show)
        await self.db.flush()
        return show
    
    async def get_by_id(self, show_id: UUID) -> Optional[Show]:
        """Get show by ID."""
        result = await self.db.execute(
            select(Show).where(Show.id == show_id).where(Show.is_active)
        )
        return result.scalar_one_or_none()
    
    async def get_by_media_key(self, media_key: str) -> Optional[Show]:
        """Get show by media key."""
        result = await self.db.execute(
            select(Show).where(Show.media_key == media_key).where(Show.is_active)
        )
        return result.scalar_one_or_none()
    
    async def list_by_genre(self, genre_id: UUID, limit: int = 50, offset: int = 0) -> Tuple[List[Show], int]:
        """List shows by genre."""
        query = select(Show).where(Show.is_active).where(Show.genre_ids.contains([genre_id]))
        total_result = await self.db.execute(select(Show).where(Show.genre_ids.contains([genre_id])).where(Show.is_active))
        total = len(total_result.scalars().all())
        
        result = await self.db.execute(
            query.order_by(desc(Show.rating)).limit(limit).offset(offset)
        )
        return result.scalars().all(), total
    
    async def list_recent(self, limit: int = 50, offset: int = 0) -> Tuple[List[Show], int]:
        """List recent shows."""
        query = select(Show).where(Show.is_active)
        total_result = await self.db.execute(query)
        total = len(total_result.scalars().all())
        
        result = await self.db.execute(
            query.order_by(desc(Show.first_air_date)).limit(limit).offset(offset)
        )
        return result.scalars().all(), total
    
    async def list_ongoing(self, limit: int = 50, offset: int = 0) -> Tuple[List[Show], int]:
        """List ongoing shows."""
        query = select(Show).where(Show.is_active).where(Show.is_ongoing)
        total_result = await self.db.execute(query)
        total = len(total_result.scalars().all())
        
        result = await self.db.execute(
            query.order_by(desc(Show.rating)).limit(limit).offset(offset)
        )
        return result.scalars().all(), total
    
    async def search(self, query: str, limit: int = 50, offset: int = 0) -> Tuple[List[Show], int]:
        """Search shows by title."""
        search_query = f"%{query}%"
        sql_query = select(Show).where(Show.is_active).where(Show.title.ilike(search_query))
        total_result = await self.db.execute(sql_query)
        total = len(total_result.scalars().all())
        
        result = await self.db.execute(
            sql_query.order_by(Show.rating.desc()).limit(limit).offset(offset)
        )
        return result.scalars().all(), total
    
    async def update(self, show: Show, **kwargs) -> Show:
        """Update show."""
        for key, value in kwargs.items():
            if value is not None:
                setattr(show, key, value)
        show.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return show
    
    async def increment_views(self, show_id: UUID) -> None:
        """Increment show view count."""
        show = await self.get_by_id(show_id)
        if show:
            show.views_count += 1
            await self.db.flush()


class SeasonRepository:
    """Repository for season data access."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create(self, show_id: UUID, season_number: int, **kwargs) -> Season:
        """Create new season."""
        season = Season(show_id=show_id, season_number=season_number, is_active=True, **kwargs)
        self.db.add(season)
        await self.db.flush()
        return season
    
    async def get_by_id(self, season_id: UUID) -> Optional[Season]:
        """Get season by ID."""
        result = await self.db.execute(
            select(Season).where(Season.id == season_id).where(Season.is_active)
        )
        return result.scalar_one_or_none()
    
    async def list_by_show(self, show_id: UUID) -> List[Season]:
        """List seasons for show."""
        result = await self.db.execute(
            select(Season).where(Season.show_id == show_id).where(Season.is_active).order_by(Season.season_number)
        )
        return result.scalars().all()
    
    async def update(self, season: Season, **kwargs) -> Season:
        """Update season."""
        for key, value in kwargs.items():
            if value is not None:
                setattr(season, key, value)
        season.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return season


class EpisodeRepository:
    """Repository for episode data access."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create(self, season_id: UUID, show_id: UUID, **kwargs) -> Episode:
        """Create new episode."""
        episode = Episode(season_id=season_id, show_id=show_id, is_active=True, **kwargs)
        self.db.add(episode)
        await self.db.flush()
        return episode
    
    async def get_by_id(self, episode_id: UUID) -> Optional[Episode]:
        """Get episode by ID."""
        result = await self.db.execute(
            select(Episode).where(Episode.id == episode_id).where(Episode.is_active)
        )
        return result.scalar_one_or_none()
    
    async def get_by_media_key(self, media_key: str) -> Optional[Episode]:
        """Get episode by media key."""
        result = await self.db.execute(
            select(Episode).where(Episode.media_key == media_key).where(Episode.is_active)
        )
        return result.scalar_one_or_none()
    
    async def list_by_season(self, season_id: UUID) -> List[Episode]:
        """List episodes for season."""
        result = await self.db.execute(
            select(Episode).where(Episode.season_id == season_id).where(Episode.is_active).order_by(Episode.episode_number)
        )
        return result.scalars().all()
    
    async def list_by_show(self, show_id: UUID, limit: int = 50, offset: int = 0) -> Tuple[List[Episode], int]:
        """List episodes for show."""
        query = select(Episode).where(Episode.show_id == show_id).where(Episode.is_active)
        total_result = await self.db.execute(query)
        total = len(total_result.scalars().all())
        
        result = await self.db.execute(
            query.order_by(Episode.air_date.desc()).limit(limit).offset(offset)
        )
        return result.scalars().all(), total
    
    async def update(self, episode: Episode, **kwargs) -> Episode:
        """Update episode."""
        for key, value in kwargs.items():
            if value is not None:
                setattr(episode, key, value)
        episode.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return episode
    
    async def increment_views(self, episode_id: UUID) -> None:
        """Increment episode view count."""
        episode = await self.get_by_id(episode_id)
        if episode:
            episode.views_count += 1
            await self.db.flush()
