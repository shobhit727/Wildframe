"""
API routes for Content Service.
Provides REST endpoints for content management operations.
"""

from typing import Annotated
from uuid import UUID

from jose import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from jose.exceptions import JWTError
from sqlalchemy.exc import IntegrityError
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


async def get_content_service(
    session: Annotated[AsyncSession, Depends(db_manager.get_session)],
) -> ContentService:
    """Dependency injection for ContentService."""
    return ContentService(session)


async def get_current_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UUID:
    """Extract and verify current user from JWT token.

    Identity is read from the verified JWT ``sub`` claim, never from a
    caller-supplied query parameter.
    """
    user_id, role, _arv = await _require_identity(authorization)
    return user_id


async def get_admin_identity(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> str:
    """Admin-only dependency for privileged catalog mutations (#51).

    Catalog writes (genres, content, seasons, episodes, recommendations,
    cast, publish) must never be reachable by unauthenticated or non-admin
    callers, regardless of how the network boundary is configured.
    """
    user_id, role, arv = await _require_identity(authorization, with_role=True)
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )
    if arv != settings.ADMIN_ROLE_VERSION:
        # #81/#101: role revocation is immediate — a token minted before
        # ADMIN_ROLE_VERSION was bumped must not retain admin access.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )
    return str(user_id)


async def _require_identity(
    authorization: str | None, *, with_role: bool = False
) -> tuple[UUID, str | None, int]:
    """Shared JWT verification returning the verified subject (role, arv)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
        # Token-type separation (#221): refresh tokens share the audience but
        # must never be accepted as access tokens.
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
            )
    except JWTError:
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
    user_id = UUID(sub)
    arv = int(payload.get("arv") or 0)
    if with_role:
        return user_id, str(payload.get("role") or "user"), arv
    return user_id, None, arv


# Genre endpoints


@router.post(
    "/genres",
    response_model=GenreResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_admin_identity)],
)
async def create_genre(
    request: GenreCreateRequest,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Create a new genre."""
    try:
        return await service.create_genre(request)
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Genre with this name or slug already exists",
        )


@router.get("/genres", response_model=list[GenreResponse])
async def list_genres(
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """List all genres."""
    return await service.list_genres()


@router.get("/genres/{genre_id}", response_model=GenreResponse)
async def get_genre(
    genre_id: UUID,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Get genre by ID."""
    genre = await service.get_genre(genre_id)
    if not genre:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Genre not found")
    return genre


@router.put(
    "/genres/{genre_id}",
    response_model=GenreResponse,
    dependencies=[Depends(get_admin_identity)],
)
async def update_genre(
    genre_id: UUID,
    request: GenreCreateRequest,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Update genre."""
    genre = await service.update_genre(genre_id, request)
    if not genre:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Genre not found")
    return genre


@router.delete(
    "/genres/{genre_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_admin_identity)],
)
async def delete_genre(
    genre_id: UUID,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Delete genre."""
    success = await service.delete_genre(genre_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Genre not found")


# Content endpoints


@router.post(
    "/content",
    response_model=ContentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_admin_identity)],
)
async def create_content(
    request: ContentCreateRequest,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Create new content."""
    return await service.create_content(request)


@router.get("/content", response_model=list[ContentListResponse])
async def list_content(
    service: Annotated[ContentService, Depends(get_content_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    content_type: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    genre_id: Annotated[UUID | None, Query()] = None,
):
    """List content with pagination and filters."""
    return await service.list_content(page, page_size, content_type, status, genre_id)


@router.get("/content/trending", response_model=list[ContentListResponse])
async def list_trending(
    service: Annotated[ContentService, Depends(get_content_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List the most popular published content, ranked by audience score and votes.

    Declared before /content/{content_id} so the popularity ranking is
    available to downstream services (recommendations fallback) instead of
    treating "first page of the catalog" as the global popularity order.
    """
    return await service.get_trending_content(limit)


@router.get("/content/{content_id}", response_model=ContentResponse)
async def get_content(
    content_id: UUID,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Get content by ID."""
    content = await service.get_content(content_id)
    if not content:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")
    return content


@router.put(
    "/content/{content_id}",
    response_model=ContentResponse,
    dependencies=[Depends(get_admin_identity)],
)
async def update_content(
    content_id: UUID,
    request: ContentUpdateRequest,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Update content."""
    content = await service.update_content(content_id, request)
    if not content:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")
    return content


@router.delete(
    "/content/{content_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_admin_identity)],
)
async def delete_content(
    content_id: UUID,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Delete content."""
    success = await service.delete_content(content_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")


@router.post(
    "/content/{content_id}/publish",
    response_model=ContentResponse,
    dependencies=[Depends(get_admin_identity)],
)
async def publish_content(
    content_id: UUID,
    request: ContentPublishRequest,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Publish content."""
    content = await service.publish_content(content_id, request)
    if not content:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")
    return content


# Season endpoints


@router.post(
    "/content/{content_id}/seasons",
    response_model=SeasonResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_admin_identity)],
)
async def create_season(
    content_id: UUID,
    request: SeasonCreateRequest,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Create a new season."""
    return await service.create_season(content_id, request)


@router.get("/content/{content_id}/seasons", response_model=list[SeasonResponse])
async def list_seasons(
    content_id: UUID,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """List seasons for content."""
    return await service.list_seasons(content_id)


@router.get("/content/{content_id}/seasons/{season_id}", response_model=SeasonResponse)
async def get_season(
    content_id: UUID,
    season_id: UUID,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Get season by ID."""
    season = await service.get_season(content_id, season_id)
    if not season:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Season not found")
    return season


@router.put(
    "/content/{content_id}/seasons/{season_id}",
    response_model=SeasonResponse,
    dependencies=[Depends(get_admin_identity)],
)
async def update_season(
    content_id: UUID,
    season_id: UUID,
    request: SeasonUpdateRequest,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Update season."""
    season = await service.update_season(content_id, season_id, request)
    if not season:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Season not found")
    return season


@router.delete(
    "/content/{content_id}/seasons/{season_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_admin_identity)],
)
async def delete_season(
    content_id: UUID,
    season_id: UUID,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Delete season."""
    success = await service.delete_season(content_id, season_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Season not found")


# Episode endpoints


@router.post(
    "/content/{content_id}/seasons/{season_id}/episodes",
    response_model=EpisodeResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_admin_identity)],
)
async def create_episode(
    content_id: UUID,
    season_id: UUID,
    request: EpisodeCreateRequest,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Create a new episode."""
    return await service.create_episode(content_id, season_id, request)


@router.get(
    "/content/{content_id}/seasons/{season_id}/episodes", response_model=list[EpisodeResponse]
)
async def list_episodes(
    content_id: UUID,
    season_id: UUID,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """List episodes for season."""
    return await service.list_episodes(content_id, season_id)


@router.get(
    "/content/{content_id}/seasons/{season_id}/episodes/{episode_id}",
    response_model=EpisodeResponse,
)
async def get_episode(
    content_id: UUID,
    season_id: UUID,
    episode_id: UUID,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Get episode by ID."""
    episode = await service.get_episode(content_id, season_id, episode_id)
    if not episode:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")
    return episode


@router.put(
    "/content/{content_id}/seasons/{season_id}/episodes/{episode_id}",
    response_model=EpisodeResponse,
    dependencies=[Depends(get_admin_identity)],
)
async def update_episode(
    content_id: UUID,
    season_id: UUID,
    episode_id: UUID,
    request: EpisodeUpdateRequest,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Update episode."""
    episode = await service.update_episode(content_id, season_id, episode_id, request)
    if not episode:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")
    return episode


@router.delete(
    "/content/{content_id}/seasons/{season_id}/episodes/{episode_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_admin_identity)],
)
async def delete_episode(
    content_id: UUID,
    season_id: UUID,
    episode_id: UUID,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Delete episode."""
    success = await service.delete_episode(content_id, season_id, episode_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")


# Rating endpoints


@router.post(
    "/content/{content_id}/ratings",
    response_model=ContentRatingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def rate_content(
    content_id: UUID,
    request: ContentRatingCreateRequest,
    user_id: Annotated[UUID, Depends(get_current_user)],
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Rate content."""
    return await service.rate_content(content_id, user_id, request)


@router.get("/content/{content_id}/ratings", response_model=list[ContentRatingResponse])
async def list_ratings(
    content_id: UUID,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """List ratings for content."""
    return await service.list_ratings(content_id)


# Recommendation endpoints


@router.post(
    "/content/{content_id}/recommendations",
    response_model=ContentRecommendationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_admin_identity)],
)
async def add_recommendation(
    content_id: UUID,
    request: ContentRecommendationCreateRequest,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Add recommendation for content."""
    return await service.add_recommendation(content_id, request)


@router.get(
    "/content/{content_id}/recommendations", response_model=list[ContentRecommendationResponse]
)
async def list_recommendations(
    content_id: UUID,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """List recommendations for content."""
    return await service.list_recommendations(content_id)


# Cast endpoints


@router.post(
    "/content/{content_id}/cast",
    response_model=CastMemberResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_admin_identity)],
)
async def add_cast_member(
    content_id: UUID,
    request: CastMemberCreateRequest,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """Add cast member to content."""
    return await service.add_cast_member(content_id, request)


@router.get("/content/{content_id}/cast", response_model=list[CastMemberResponse])
async def list_cast(
    content_id: UUID,
    service: Annotated[ContentService, Depends(get_content_service)],
):
    """List cast for content."""
    return await service.list_cast(content_id)


# Reindex endpoints (#116)
# In-memory job registry for async reindex jobs
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import uuid as uuid_lib


class JobStatus(str, Enum):
    """Job status enumeration."""

    ACCEPTED = "accepted"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class ReindexJob:
    job_id: UUID
    status: JobStatus
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    progress: int = 0  # 0-100


_reindex_jobs: dict[UUID, ReindexJob] = {}


@router.post("/reindex", response_model=dict[str, str])
async def start_reindex() -> dict[str, str]:
    """Start async reindex job, returns job_id."""
    job_id = uuid_lib.uuid4()
    job = ReindexJob(job_id=job_id, status=JobStatus.ACCEPTED)
    _reindex_jobs[job_id] = job
    # TODO: integrate with actual reindex background task
    return {"job_id": str(job_id)}


@router.get("/reindex/{job_id}", response_model=dict)
async def get_reindex_status(job_id: UUID) -> dict:
    """Get reindex job status."""
    job = _reindex_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return {
        "job_id": str(job.job_id),
        "status": job.status.value,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "error": job.error,
        "progress": job.progress,
    }
