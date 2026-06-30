"""Uploads service API routes.

All routes are prefixed with ``/uploads`` (see ``main.py``).

Endpoints:
    POST /uploads/sessions           — create a session + chunk plan + URLs
    GET  /uploads/sessions/{id}      — get session status
    POST /uploads/sessions/{id}/chunks — register a received chunk
    POST /uploads/sessions/{id}/complete — verify + finalize
    POST /uploads/sessions/{id}/abort — abort
"""
from uuid import UUID
from typing import List, Optional
from fastapi import APIRouter, Depends, Body, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.repositories import UploadChunkRepository
from app.services import UploadService, UploadError

router = APIRouter(prefix="/uploads", tags=["uploads"])


async def get_upload_service(
    db: AsyncSession = Depends(get_db),
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
    checksum_sha256: Optional[str] = None
    chunk_size: Optional[int] = None


class PresignedUploadResponse(BaseModel):
    storage_key: str
    upload_url: str
    method: str = "PUT"
    headers: Optional[dict] = None


class CreateSessionResponse(BaseModel):
    session_id: UUID
    status: str
    chunk_size: int
    total_chunks: int
    expires_at: str
    uploads: List[PresignedUploadResponse]


class RegisterChunkRequest(BaseModel):
    index: int
    size_bytes: int
    etag: Optional[str] = None


class SessionResponse(BaseModel):
    session_id: UUID
    creator_id: UUID
    filename: str
    mime: str
    size_bytes: int
    status: str
    storage_key: Optional[str] = None
    checksum_sha256: Optional[str] = None
    total_chunks: int
    uploaded_chunks: int
    expires_at: str


# ---------------------------------------------------------------------------
# Routes.
# ---------------------------------------------------------------------------


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(
    request: CreateSessionRequest,
    service: UploadService = Depends(get_upload_service),
):
    """Create an upload session and return a pre-signed URL per chunk."""
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
        session_id=session.id,
        status=session.status.value,
        chunk_size=session.chunk_size,
        total_chunks=session.total_chunks,
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
    service: UploadService = Depends(get_upload_service),
):
    """Get an upload session's current status."""
    from app.models import UploadSession as _  # noqa: F401 (ensure importable)

    session = await service.repo.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Upload session not found")
    return _session_to_response(session)


@router.post("/sessions/{session_id}/chunks")
async def register_chunk(
    session_id: UUID,
    request: RegisterChunkRequest,
    service: UploadService = Depends(get_upload_service),
):
    """Register a received chunk."""
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
    checksum_sha256: Optional[str] = Body(None),
    service: UploadService = Depends(get_upload_service),
):
    """Verify all chunks + checksum and finalize the upload."""
    try:
        session = await service.complete_session(
            session_id, checksum_sha256=checksum_sha256
        )
    except UploadError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _session_to_response(session)


@router.post("/sessions/{session_id}/abort", response_model=SessionResponse)
async def abort_session(
    session_id: UUID,
    reason: str = Body(""),
    service: UploadService = Depends(get_upload_service),
):
    """Abort an in-progress upload."""
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
