"""Failure and compensation paths in the upload lifecycle.

These are the branches that only run when something goes wrong: a database
failure while persisting a new session, a storage failure while minting upload
URLs, a missing or mis-sized chunk, a session that has vanished, and the
advisory-checksum rule. Together they are what stop a partial upload from
leaving orphaned objects or a forged digest behind.

Covered: ``services.py:185-196, 210-212, 248, 274, 277, 341, 385, 429``.
"""

import hashlib
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.core.events import InMemoryEventPublisher, set_event_publisher
from app.core.storage import (
    StorageError,
    StubStoragePort,
    set_storage,
    storage_key_for,
)
from app.models import UploadSessionStatus
from app.services import UploadError, UploadService
from tests.test_upload_state_machine import FakeRepo, put_chunk

MIB = 1024 * 1024


class RecordingStorage(StubStoragePort):
    """Stub storage that records the compensation calls it receives."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cleanup_calls: list[dict] = []
        self.create_upload_calls: list[dict] = []
        self.fail_create_upload_at: int | None = None
        self.fail_cleanup: bool = False

    async def create_upload(self, **kwargs):
        self.create_upload_calls.append(kwargs)
        if (
            self.fail_create_upload_at is not None
            and len(self.create_upload_calls) > self.fail_create_upload_at
        ):
            raise StorageError("cannot sign upload url")
        return await super().create_upload(**kwargs)

    async def cleanup_upload(self, **kwargs):
        self.cleanup_calls.append(kwargs)
        if self.fail_cleanup:
            raise StorageError("object store unreachable")
        return await super().cleanup_upload(**kwargs)


@pytest.fixture
def service():
    """A service wired to a FakeRepo and a RecordingStorage stub."""
    set_event_publisher(InMemoryEventPublisher())
    storage = RecordingStorage()
    set_storage(storage)
    return UploadService(repo=FakeRepo(), storage=storage)


def make_service(repo=None, storage=None):
    set_event_publisher(InMemoryEventPublisher())
    storage = storage or RecordingStorage()
    set_storage(storage)
    return UploadService(repo=repo or FakeRepo(), storage=storage), storage


async def new_session(service, *, chunks=1, size=None, chunk_size=MIB, **kwargs):
    """Create a live session and pre-fill its chunk objects in storage."""
    size = size if size is not None else chunks * chunk_size
    session, uploads = await service.create_session(
        creator_id=uuid4(),
        filename="clip.mp4",
        mime="video/mp4",
        size_bytes=size,
        chunk_size=chunk_size,
        **kwargs,
    )
    for index in range(chunks):
        expected = (
            chunk_size
            if index < session.total_chunks - 1
            else size - chunk_size * (session.total_chunks - 1)
        )
        put_chunk(service.storage, uploads, index, b"x" * expected)
    return session, uploads


# ---------------------------------------------------------------------------
# create_session: database failure -> rollback + compensating storage cleanup.
# ---------------------------------------------------------------------------


class TestCreateSessionDatabaseFailure:
    """services.py:185-196 — an unpersisted upload must not leak objects."""

    @pytest.fixture
    def failing_repo(self):
        class RepoThatFailsToPersist(FakeRepo):
            def __init__(self):
                super().__init__()
                self.rolled_back = False

            async def create(self, session):
                raise RuntimeError("deadlock detected")

            async def _noop(self):  # pragma: no cover - placeholder
                return None

        repo = RepoThatFailsToPersist()
        # Give the transaction stub a rollback that records itself.
        original = repo.session.rollback

        async def rollback():
            repo.rolled_back = True
            await original()

        repo.session.rollback = rollback
        return repo

    async def test_the_transaction_is_rolled_back_and_storage_is_cleaned(self, failing_repo):
        service, storage = make_service(repo=failing_repo)

        with pytest.raises(RuntimeError, match="deadlock detected"):
            await service.create_session(
                creator_id=uuid4(),
                filename="clip.mp4",
                mime="video/mp4",
                size_bytes=10 * MIB,
                chunk_size=5 * MIB,
            )

        assert failing_repo.rolled_back is True, "a failed insert must roll back"
        assert len(storage.cleanup_calls) == 1, "the unpersisted upload must be cleaned up"
        call = storage.cleanup_calls[0]
        assert call["chunk_keys"] == [], "nothing was registered, so no chunk keys exist"
        assert call["final_key"] == storage_key_for(call["session_id"], None)

    async def test_a_cleanup_failure_does_not_mask_the_original_error(self, failing_repo):
        """The caller must see the database error, not the compensation error."""
        service, storage = make_service(repo=failing_repo)
        storage.fail_cleanup = True

        with pytest.raises(RuntimeError, match="deadlock detected"):
            await service.create_session(
                creator_id=uuid4(),
                filename="clip.mp4",
                mime="video/mp4",
                size_bytes=5 * MIB,
                chunk_size=5 * MIB,
            )

        assert storage.cleanup_calls, "cleanup must still have been attempted"

    async def test_no_session_row_survives_the_failure(self, failing_repo):
        service, _storage = make_service(repo=failing_repo)
        with pytest.raises(RuntimeError):
            await service.create_session(
                creator_id=uuid4(),
                filename="clip.mp4",
                mime="video/mp4",
                size_bytes=5 * MIB,
                chunk_size=5 * MIB,
            )
        assert failing_repo.sessions == {}

    async def test_a_single_chunk_upload_never_opens_a_multipart_upload(self):
        """A 1-chunk session must not leave an orphaned multipart upload id."""
        service, storage = make_service()
        session, _uploads = await service.create_session(
            creator_id=uuid4(),
            filename="clip.mp4",
            mime="video/mp4",
            size_bytes=5 * MIB,
            chunk_size=5 * MIB,
        )
        assert session.total_chunks == 1
        assert session.multipart_upload_id is None


# ---------------------------------------------------------------------------
# create_session: upload-URL minting failure -> self-abort.
# ---------------------------------------------------------------------------


class TestCreateSessionUrlFailure:
    """services.py:210-212 — a half-minted session is aborted, not left dangling."""

    @pytest.fixture
    def service_and_repo(self):
        service, storage = make_service()
        return service, storage, service.repo

    async def test_the_session_is_aborted_with_the_generation_reason(self, service_and_repo):
        service, storage, repo = service_and_repo
        storage.fail_create_upload_at = 1  # fail on the second chunk's URL

        with pytest.raises(StorageError, match="cannot sign upload url"):
            await service.create_session(
                creator_id=uuid4(),
                filename="clip.mp4",
                mime="video/mp4",
                size_bytes=15 * MIB,
                chunk_size=5 * MIB,
            )

        assert len(repo.sessions) == 1
        session = next(iter(repo.sessions.values()))
        assert session.status == UploadSessionStatus.ABORTED

        aborts = [e for e in repo.enqueued_events if e["topic"] == "content.uploaded.aborted"]
        assert len(aborts) == 1
        assert aborts[0]["payload"]["reason"] == "upload URL generation failed"
        assert aborts[0]["key"] == str(session.id)

    async def test_storage_is_cleaned_before_the_error_surfaces(self, service_and_repo):
        service, storage, _repo = service_and_repo
        storage.fail_create_upload_at = 0  # fail on the very first chunk

        with pytest.raises(StorageError):
            await service.create_session(
                creator_id=uuid4(),
                filename="clip.mp4",
                mime="video/mp4",
                size_bytes=10 * MIB,
                chunk_size=5 * MIB,
            )

        assert storage.cleanup_calls, "the partial upload must be removed from storage"

    async def test_a_completed_upload_enqueues_no_abort_event(self, service_and_repo):
        service, _storage, repo = service_and_repo
        await service.create_session(
            creator_id=uuid4(),
            filename="clip.mp4",
            mime="video/mp4",
            size_bytes=5 * MIB,
            chunk_size=5 * MIB,
        )
        assert [e["topic"] for e in repo.enqueued_events] == []


# ---------------------------------------------------------------------------
# register_chunk: unknown session, missing object, wrong size.
# ---------------------------------------------------------------------------


class TestRegisterChunkGuards:
    async def test_an_unknown_session_is_rejected(self, service):
        with pytest.raises(UploadError, match="not found"):
            await service.register_chunk(session_id=uuid4(), index=0)

    async def test_a_chunk_absent_from_storage_is_rejected(self, service):
        """The client says it uploaded; storage says otherwise — storage wins."""
        session, _uploads = await service.create_session(
            creator_id=uuid4(),
            filename="clip.mp4",
            mime="video/mp4",
            size_bytes=5 * MIB,
            chunk_size=5 * MIB,
        )
        # Deliberately do not write any bytes to storage.

        with pytest.raises(UploadError, match="chunk 0 not found in storage"):
            await service.register_chunk(session_id=session.id, index=0)

    async def test_a_size_mismatch_is_rejected_and_names_both_numbers(self, service):
        """A truncated or padded object must not be accepted."""
        session, uploads = await service.create_session(
            creator_id=uuid4(),
            filename="clip.mp4",
            mime="video/mp4",
            size_bytes=10 * MIB,
            chunk_size=5 * MIB,
        )
        # Storage has 1 MiB where the plan demands 5 MiB.
        put_chunk(service.storage, uploads, 0, b"x" * (1 * MIB))

        with pytest.raises(UploadError) as excinfo:
            await service.register_chunk(session_id=session.id, index=0)

        message = str(excinfo.value)
        assert "size mismatch" in message
        assert str(1 * MIB) in message
        assert str(5 * MIB) in message
        assert session.uploaded_chunks == 0, "a rejected chunk must not be counted"

    async def test_the_client_reported_size_is_ignored(self, service):
        """The authoritative size comes from storage, not the request body."""
        session, _uploads = await new_session(service, chunks=1, size=5 * MIB, chunk_size=5 * MIB)

        chunk = await service.register_chunk(
            session_id=session.id, index=0, size_bytes=1  # a deliberate lie
        )

        assert chunk.size_bytes == 5 * MIB, "the stored size must win over the request"

    async def test_the_last_chunk_may_be_shorter_than_the_chunk_size(self, service):
        """A 7 MiB object in 5 MiB chunks leaves a 2 MiB tail, which is legal."""
        session, _uploads = await new_session(service, chunks=2, size=7 * MIB, chunk_size=5 * MIB)
        assert session.total_chunks == 2

        await service.register_chunk(session_id=session.id, index=1)

        assert session.uploaded_chunks == 1


# ---------------------------------------------------------------------------
# complete_session / abort: unknown session + advisory checksum.
# ---------------------------------------------------------------------------


class TestCompleteAndAbortGuards:
    async def test_completing_an_unknown_session_is_rejected(self, service):
        with pytest.raises(UploadError, match="not found"):
            await service.complete_session(uuid4())

    async def test_aborting_an_unknown_session_is_rejected(self, service):
        with pytest.raises(UploadError, match="not found"):
            await service.abort(uuid4())

    async def test_a_client_checksum_cannot_forge_the_recorded_digest(self, service):
        """services.py:385 — an unverified client digest is ignored, not stored.

        The session declared no expectation, so the value supplied at completion
        is advisory only. The digest that lands on the row must be the one the
        server computed from the assembled bytes.
        """
        session, _uploads = await new_session(service, chunks=1, size=5 * MIB, chunk_size=5 * MIB)
        assert session.checksum_sha256 is None

        await service.register_chunk(session_id=session.id, index=0)
        forged = "f" * 64
        completed = await service.complete_session(session.id, checksum_sha256=forged)

        expected = hashlib.sha256(b"x" * (5 * MIB)).hexdigest()
        assert completed.checksum_sha256 == expected
        assert completed.checksum_sha256 != forged

    async def test_a_declared_checksum_is_still_enforced_at_completion(self, service):
        """The advisory rule must not weaken a *declared* expectation."""
        session, _uploads = await service.create_session(
            creator_id=uuid4(),
            filename="clip.mp4",
            mime="video/mp4",
            size_bytes=5 * MIB,
            chunk_size=5 * MIB,
            checksum_sha256="a" * 64,
        )
        put_chunk(service.storage, _uploads, 0, b"x" * (5 * MIB))
        await service.register_chunk(session_id=session.id, index=0)

        with pytest.raises(UploadError, match="checksum mismatch"):
            await service.complete_session(session.id)

    async def test_completion_is_idempotent(self, service):
        session, _uploads = await new_session(service, chunks=1, size=5 * MIB, chunk_size=5 * MIB)
        await service.register_chunk(session_id=session.id, index=0)
        first = await service.complete_session(session.id)
        second = await service.complete_session(session.id)

        assert second.status == UploadSessionStatus.COMPLETE
        uploaded = [e for e in service.repo.enqueued_events if e["topic"] == "content.uploaded"]
        assert len(uploaded) == 1, "an idempotent replay must not re-emit content.uploaded"
        assert first.storage_key == second.storage_key

    async def test_a_storage_completion_failure_leaves_the_session_retryable(self, service):
        """A 500 from storage must not be reported as success."""
        storage = service.storage
        session, _uploads = await new_session(service, chunks=1, size=5 * MIB, chunk_size=5 * MIB)
        await service.register_chunk(session_id=session.id, index=0)

        async def boom(**kwargs):
            raise StorageError("part upload incomplete")

        with patch.object(storage, "complete_upload", side_effect=boom):
            with pytest.raises(UploadError, match="storage completion failed"):
                await service.complete_session(session.id)

        assert session.status == UploadSessionStatus.UPLOADING
        assert not [e for e in service.repo.enqueued_events if e["topic"] == "content.uploaded"]

    async def test_aborting_a_complete_session_is_refused(self, service):
        session, _uploads = await new_session(service, chunks=1, size=5 * MIB, chunk_size=5 * MIB)
        await service.register_chunk(session_id=session.id, index=0)
        await service.complete_session(session.id)

        with pytest.raises(UploadError, match="already complete"):
            await service.abort(session.id)

    async def test_repeated_abort_recleans_storage_but_emits_once(self, service):
        storage = service.storage
        session, _uploads = await new_session(service, chunks=1, size=5 * MIB, chunk_size=5 * MIB)

        await service.abort(session.id, reason="first")
        cleanups_after_first = len(storage.cleanup_calls)
        await service.abort(session.id, reason="second")

        assert session.status == UploadSessionStatus.ABORTED
        assert (
            len(storage.cleanup_calls) == cleanups_after_first + 1
        ), "cleanup is retried even on a repeated abort"
        aborts = [
            e for e in service.repo.enqueued_events if e["topic"] == "content.uploaded.aborted"
        ]
        assert len(aborts) == 1, "the abort event must not be re-emitted"

    async def test_a_cleanup_failure_leaves_the_retry_marker_unset(self, service):
        """storage_cleaned_at stays None so the reaper picks the session up again."""
        storage = service.storage
        session, _uploads = await new_session(service, chunks=1, size=5 * MIB, chunk_size=5 * MIB)

        async def boom(**kwargs):
            raise RuntimeError("object store unreachable")

        with patch.object(storage, "cleanup_upload", side_effect=boom):
            aborted = await service.abort(session.id, reason="x")

        assert aborted.status == UploadSessionStatus.ABORTED
        assert aborted.storage_cleaned_at is None

    async def test_a_successful_abort_stamps_the_cleanup_marker(self, service):
        session, _uploads = await new_session(service, chunks=1, size=5 * MIB, chunk_size=5 * MIB)
        aborted = await service.abort(session.id, reason="x")
        assert aborted.storage_cleaned_at is not None

    async def test_an_expired_session_rejects_new_chunks(self, service):
        session, _uploads = await new_session(service, chunks=1, size=5 * MIB, chunk_size=5 * MIB)
        session.expires_at = session.expires_at.replace(year=2000)

        with pytest.raises(UploadError, match="has expired"):
            await service.register_chunk(session_id=session.id, index=0)

    async def test_an_expired_session_cannot_be_completed(self, service):
        session, _uploads = await new_session(service, chunks=1, size=5 * MIB, chunk_size=5 * MIB)
        await service.register_chunk(session_id=session.id, index=0)
        session.expires_at = session.expires_at.replace(year=2000)

        with pytest.raises(UploadError, match="has expired"):
            await service.complete_session(session.id)

    async def test_a_settled_session_takes_no_more_chunks(self, service):
        session, _uploads = await new_session(service, chunks=1, size=5 * MIB, chunk_size=5 * MIB)
        await service.register_chunk(session_id=session.id, index=0)
        await service.complete_session(session.id)

        with pytest.raises(UploadError, match="no more chunks accepted"):
            await service.register_chunk(session_id=session.id, index=0)

    async def test_a_completed_session_cannot_be_completed_twice_over_storage(self, service):
        """The idempotent path must not call storage again."""
        storage = service.storage
        session, _uploads = await new_session(service, chunks=1, size=5 * MIB, chunk_size=5 * MIB)
        await service.register_chunk(session_id=session.id, index=0)
        await service.complete_session(session.id)

        calls = len(storage.cleanup_calls)
        again = await service.complete_session(session.id)

        assert again.status == UploadSessionStatus.COMPLETE
        assert len(storage.cleanup_calls) == calls


class _BogusIndexRepo(FakeRepo):
    """Reports a stray out-of-range chunk index alongside the real ones."""

    def __init__(self, indices: list[int]) -> None:
        super().__init__()
        self._indices = indices

    async def received_indices(self, session_id):
        return list(self._indices)


class TestCompletenessIsCheckedByContentNotCount:
    """``received != expected`` — a count comparison would not catch this.

    A length check is weaker than a content check: a session planned for three
    chunks whose stored indices are ``[0, 1, 99]`` has the right *count* but is
    missing chunk 2. Nothing stops such a row at the DB level (the unique index
    is on ``(session_id, index)`` with no bound on the value), so the service is
    the only place that can catch it.
    """

    async def test_an_out_of_range_index_does_not_satisfy_the_chunk_plan(self):
        repo = _BogusIndexRepo([0, 1, 99])
        service, _storage = make_service(repo=repo)
        session, _uploads = await service.create_session(
            creator_id=uuid4(),
            filename="clip.mp4",
            mime="video/mp4",
            size_bytes=15 * MIB,
            chunk_size=5 * MIB,
        )
        assert session.total_chunks == 3

        with pytest.raises(UploadError) as excinfo:
            await service.complete_session(session.id)

        assert "missing chunks" in str(excinfo.value)
        assert "[2]" in str(excinfo.value), "the absent index must be named"

    async def test_a_duplicate_index_pair_is_also_rejected(self):
        repo = _BogusIndexRepo([0, 0, 1])
        service, _storage = make_service(repo=repo)
        session, _uploads = await service.create_session(
            creator_id=uuid4(),
            filename="clip.mp4",
            mime="video/mp4",
            size_bytes=15 * MIB,
            chunk_size=5 * MIB,
        )

        with pytest.raises(UploadError, match="missing chunks"):
            await service.complete_session(session.id)

    async def test_the_exact_plan_is_accepted(self):
        """Control: the happy path still passes, so the check is not over-strict."""
        repo = _BogusIndexRepo([0, 1, 2])
        service, _storage = make_service(repo=repo)
        session, uploads = await service.create_session(
            creator_id=uuid4(),
            filename="clip.mp4",
            mime="video/mp4",
            size_bytes=15 * MIB,
            chunk_size=5 * MIB,
        )
        for index in range(3):
            put_chunk(service.storage, uploads, index, b"x" * (5 * MIB))

        completed = await service.complete_session(session.id)

        assert completed.status == UploadSessionStatus.COMPLETE
        assert completed.uploaded_chunks == 3
