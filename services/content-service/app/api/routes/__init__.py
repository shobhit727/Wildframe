"""
API routes for Content Service.
Provides REST endpoints for content management operations.
"""


from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from jwt.exceptions import PyJWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import db_manager
from app.core.settings import settings
from app.schemas import (
    CastMemberCreateRequest,
    CastMemberResponse,
    ContentCreateRequest,
    ContentListResponse,
    ContentPublishRequest,
    ContentRatingCreateRequest,
    ContentRatingResponse,
    ContentRecommendationCreateRequest,
    ContentRecommendationResponse,
    ContentResponse,
    ContentUpdateRequest,
    EpisodeCreateRequest,
    EpisodeResponse,
    EpisodeUpdateRequest,
    GenreCreateRequest,
    GenreResponse,
    SeasonCreateRequest,
    SeasonResponse,
    SeasonUpdateRequest,
)
from app.services import ContentService

router = APIRouter(prefix="/api/v1", tags=["content"])


async def get_content_service(session: AsyncSession = Depends(db_manager.get_session)) -> ContentService:
    """Dependency injection for ContentService."""
    return ContentService(session)


async def get_current_user(authorization: str | None = Header(None, alias="Authorization")) -> UUID:
    """Extract and verify current user from JWT token.

    Identity is read from the verified JWT ``sub`` claim, never from a
    caller-supplied query parameter.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
        )
    return UUID(sub)


# Genre endpoints

@router.post("/genres", response_model=GenreResponse, status_code=status.HTTP_201_CREATED)
async def create_genre(
    request: GenreCreateRequest,
    service: ContentService = Depends(get_content_service)
):
    """Create a new genre."""
    return await service.create_genre(request)


@router.get("/genres", response_model=list[GenreResponse])
async def list_genres(
    service: ContentService = Depends(get_content_service)
):
    """List all genres."""
    return await service.list_genres()


@router.get("/genres/{genre_id}", response_model=GenreResponse)
async def get_genre(
    genre_id: UUID,
    service: ContentService = Depends(get_content_service)
):
    """Get genre by ID."""
    genre = await service.get_genre(genre_id)
    if not genre:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Genre not found")
    return genre


@router.delete("/genres/{genre_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_genre(
    genre_id: UUID,
    service: ContentService = Depends(get_content_service)
):
    """Delete a genre."""
    success = await service.delete_genre(genre_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Genre not found")


# Cast member endpoints

@router.post("/cast-members", response_model=CastMemberResponse, status_code=status.HTTP_201_CREATED)
async def create_cast_member(
    request: CastMemberCreateRequest,
    service: ContentService = Depends(get_content_service)
):
    """Create a new cast member."""
    return await service.create_cast_member(request)


@router.get("/cast-members/{member_id}", response_model=CastMemberResponse)
async def get_cast_member(
    member_id: UUID,
    service: ContentService = Depends(get_content_service)
):
    """Get cast member by ID."""
    member = await service.get_cast_member(member_id)
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cast member not found")
    return member


@router.get("/cast-members/search", response_model=list[CastMemberResponse])
async def search_cast_members(
    q: str = Query(..., min_length=1),
    service: ContentService = Depends(get_content_service)
):
    """Search cast members by name."""
    return await service.search_cast_members(q)


# Content endpoints

@router.post("/content", response_model=ContentResponse, status_code=status.HTTP_201_CREATED)
async def create_content(
    request: ContentCreateRequest,
    service: ContentService = Depends(get_content_service)
):
    """Create new content."""
    return await service.create_content(request)


@router.get("/content", response_model=list[ContentListResponse])
async def list_content(
    service: ContentService = Depends(get_content_service)
):
    """List published content."""
    return await service.content_repo.get_published()


@router.get("/content/trending", response_model=list[ContentListResponse])
async def get_trending_content(
    limit: int = Query(10, ge=1, le=50),
    service: ContentService = Depends(get_content_service)
):
    """Get trending content."""
    return await service.get_trending_content(limit)


@router.get("/content/premium", response_model=list[ContentListResponse])
async def get_premium_content(
    service: ContentService = Depends(get_content_service)
):
    """Get premium content."""
    return await service.get_premium_content()


@router.get("/content/by-type/{content_type}", response_model=list[ContentListResponse])
async def list_content_by_type(
    content_type: str,
    service: ContentService = Depends(get_content_service)
):
    """List content by type."""
    return await service.list_content_by_type(content_type)


@router.get("/content/by-genre/{genre_id}", response_model=list[ContentListResponse])
async def list_content_by_genre(
    genre_id: UUID,
    service: ContentService = Depends(get_content_service)
):
    """List content by genre."""
    return await service.list_content_by_genre(genre_id)


@router.get("/content/search", response_model=list[ContentListResponse])
async def search_content(
    q: str = Query(..., min_length=1),
    service: ContentService = Depends(get_content_service)
):
    """Search content by title and description."""
    return await service.search_content(q)


@router.get("/content/{content_id}", response_model=ContentResponse)
async def get_content(
    content_id: UUID,
    service: ContentService = Depends(get_content_service)
):
    """Get content by ID."""
    content = await service.get_content(content_id)
    if not content:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")
    return content


@router.patch("/content/{content_id}", response_model=ContentResponse)
async def update_content(
    content_id: UUID,
    request: ContentUpdateRequest,
    service: ContentService = Depends(get_content_service)
):
    """Update content."""
    content = await service.update_content(content_id, request)
    if not content:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")
    return content


@router.post("/content/{content_id}/publish", response_model=ContentResponse)
async def publish_content(
    content_id: UUID,
    request: ContentPublishRequest,
    service: ContentService = Depends(get_content_service)
):
    """Publish or archive content."""
    content = await service.publish_content(content_id, request)
    if not content:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")
    return content


@router.delete("/content/{content_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_content(
    content_id: UUID,
    service: ContentService = Depends(get_content_service)
):
    """Delete content."""
    success = await service.delete_content(content_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")


# Season endpoints

@router.post("/content/{content_id}/seasons", response_model=SeasonResponse, status_code=status.HTTP_201_CREATED)
async def create_season(
    content_id: UUID,
    request: SeasonCreateRequest,
    service: ContentService = Depends(get_content_service)
):
    """Create a new season."""
    return await service.create_season(content_id, request)


@router.get("/content/{content_id}/seasons", response_model=list[SeasonResponse])
async def list_seasons(
    content_id: UUID,
    service: ContentService = Depends(get_content_service)
):
    """List all seasons for content."""
    return await service.list_content_seasons(content_id)


@router.get("/seasons/{season_id}", response_model=SeasonResponse)
async def get_season(
    season_id: UUID,
    service: ContentService = Depends(get_content_service)
):
    """Get season by ID."""
    season = await service.get_season(season_id)
    if not season:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Season not found")
    return season


@router.patch("/seasons/{season_id}", response_model=SeasonResponse)
async def update_season(
    season_id: UUID,
    request: SeasonUpdateRequest,
    service: ContentService = Depends(get_content_service)
):
    """Update season."""
    season = await service.update_season(season_id, request)
    if not season:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Season not found")
    return season


# Episode endpoints

@router.post("/seasons/{season_id}/episodes", response_model=EpisodeResponse, status_code=status.HTTP_201_CREATED)
async def create_episode(
    season_id: UUID,
    request: EpisodeCreateRequest,
    service: ContentService = Depends(get_content_service)
):
    """Create a new episode."""
    season = await service.get_season(season_id)
    if not season:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Season not found")
    return await service.create_episode(season.content_id, season_id, request)


@router.get("/seasons/{season_id}/episodes", response_model=list[EpisodeResponse])
async def list_episodes(
    season_id: UUID,
    service: ContentService = Depends(get_content_service)
):
    """List all episodes in a season."""
    return await service.list_season_episodes(season_id)


@router.get("/episodes/{episode_id}", response_model=EpisodeResponse)
async def get_episode(
    episode_id: UUID,
    service: ContentService = Depends(get_content_service)
):
    """Get episode by ID."""
    episode = await service.get_episode(episode_id)
    if not episode:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")
    return episode


@router.patch("/episodes/{episode_id}", response_model=EpisodeResponse)
async def update_episode(
    episode_id: UUID,
    request: EpisodeUpdateRequest,
    service: ContentService = Depends(get_content_service)
):
    """Update episode."""
    episode = await service.update_episode(episode_id, request)
    if not episode:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")
    return episode


# Rating endpoints

@router.post("/content/{content_id}/ratings", response_model=ContentRatingResponse, status_code=status.HTTP_201_CREATED)
async def rate_content(
    content_id: UUID,
    request: ContentRatingCreateRequest,
    current_user: UUID = Depends(get_current_user),
    service: ContentService = Depends(get_content_service)
):
    """Rate content."""
    return await service.rate_content(content_id, current_user, request)


@router.get("/content/{content_id}/ratings", response_model=list[ContentRatingResponse])
async def get_ratings(
    content_id: UUID,
    service: ContentService = Depends(get_content_service)
):
    """Get all ratings for content."""
    return await service.get_content_ratings(content_id)


# Recommendation endpoints

@router.post("/content/{content_id}/recommendations", response_model=ContentRecommendationResponse, 
            status_code=status.HTTP_201_CREATED)
async def add_recommendation(
    content_id: UUID,
    request: ContentRecommendationCreateRequest,
    service: ContentService = Depends(get_content_service)
):
    """Add content recommendation."""
    return await service.add_recommendation(content_id, request)


@router.get("/content/{content_id}/recommendations", response_model=list[ContentRecommendationResponse])
async def get_recommendations(
    content_id: UUID,
    limit: int = Query(10, ge=1, le=50),
    service: ContentService = Depends(get_content_service)
):
    """Get content recommendations."""
    return await service.get_recommendations(content_id, limit)


@router.delete("/recommendations/{recommendation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recommendation(
    recommendation_id: UUID,
    service: ContentService = Depends(get_content_service)
):
    """Delete content recommendation."""
    success = await service.remove_recommendation(recommendation_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found")
