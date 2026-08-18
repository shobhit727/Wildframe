"""Upload lifecycle security tests (#217).

Pins the five audit findings:
  1. Sessions are terminal: no chunk can be registered after complete/abort.
  2. Pre-signed URLs and sessions expire; the reaper aborts + cleans stale
     sessions.
  3. Storage keys derive only from the unguessable session UUID — the client
     filename never influences the path, and no route accepts a client-chosen
     storage key.
  4. Cleanup deletes only the owning session's objects (identifier reuse
     cannot orphan-cross), and failed cleanup is retried via
     ``storage_cleaned_at``.
  5. Completion re-reads authoritative object-storage metadata (size,
     checksum) instead of trusting client-reported values.
"""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.events import InMemoryEventPublisher, set_event_publisher
from app.core.storage import StubStoragePort, clamp_ttl, set_storage, storage_key_for
from app.models import UploadChunk, UploadSession, UploadSessionStatus
from app.services import UploadError, UploadService

ROUTES_FILE = Path(__file__).resolve().parent.parent / "app" / "api" / "uploads_routes.py"


class FakeRepo:
    """In-memory repository stand-in (full surface incl. reaper queries)."""

    def __init__(self) -> None:
        self.sessions: dict = {}
        self.chunks: dict = {}
        self.enqueued_events: list[dict] = []

    async def create(self, session):
        self.sessions[session.id] = session
        self.chunks.setdefault(session.id, [])
        return session

    async def get(self, session_id):
        return self.sessions.get(session_id)

    async def save(self, session):
        self.sessions[session.id] = session
        return session

    async def add_chunk(self, chunk):
        self.chunks.setdefault(chunk.session_id, []).append(chunk)
        return chunk

    async def count_chunks(self, session_id):
        return len(self.chunks.get(session_id, []))

    async def received_indices(self, session_id):
        return sorted(c.index for c in self.chunks.get(session_id, []))

    async def enqueue_event(self, topic, event_key, payload):
        self.enqueued_events.append({"topic": topic, "key": event_key, "payload": payload})

    async def expired_sessions(self, now):
        return [
            s
            for s in self.sessions.values()
            if s.expires_at < now
            and s.status in (UploadSessionStatus.INITIATED, UploadSessionStatus.UPLOADING)
        ]

    async def uncleaned_aborted(self, now, grace):
        cutoff = now - grace
        return [
            s
            for s in self.sessions.values()
            if s.status == UploadSessionStatus.ABORTED
            and (s.storage_cleaned_at is None or s.storage_cleaned_at < cutoff)
        ]


def make_service(storage: StubStoragePort | None = None) -> tuple[UploadService, FakeRepo]:
    set_event_publisher(InMemoryEventPublisher())
    set_storage(storage or StubStoragePort())
    repo = FakeRepo()
    return UploadService(repo=repo), repo


async def _completed_session(service, repo, *, size_bytes=5 * 1024 * 1024, checksum=None):
    session, _ = await service.create_session(
        creator_id=uuid4(),
        filename="clip.mp4",
        mime="video/mp4",
        size_bytes=size_bytes,
        chunk_size=size_bytes,
        checksum_sha256=checksum,
    )
    key = storage_key_for(str(session.id), 0)
    service.storage.upload_bytes(key, b"x" * size_bytes, "video/mp4")
    await service.register_chunk(session_id=session.id, index=0, size_bytes=size_bytes)
    return await service.complete_session(session.id), repo


# ---------------------------------------------------------------------------
# Finding 1: sessions are terminal after completion or cancellation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_blocks_further_chunks():
    service, _repo = make_service()
    completed, _ = await _completed_session(service, None)
    with pytest.raises(UploadError, match="no more chunks accepted"):
        await service.register_chunk(session_id=completed.id, index=0)


@pytest.mark.asyncio
async def test_abort_after_complete_raises():
    service, _repo = make_service()
    completed, _ = await _completed_session(service, None)
    with pytest.raises(UploadError, match="already complete"):
        await service.abort(completed.id, reason="late cancellation")


@pytest.mark.asyncio
async def test_complete_after_abort_raises():
    service, _repo = make_service()
    session, _ = await service.create_session(
        creator_id=uuid4(), filename="clip.mp4", mime="video/mp4",
        size_bytes=5 * 1024 * 1024, chunk_size=5 * 1024 * 1024,
    )
    await service.abort(session.id, reason="cancelled")
    with pytest.raises(UploadError, match="is aborted"):
        await service.complete_session(session.id)


# ---------------------------------------------------------------------------
# Finding 2: pre-signed URLs and sessions expire; reaper cleans stale ones.
# ---------------------------------------------------------------------------


def test_presigned_url_ttl_is_bounded():
    assert clamp_ttl(7200, 3600) == 3600
    assert clamp_ttl(0, 3600) == 1
    stub = StubStoragePort(ttl_seconds=7200, max_ttl_seconds=3600)
    assert stub.ttl_seconds == 3600


@pytest.mark.asyncio
async def test_expired_session_rejects_chunks_and_completion():
    service, _repo = make_service()
    session, _ = await service.create_session(
        creator_id=uuid4(), filename="clip.mp4", mime="video/mp4",
        size_bytes=5 * 1024 * 1024, chunk_size=5 * 1024 * 1024,
    )
    session.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    with pytest.raises(UploadError, match="has expired"):
        await service.register_chunk(session_id=session.id, index=0)
    with pytest.raises(UploadError, match="has expired"):
        await service.complete_session(session.id)


@pytest.mark.asyncio
async def test_reaper_aborts_expired_session_and_cleans_storage():
    service, repo = make_service()
    session, uploads = await service.create_session(
        creator_id=uuid4(), filename="clip.mp4", mime="video/mp4",
        size_bytes=5 * 1024 * 1024, chunk_size=5 * 1024 * 1024,
    )
    key = storage_key_for(str(session.id), 0)
    service.storage.upload_bytes(key, b"x" * 5 * 1024 * 1024, "video/mp4")
    session.expires_at = datetime.now(UTC) - timedelta(minutes=1)

    touched = await service.reap_expired()

    assert touched == 1
    assert repo.sessions[session.id].status == UploadSessionStatus.ABORTED
    assert service.storage.get_object_metadata is not None  # storage alive
    assert key not in service.storage.objects  # cleanup ran
    assert repo.enqueued_events[-1]["payload"]["reason"] == "expired"


# ---------------------------------------------------------------------------
# Finding 3: keys are unguessable; clients can never choose a storage key.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_keys_never_contain_client_filename():
    service, _repo = make_service()
    session, uploads = await service.create_session(
        creator_id=uuid4(),
        filename="../../etc/passwd.mp4",
        mime="video/mp4",
        size_bytes=5 * 1024 * 1024,
        chunk_size=5 * 1024 * 1024,
    )
    for upload in uploads:
        assert upload.storage_key.startswith(f"uploads/{session.id}/")
        assert "passwd" not in upload.storage_key
        assert ".." not in upload.storage_key


def test_no_route_accepts_a_client_chosen_storage_key():
    """Static route-surface check: request schemas and handler params never
    carry ``storage_key``; clients can only write to keys the server derived."""
    source = ROUTES_FILE.read_text()
    tree = ast.parse(source)
    request_models = {"CreateSessionRequest", "RegisterChunkRequest"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name in request_models:
            fields = [n.target.id for n in node.body if isinstance(n, ast.AnnAssign)]
            assert "storage_key" not in fields, node.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = [a.arg for a in node.args.args]
            assert "storage_key" not in params, node.name


# ---------------------------------------------------------------------------
# Finding 4: cleanup is scoped to the owning session and retried on failure.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abort_cleanup_does_not_touch_other_sessions():
    service, _repo = make_service()
    s1, _ = await service.create_session(
        creator_id=uuid4(), filename="a.mp4", mime="video/mp4",
        size_bytes=5 * 1024 * 1024, chunk_size=5 * 1024 * 1024,
    )
    s2, _ = await service.create_session(
        creator_id=uuid4(), filename="b.mp4", mime="video/mp4",
        size_bytes=5 * 1024 * 1024, chunk_size=5 * 1024 * 1024,
    )
    key1 = storage_key_for(str(s1.id), 0)
    key2 = storage_key_for(str(s2.id), 0)
    service.storage.upload_bytes(key1, b"a" * 5 * 1024 * 1024, "video/mp4")
    service.storage.upload_bytes(key2, b"b" * 5 * 1024 * 1024, "video/mp4")

    await service.abort(s1.id, reason="cancelled")

    assert key1 not in service.storage.objects
    assert key2 in service.storage.objects  # untouched


@pytest.mark.asyncio
async def test_cleanup_failure_is_retried_by_reaper():
    storage = StubStoragePort()
    service, repo = make_service(storage)
    session, _ = await service.create_session(
        creator_id=uuid4(), filename="clip.mp4", mime="video/mp4",
        size_bytes=5 * 1024 * 1024, chunk_size=5 * 1024 * 1024,
    )
    key = storage_key_for(str(session.id), 0)
    storage.upload_bytes(key, b"x" * 5 * 1024 * 1024, "video/mp4")

    original = storage.cleanup_upload
    async def broken(**kwargs):
        raise RuntimeError("storage unreachable")
    storage.cleanup_upload = broken

    aborted = await service.abort(session.id, reason="cancelled")
    assert aborted.storage_cleaned_at is None  # not marked cleaned
    assert key in storage.objects  # still there

    storage.cleanup_upload = original
    touched = await service.reap_expired()  # retry via uncleaned_aborted
    assert touched == 1
    assert repo.sessions[session.id].storage_cleaned_at is not None
    assert key not in storage.objects


# ---------------------------------------------------------------------------
# Finding 5: finalization re-reads object storage, never client assertions.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_chunk_uses_storage_size_not_client_size():
    service, _repo = make_service()
    session, _ = await service.create_session(
        creator_id=uuid4(), filename="clip.mp4", mime="video/mp4",
        size_bytes=5 * 1024 * 1024, chunk_size=5 * 1024 * 1024,
    )
    key = storage_key_for(str(session.id), 0)
    service.storage.upload_bytes(key, b"x" * 5 * 1024 * 1024, "video/mp4")

    chunk = await service.register_chunk(
        session_id=session.id, index=0, size_bytes=1  # client lies
    )
    assert chunk.size_bytes == 5 * 1024 * 1024  # authoritative storage size


@pytest.mark.asyncio
async def test_completion_rereads_storage_before_finalizing():
    service, _repo = make_service()
    session, _ = await service.create_session(
        creator_id=uuid4(), filename="clip.mp4", mime="video/mp4",
        size_bytes=5 * 1024 * 1024, chunk_size=5 * 1024 * 1024,
    )
    key = storage_key_for(str(session.id), 0)
    service.storage.upload_bytes(key, b"x" * 5 * 1024 * 1024, "video/mp4")
    await service.register_chunk(session_id=session.id, index=0)

    # Bytes change in storage after registration (client tamper / partial PUT).
    service.storage.upload_bytes(key, b"y" * 1000, "video/mp4")

    with pytest.raises(UploadError, match="size mismatch at completion"):
        await service.complete_session(session.id)


@pytest.mark.asyncio
async def test_storage_computed_checksum_is_authoritative():
    import hashlib

    service, _repo = make_service()
    payload = b"z" * 5 * 1024 * 1024
    declared = hashlib.sha256(payload).hexdigest()
    session, _ = await service.create_session(
        creator_id=uuid4(), filename="clip.mp4", mime="video/mp4",
        size_bytes=len(payload), chunk_size=len(payload),
        checksum_sha256=declared,
    )
    key = storage_key_for(str(session.id), 0)
    service.storage.upload_bytes(key, payload, "video/mp4")
    await service.register_chunk(session_id=session.id, index=0)

    completed = await service.complete_session(session.id, checksum_sha256="client-lies")

    assert completed.status == UploadSessionStatus.COMPLETE
    assert completed.storage_key == storage_key_for(str(session.id), None)
    assert completed.checksum_sha256 == declared  # storage-computed, not client