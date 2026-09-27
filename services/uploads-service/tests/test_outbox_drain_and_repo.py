"""The outbox drain worker and the two uncovered repository queries.

``drain_outbox`` is the transactional-outbox pump: it publishes PENDING rows to
the bus and marks them DISPATCHED. Its whole failure contract — a row whose
publish fails must stay PENDING and be retried, and a crash between the two must
not lose the event — was untested.

Unlike the rest of this module, these tests run against a **real** in-memory
SQLite database and the **real** ``UploadChunkRepository``, so the
``pending_events`` / ``mark_dispatched`` / ``list_by_creator`` /
``received_indices`` SQL actually executes rather than being mocked.

Covered: ``services.py:487-506``; ``repositories.py:43-49, 61-63``.
"""

import datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.events import Event, EventPublisher
from app.core.storage import set_storage
from app.models import (
    Base,
    OutboxEventStatus,
    UploadChunk,
    UploadSession,
    UploadSessionStatus,
)
from app.repositories import UploadChunkRepository
from app.services import UploadService
from tests.test_upload_failure_paths import RecordingStorage

MIB = 1024 * 1024


class FlakyPublisher(EventPublisher):
    """Publisher that fails the first ``failures`` publishes, then succeeds."""

    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.published: list[Event] = []
        self.attempts: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.attempts.append(event)
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("broker unavailable")
        self.published.append(event)

    async def publish_many(self, events: list[Event]) -> None:
        for event in events:
            await self.publish(event)


@pytest.fixture
async def db():
    """Async in-memory SQLite session, following tests/test_repositories.py."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def storage():
    stub = RecordingStorage()
    set_storage(stub)
    return stub


def make_service(db, storage, publisher) -> UploadService:
    return UploadService(repo=UploadChunkRepository(db), storage=storage, publisher=publisher)


async def seed_outbox(service: UploadService, topic: str = "content.uploaded", **payload):
    """Insert one outbox row the way the completion path does.

    ``drain_outbox``'s only input is PENDING ``outbox_events`` rows, so they are
    created directly rather than by driving a full upload: the chunk-registration
    path cannot run against a real database at all (see
    :class:`TestExpiryComparisonNeedsATzAwareTimestamp`).
    """
    fields = {
        "session_id": str(uuid4()),
        "creator_id": str(uuid4()),
        "filename": "clip.mp4",
        "mime": "video/mp4",
        "size_bytes": 1 * MIB,
        "storage_key": "uploads/x/final",
        "checksum_sha256": "a" * 64,
        "total_chunks": 1,
    }
    fields.update(payload)
    row = await service.repo.enqueue_event(
        topic=topic, event_key=fields["session_id"], payload=fields
    )
    await service.repo.session.commit()
    return row


# ---------------------------------------------------------------------------
# drain_outbox (services.py:487-506).
# ---------------------------------------------------------------------------


class TestDrainOutbox:
    async def test_a_pending_row_is_published_and_marked_dispatched(self, db, storage):
        publisher = FlakyPublisher()
        service = make_service(db, storage, publisher)
        row = await seed_outbox(service)
        assert await service.repo.pending_events() != [], "the row must be PENDING"

        processed = await service.drain_outbox()

        assert processed == 1
        assert [e.topic for e in publisher.published] == ["content.uploaded"]
        assert publisher.published[0].key == row.event_key
        assert publisher.published[0].payload["session_id"] == row.payload["session_id"]
        assert await service.repo.pending_events() == [], "the row must be marked dispatched"

    async def test_the_published_payload_carries_only_verified_metadata(self, db, storage):
        publisher = FlakyPublisher()
        service = make_service(db, storage, publisher)
        await seed_outbox(service)
        await service.drain_outbox()

        payload = publisher.published[0].payload
        assert payload["size_bytes"] == 1 * MIB
        assert payload["storage_key"] == "uploads/x/final"
        assert payload["checksum_sha256"] == "a" * 64
        assert payload["total_chunks"] == 1

    async def test_a_publish_failure_leaves_the_row_pending_for_retry(self, db, storage):
        """At-least-once: a broker outage must not drop the event."""
        publisher = FlakyPublisher(failures=1)
        service = make_service(db, storage, publisher)
        await seed_outbox(service)

        first = await service.drain_outbox()

        assert first == 1, "the row was processed (and failed), not skipped"
        assert publisher.published == []
        pending = await service.repo.pending_events()
        assert len(pending) == 1, "a failed publish must leave the row PENDING"
        assert pending[0].status == OutboxEventStatus.PENDING

        # The retry succeeds and the row is finally retired.
        second = await service.drain_outbox()
        assert second == 1
        assert [e.topic for e in publisher.published] == ["content.uploaded"]
        assert await service.repo.pending_events() == []

    async def test_a_failed_row_does_not_block_the_others(self, db, storage):
        """One poisonous event must not stall the queue behind it."""
        publisher = FlakyPublisher()
        service = make_service(db, storage, publisher)
        await seed_outbox(service)
        # A second, independent row that will fail first.
        await service.repo.enqueue_event(topic="poison", event_key="k1", payload={"bad": True})
        await db.commit()

        original = publisher.publish

        async def fail_poison(event: Event) -> None:
            if event.topic == "poison":
                raise RuntimeError("unserialisable payload")
            await original(event)

        publisher.publish = fail_poison  # type: ignore[method-assign]

        processed = await service.drain_outbox()

        assert processed == 2, "both rows were attempted"
        assert [e.topic for e in publisher.published] == ["content.uploaded"]
        remaining = await service.repo.pending_events()
        assert [e.topic for e in remaining] == ["poison"], "only the failed row stays pending"

    async def test_draining_an_empty_outbox_is_a_zero_no_op(self, db, storage):
        service = make_service(db, storage, FlakyPublisher())
        assert await service.drain_outbox() == 0

    async def test_a_second_drain_finds_nothing_left(self, db, storage):
        service = make_service(db, storage, FlakyPublisher())
        await seed_outbox(service)
        assert await service.drain_outbox() == 1
        assert await service.drain_outbox() == 0, "dispatched rows must not be re-published"

    async def test_the_batch_size_caps_how_many_rows_one_pass_takes(self, db, storage):
        from unittest.mock import patch

        from app.core.settings import settings

        publisher = FlakyPublisher()
        service = make_service(db, storage, publisher)
        for index in range(5):
            await service.repo.enqueue_event(topic="t", event_key=f"k{index}", payload={"i": index})
        await db.commit()

        with patch.object(settings, "OUTBOX_BATCH_SIZE", 2):
            assert await service.drain_outbox() == 2
        assert len(publisher.published) == 2
        assert len(await service.repo.pending_events()) == 3

        assert await service.drain_outbox() == 3
        assert await service.repo.pending_events() == []

    async def test_the_abort_event_is_drained_too(self, db, storage):
        publisher = FlakyPublisher()
        service = make_service(db, storage, publisher)
        await seed_outbox(service, topic="content.uploaded.aborted", reason="user cancelled")

        assert await service.drain_outbox() == 1
        event = publisher.published[0]
        assert event.topic == "content.uploaded.aborted"
        assert event.payload["reason"] == "user cancelled"


# ---------------------------------------------------------------------------
# repositories.py:43-49 — list_by_creator.
# ---------------------------------------------------------------------------


class TestListByCreator:
    async def _seed(self, repo, creator, count, **overrides):
        rows = []
        for _ in range(count):
            row = UploadSession(
                creator_id=creator,
                filename="clip.mp4",
                mime="video/mp4",
                size_bytes=1024,
                chunk_size=1024,
                total_chunks=1,
                expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
                **overrides,
            )
            rows.append(await repo.create(row))
        await repo.session.commit()
        return rows

    async def test_only_the_requested_creator_sessions_come_back(self, db):
        repo = UploadChunkRepository(db)
        mine, theirs = uuid4(), uuid4()
        await self._seed(repo, mine, 3)
        await self._seed(repo, theirs, 2)

        result = await repo.list_by_creator(mine)

        assert len(result) == 3
        assert {row.creator_id for row in result} == {mine}

    async def test_an_creator_with_no_sessions_gets_an_empty_list(self, db):
        repo = UploadChunkRepository(db)
        await self._seed(repo, uuid4(), 2)
        assert await repo.list_by_creator(uuid4()) == []

    async def test_the_limit_is_applied(self, db):
        repo = UploadChunkRepository(db)
        creator = uuid4()
        await self._seed(repo, creator, 5)

        assert len(await repo.list_by_creator(creator, limit=2)) == 2

    async def test_rows_come_back_newest_first(self, db):
        """The list endpoint is a session browser, so recency is the contract."""
        repo = UploadChunkRepository(db)
        creator = uuid4()
        rows = await self._seed(repo, creator, 3)
        # Make the ordering unambiguous with explicit, distinct timestamps.
        base = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        for offset, row in enumerate(rows):
            row.created_at = base + datetime.timedelta(hours=offset)
        await repo.session.commit()

        result = await repo.list_by_creator(creator)

        assert [row.created_at for row in result] == sorted(
            (row.created_at for row in result), reverse=True
        )

    async def test_a_limit_larger_than_the_row_count_is_harmless(self, db):
        repo = UploadChunkRepository(db)
        creator = uuid4()
        await self._seed(repo, creator, 2)
        assert len(await repo.list_by_creator(creator, limit=1000)) == 2


# ---------------------------------------------------------------------------
# repositories.py:61-63 — received_indices.
# ---------------------------------------------------------------------------


class TestReceivedIndices:
    async def _session(self, repo) -> UploadSession:
        row = await repo.create(
            UploadSession(
                creator_id=uuid4(),
                filename="clip.mp4",
                mime="video/mp4",
                size_bytes=3072,
                chunk_size=1024,
                total_chunks=3,
                expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
            )
        )
        await repo.session.commit()
        return row

    async def test_only_the_recorded_indices_come_back_in_order(self, db):
        repo = UploadChunkRepository(db)
        session = await self._session(repo)
        # Insert out of order on purpose.
        for index in (2, 0, 1):
            await repo.add_chunk(UploadChunk(session_id=session.id, index=index, size_bytes=1024))
        await repo.session.commit()

        assert await repo.received_indices(session.id) == [0, 1, 2]

    async def test_a_gap_is_visible_as_a_gap(self, db):
        """Completeness is decided by comparing this list to range(total_chunks)."""
        repo = UploadChunkRepository(db)
        session = await self._session(repo)
        for index in (0, 2):
            await repo.add_chunk(UploadChunk(session_id=session.id, index=index, size_bytes=1024))
        await repo.session.commit()

        assert await repo.received_indices(session.id) == [0, 2]

    async def test_a_session_with_no_chunks_reports_nothing(self, db):
        repo = UploadChunkRepository(db)
        session = await self._session(repo)
        assert await repo.received_indices(session.id) == []

    async def test_another_sessions_chunks_are_not_leaked(self, db):
        repo = UploadChunkRepository(db)
        mine = await self._session(repo)
        other = await self._session(repo)
        await repo.add_chunk(UploadChunk(session_id=other.id, index=0, size_bytes=1024))
        await repo.add_chunk(UploadChunk(session_id=mine.id, index=7, size_bytes=1024))
        await repo.session.commit()

        assert await repo.received_indices(mine.id) == [7]
        assert await repo.received_indices(other.id) == [0]

    async def test_the_returned_values_are_bare_integers(self, db):
        """``row[0]`` unwrapping must yield ints, not Row objects."""
        repo = UploadChunkRepository(db)
        session = await self._session(repo)
        await repo.add_chunk(UploadChunk(session_id=session.id, index=1, size_bytes=1024))
        await repo.session.commit()

        (index,) = await repo.received_indices(session.id)
        assert index == 1
        assert isinstance(index, int)


# ---------------------------------------------------------------------------
# BUG PIN — services.py:256 and :346 compare an aware clock against a column
# that comes back naive.
# ---------------------------------------------------------------------------


class TestExpiryComparisonNeedsATzAwareTimestamp:
    """**Pin for an unfixed bug — do not "correct" this test.**

    ``UploadService.register_chunk`` (services.py:256) and
    ``complete_session`` (services.py:346) both do::

        if datetime.now(UTC) > session.expires_at:

    where ``session`` was just loaded by ``repo.get_for_update``. ``expires_at``
    is declared ``DateTime(timezone=True)`` but, per the repo-wide note in
    AGENTS.md, these service tables use ``TIMESTAMP WITHOUT TIME ZONE`` — and
    SQLite demonstrably returns a *naive* datetime. Comparing an aware
    ``datetime.now(UTC)`` with a naive value raises ``TypeError``, which
    FastAPI surfaces as a 500.

    The sibling orchestrator already defends against exactly this
    (``media-pipeline/app/services.py:326-327`` does
    ``if leased_at.tzinfo is None: leased_at = leased_at.replace(tzinfo=UTC)``);
    uploads-service does not.

    Consequence if the deployed column is indeed ``timestamp without time
    zone``: ``POST /api/v1/uploads/sessions/{id}/chunks`` and
    ``POST .../complete`` raise 500 for every request, so no upload can ever be
    registered or finalised. The unit tests miss it because
    ``test_upload_state_machine.py`` uses an in-memory ``FakeRepo`` where the
    aware datetime never round-trips through a driver.

    The assertions below describe what actually happens. They are expected to
    start failing the moment someone fixes the comparison.
    """

    async def test_a_naive_expiry_from_the_database_crashes_the_comparison(self, db, storage):
        """This is the current, unfixed behaviour."""
        publisher = FlakyPublisher()
        service = make_service(db, storage, publisher)

        session = UploadSession(
            creator_id=uuid4(),
            filename="clip.mp4",
            mime="video/mp4",
            size_bytes=1 * MIB,
            chunk_size=1 * MIB,
            total_chunks=1,
            expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
        )
        await service.repo.create(session)
        await db.commit()

        # Confirm the round trip really does drop the tzinfo.
        reloaded = await service.repo.get_for_update(session.id)
        assert (
            reloaded.expires_at.tzinfo is None
        ), "the driver returns a naive datetime for this column"

        with pytest.raises(TypeError, match="offset-naive and offset-aware"):
            await service.register_chunk(session_id=session.id, index=0)

    async def test_the_same_comparison_breaks_completion(self, db, storage):
        publisher = FlakyPublisher()
        service = make_service(db, storage, publisher)

        session = UploadSession(
            creator_id=uuid4(),
            filename="clip.mp4",
            mime="video/mp4",
            size_bytes=1 * MIB,
            chunk_size=1 * MIB,
            total_chunks=1,
            # Not COMPLETE and not ABORTED, so control reaches the expiry check
            # at services.py:346 rather than short-circuiting earlier.
            status=UploadSessionStatus.UPLOADING,
            expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
        )
        await service.repo.create(session)
        await db.commit()

        with pytest.raises(TypeError, match="offset-naive and offset-aware"):
            await service.complete_session(session.id)

    async def test_an_in_memory_repo_hides_the_bug(self, storage):
        """Why the existing suite is green: FakeRepo never loses the tzinfo.

        This is the contrast that makes the bug a coverage gap rather than a
        known-broken feature.
        """
        from tests.test_upload_state_machine import FakeRepo

        service = UploadService(repo=FakeRepo(), storage=storage, publisher=FlakyPublisher())
        session, uploads = await service.create_session(
            creator_id=uuid4(),
            filename="clip.mp4",
            mime="video/mp4",
            size_bytes=1 * MIB,
            chunk_size=1 * MIB,
        )
        assert session.expires_at.tzinfo is not None
        storage.upload_bytes(uploads[0].storage_key, b"x" * (1 * MIB), "video/mp4")

        # Works fine in memory — which is exactly why nobody noticed.
        await service.register_chunk(session_id=session.id, index=0)
        assert session.uploaded_chunks == 1


# ---------------------------------------------------------------------------
# repositories.py:61-63 — save() stamps updated_at.
# ---------------------------------------------------------------------------


class TestSaveStampsUpdatedAt:
    async def _session(self, repo) -> UploadSession:
        row = await repo.create(
            UploadSession(
                creator_id=uuid4(),
                filename="clip.mp4",
                mime="video/mp4",
                size_bytes=1024,
                chunk_size=1024,
                total_chunks=1,
                expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
            )
        )
        await repo.session.commit()
        return row

    async def test_save_advances_updated_at(self, db):
        repo = UploadChunkRepository(db)
        session = await self._session(repo)
        session.updated_at = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
        await db.commit()

        saved = await repo.save(session)

        assert saved.updated_at > datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
        assert saved.updated_at.tzinfo is not None, "the stamp is written tz-aware"

    async def test_save_persists_the_new_timestamp(self, db):
        """The stamp must be flushed, not merely set on the Python instance."""
        repo = UploadChunkRepository(db)
        session = await self._session(repo)
        session.updated_at = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
        await db.commit()

        await repo.save(session)
        await db.commit()
        # rollback() expires every identity-mapped row, so the next read must
        # come from SQLite rather than the in-memory instance.
        await db.rollback()

        reloaded = await repo.get(session.id)
        assert reloaded.updated_at.year > 2000

    async def test_save_returns_the_same_instance(self, db):
        repo = UploadChunkRepository(db)
        session = await self._session(repo)
        assert await repo.save(session) is session

    async def test_save_does_not_touch_the_expiry(self, db):
        """updated_at moving must not extend the session's lifetime."""
        repo = UploadChunkRepository(db)
        session = await self._session(repo)
        expiry = session.expires_at

        await repo.save(session)

        assert session.expires_at == expiry
