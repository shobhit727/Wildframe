"""Uploads service tests.

Two layers:

1. Pure in-memory tests of the upload *state machine* (no DB, no network).
   These run anywhere and cover the core lifecycle: create → register chunks →
   complete (happy path), missing-chunk rejection, double-chunk rejection,
   checksum verification, abort, and event emission. They use in-memory stubs
   for the storage port, the event publisher, and a fake repository.

2. A thin DB-backed smoke test mirroring the billing/streaming tests that
   constructs the real ``UploadService`` against a ``db`` session. This only
   runs where Postgres is available; it asserts the service wires up.
"""
import pytest
from uuid import uuid4, UUID

from app.core.events import Event, InMemoryEventPublisher, set_event_publisher
from app.core.storage import PresignedUpload, StubStoragePort, set_storage
from app.models import UploadSession, UploadSessionStatus, UploadChunk
from app.repositories import UploadChunkRepository
from app.services import UploadService, UploadError


# ---------------------------------------------------------------------------
# In-memory fakes (no DB). They mirror the repository's surface just enough
# for the service to run its state machine.
# ---------------------------------------------------------------------------


class FakeRepo:
    """In-memory UploadChunkRepository stand-in."""

    def __init__(self) -> None:
        self.sessions: dict[UUID, UploadSession] = {}
        self.chunks: dict[UUID, list[UploadChunk]] = {}

    async def create(self, session: UploadSession) -> UploadSession:
        self.sessions[session.id] = session
        self.chunks.setdefault(session.id, [])
        return session

    async def get(self, session_id: UUID):
        return self.sessions.get(session_id)

    async def save(self, session: UploadSession) -> UploadSession:
        self.sessions[session.id] = session
        return session

    async def add_chunk(self, chunk: UploadChunk) -> UploadChunk:
        self.chunks.setdefault(chunk.session_id, []).append(chunk)
        return chunk

    async def count_chunks(self, session_id: UUID) -> int:
        return len(self.chunks.get(session_id, []))

    async def received_indices(self, session_id: UUID) -> list[int]:
        return sorted(c.index for c in self.chunks.get(session_id, []))


def make_service():
    """Build an UploadService wired to in-memory stubs."""
    set_event_publisher(InMemoryEventPublisher())
    set_storage(StubStoragePort())
    repo = FakeRepo()
    return UploadService(repo=repo), repo


# ---------------------------------------------------------------------------
# 1. Pure state-machine tests.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_plans_chunks_and_issues_urls():
    service, repo = make_service()
    creator = uuid4()
    session, uploads = await service.create_session(
        creator_id=creator,
        filename="clip.mp4",
        mime="video/mp4",
        size_bytes=12 * 1024 * 1024,
        chunk_size=5 * 1024 * 1024,
    )
    assert session.status == UploadSessionStatus.INITIATED
    # 12 MiB at 5 MiB/chunk => 3 chunks.
    assert session.total_chunks == 3
    assert len(uploads) == 3
    assert all(u.upload_url for u in uploads)
    # Session is persisted.
    assert repo.get(session.id) is session


@pytest.mark.asyncio
async def test_register_chunk_advances_status_and_counts():
    service, repo = make_service()
    session, _ = await service.create_session(
        creator_id=uuid4(),
        filename="clip.mp4",
        mime="video/mp4",
        size_bytes=5 * 1024 * 1024,
        chunk_size=5 * 1024 * 1024,
    )
    assert session.total_chunks == 1
    await service.register_chunk(
        session_id=session.id, index=0, size_bytes=5 * 1024 * 1024
    )
    assert session.status == UploadSessionStatus.UPLOADING
    assert session.uploaded_chunks == 1


@pytest.mark.asyncio
async def test_complete_happy_path_emits_content_uploaded():
    service, repo = make_service()
    creator = uuid4()
    session, _ = await service.create_session(
        creator_id=creator,
        filename="clip.mp4",
        mime="video/mp4",
        size_bytes=5 * 1024 * 1024,
        chunk_size=5 * 1024 * 1024,
        checksum_sha256="abc123",
    )
    await service.register_chunk(
        session_id=session.id, index=0, size_bytes=5 * 1024 * 1024
    )
    completed = await service.complete_session(session.id, checksum_sha256="abc123")
    assert completed.status == UploadSessionStatus.COMPLETE
    assert completed.storage_key == f"uploads/{session.id}/clip.mp4"

    # The content.uploaded event was emitted with the right payload.
    publisher = service.publisher
    assert isinstance(publisher, InMemoryEventPublisher)
    assert len(publisher.sent) == 1
    event = publisher.sent[0]
    assert event.topic == "content.uploaded"
    assert event.key == str(session.id)
    assert event.payload["creator_id"] == str(creator)
    assert event.payload["size_bytes"] == 5 * 1024 * 1024


@pytest.mark.asyncio
async def test_complete_rejects_missing_chunks():
    service, repo = make_service()
    session, _ = await service.create_session(
        creator_id=uuid4(),
        filename="clip.mp4",
        mime="video/mp4",
        size_bytes=12 * 1024 * 1024,
        chunk_size=5 * 1024 * 1024,
    )
    # Only register chunk 0 of 3.
    await service.register_chunk(
        session_id=session.id, index=0, size_bytes=5 * 1024 * 1024
    )
    with pytest.raises(UploadError) as exc:
        await service.complete_session(session.id)
    assert "missing chunks" in str(exc.value)
    assert session.status != UploadSessionStatus.COMPLETE


@pytest.mark.asyncio
async def test_register_rejects_duplicate_chunk():
    service, repo = make_service()
    session, _ = await service.create_session(
        creator_id=uuid4(),
        filename="clip.mp4",
        mime="video/mp4",
        size_bytes=5 * 1024 * 1024,
        chunk_size=5 * 1024 * 1024,
    )
    await service.register_chunk(session_id=session.id, index=0, size_bytes=1024)
    with pytest.raises(UploadError) as exc:
        await service.register_chunk(session_id=session.id, index=0, size_bytes=1024)
    assert "already received" in str(exc.value)


@pytest.mark.asyncio
async def test_register_rejects_out_of_range_chunk():
    service, repo = make_service()
    session, _ = await service.create_session(
        creator_id=uuid4(),
        filename="clip.mp4",
        mime="video/mp4",
        size_bytes=5 * 1024 * 1024,
        chunk_size=5 * 1024 * 1024,
    )
    with pytest.raises(UploadError) as exc:
        await service.register_chunk(session_id=session.id, index=5, size_bytes=1024)
    assert "out of range" in str(exc.value)


@pytest.mark.asyncio
async def test_complete_rejects_checksum_mismatch():
    service, repo = make_service()
    session, _ = await service.create_session(
        creator_id=uuid4(),
        filename="clip.mp4",
        mime="video/mp4",
        size_bytes=5 * 1024 * 1024,
        chunk_size=5 * 1024 * 1024,
        checksum_sha256="right-hash",
    )
    await service.register_chunk(
        session_id=session.id, index=0, size_bytes=5 * 1024 * 1024
    )
    with pytest.raises(UploadError) as exc:
        await service.complete_session(session.id, checksum_sha256="wrong-hash")
    assert "checksum mismatch" in str(exc.value)


@pytest.mark.asyncio
async def test_abort_emits_content_uploaded_aborted_and_blocks_chunks():
    service, repo = make_service()
    session, _ = await service.create_session(
        creator_id=uuid4(),
        filename="clip.mp4",
        mime="video/mp4",
        size_bytes=5 * 1024 * 1024,
        chunk_size=5 * 1024 * 1024,
    )
    aborted = await service.abort(session.id, reason="user cancelled")
    assert aborted.status == UploadSessionStatus.ABORTED

    publisher = service.publisher
    assert isinstance(publisher, InMemoryEventPublisher)
    assert len(publisher.sent) == 1
    assert publisher.sent[0].topic == "content.uploaded.aborted"

    # No more chunks accepted after abort.
    with pytest.raises(UploadError) as exc:
        await service.register_chunk(session_id=session.id, index=0, size_bytes=1024)
    assert "aborted" in str(exc.value)


@pytest.mark.asyncio
async def test_complete_is_idempotent_guard():
    """Completing an already-complete session raises rather than double-emitting."""
    service, repo = make_service()
    session, _ = await service.create_session(
        creator_id=uuid4(),
        filename="clip.mp4",
        mime="video/mp4",
        size_bytes=5 * 1024 * 1024,
        chunk_size=5 * 1024 * 1024,
    )
    await service.register_chunk(
        session_id=session.id, index=0, size_bytes=5 * 1024 * 1024
    )
    await service.complete_session(session.id)
    with pytest.raises(UploadError) as exc:
        await service.complete_session(session.id)
    assert "already complete" in str(exc.value)


# ---------------------------------------------------------------------------
# 2. DB-backed smoke test (mirrors billing/streaming style).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_persists_to_db(db):
    """Creating a session persists a row via the real repository."""
    service = UploadService(UploadChunkRepository(db))
    session, uploads = await service.create_session(
        creator_id=uuid4(),
        filename="clip.mp4",
        mime="video/mp4",
        size_bytes=5 * 1024 * 1024,
        chunk_size=5 * 1024 * 1024,
    )
    assert session.id is not None
    fetched = await service.repo.get(session.id)
    assert fetched is not None
    assert fetched.filename == "clip.mp4"
    assert fetched.total_chunks == 1
