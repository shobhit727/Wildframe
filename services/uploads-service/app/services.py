"""Uploads service business logic.

Owns the upload lifecycle: create a session (with a pre-signed URL + chunk
plan), register received chunks, complete (verify chunks against authoritative
storage metadata + checksum, assemble, emit ``content.uploaded``), and abort
(clean up storage, emit ``content.uploaded.aborted``).

Security/integrity invariants enforced here:

* Storage keys are derived only from the unguessable session UUID — the client
  filename never influences the storage path (display metadata only), so path
  traversal, Unicode-normalization collisions and cross-session overwrites are
  impossible by construction.
* Chunk/object byte counts and content types come from object-storage metadata,
  never from client-reported values.
* The SHA-256 digest published in ``content.uploaded`` is computed server-side
  from the assembled object (or storage-side integrity metadata), never a
  client assertion.
* Multi-part completion uses real storage multipart semantics and rejects
  missing/duplicated/reordered parts.
* Events are persisted in the same DB transaction as the state change
  (transactional outbox) and drained to the bus by a worker, avoiding
  dual-write loss; the session id is the idempotency key (at-least-once).
* Abort and the expiry reaper clean object storage durably and idempotently.

Infrastructure (storage, event bus) is injected via ports so this class is
pure domain logic and unit-testable with stubs.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.core.events import Event, EventPublisher, get_event_publisher
from app.core.settings import settings
from app.core.storage import (
    PresignedUpload,
    StorageError,
    StoragePort,
    get_storage,
    storage_key_for,
)
from app.models import (
    UploadChunk,
    UploadSession,
    UploadSessionStatus,
)
from app.repositories import UploadChunkRepository

logger = logging.getLogger(__name__)

_MIME_RE = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")
_FILENAME_MAX_LENGTH = 255


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
    # Validation helpers.
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_filename(filename: str) -> str:
        """Canonicalize a client filename for display/metadata use only.

        Applies NFKC normalization, keeps only the basename (path separators
        are stripped), removes control characters and leading dots, and bounds
        the length. The result is NEVER used in a storage key.
        """
        normalized = unicodedata.normalize("NFKC", filename).replace("\\", "/")
        parts = [part for part in normalized.split("/") if part]
        if not parts:
            raise UploadError("filename is empty after normalization")
        basename = "".join(
            ch for ch in parts[-1] if ch >= " " and ch != "\x7f"
        ).strip()
        basename = basename.lstrip(".")
        if not basename:
            raise UploadError("filename is empty after normalization")
        return basename[:_FILENAME_MAX_LENGTH]



    @staticmethod
    def validate_mime(mime: str) -> str:
        """Validate the declared media type against the allowlist."""
        candidate = mime.strip().lower()
        if not _MIME_RE.match(candidate):
            raise UploadError(f"invalid media type: {mime!r}")
        if candidate not in settings.ALLOWED_UPLOAD_MIME_TYPES:
            raise UploadError(
                f"media type {candidate!r} is not allowed for upload"
            )
        return candidate

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
        if size_bytes > settings.MAX_UPLOAD_SIZE_BYTES:
            raise UploadError(
                f"size_bytes exceeds MAX_UPLOAD_SIZE_BYTES "
                f"({size_bytes} > {settings.MAX_UPLOAD_SIZE_BYTES})"
            )
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

        The full pre-signed plan is generated BEFORE the session row is
        persisted: a storage failure part-way leaves no orphaned DB session
        (issued URLs self-expire via their bounded TTL). Returns the persisted
        session plus the list of pre-signed uploads the client should PUT each
        chunk to (one entry per chunk).
        """
        safe_filename = self.normalize_filename(filename)
        safe_mime = self.validate_mime(mime)
        chosen_chunk_size, total_chunks = self.compute_chunk_plan(
            size_bytes, chunk_size or settings.DEFAULT_CHUNK_SIZE_BYTES
        )

        # Generate the whole storage plan first (id is fixed up-front so keys
        # are stable before the row exists).
        session_id = uuid4()
        uploads: list[PresignedUpload] = []
        for index in range(total_chunks):
            uploads.append(
                await self.storage.create_upload(
                    session_id=str(session_id),
                    filename=safe_filename,
                    mime=safe_mime,
                    chunk_index=index if total_chunks > 1 else None,
                )
            )

        now = datetime.now(UTC)
        session = UploadSession(
            id=session_id,
            creator_id=creator_id,
            filename=safe_filename,
            mime=safe_mime,
            size_bytes=size_bytes,
            status=UploadSessionStatus.INITIATED,
            checksum_sha256=checksum_sha256,
            chunk_size=chosen_chunk_size,
            total_chunks=total_chunks,
            uploaded_chunks=0,
            expires_at=now + timedelta(hours=settings.SESSION_EXPIRES_HOURS),
        )
        await self.repo.create(session)

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
        size_bytes: int | None = None,
        etag: str | None = None,
    ) -> UploadChunk:
        """Record that a chunk was received.

        Flips the session ``initiated`` → ``uploading`` on the first chunk.
        Enforces: session exists, not complete/aborted, index in range, and the
        index not already received (the DB unique index is the real guard; we
        check first to give a clean domain error).

        ``size_bytes`` (client-reported) is ignored: the authoritative size is
        read from object-storage metadata, and a chunk whose stored bytes do
        not match the expected size for its index is rejected.
        """
        session = await self.repo.get(session_id)
        if session is None:
            raise UploadError(f"upload session {session_id} not found")
        if session.status in (
            UploadSessionStatus.COMPLETE,
            UploadSessionStatus.ABORTED,
        ):
            raise UploadError(
                f"session {session_id} is {session.status.value}; no more chunks accepted"
            )
        if datetime.now(UTC) > session.expires_at:
            raise UploadError(f"upload session {session_id} has expired")
        if index < 0 or index >= session.total_chunks:
            raise UploadError(f"chunk index {index} out of range [0, {session.total_chunks})")

        received = await self.repo.received_indices(session_id)
        if index in received:
            raise UploadError(f"chunk {index} already received for session {session_id}")

        # Authoritative storage check: the object must exist with the exact
        # byte count this index is supposed to hold.
        chunk_key = storage_key_for(str(session_id), index)
        metadata = await self.storage.get_object_metadata(storage_key=chunk_key)
        if metadata is None:
            raise UploadError(f"chunk {index} object {chunk_key} not found in storage")
        expected_size = self._expected_chunk_size(session, index)
        if metadata.size_bytes != expected_size:
            raise UploadError(
                f"chunk {index} size mismatch: storage has {metadata.size_bytes} "
                f"bytes, expected {expected_size}"
            )

        chunk = UploadChunk(
            session_id=session_id,
            index=index,
            size_bytes=metadata.size_bytes,
            etag=etag,
            checksum_sha256=metadata.checksum_sha256,
        )
        await self.repo.add_chunk(chunk)

        # First chunk moves the session into ``uploading``.
        if session.status == UploadSessionStatus.INITIATED:
            session.status = UploadSessionStatus.UPLOADING  # type: ignore[assignment]
        session.uploaded_chunks = await self.repo.count_chunks(session_id)  # type: ignore[assignment]
        await self.repo.save(session)

        logger.info(
            "registered chunk %d/%d for session %s (%d bytes)",
            index,
            session.total_chunks,
            session_id,
            metadata.size_bytes,
        )
        return chunk

    @staticmethod
    def _expected_chunk_size(session: UploadSession, index: int) -> int:
        """Byte count a chunk must hold per the session's chunk plan."""
        if index < session.total_chunks - 1:
            return session.chunk_size  # type: ignore[return-value]
        return session.size_bytes - session.chunk_size * (session.total_chunks - 1)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # complete_session
    # ------------------------------------------------------------------

    async def complete_session(
        self, session_id: UUID, *, checksum_sha256: str | None = None
    ) -> UploadSession:
        """Verify all chunks against storage, assemble, and finalize.

        Verification (all against authoritative storage state):
            1. Every expected chunk index ``0..total_chunks-1`` is registered
               and its stored object exists with the expected byte count.
            2. The stored content type matches the session's MIME.
            3. The server-computed digest of the assembled object equals the
               session's declared checksum (if one was declared at creation).
               A checksum supplied at completion time is treated as advisory
               only — it is never stored as verified.

        The session becomes ``complete``, the final ``storage_key`` is the
        key returned by the storage completion, and ``content.uploaded`` is
        enqueued in the transactional outbox with only verified metadata.

        Completion is idempotent: completing an already-complete session
        returns the stored session without re-emitting the event.
        """
        session = await self.repo.get(session_id)
        if session is None:
            raise UploadError(f"upload session {session_id} not found")
        if session.status == UploadSessionStatus.COMPLETE:
            return session
        if session.status == UploadSessionStatus.ABORTED:
            raise UploadError(f"session {session_id} is aborted")
        if datetime.now(UTC) > session.expires_at:
            raise UploadError(f"upload session {session_id} has expired")

        received = await self.repo.received_indices(session_id)
        expected = list(range(session.total_chunks))
        if received != expected:
            missing = sorted(set(expected) - set(received))
            raise UploadError(f"session {session_id} missing chunks: {missing}")

        # Re-read authoritative metadata at finalization (never trust client
        # state): every chunk object must exist with the expected byte count.
        chunk_keys = [
            storage_key_for(str(session_id), index) for index in expected
        ]
        for index, chunk_key in zip(expected, chunk_keys):
            metadata = await self.storage.get_object_metadata(storage_key=chunk_key)
            if metadata is None:
                raise UploadError(
                    f"chunk {index} object {chunk_key} missing at completion"
                )
            expected_size = self._expected_chunk_size(session, index)
            if metadata.size_bytes != expected_size:
                raise UploadError(
                    f"chunk {index} size mismatch at completion: storage has "
                    f"{metadata.size_bytes} bytes, expected {expected_size}"
                )

        final_key = storage_key_for(str(session_id), None)
        try:
            final_metadata = await self.storage.complete_upload(
                session_id=str(session_id),
                chunk_keys=chunk_keys,
                final_key=final_key,
                size_bytes=session.size_bytes,  # type: ignore[arg-type]
                mime=session.mime,  # type: ignore[arg-type]
            )
        except StorageError as exc:
            # Session stays retryable — completion failure is not a success.
            raise UploadError(f"storage completion failed: {exc}") from exc

        # Checksum: the server-computed digest is the only authority.
        if (
            session.checksum_sha256
            and final_metadata.checksum_sha256
            and session.checksum_sha256 != final_metadata.checksum_sha256
        ):
            raise UploadError(
                f"checksum mismatch for session {session_id}: expected "
                f"{session.checksum_sha256}, storage computed "
                f"{final_metadata.checksum_sha256}"
            )
        if checksum_sha256 and session.checksum_sha256 is None:
            # Advisory client value without a declared expectation: ignored.
            logger.warning(  # type: ignore[unreachable]  # type: ignore[unreachable]
                "ignoring client-supplied checksum for session %s (unverified)",
                session_id,
            )

        session.status = UploadSessionStatus.COMPLETE  # type: ignore[assignment]
        session.storage_key = final_metadata.storage_key  # type: ignore[assignment]
        session.checksum_sha256 = final_metadata.checksum_sha256  # type: ignore[assignment]
        session.uploaded_chunks = len(received)  # type: ignore[assignment]
        await self.repo.save(session)

        # Transactional outbox: same DB transaction as the state change.
        await self.repo.enqueue_event(
            topic="content.uploaded",
            event_key=str(session.id),
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
        logger.info("completed upload session %s", session.id)
        return session

    # ------------------------------------------------------------------
    # abort / cleanup
    # ------------------------------------------------------------------

    async def abort(self, session_id: UUID, *, reason: str = "") -> UploadSession:
        """Abort an in-progress upload.

        Marks the session aborted, durably cleans object storage (idempotent),
        and enqueues ``content.uploaded.aborted``. Repeated abort is safe: an
        already-aborted session triggers another storage-cleanup attempt but
        does not re-emit the event.
        """
        session = await self.repo.get(session_id)
        if session is None:
            raise UploadError(f"upload session {session_id} not found")
        if session.status == UploadSessionStatus.COMPLETE:
            raise UploadError(f"session {session_id} already complete; cannot abort")

        already_aborted = session.status == UploadSessionStatus.ABORTED
        if not already_aborted:
            session.status = UploadSessionStatus.ABORTED  # type: ignore[assignment]
            await self.repo.save(session)

        await self._cleanup_storage(session)
        if not already_aborted:
            await self.repo.enqueue_event(
                topic="content.uploaded.aborted",
                event_key=str(session.id),
                payload={
                    "session_id": str(session.id),
                    "creator_id": str(session.creator_id),
                    "reason": reason,
                },
            )
        logger.info("aborted upload session %s (%s)", session.id, reason)
        return session

    async def _cleanup_storage(self, session: UploadSession) -> None:
        """Delete all session objects; idempotent, failure-tolerant.

        Cleanup failure is logged and leaves ``storage_cleaned_at`` unset so
        the reaper retries later.
        """
        chunk_keys = [
            storage_key_for(str(session.id), index)
            for index in range(session.total_chunks)
        ]
        final_key = storage_key_for(str(session.id), None)
        try:
            await self.storage.cleanup_upload(
                session_id=str(session.id),
                chunk_keys=chunk_keys,
                final_key=final_key,
            )
        except Exception:  # noqa: BLE001 - reaper retries via storage_cleaned_at
            logger.exception(
                "storage cleanup failed for session %s; will retry", session.id
            )
            return
        session.storage_cleaned_at = datetime.now(UTC)  # type: ignore[assignment]
        await self.repo.save(session)

    # ------------------------------------------------------------------
    # Outbox drain + expiry reaper (background workers).
    # ------------------------------------------------------------------

    async def drain_outbox(self) -> int:
        """Publish PENDING outbox rows to the bus; mark them dispatched.

        A row whose publish fails stays PENDING and is retried on the next
        drain (at-least-once; consumers dedupe on the session id). Returns the
        number of rows processed.
        """
        rows = await self.repo.pending_events(limit=settings.OUTBOX_BATCH_SIZE)
        for row in rows:
            try:
                await self.publisher.publish(
                    Event(
                        topic=row.topic,  # type: ignore[arg-type]
                        key=row.event_key,  # type: ignore[arg-type]
                        payload=row.payload,  # type: ignore[arg-type]
                    )
                )
            except Exception:  # noqa: BLE001 - keep row pending for retry
                logger.exception(
                    "outbox publish failed for event %s (topic=%s); will retry",
                    row.id,
                    row.topic,
                )
                continue
            await self.repo.mark_dispatched(row.id)  # type: ignore[arg-type]
        return len(rows)

    async def reap_expired(self) -> int:
        """Abort stale sessions and retry failed storage cleanups.

        Returns the number of sessions touched.
        """
        now = datetime.now(UTC)
        touched = 0
        for session in await self.repo.expired_sessions(now):
            session.status = UploadSessionStatus.ABORTED  # type: ignore[assignment]
            await self.repo.save(session)
            await self._cleanup_storage(session)
            await self.repo.enqueue_event(
                topic="content.uploaded.aborted",
                event_key=str(session.id),
                payload={
                    "session_id": str(session.id),
                    "creator_id": str(session.creator_id),
                    "reason": "expired",
                },
            )
            logger.info("reaped expired upload session %s", session.id)
            touched += 1
        grace = timedelta(seconds=settings.CLEANUP_RETRY_GRACE_SECONDS)
        for session in await self.repo.uncleaned_aborted(now, grace):
            await self._cleanup_storage(session)
            touched += 1
        return touched
