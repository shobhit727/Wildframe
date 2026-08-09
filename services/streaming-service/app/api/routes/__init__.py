"""API routes for Streaming Service."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Header

from app.core.database import db_manager
from app.core.settings import settings
from app.schemas import (
    CDNRegionCreateRequest,
    CDNRegionResponse,
    DownloadSessionCreateRequest,
    DownloadSessionResponse,
    ManifestGenerationRequest,
    PlaybackSessionCreateRequest,
    PlaybackSessionResponse,
    PlaybackSessionUpdateRequest,
    QualityProfileCreateRequest,
    QualityProfileResponse,
    TranscodingJobCreateRequest,
    TranscodingJobResponse,
    VideoManifestResponse,
)
from app.services import StreamingService

router = APIRouter(prefix="/api/v1", tags=["streaming"])


async def get_current_user_id(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UUID:
    """Resolve the authenticated user id from the JWT sub claim."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
        )
    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    sub = payload.get("sub") or payload.get("user_id")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        )
    try:
        return UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        )


async def get_streaming_service(
    session: Annotated[AsyncSession, Depends(db_manager.get_session)],
) -> StreamingService:
    """Dependency injection for StreamingService."""
    return StreamingService(session)


# Playback session endpoints


@router.post(
    "/playback-sessions",
    response_model=PlaybackSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_playback(
    request: PlaybackSessionCreateRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
):
    """Start a new playback session (bound to the authenticated user)."""
    data = request.model_dump()
    data["user_id"] = current_user
    return await service.start_playback_session(PlaybackSessionCreateRequest(**data))


@router.get("/playback-sessions/{session_id}", response_model=PlaybackSessionResponse)
async def get_playback_session(
    session_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
):
    """Get playback session (owner only)."""
    session = await service.get_playback_session(session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this session",
        )
    return session


@router.get("/users/{user_id}/playback-sessions", response_model=list[PlaybackSessionResponse])
async def get_user_playback_sessions(
    user_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
):
    """Get active playback sessions for the authenticated user (owner only)."""
    if user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this user",
        )
    return await service.get_active_sessions(user_id)


@router.patch("/playback-sessions/{session_id}", response_model=PlaybackSessionResponse)
async def update_playback_session(
    session_id: UUID,
    request: PlaybackSessionUpdateRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
):
    """Update playback session (owner only)."""
    session = await service.update_playback_session(session_id, request)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this session",
        )
    return session


@router.post("/playback-sessions/{session_id}/end", status_code=status.HTTP_204_NO_CONTENT)
async def end_playback_session(
    session_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
):
    """End playback session (owner only)."""
    session = await service.get_playback_session(session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this session"
        )
    await service.end_playback_session(session_id)


# Video manifest endpoints


@router.post(
    "/manifests",
    response_model=VideoManifestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_manifest(
    request: ManifestGenerationRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
):
    """Generate video manifest for streaming."""
    return await service.generate_manifest(request)


@router.get("/manifests/{manifest_id}", response_model=VideoManifestResponse)
async def get_manifest(
    manifest_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
):
    """Get manifest by ID."""
    manifest = await service.get_manifest(manifest_id)
    if not manifest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manifest not found")
    return manifest


@router.get("/episodes/{episode_id}/manifest", response_model=VideoManifestResponse)
async def get_episode_manifest(
    episode_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    protocol: str = Query(default="hls", pattern="^(hls|dash|smooth_streaming)$"),
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
):
    """Get manifest for episode and protocol."""
    manifest = await service.get_manifest_for_episode(episode_id, protocol)
    if not manifest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manifest not found")
    return manifest


# Transcoding job endpoints


@router.post(
    "/transcoding-jobs",
    response_model=TranscodingJobResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_transcoding_job(
    request: TranscodingJobCreateRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
):
    """Create a transcoding job."""
    return await service.create_transcoding_job(request)


@router.get("/transcoding-jobs/pending", response_model=list[TranscodingJobResponse])
async def get_pending_jobs(
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
):
    """Get pending transcoding jobs."""
    return await service.get_pending_jobs(limit)


@router.get("/transcoding-jobs/{job_id}", response_model=TranscodingJobResponse)
async def get_transcoding_job(
    job_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
):
    """Get transcoding job."""
    job = await service.get_transcoding_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.patch("/transcoding-jobs/{job_id}/progress")
async def update_transcoding_progress(
    job_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
    progress_percent: Annotated[int, Query(ge=0, le=100)] = ...,
    error_message: Annotated[str | None, Query()] = None,
):
    """Update transcoding progress."""
    job = await service.update_transcoding_progress(job_id, progress_percent, error_message)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


# Quality profile endpoints


@router.post(
    "/quality-profiles",
    response_model=QualityProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_quality_profile(
    request: QualityProfileCreateRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
):
    """Create quality profile."""
    return await service.create_quality_profile(request)


@router.get("/quality-profiles/{profile_id}", response_model=QualityProfileResponse)
async def get_quality_profile(
    profile_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
):
    """Get quality profile."""
    profile = await service.get_quality_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return profile


@router.get("/quality-profiles", response_model=list[QualityProfileResponse])
async def list_quality_profiles_for_bandwidth(
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    bandwidth_kbps: Annotated[int, Query(ge=100)],
):
    """Get quality profiles for bandwidth."""
    return await service.get_quality_profiles_for_bandwidth(bandwidth_kbps)


# CDN region endpoints


@router.post(
    "/cdn-regions",
    response_model=CDNRegionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_cdn_region(
    request: CDNRegionCreateRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
):
    """Create CDN region."""
    return await service.create_cdn_region(request)


@router.get("/cdn-regions/{region_id}", response_model=CDNRegionResponse)
async def get_cdn_region(
    region_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
):
    """Get CDN region."""
    region = await service.get_cdn_region(region_id)
    if not region:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Region not found")
    return region


@router.get("/cdn-regions", response_model=list[CDNRegionResponse])
async def list_cdn_regions(
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
):
    """List all CDN regions."""
    return await service.get_all_cdn_regions()


# Download session endpoints


@router.post(
    "/download-sessions",
    response_model=DownloadSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_download(
    request: DownloadSessionCreateRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
):
    """Create download session (bound to the authenticated user)."""
    data = request.model_dump()
    if data["user_id"] not in (None, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this user",
        )
    data["user_id"] = current_user
    return await service.create_download_session(DownloadSessionCreateRequest(**data))


@router.get("/download-sessions/{download_id}", response_model=DownloadSessionResponse)
async def get_download(
    download_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
):
    """Get download session (owner only)."""
    download = await service.get_download_session(download_id)
    if not download:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Download not found")
    if download.user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this download",
        )
    return download


@router.get("/users/{user_id}/downloads", response_model=list[DownloadSessionResponse])
async def get_user_downloads(
    user_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
):
    """Get downloads for the authenticated user (owner only)."""
    if user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this user",
        )
    return await service.get_user_downloads(user_id)


@router.patch("/download-sessions/{download_id}/progress")
async def update_download_progress(
    download_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
    bytes_downloaded: Annotated[int, Query(ge=0)] = ...,
):
    """Update download progress."""
    download = await service.update_download_progress(download_id, bytes_downloaded)
    if not download:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Download not found")
    return download
