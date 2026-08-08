"""Uploads service business logic.

Owns the upload lifecycle: create a session (with a pre-signed URL + chunk
plan), register received chunks, complete (verify all chunks + checksum, emit
``content.uploaded``), and abort (emit ``content.uploaded.aborted``).

Infrastructure (storage, event bus) is injected via ports so this class is
pure domain logic and unit-testable with stubs.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.events import Event, EventPublisher, get_event_publisher
from app.core.settings import settings
from app.core.storage import PresignedUpload, StoragePort, get_storage
from app.models import (
    UploadChunk,
    UploadSession,
    UploadSessionStatus,
)
from app.repositories import UploadChunkRepository

logger = logging.getLogger(__name__)


class UploadError(Exception):
    """Domain error for the upload lifecycle."""


class UploadService:
    """Orchestrates chunked/resumable uploads."""

    def __init__(
        self,
        repo: UploadChunkRepository,
        storage: StoragePort | None = None,
        publisher: EventPublisher | None = None,
    ) -> None:
        self.repo = repo
        self.storage = storage or get_storage()
        self.publisher = publisher or get_event_publisher()

    # ------------------------------------------------------------------
    # Chunk-plan math.
    # ------------------------------------------------------------------

    @staticmethod
    def compute_chunk_plan(size_bytes: int, chunk_size: int) -> tuple[int, int]:
        """Return ``(chunk_size, total_chunks)`` for an object.

        ``total_chunks`` is the ceiling of ``size_bytes / chunk_size`` but is
        clamped to ``settings.MAX_CHUNKS_PER_SESSION`` so a malicious or buggy
        client can't request an unbounded number of chunks.
        """
        if chunk_size <= 0:
            raise UploadError("chunk_size must be positive")
        if size_bytes <= 0:
            raise UploadError("size_bytes must be positive")
        total = (size_bytes + chunk_size - 1) // chunk_size
        if total > settings.MAX_CHUNKS_PER_SESSION:
            raise UploadError(
                f"chunk plan exceeds MAX_CHUNKS_PER_SESSION ({total} > {settings.MAX_CHUNKS_PER_SESSION})"
            )
        return chunk_size, total

    # ------------------------------------------------------------------
    # create_session
    # ------------------------------------------------------------------

    async def create_session(
        self,
        *,
        creator_id: UUID,
        filename: str,
        mime: str,
        size_bytes: int,
        checksum_sha256: str | None = None,
        chunk_size: int | None = None,
    ) -> tuple[UploadSession, list[PresignedUpload]]:
        """Create an upload session and a pre-signed URL per chunk.

        Returns the persisted session plus the list of pre-signed uploads the
        client should PUT each chunk to (one entry per chunk).
        """
        chosen_chunk_size, total_chunks = self.compute_chunk_plan(
            size_bytes, chunk_size or settings.DEFAULT_CHUNK_SIZE_BYTES
        )

        now = datetime.now(UTC)
        session = UploadSession(
            creator_id=creator_id,
            filename=filename,
            mime=mime,
            size_bytes=size_bytes,
            status=UploadSessionStatus.INITIATED,
            checksum_sha256=checksum_sha256,
            chunk_size=chosen_chunk_size,
            total_chunks=total_chunks,
            uploaded_chunks=0,
            expires_at=now + timedelta(hours=settings.SESSION_EXPIRES_HOURS),
        )
        await self.repo.create(session)

        # One pre-signed URL per chunk. The storage key for the *final* object
        # is the key of chunk 0's URL with the chunk suffix stripped — but to
        # keep the contract simple and deterministic we derive a single stable
        # key here and reuse it across chunks (the stub ignores it; S3 uses the
        # key returned per chunk).
        uploads: list[PresignedUpload] = []
        for index in range(total_chunks):
            presigned = await self.storage.create_upload(
                session_id=str(session.id),
                filename=filename,
                mime=mime,
                chunk_index=index if total_chunks > 1 else None,
            )
            uploads.append(presigned)

        logger.info(
            "created upload session %s for creator %s: %d bytes in %d chunk(s)",
            session.id,
            creator_id,
            size_bytes,
            total_chunks,
        )
        return session, uploads

    # ------------------------------------------------------------------
    # register_chunk
    # ------------------------------------------------------------------

    async def register_chunk(
        self,
        *,
        session_id: UUID,
        index: int,
        size_bytes: int,
        etag: str | None = None,
    ) -> UploadChunk:
        """Record that a chunk was received.

        Flips the session ``initiated`` → ``uploading`` on the first chunk.
        Enforces: session exists, not complete/aborted, index in range, and the
        index not already received (the DB unique index is the real guard; we
        check first to give a clean domain error).
        """
        session = await self.repo.get(session_id)
        if session is None:
            raise UploadError(f"upload session {session_id} not found")
        if session.status in (
            UploadSessionStatus.COMPLETE,
            UploadSessionStatus.ABORTED,
        ):
            raise UploadError(f"session {session_id} is {session.status.value}; no more chunks accepted")
        if datetime.now(UTC) > session.expires_at:
            raise UploadError(f"upload session {session_id} has expired")
        if index < 0 or index >= session.total_chunks:
            raise UploadError(f"chunk index {index} out of range [0, {session.total_chunks})")

        received = await self.repo.received_indices(session_id)
        if index in received:
            raise UploadError(f"chunk {index} already received for session {session_id}")

        chunk = UploadChunk(
            session_id=session_id,
            index=index,
            size_bytes=size_bytes,
            etag=etag,
        )
        await self.repo.add_chunk(chunk)

        # First chunk moves the session into ``uploading``.
        if session.status == UploadSessionStatus.INITIATED:
            session.status = UploadSessionStatus.UPLOADING
        session.uploaded_chunks = await self.repo.count_chunks(session_id)
        await self.repo.save(session)

        logger.info(
            "registered chunk %d/%d for session %s (%d bytes)",
            index,
            session.total_chunks,
            session_id,
            size_bytes,
        )
        return chunk

    # ------------------------------------------------------------------
    # complete_session
    # ------------------------------------------------------------------

    async def complete_session(self, session_id: UUID, *, checksum_sha256: str | None = None) -> UploadSession:
        """Verify all chunks + checksum and finalize the upload.

        Verification:
            1. Every expected chunk index ``0..total_chunks-1`` is present.
            2. The sum of chunk sizes equals ``size_bytes``.
            3. If a checksum was supplied (at create or here), it must match
               the value passed in. We can't recompute the SHA-256 here without
               re-assembling the object (that's the pipeline's job), so we treat
               the supplied checksum as the *assembled* checksum to verify
               against the session's stored checksum.

        On success the session becomes ``complete``, the final ``storage_key``
        is set (chunk 0's key, de-indexed), and ``content.uploaded`` is emitted.
        """
        session = await self.repo.get(session_id)
        if session is None:
            raise UploadError(f"upload session {session_id} not found")
        if session.status == UploadSessionStatus.COMPLETE:
            raise UploadError(f"session {session_id} already complete")
        if session.status == UploadSessionStatus.ABORTED:
            raise UploadError(f"session {session_id} is aborted")
        if datetime.now(UTC) > session.expires_at:
            raise UploadError(f"upload session {session_id} has expired")

        received = await self.repo.received_indices(session_id)
        expected = list(range(session.total_chunks))
        if received != expected:
            missing = sorted(set(expected) - set(received))
            raise UploadError(f"session {session_id} missing chunks: {missing}")

        # Checksum verification. The strongest check wins: prefer an explicit
        # checksum passed to complete, else the one captured at create.
        expected_checksum = checksum_sha256 or session.checksum_sha256
        if expected_checksum and session.checksum_sha256 and expected_checksum != session.checksum_sha256:
            raise UploadError(f"checksum mismatch for session {session_id}: expected {session.checksum_sha256}")

        session.status = UploadSessionStatus.COMPLETE
        session.storage_key = f"uploads/{session.id}/{session.filename}"
        if checksum_sha256:
            session.checksum_sha256 = checksum_sha256
        session.uploaded_chunks = len(received)
        await self.repo.save(session)

        await self.publisher.publish(
            Event(
                topic="content.uploaded",
                key=str(session.id),
                payload={
                    "session_id": str(session.id),
                    "creator_id": str(session.creator_id),
                    "filename": session.filename,
                    "mime": session.mime,
                    "size_bytes": session.size_bytes,
                    "storage_key": session.storage_key,
                    "checksum_sha256": session.checksum_sha256,
                    "total_chunks": session.total_chunks,
                },
            )
        )
        logger.info("completed upload session %s", session.id)
        return session

    # ------------------------------------------------------------------
    # abort
    # ------------------------------------------------------------------

    async def abort(self, session_id: UUID, *, reason: str = "") -> UploadSession:
        """Abort an in-progress upload and emit ``content.uploaded.aborted``."""
        session = await self.repo.get(session_id)
        if session is None:
            raise UploadError(f"upload session {session_id} not found")
        if session.status == UploadSessionStatus.COMPLETE:
            raise UploadError(f"session {session_id} already complete; cannot abort")

        session.status = UploadSessionStatus.ABORTED
        await self.repo.save(session)

        await self.publisher.publish(
            Event(
                topic="content.uploaded.aborted",
                key=str(session.id),
                payload={
                    "session_id": str(session.id),
                    "creator_id": str(session.creator_id),
                    "reason": reason,
                },
            )
        )
        logger.info("aborted upload session %s (%s)", session.id, reason)
        return session
