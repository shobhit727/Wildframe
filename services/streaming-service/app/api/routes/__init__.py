"""API routes for Streaming Service."""

import inspect
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

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
    SignedPlaybackUrlRequest,
    SignedPlaybackUrlResponse,
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
    except jwt.InvalidTokenError:
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


async def get_current_user_id_or_none(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UUID | None:
    """Resolve the user id when a bearer token is present; otherwise None.

    Used by signed-URL endpoints (#489, #491) where possession of a valid
    signature + session id is the credential, so a missing/invalid bearer
    token must not block the signed path. Honors dependency overrides so
    route tests can inject a user without minting a token.
    """
    override = request.app.dependency_overrides.get(get_current_user_id)
    if override is not None:
        try:
            result = override()
            value = await result if inspect.isawaitable(result) else result
            return value if value is None or isinstance(value, UUID) else None
        except HTTPException:
            return None
    try:
        return await get_current_user_id(authorization)
    except HTTPException:
        return None


async def require_self(
    jwt_user_id: Annotated[UUID, Depends(get_current_user_id)],
    request: Request,
) -> UUID:
    """Ensure the path user_id matches the authenticated user."""
    path_user_id = request.path_params.get("user_id")
    if path_user_id is None or str(path_user_id) == str(jwt_user_id):
        return jwt_user_id
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You can only access your own data",
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
    current_user: Annotated[UUID, Depends(get_current_user_id)],
):
    """Start a new playback session."""
    if request.user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only start sessions for your own account",
        )
    return await service.start_playback_session(request)


@router.get("/playback-sessions/{session_id}", response_model=PlaybackSessionResponse)
async def get_playback_session(
    session_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)],
):
    """Get playback session (owner only)."""
    session = await service.get_playback_session(session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access your own sessions",
        )
    return session


@router.get("/users/{user_id}/playback-sessions", response_model=list[PlaybackSessionResponse])
async def get_user_playback_sessions(
    user_id: Annotated[UUID, Depends(require_self)],
    service: Annotated[StreamingService, Depends(get_streaming_service)],
):
    """Get active playback sessions for user."""
    return await service.get_active_sessions(user_id)


@router.patch("/playback-sessions/{session_id}", response_model=PlaybackSessionResponse)
async def update_playback_session(
    session_id: UUID,
    request: PlaybackSessionUpdateRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)],
):
    """Update playback session (owner only)."""
    session = await service.get_playback_session(session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access your own sessions",
        )
    return await service.update_playback_session(session_id, request)


@router.post("/playback-sessions/{session_id}/end", status_code=status.HTTP_204_NO_CONTENT)
async def end_playback_session(
    session_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)],
):
    """End playback session (owner only)."""
    session = await service.get_playback_session(session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access your own sessions",
        )
    await service.end_playback_session(session_id)


# Video manifest endpoints


@router.post(
    "/manifests", response_model=VideoManifestResponse, status_code=status.HTTP_201_CREATED
)
async def generate_manifest(
    request: ManifestGenerationRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)],
):
    """Generate video manifest for streaming (requires authentication)."""
    return await service.generate_manifest(request)


@router.get("/manifests/{manifest_id}", response_model=VideoManifestResponse)
async def get_manifest(
    manifest_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)],
):
    """Get manifest by ID (requires authentication)."""
    manifest = await service.get_manifest(manifest_id)
    if not manifest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manifest not found")
    return manifest


@router.get("/episodes/{episode_id}/manifest", response_model=VideoManifestResponse)
async def get_episode_manifest(
    episode_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID | None, Depends(get_current_user_id_or_none)] = None,
    protocol: str = Query(default="hls", pattern="^(hls|dash|smooth_streaming)$"),
    # Signed URL params for token-scoped access (#489, #491)
    session_id: UUID | None = Query(default=None),
    signature: str | None = Query(default=None),
    expires: int | None = Query(default=None),
):
    """Get manifest for episode and protocol (requires authentication or signed URL)."""
    # If signed URL params provided, verify them
    if session_id and signature and expires:
        if not service.verify_signed_url(session_id, episode_id, signature, expires):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or expired signed URL",
            )
        # Verify session is valid for playback (user binding via session)
        if not await service.check_session_valid_for_playback(session_id, current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Session expired or revoked",
            )
    else:
        # Regular auth path - could add entitlement check here (#587)
        if current_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid authorization header",
            )
        if settings.ENTITLEMENT_CHECK_ENABLED:
            # Light entitlement check - in real impl, check user subscription tier
            # For now, we just ensure user is authenticated (done by get_current_user_id)
            pass

    manifest = await service.get_manifest_for_episode(episode_id, protocol)
    if not manifest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manifest not found")
    # Authorized media must not be cached by shared intermediaries: a cached
    # copy would keep serving after session revocation until CDN TTL expires
    # (#528/#526). Private + no-store keeps CDNs and browsers honest.
    return JSONResponse(
        content=VideoManifestResponse.model_validate(manifest).model_dump(mode="json"),
        headers={"Cache-Control": "private, no-store"},
    )


# Transcoding job endpoints


@router.post(
    "/transcoding-jobs", response_model=TranscodingJobResponse, status_code=status.HTTP_201_CREATED
)
async def create_transcoding_job(
    request: TranscodingJobCreateRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
):
    """Create a transcoding job."""
    return await service.create_transcoding_job(request)


@router.get("/transcoding-jobs/pending", response_model=list[TranscodingJobResponse])
async def get_pending_jobs(
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
):
    """Get pending transcoding jobs."""
    return await service.get_pending_jobs(limit)


@router.get("/transcoding-jobs/{job_id}", response_model=TranscodingJobResponse)
async def get_transcoding_job(
    job_id: UUID, service: Annotated[StreamingService, Depends(get_streaming_service)]
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
    progress_percent: Annotated[int, Query(ge=0, le=100)],
    error_message: Annotated[str | None, Query()] = None,
):
    """Update transcoding progress."""
    job = await service.update_transcoding_progress(job_id, progress_percent, error_message)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


# Quality profile endpoints


@router.post(
    "/quality-profiles", response_model=QualityProfileResponse, status_code=status.HTTP_201_CREATED
)
async def create_quality_profile(
    request: QualityProfileCreateRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
):
    """Create quality profile."""
    return await service.create_quality_profile(request)


@router.get("/quality-profiles/{profile_id}", response_model=QualityProfileResponse)
async def get_quality_profile(
    profile_id: UUID, service: Annotated[StreamingService, Depends(get_streaming_service)]
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


@router.post("/cdn-regions", response_model=CDNRegionResponse, status_code=status.HTTP_201_CREATED)
async def create_cdn_region(
    request: CDNRegionCreateRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
):
    """Create CDN region."""
    return await service.create_cdn_region(request)


@router.get("/cdn-regions/{region_id}", response_model=CDNRegionResponse)
async def get_cdn_region(
    region_id: UUID, service: Annotated[StreamingService, Depends(get_streaming_service)]
):
    """Get CDN region."""
    region = await service.get_cdn_region(region_id)
    if not region:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Region not found")
    return region


@router.get("/cdn-regions", response_model=list[CDNRegionResponse])
async def list_cdn_regions(service: Annotated[StreamingService, Depends(get_streaming_service)]):
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
    current_user: Annotated[UUID, Depends(get_current_user_id)],
):
    """Create download session."""
    if request.user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only create downloads for your own account",
        )
    return await service.create_download_session(request)


@router.get("/download-sessions/{download_id}", response_model=DownloadSessionResponse)
async def get_download(
    download_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)],
):
    """Get download session (owner only)."""
    download = await service.get_download_session(download_id)
    if not download:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Download not found")
    if download.user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access your own downloads",
        )
    return download


@router.get("/users/{user_id}/downloads", response_model=list[DownloadSessionResponse])
async def get_user_downloads(
    user_id: Annotated[UUID, Depends(require_self)],
    service: Annotated[StreamingService, Depends(get_streaming_service)],
):
    """Get downloads for user."""
    return await service.get_user_downloads(user_id)


@router.patch("/download-sessions/{download_id}/progress")
async def update_download_progress(
    download_id: UUID,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)],
    bytes_downloaded: Annotated[int, Query(ge=0)],
):
    """Update download progress (owner only)."""
    download = await service.get_download_session(download_id)
    if not download:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Download not found")
    if download.user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access your own downloads",
        )
    return await service.update_download_progress(download_id, bytes_downloaded)


# Signed playback URL endpoints (#489, #491)


@router.post(
    "/playback-sessions/signed-url",
    response_model=SignedPlaybackUrlResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_signed_playback_url(
    request: SignedPlaybackUrlRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)],
):
    """Generate HMAC-signed playback URL for manifest access."""
    # Verify the session belongs to the current user
    session = await service.get_playback_session(request.session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only generate signed URLs for your own sessions",
        )
    if session.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session is not active",
        )

    signed_url, expires_at = service.generate_signed_url(request)
    return SignedPlaybackUrlResponse(
        signed_url=signed_url,
        expires_at=expires_at,
        session_id=request.session_id,
        content_id=request.content_id,
    )
