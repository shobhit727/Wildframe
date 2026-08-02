"""Content service API routes."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.content import (
    CreateEpisodeRequest,
    CreateGenreRequest,
    CreateMovieRequest,
    CreateSeasonRequest,
    CreateShowRequest,
    EpisodeResponse,
    GenreResponse,
    ListEpisodesResponse,
    ListMoviesResponse,
    ListSeasonsResponse,
    ListShowsResponse,
    MovieResponse,
    SeasonResponse,
    ShowResponse,
    UpdateMovieRequest,
)
from app.services import ContentService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/content", tags=["content"])


async def get_content_service(db: Annotated[AsyncSession, Depends(get_db)]) -> ContentService:
    """Get content service instance."""
    return ContentService(db)


# Genre Endpoints


@router.post("/genres", response_model=GenreResponse, status_code=status.HTTP_201_CREATED)
async def create_genre(
    data: CreateGenreRequest, service: Annotated[ContentService, Depends(get_content_service)]
) -> GenreResponse:
    """Create new genre."""
    try:
        genre = await service.create_genre(data.name, data.description)
        return genre
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error creating genre: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create genre"
        )


@router.get("/genres", response_model=list[GenreResponse])
async def list_genres(
    service: Annotated[ContentService, Depends(get_content_service)],
) -> list[GenreResponse]:
    """List all genres."""
    try:
        genres = await service.list_genres()
        return genres
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error listing genres: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list genres"
        )


@router.get("/genres/{genre_id}", response_model=GenreResponse)
async def get_genre(
    genre_id: UUID, service: Annotated[ContentService, Depends(get_content_service)]
) -> GenreResponse:
    """Get genre by ID."""
    try:
        genre = await service.get_genre(genre_id)
        if not genre:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Genre not found")
        return genre
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error getting genre: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get genre"
        )


# Movie Endpoints


@router.post("/movies", response_model=MovieResponse, status_code=status.HTTP_201_CREATED)
async def create_movie(
    data: CreateMovieRequest, service: Annotated[ContentService, Depends(get_content_service)]
) -> MovieResponse:
    """Create new movie."""
    try:
        movie = await service.create_movie(data.model_dump())
        return movie
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error creating movie: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create movie"
        )


@router.get("/movies/{movie_id}", response_model=MovieResponse)
async def get_movie(
    movie_id: UUID, service: Annotated[ContentService, Depends(get_content_service)]
) -> MovieResponse:
    """Get movie by ID."""
    try:
        movie = await service.get_movie(movie_id)
        if not movie:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Movie not found")
        return movie
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error getting movie: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get movie"
        )


@router.get("/movies/by-key/{media_key}", response_model=MovieResponse)
async def get_movie_by_key(
    media_key: str, service: Annotated[ContentService, Depends(get_content_service)]
) -> MovieResponse:
    """Get movie by media key."""
    try:
        movie = await service.get_movie_by_media_key(media_key)
        if not movie:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Movie not found")
        return movie
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error getting movie: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get movie"
        )


@router.get("/movies", response_model=ListMoviesResponse)
async def list_movies(
    service: Annotated[ContentService, Depends(get_content_service)],
    genre_id: Annotated[UUID | None, Query()] = None,
    trending: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ListMoviesResponse:
    """List movies."""
    try:
        if genre_id:
            movies, total = await service.list_movies_by_genre(genre_id, limit, offset)
        elif trending:
            movies, total = await service.list_trending_movies(limit, offset)
        else:
            movies, total = await service.list_recent_movies(limit, offset)

        return ListMoviesResponse(
            movies=movies, total=total, page=offset // limit + 1, page_size=limit
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error listing movies: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list movies"
        )


@router.get("/movies/search", response_model=ListMoviesResponse)
async def search_movies(
    service: Annotated[ContentService, Depends(get_content_service)],
    q: Annotated[str, Query(min_length=2)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ListMoviesResponse:
    """Search movies."""
    try:
        movies, total = await service.search_movies(q, limit, offset)
        return ListMoviesResponse(
            movies=movies, total=total, page=offset // limit + 1, page_size=limit
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error searching movies: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to search movies"
        )


@router.put("/movies/{movie_id}", response_model=MovieResponse)
async def update_movie(
    movie_id: UUID,
    data: UpdateMovieRequest,
    service: Annotated[ContentService, Depends(get_content_service)],
) -> MovieResponse:
    """Update movie."""
    try:
        movie = await service.update_movie(movie_id, data.model_dump(exclude_none=True))
        return movie
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error updating movie: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update movie"
        )


@router.post("/movies/{movie_id}/view")
async def record_movie_view(
    movie_id: UUID, service: Annotated[ContentService, Depends(get_content_service)]
) -> dict:
    """Record movie view."""
    try:
        await service.increment_movie_views(movie_id)
        return {"status": "success"}
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error recording view: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to record view"
        )


# Show Endpoints


@router.post("/shows", response_model=ShowResponse, status_code=status.HTTP_201_CREATED)
async def create_show(
    data: CreateShowRequest, service: Annotated[ContentService, Depends(get_content_service)]
) -> ShowResponse:
    """Create new show."""
    try:
        show = await service.create_show(data.model_dump())
        return show
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error creating show: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create show"
        )


@router.get("/shows/{show_id}", response_model=ShowResponse)
async def get_show(
    show_id: UUID, service: Annotated[ContentService, Depends(get_content_service)]
) -> ShowResponse:
    """Get show by ID."""
    try:
        show = await service.get_show(show_id)
        if not show:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Show not found")
        return show
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error getting show: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get show"
        )


@router.get("/shows", response_model=ListShowsResponse)
async def list_shows(
    service: Annotated[ContentService, Depends(get_content_service)],
    genre_id: Annotated[UUID | None, Query()] = None,
    ongoing_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ListShowsResponse:
    """List shows."""
    try:
        if genre_id:
            shows, total = await service.list_shows_by_genre(genre_id, limit, offset)
        elif ongoing_only:
            shows, total = await service.list_ongoing_shows(limit, offset)
        else:
            shows, total = await service.list_recent_shows(limit, offset)

        return ListShowsResponse(
            shows=shows, total=total, page=offset // limit + 1, page_size=limit
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error listing shows: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list shows"
        )


@router.get("/shows/search", response_model=ListShowsResponse)
async def search_shows(
    service: Annotated[ContentService, Depends(get_content_service)],
    q: Annotated[str, Query(min_length=2)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ListShowsResponse:
    """Search shows."""
    try:
        shows, total = await service.search_shows(q, limit, offset)
        return ListShowsResponse(
            shows=shows, total=total, page=offset // limit + 1, page_size=limit
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error searching shows: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to search shows"
        )


@router.post("/shows/{show_id}/view")
async def record_show_view(
    show_id: UUID, service: Annotated[ContentService, Depends(get_content_service)]
) -> dict:
    """Record show view."""
    try:
        await service.increment_show_views(show_id)
        return {"status": "success"}
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error recording view: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to record view"
        )


# Season Endpoints


@router.post("/seasons", response_model=SeasonResponse, status_code=status.HTTP_201_CREATED)
async def create_season(
    data: CreateSeasonRequest, service: Annotated[ContentService, Depends(get_content_service)]
) -> SeasonResponse:
    """Create new season."""
    try:
        season = await service.create_season(
            data.show_id, data.season_number, data.model_dump(exclude=["show_id", "season_number"])
        )
        return season
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error creating season: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create season"
        )


@router.get("/seasons/{season_id}", response_model=SeasonResponse)
async def get_season(
    season_id: UUID, service: Annotated[ContentService, Depends(get_content_service)]
) -> SeasonResponse:
    """Get season by ID."""
    try:
        season = await service.get_season(season_id)
        if not season:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Season not found")
        return season
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error getting season: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get season"
        )


@router.get("/shows/{show_id}/seasons", response_model=ListSeasonsResponse)
async def list_seasons(
    show_id: UUID, service: Annotated[ContentService, Depends(get_content_service)]
) -> ListSeasonsResponse:
    """List seasons for show."""
    try:
        seasons = await service.list_seasons(show_id)
        return ListSeasonsResponse(seasons=seasons, total=len(seasons))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error listing seasons: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list seasons"
        )


# Episode Endpoints


@router.post("/episodes", response_model=EpisodeResponse, status_code=status.HTTP_201_CREATED)
async def create_episode(
    data: CreateEpisodeRequest, service: Annotated[ContentService, Depends(get_content_service)]
) -> EpisodeResponse:
    """Create new episode."""
    try:
        episode = await service.create_episode(
            data.season_id, data.show_id, data.model_dump(exclude=["season_id", "show_id"])
        )
        return episode
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error creating episode: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create episode"
        )


@router.get("/episodes/{episode_id}", response_model=EpisodeResponse)
async def get_episode(
    episode_id: UUID, service: Annotated[ContentService, Depends(get_content_service)]
) -> EpisodeResponse:
    """Get episode by ID."""
    try:
        episode = await service.get_episode(episode_id)
        if not episode:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")
        return episode
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error getting episode: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get episode"
        )


@router.get("/seasons/{season_id}/episodes", response_model=ListEpisodesResponse)
async def list_season_episodes(
    season_id: UUID, service: Annotated[ContentService, Depends(get_content_service)]
) -> ListEpisodesResponse:
    """List episodes for season."""
    try:
        episodes = await service.list_episodes_by_season(season_id)
        return ListEpisodesResponse(episodes=episodes, total=len(episodes))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error listing episodes: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list episodes"
        )


@router.get("/shows/{show_id}/episodes", response_model=ListEpisodesResponse)
async def list_show_episodes(
    show_id: UUID,
    limit: Annotated[int, Query(50, ge=1, le=500)],
    offset: Annotated[int, Query(0, ge=0)],
    service: Annotated[ContentService, Depends(get_content_service)],
) -> ListEpisodesResponse:
    """List episodes for show."""
    try:
        episodes, total = await service.list_episodes_by_show(show_id, limit, offset)
        return ListEpisodesResponse(episodes=episodes, total=total)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error listing episodes: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list episodes"
        )


@router.post("/episodes/{episode_id}/view")
async def record_episode_view(
    episode_id: UUID, service: Annotated[ContentService, Depends(get_content_service)]
) -> dict:
    """Record episode view."""
    try:
        await service.increment_episode_views(episode_id)
        return {"status": "success"}
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error recording view: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to record view"
        )
