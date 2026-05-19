"""Content service business logic."""

import logging
from uuid import UUID
from typing import Optional, List, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import Genre, Movie, Show, Season, Episode
from app.repositories.content import (
    GenreRepository,
    MovieRepository,
    ShowRepository,
    SeasonRepository,
    EpisodeRepository
)

logger = logging.getLogger(__name__)


class ContentService:
    """Service for content management."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.genre_repo = GenreRepository(db)
        self.movie_repo = MovieRepository(db)
        self.show_repo = ShowRepository(db)
        self.season_repo = SeasonRepository(db)
        self.episode_repo = EpisodeRepository(db)
    
    # Genre Management
    async def create_genre(self, name: str, description: Optional[str] = None) -> Genre:
        """Create new genre."""
        existing = await self.genre_repo.get_by_name(name)
        if existing:
            raise ValueError(f"Genre '{name}' already exists")
        
        genre = await self.genre_repo.create(name, description)
        logger.info(f"Created genre: {name}")
        return genre
    
    async def get_genre(self, genre_id: UUID) -> Optional[Genre]:
        """Get genre by ID."""
        return await self.genre_repo.get_by_id(genre_id)
    
    async def list_genres(self) -> List[Genre]:
        """List all genres."""
        return await self.genre_repo.list_all()
    
    # Movie Management
    async def create_movie(self, movie_data: dict) -> Movie:
        """Create new movie."""
        existing = await self.movie_repo.get_by_media_key(movie_data.get("media_key", ""))
        if existing:
            raise ValueError("Movie with this media key already exists")
        
        movie = await self.movie_repo.create(**movie_data)
        logger.info(f"Created movie: {movie.title}")
        return movie
    
    async def get_movie(self, movie_id: UUID) -> Optional[Movie]:
        """Get movie by ID."""
        return await self.movie_repo.get_by_id(movie_id)
    
    async def get_movie_by_media_key(self, media_key: str) -> Optional[Movie]:
        """Get movie by media key."""
        return await self.movie_repo.get_by_media_key(media_key)
    
    async def list_movies_by_genre(self, genre_id: UUID, limit: int = 50, offset: int = 0) -> Tuple[List[Movie], int]:
        """List movies by genre."""
        return await self.movie_repo.list_by_genre(genre_id, limit, offset)
    
    async def list_recent_movies(self, limit: int = 50, offset: int = 0) -> Tuple[List[Movie], int]:
        """List recent movies."""
        return await self.movie_repo.list_recent(limit, offset)
    
    async def list_trending_movies(self, limit: int = 50, offset: int = 0) -> Tuple[List[Movie], int]:
        """List trending movies."""
        return await self.movie_repo.list_trending(limit, offset)
    
    async def search_movies(self, query: str, limit: int = 50, offset: int = 0) -> Tuple[List[Movie], int]:
        """Search movies."""
        if len(query) < 2:
            raise ValueError("Search query must be at least 2 characters")
        return await self.movie_repo.search(query, limit, offset)
    
    async def update_movie(self, movie_id: UUID, movie_data: dict) -> Movie:
        """Update movie."""
        movie = await self.get_movie(movie_id)
        if not movie:
            raise ValueError("Movie not found")
        
        movie = await self.movie_repo.update(movie, **movie_data)
        logger.info(f"Updated movie: {movie.title}")
        return movie
    
    async def increment_movie_views(self, movie_id: UUID) -> None:
        """Increment movie views."""
        await self.movie_repo.increment_views(movie_id)
    
    # Show Management
    async def create_show(self, show_data: dict) -> Show:
        """Create new show."""
        existing = await self.show_repo.get_by_media_key(show_data.get("media_key", ""))
        if existing:
            raise ValueError("Show with this media key already exists")
        
        show = await self.show_repo.create(**show_data)
        logger.info(f"Created show: {show.title}")
        return show
    
    async def get_show(self, show_id: UUID) -> Optional[Show]:
        """Get show by ID."""
        return await self.show_repo.get_by_id(show_id)
    
    async def get_show_by_media_key(self, media_key: str) -> Optional[Show]:
        """Get show by media key."""
        return await self.show_repo.get_by_media_key(media_key)
    
    async def list_shows_by_genre(self, genre_id: UUID, limit: int = 50, offset: int = 0) -> Tuple[List[Show], int]:
        """List shows by genre."""
        return await self.show_repo.list_by_genre(genre_id, limit, offset)
    
    async def list_recent_shows(self, limit: int = 50, offset: int = 0) -> Tuple[List[Show], int]:
        """List recent shows."""
        return await self.show_repo.list_recent(limit, offset)
    
    async def list_ongoing_shows(self, limit: int = 50, offset: int = 0) -> Tuple[List[Show], int]:
        """List ongoing shows."""
        return await self.show_repo.list_ongoing(limit, offset)
    
    async def search_shows(self, query: str, limit: int = 50, offset: int = 0) -> Tuple[List[Show], int]:
        """Search shows."""
        if len(query) < 2:
            raise ValueError("Search query must be at least 2 characters")
        return await self.show_repo.search(query, limit, offset)
    
    async def update_show(self, show_id: UUID, show_data: dict) -> Show:
        """Update show."""
        show = await self.get_show(show_id)
        if not show:
            raise ValueError("Show not found")
        
        show = await self.show_repo.update(show, **show_data)
        logger.info(f"Updated show: {show.title}")
        return show
    
    async def increment_show_views(self, show_id: UUID) -> None:
        """Increment show views."""
        await self.show_repo.increment_views(show_id)
    
    # Season Management
    async def create_season(self, show_id: UUID, season_number: int, season_data: dict) -> Season:
        """Create new season."""
        show = await self.get_show(show_id)
        if not show:
            raise ValueError("Show not found")
        
        season = await self.season_repo.create(show_id, season_number, **season_data)
        logger.info(f"Created season {season_number} for show: {show.title}")
        return season
    
    async def get_season(self, season_id: UUID) -> Optional[Season]:
        """Get season by ID."""
        return await self.season_repo.get_by_id(season_id)
    
    async def list_seasons(self, show_id: UUID) -> List[Season]:
        """List seasons for show."""
        show = await self.get_show(show_id)
        if not show:
            raise ValueError("Show not found")
        return await self.season_repo.list_by_show(show_id)
    
    async def update_season(self, season_id: UUID, season_data: dict) -> Season:
        """Update season."""
        season = await self.get_season(season_id)
        if not season:
            raise ValueError("Season not found")
        
        return await self.season_repo.update(season, **season_data)
    
    # Episode Management
    async def create_episode(self, season_id: UUID, show_id: UUID, episode_data: dict) -> Episode:
        """Create new episode."""
        season = await self.get_season(season_id)
        if not season:
            raise ValueError("Season not found")
        
        show = await self.get_show(show_id)
        if not show:
            raise ValueError("Show not found")
        
        episode = await self.episode_repo.create(season_id, show_id, **episode_data)
        logger.info(f"Created episode {episode.episode_number} for season {season.season_number}")
        return episode
    
    async def get_episode(self, episode_id: UUID) -> Optional[Episode]:
        """Get episode by ID."""
        return await self.episode_repo.get_by_id(episode_id)
    
    async def get_episode_by_media_key(self, media_key: str) -> Optional[Episode]:
        """Get episode by media key."""
        return await self.episode_repo.get_by_media_key(media_key)
    
    async def list_episodes_by_season(self, season_id: UUID) -> List[Episode]:
        """List episodes for season."""
        season = await self.get_season(season_id)
        if not season:
            raise ValueError("Season not found")
        return await self.episode_repo.list_by_season(season_id)
    
    async def list_episodes_by_show(self, show_id: UUID, limit: int = 50, offset: int = 0) -> Tuple[List[Episode], int]:
        """List episodes for show."""
        show = await self.get_show(show_id)
        if not show:
            raise ValueError("Show not found")
        return await self.episode_repo.list_by_show(show_id, limit, offset)
    
    async def update_episode(self, episode_id: UUID, episode_data: dict) -> Episode:
        """Update episode."""
        episode = await self.get_episode(episode_id)
        if not episode:
            raise ValueError("Episode not found")
        
        return await self.episode_repo.update(episode, **episode_data)
    
    async def increment_episode_views(self, episode_id: UUID) -> None:
        """Increment episode views."""
        await self.episode_repo.increment_views(episode_id)
