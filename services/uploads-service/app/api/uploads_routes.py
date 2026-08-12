"""Uploads service API routes.

All routes are prefixed with ``/uploads`` (see ``main.py``).

Endpoints:
    POST /uploads/sessions           — create a session + chunk plan + URLs
    GET  /uploads/sessions/{id}      — get session status
    POST /uploads/sessions/{id}/chunks — register a received chunk
    POST /uploads/sessions/{id}/complete — verify + finalize
    POST /uploads/sessions/{id}/abort — abort
"""

from typing import Annotated
from uuid import UUID

from jose import jwt  # type: ignore[import-untyped]
from fastapi import APIRouter, Body, Depends, Header, HTTPException, status as http_status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.settings import settings
from app.repositories import UploadChunkRepository
from app.services import UploadError, UploadService

router = APIRouter(prefix="/api/v1/uploads", tags=["uploads"])


async def get_current_user_id(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UUID:
    """Resolve the authenticated user id from the JWT sub claim."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
        )
    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.JWTError:
        raise HTTPException(status_code=http_status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    sub = payload.get("sub") or payload.get("user_id")
    if not sub:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        )
    try:
        return UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        )


async def get_upload_service(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UploadService:
    return UploadService(UploadChunkRepository(db))


# ---------------------------------------------------------------------------
# Request / response schemas.
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    creator_id: UUID
    filename: str
    mime: str
    size_bytes: int
    checksum_sha256: str | None = None
    chunk_size: int | None = None


class PresignedUploadResponse(BaseModel):
    storage_key: str
    upload_url: str
    method: str = "PUT"
    headers: dict | None = None


class CreateSessionResponse(BaseModel):
    session_id: UUID
    status: str
    chunk_size: int
    total_chunks: int
    expires_at: str
    uploads: list[PresignedUploadResponse]


class RegisterChunkRequest(BaseModel):
    index: int
    # Deprecated: the service reads the authoritative byte count from object
    # storage; client-reported values are ignored.
    size_bytes: int | None = None
    etag: str | None = None


class SessionResponse(BaseModel):
    session_id: UUID
    creator_id: UUID
    filename: str
    mime: str
    size_bytes: int
    status: str
    storage_key: str | None = None
    checksum_sha256: str | None = None
    total_chunks: int
    uploaded_chunks: int
    expires_at: str


# ---------------------------------------------------------------------------
# Routes.
# ---------------------------------------------------------------------------


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(
    request: CreateSessionRequest,
    service: Annotated[UploadService, Depends(get_upload_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,  # type: ignore[assignment]
):
    """Create an upload session and return a pre-signed URL per chunk."""
    if request.creator_id != current_user:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="You can only create upload sessions for your own account",
        )
    try:
        session, uploads = await service.create_session(
            creator_id=request.creator_id,
            filename=request.filename,
            mime=request.mime,
            size_bytes=request.size_bytes,
            checksum_sha256=request.checksum_sha256,
            chunk_size=request.chunk_size,
        )
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CreateSessionResponse(
        session_id=session.id,  # type: ignore[arg-type]
        status=session.status.value,
        chunk_size=session.chunk_size,  # type: ignore[arg-type]
        total_chunks=session.total_chunks,  # type: ignore[arg-type]
        expires_at=session.expires_at.isoformat(),
        uploads=[
            PresignedUploadResponse(
                storage_key=u.storage_key,
                upload_url=u.upload_url,
                method=u.method,
                headers=u.headers,
            )
            for u in uploads
        ],
    )


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: UUID,
    service: Annotated[UploadService, Depends(get_upload_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,  # type: ignore[assignment]
):
    """Get an upload session's current status (owner only)."""
    from app.models import UploadSession as _  # noqa: F401 (ensure importable)

    session = await _get_owned_session(service, session_id, current_user)
    return _session_to_response(session)


@router.post("/sessions/{session_id}/chunks")
async def register_chunk(
    session_id: UUID,
    request: RegisterChunkRequest,
    service: Annotated[UploadService, Depends(get_upload_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,  # type: ignore[assignment]
):
    """Register a received chunk (owner only)."""
    await _get_owned_session(service, session_id, current_user)
    try:
        chunk = await service.register_chunk(
            session_id=session_id,
            index=request.index,
            size_bytes=request.size_bytes,
            etag=request.etag,
        )
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "chunk_id": str(chunk.id),
        "session_id": str(session_id),
        "index": chunk.index,
        "received": True,
    }


@router.post("/sessions/{session_id}/complete", response_model=SessionResponse)
async def complete_session(
    session_id: UUID,
    service: Annotated[UploadService, Depends(get_upload_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,  # type: ignore[assignment]
    checksum_sha256: Annotated[str | None, Body()] = None,
):
    """Verify all chunks + checksum and finalize (owner only)."""
    await _get_owned_session(service, session_id, current_user)
    try:
        session = await service.complete_session(session_id, checksum_sha256=checksum_sha256)
    except UploadError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _session_to_response(session)


@router.post("/sessions/{session_id}/abort", response_model=SessionResponse)
async def abort_session(
    session_id: UUID,
    service: Annotated[UploadService, Depends(get_upload_service)],
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,  # type: ignore[assignment]
    reason: Annotated[str, Body()] = "",
):
    """Abort an in-progress upload (owner only)."""
    await _get_owned_session(service, session_id, current_user)
    try:
        session = await service.abort(session_id, reason=reason)
    except UploadError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _session_to_response(session)


def _session_to_response(session) -> SessionResponse:
    return SessionResponse(
        session_id=session.id,
        creator_id=session.creator_id,
        filename=session.filename,
        mime=session.mime,
        size_bytes=session.size_bytes,
        status=session.status.value,
        storage_key=session.storage_key,
        checksum_sha256=session.checksum_sha256,
        total_chunks=session.total_chunks,
        uploaded_chunks=session.uploaded_chunks,
        expires_at=session.expires_at.isoformat(),
    )


async def _get_owned_session(service, session_id: UUID, current_user: UUID):
    """Load a session and enforce ownership (404 for unknown/foreign sessions)."""
    session = await service.repo.get(session_id)
    if session is None or session.creator_id != current_user:
        raise HTTPException(status_code=404, detail="Upload session not found")
    return session
