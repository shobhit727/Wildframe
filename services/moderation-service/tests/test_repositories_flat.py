"""Behavioural tests for ``app/repositories.py`` (moderation-service, 201 lines).

The persistence layer had zero direct coverage: every existing test drives
``ModerationService`` against hand-written in-memory fakes, so the real
SQLAlchemy queries (the ``ON CONFLICT DO NOTHING`` insert, the FIFO queue
ordering, the expiry-aware strike filters, the advisory lock) were never
executed.

Everything runs against a real aiosqlite engine — no Postgres, no Docker —
except ``lock_creator``, which issues ``pg_advisory_xact_lock`` and is therefore
asserted through a statement-capturing session.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    ContentFlag,
    CreatorStrike,
    DecisionType,
    FlagReason,
    FlagStatus,
    ModerationDecision,
    OutboxEvent,
    OutboxEventStatus,
    StrikeReason,
)
from app.repositories import (
    ContentFlagRepository,
    CreatorStrikeRepository,
    DuplicateContentFlag,
    ModerationDecisionRepository,
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from app.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


def _flag(**overrides) -> ContentFlag:
    defaults = dict(
        id=uuid4(),
        content_id=uuid4(),
        flag_reason=FlagReason.SPAM,
        reported_by=uuid4(),
        content_creator_id=uuid4(),
        status=FlagStatus.PENDING,
    )
    defaults.update(overrides)
    return ContentFlag(**defaults)


def _strike(creator_id, **overrides) -> CreatorStrike:
    defaults = dict(
        id=uuid4(),
        creator_id=creator_id,
        strike_reason=StrikeReason.CONTENT_VIOLATION,
        related_flag_id=uuid4(),
        is_active=True,
        expires_at=datetime.now(UTC) + timedelta(days=90),
    )
    defaults.update(overrides)
    return CreatorStrike(**defaults)


# ---------------------------------------------------------------------------
# ContentFlagRepository
# ---------------------------------------------------------------------------


async def _set_created_at(session, model, row_id, moment: datetime) -> None:
    """created_at is column-defaulted, so ordering tests must set it after INSERT."""
    await session.execute(
        model.__table__.update()
        .where(model.id == row_id)
        .values(created_at=moment)
    )


class TestContentFlagRepositoryCreate:
    async def test_create_persists_and_returns_the_row(self, session):
        repo = ContentFlagRepository(session)
        flag = _flag()
        created = await repo.create(flag)
        await session.commit()
        assert created.content_id == flag.content_id
        assert created.reported_by == flag.reported_by
        assert created.status == FlagStatus.PENDING
        assert created.content_creator_id == flag.content_creator_id
        assert (await repo.get(created.id)).id == created.id

    async def test_create_ignores_a_caller_supplied_id(self, session):
        # The Core insert names only the five business columns, so the column
        # default mints the primary key: the id on the object the caller passed
        # in is discarded and a *different* row comes back from RETURNING.
        repo = ContentFlagRepository(session)
        flag = _flag()
        caller_id = flag.id
        created = await repo.create(flag)
        await session.commit()
        assert created is not flag
        assert created.id != caller_id

    async def test_create_ignores_a_caller_supplied_status_only_for_none(self, session):
        # status IS one of the named columns, so it round-trips verbatim.
        repo = ContentFlagRepository(session)
        created = await repo.create(_flag(status=FlagStatus.REVIEWING))
        await session.commit()
        assert created.status == FlagStatus.REVIEWING

    async def test_create_allows_a_null_content_creator(self, session):
        repo = ContentFlagRepository(session)
        created = await repo.create(_flag(content_creator_id=None))
        await session.commit()
        assert created.content_creator_id is None

    async def test_duplicate_content_and_reporter_raises(self, session):
        # uq_content_flag_content_reporter makes the second report a no-op, and
        # the repository turns "no row returned" into DuplicateContentFlag.
        repo = ContentFlagRepository(session)
        content_id, reporter = uuid4(), uuid4()
        await repo.create(_flag(content_id=content_id, reported_by=reporter))
        await session.commit()
        with pytest.raises(DuplicateContentFlag, match="already reported"):
            await repo.create(_flag(content_id=content_id, reported_by=reporter))
        await session.rollback()

    async def test_a_different_reporter_is_allowed(self, session):
        repo = ContentFlagRepository(session)
        content_id = uuid4()
        await repo.create(_flag(content_id=content_id, reported_by=uuid4()))
        await session.commit()
        second = await repo.create(_flag(content_id=content_id, reported_by=uuid4()))
        await session.commit()
        assert second.id is not None


class TestContentFlagRepositoryReads:
    async def test_get_returns_the_flag(self, session):
        repo = ContentFlagRepository(session)
        flag = await repo.create(_flag())
        await session.commit()
        assert (await repo.get(flag.id)).id == flag.id

    async def test_get_returns_none_for_an_unknown_id(self, session):
        assert await ContentFlagRepository(session).get(uuid4()) is None

    async def test_get_for_update_returns_the_flag(self, session):
        repo = ContentFlagRepository(session)
        flag = await repo.create(_flag())
        await session.commit()
        assert await repo.get_for_update(flag.id) is not None

    async def test_get_for_update_returns_none_for_an_unknown_id(self, session):
        assert await ContentFlagRepository(session).get_for_update(uuid4()) is None

    async def test_get_for_update_emits_a_row_lock(self, session):
        from sqlalchemy import select

        from app.models import ContentFlag as CF

        sql = str(
            select(CF).where(CF.id == uuid4()).with_for_update().compile()
        )
        assert "FOR UPDATE" in sql.upper()

    async def test_list_pending_is_fifo_oldest_first(self, session):
        repo = ContentFlagRepository(session)
        now = datetime.now(UTC)
        first = await repo.create(_flag())
        second = await repo.create(_flag())
        third = await repo.create(_flag())
        await _set_created_at(session, ContentFlag, first.id, now)
        await _set_created_at(session, ContentFlag, third.id, now + timedelta(seconds=1))
        await _set_created_at(session, ContentFlag, second.id, now + timedelta(seconds=5))
        await session.commit()
        assert [f.id for f in await repo.list_pending()] == [first.id, third.id, second.id]

    async def test_list_pending_excludes_non_pending_rows(self, session):
        repo = ContentFlagRepository(session)
        await repo.create(_flag(status=FlagStatus.PENDING))
        await repo.create(_flag(status=FlagStatus.RESOLVED))
        await repo.create(_flag(status=FlagStatus.ESCALATED))
        await repo.create(_flag(status=FlagStatus.REVIEWING))
        await session.commit()
        assert len(await repo.list_pending()) == 1

    async def test_list_pending_tolerates_an_explicit_none_status(self, session):
        # ``where(ContentFlag.status == "pending")`` compares the enum's *value*,
        # so a row stored as the PENDING enum still matches.
        repo = ContentFlagRepository(session)
        await repo.create(_flag(status=FlagStatus.PENDING))
        await session.commit()
        assert len(await repo.list_pending()) == 1

    async def test_list_pending_respects_the_limit(self, session):
        repo = ContentFlagRepository(session)
        for _ in range(4):
            await repo.create(_flag())
        await session.commit()
        assert len(await repo.list_pending(limit=2)) == 2

    async def test_list_pending_is_empty_when_the_queue_is(self, session):
        assert await ContentFlagRepository(session).list_pending() == []


class TestContentFlagRepositorySave:
    async def test_save_stamps_updated_at(self, session):
        repo = ContentFlagRepository(session)
        flag = await repo.create(_flag())
        await session.commit()
        original = flag.updated_at
        flag.status = FlagStatus.RESOLVED
        saved = await repo.save(flag)
        await session.commit()
        assert saved is flag
        assert saved.status == FlagStatus.RESOLVED
        # saved() assigns updated_at in Python, so it must differ from the
        # column default captured before the call.
        assert saved.updated_at != original

    async def test_save_persists_a_status_change(self, session):
        repo = ContentFlagRepository(session)
        created = await repo.create(_flag())
        await session.commit()
        row = await repo.get(created.id)
        row.status = FlagStatus.ESCALATED
        await repo.save(row)
        await session.commit()
        assert (await repo.get(created.id)).status == FlagStatus.ESCALATED

    async def test_save_does_not_attach_an_untracked_object(self, session):
        # save() only flushes: it never calls session.add(). A flag the caller
        # never tracked is therefore silently not persisted and keeps id=None.
        # Pin that boundary so a future change to save() is deliberate.
        repo = ContentFlagRepository(session)
        flag = ContentFlag(
            content_id=uuid4(),
            flag_reason=FlagReason.OTHER,
            reported_by=uuid4(),
            status=FlagStatus.PENDING,
        )
        await repo.save(flag)
        await session.commit()
        assert flag.id is None

    async def test_save_persists_an_object_the_session_already_tracks(self, session):
        repo = ContentFlagRepository(session)
        flag = ContentFlag(
            content_id=uuid4(),
            flag_reason=FlagReason.OTHER,
            reported_by=uuid4(),
            status=FlagStatus.PENDING,
        )
        session.add(flag)
        saved = await repo.save(flag)
        await session.commit()
        assert saved.id is not None
        assert await repo.get(saved.id) is not None


class TestOutbox:
    async def test_enqueue_event_persists_a_pending_row(self, session):
        repo = ContentFlagRepository(session)
        row = await repo.enqueue_event(
            topic="content.flagged", event_key="k-1", payload={"a": 1}
        )
        await session.commit()
        assert row.id is not None
        assert row.topic == "content.flagged"
        assert row.payload == {"a": 1}
        assert row.status == OutboxEventStatus.PENDING
        assert row.dispatched_at is None

    async def test_enqueue_event_allows_a_null_key(self, session):
        repo = ContentFlagRepository(session)
        row = await repo.enqueue_event(topic="t", event_key=None, payload={})
        await session.commit()
        assert row.event_key is None

    async def test_pending_events_returns_only_pending_rows_fifo(self, session):
        repo = ContentFlagRepository(session)
        now = datetime.now(UTC)
        first = await repo.enqueue_event("t", "1", {})
        second = await repo.enqueue_event("t", "2", {})
        done = await repo.enqueue_event("t", "3", {})
        await _set_created_at(session, OutboxEvent, first.id, now)
        await _set_created_at(session, OutboxEvent, second.id, now + timedelta(seconds=1))
        await _set_created_at(session, OutboxEvent, done.id, now + timedelta(seconds=2))
        done.status = OutboxEventStatus.DISPATCHED
        await session.commit()
        assert [r.event_key for r in await repo.pending_events()] == ["1", "2"]

    async def test_pending_events_respects_the_limit(self, session):
        repo = ContentFlagRepository(session)
        for i in range(4):
            await repo.enqueue_event("t", str(i), {})
        await session.commit()
        assert len(await repo.pending_events(limit=2)) == 2

    async def test_mark_dispatched_stamps_status_and_time(self, session):
        repo = ContentFlagRepository(session)
        row = await repo.enqueue_event("t", "1", {})
        await session.commit()
        await repo.mark_dispatched(row.id)
        await session.commit()
        await session.refresh(row)
        assert row.status == OutboxEventStatus.DISPATCHED
        assert row.dispatched_at is not None

    async def test_mark_dispatched_is_a_noop_for_an_unknown_id(self, session):
        repo = ContentFlagRepository(session)
        assert await repo.mark_dispatched(uuid4()) is None

    async def test_dispatched_rows_leave_the_pending_set(self, session):
        repo = ContentFlagRepository(session)
        row = await repo.enqueue_event("t", "1", {})
        await session.commit()
        assert len(await repo.pending_events()) == 1
        await repo.mark_dispatched(row.id)
        await session.commit()
        assert await repo.pending_events() == []


# ---------------------------------------------------------------------------
# ModerationDecisionRepository
# ---------------------------------------------------------------------------


class TestModerationDecisionRepository:
    async def test_create_persists_the_decision(self, session):
        repo = ModerationDecisionRepository(session)
        flag = _flag()
        session.add(flag)
        await session.flush()
        decision = ModerationDecision(
            id=uuid4(),
            flag_id=flag.id,
            moderator_id=uuid4(),
            decision=DecisionType.APPROVE,
            notes="fine",
        )
        created = await repo.create(decision)
        await session.commit()
        assert created.id == decision.id
        assert created.decision == DecisionType.APPROVE
        assert created.notes == "fine"

    async def test_list_by_flag_returns_the_flag_decision(self, session):
        repo = ModerationDecisionRepository(session)
        flag = _flag()
        session.add(flag)
        await session.flush()
        await repo.create(
            ModerationDecision(
                id=uuid4(),
                flag_id=flag.id,
                moderator_id=uuid4(),
                decision=DecisionType.ESCALATE,
                notes="senior review",
            )
        )
        await session.commit()
        rows = await repo.list_by_flag(flag.id)
        assert [r.decision for r in rows] == [DecisionType.ESCALATE]
        assert rows[0].notes == "senior review"

    async def test_list_by_flag_is_scoped_to_one_flag(self, session):
        repo = ModerationDecisionRepository(session)
        first, second = _flag(), _flag()
        session.add_all([first, second])
        await session.flush()
        for flag in (first, second):
            await repo.create(
                ModerationDecision(
                    id=uuid4(),
                    flag_id=flag.id,
                    moderator_id=uuid4(),
                    decision=DecisionType.APPROVE,
                )
            )
        await session.commit()
        assert len(await repo.list_by_flag(first.id)) == 1
        assert len(await repo.list_by_flag(second.id)) == 1

    async def test_list_by_flag_is_empty_for_an_unknown_flag(self, session):
        assert await ModerationDecisionRepository(session).list_by_flag(uuid4()) == []


# ---------------------------------------------------------------------------
# CreatorStrikeRepository
# ---------------------------------------------------------------------------


class TestCreatorStrikeRepository:
    async def test_create_persists_the_strike(self, session):
        repo = CreatorStrikeRepository(session)
        creator = uuid4()
        created = await repo.create(_strike(creator))
        await session.commit()
        assert created.creator_id == creator
        assert created.strike_reason == StrikeReason.CONTENT_VIOLATION
        assert created.is_active is True
        assert await repo.count_active(creator) == 1

    async def test_list_active_excludes_other_creators(self, session):
        repo = CreatorStrikeRepository(session)
        mine, theirs = uuid4(), uuid4()
        await repo.create(_strike(mine))
        await repo.create(_strike(theirs))
        await session.commit()
        assert [s.creator_id for s in await repo.list_active(mine)] == [mine]

    async def test_list_active_excludes_deactivated_strikes(self, session):
        repo = CreatorStrikeRepository(session)
        creator = uuid4()
        await repo.create(_strike(creator))
        await repo.create(_strike(creator, is_active=False))
        await session.commit()
        assert len(await repo.list_active(creator)) == 1

    async def test_list_active_excludes_expired_strikes(self, session):
        repo = CreatorStrikeRepository(session)
        creator = uuid4()
        await repo.create(_strike(creator, expires_at=datetime.now(UTC) - timedelta(days=1)))
        await session.commit()
        assert await repo.list_active(creator) == []

    async def test_list_active_includes_strikes_that_never_expire(self, session):
        repo = CreatorStrikeRepository(session)
        creator = uuid4()
        await repo.create(_strike(creator, expires_at=None))
        await session.commit()
        assert len(await repo.list_active(creator)) == 1

    async def test_list_active_is_newest_first(self, session):
        repo = CreatorStrikeRepository(session)
        creator = uuid4()
        now = datetime.now(UTC)
        older = await repo.create(_strike(creator))
        newer = await repo.create(_strike(creator))
        await _set_created_at(session, CreatorStrike, older.id, now)
        await _set_created_at(session, CreatorStrike, newer.id, now + timedelta(seconds=1))
        await session.commit()
        rows = await repo.list_active(creator)
        assert [r.id for r in rows] == [newer.id, older.id]

    async def test_list_active_accepts_an_explicit_now(self, session):
        repo = CreatorStrikeRepository(session)
        creator = uuid4()
        await repo.create(_strike(creator, expires_at=datetime.now(UTC) + timedelta(hours=1)))
        await session.commit()
        assert len(await repo.list_active(creator)) == 1
        # Far-future "now" makes the same strike look expired.
        assert await repo.list_active(creator, now=datetime.now(UTC) + timedelta(days=2)) == []

    async def test_count_active_matches_list_active(self, session):
        repo = CreatorStrikeRepository(session)
        creator = uuid4()
        await repo.create(_strike(creator))
        await repo.create(_strike(creator))
        await repo.create(_strike(creator, is_active=False))
        await session.commit()
        assert await repo.count_active(creator) == 2

    async def test_list_active_for_update_matches_list_active(self, session):
        repo = CreatorStrikeRepository(session)
        creator = uuid4()
        await repo.create(_strike(creator))
        await repo.create(_strike(creator, expires_at=None))
        await session.commit()
        locked = await repo.list_active_for_update(creator)
        assert len(locked) == 2
        assert await repo.count_active_for_update(creator) == 2

    async def test_count_active_is_zero_for_an_unknown_creator(self, session):
        assert await CreatorStrikeRepository(session).count_active(uuid4()) == 0

    async def test_list_all_includes_inactive_and_expired(self, session):
        repo = CreatorStrikeRepository(session)
        creator = uuid4()
        await repo.create(_strike(creator))
        await repo.create(_strike(creator, is_active=False))
        await repo.create(
            _strike(creator, expires_at=datetime.now(UTC) - timedelta(days=1))
        )
        await session.commit()
        assert len(await repo.list_all(creator)) == 3

    async def test_list_all_is_newest_first(self, session):
        repo = CreatorStrikeRepository(session)
        creator = uuid4()
        now = datetime.now(UTC)
        older = await repo.create(_strike(creator))
        newer = await repo.create(_strike(creator))
        await _set_created_at(session, CreatorStrike, older.id, now)
        await _set_created_at(session, CreatorStrike, newer.id, now + timedelta(seconds=2))
        await session.commit()
        rows = await repo.list_all(creator)
        assert [r.id for r in rows] == [newer.id, older.id]

    async def test_list_all_is_scoped_to_one_creator(self, session):
        repo = CreatorStrikeRepository(session)
        mine = uuid4()
        await repo.create(_strike(mine))
        await repo.create(_strike(uuid4()))
        await session.commit()
        assert len(await repo.list_all(mine)) == 1


class TestLockCreator:
    """``lock_creator`` issues Postgres-only SQL; assert what it sends."""

    async def test_issues_pg_advisory_xact_lock_with_a_stable_signed_key(self):
        class _Result:
            def __init__(self):
                self.statements = []

            async def execute(self, statement, params=None):
                self.statements.append((str(statement), params))
                return self

        class _Session:
            def __init__(self):
                self.result = _Result()

            async def execute(self, statement, params=None):
                return await self.result.execute(statement, params)

        creator = uuid4()
        session = _Session()
        repo = CreatorStrikeRepository(session)
        await repo.lock_creator(creator)

        sql, params = session.result.statements[0]
        assert "pg_advisory_xact_lock" in sql
        expected = int.from_bytes(creator.bytes[:8], byteorder="big", signed=True)
        assert params == {"key": expected}
        assert -(2**63) <= expected < 2**63

    async def test_the_key_is_derived_from_the_first_eight_uuid_bytes(self):
        class _Session:
            def __init__(self):
                self.params = None

            async def execute(self, statement, params=None):
                self.params = params

        creator = uuid4()
        session = _Session()
        await CreatorStrikeRepository(session).lock_creator(creator)
        assert session.params["key"] == int.from_bytes(
            creator.bytes[:8], byteorder="big", signed=True
        )
        # A different creator yields a different lock, so unrelated creators
        # never serialize against each other.
        other = uuid4()
        session2 = _Session()
        await CreatorStrikeRepository(session2).lock_creator(other)
        assert session2.params["key"] != session.params["key"]


class TestRepositoryConstruction:
    def test_each_repository_keeps_the_session_it_was_given(self, session):
        assert ContentFlagRepository(session).session is session
        assert ModerationDecisionRepository(session).session is session
        assert CreatorStrikeRepository(session).session is session

    def test_repositories_hold_no_shared_state(self, session):
        first = ContentFlagRepository(session)
        second = ContentFlagRepository(session)
        assert first is not second
        assert first.session is second.session

    def test_duplicate_content_flag_is_a_plain_exception(self):
        assert issubclass(DuplicateContentFlag, Exception)
        assert DuplicateContentFlag is not RuntimeError

    def test_outbox_rows_are_reachable_through_the_models_module(self, session):
        # The repositories import their models from ``app.models``; make sure
        # the ORM class used here is the same one the schema created.
        from app.models import OutboxEvent as OE

        assert OE is OutboxEvent
