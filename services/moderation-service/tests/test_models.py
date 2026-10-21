"""Behavioural tests for the moderation models.

These used to be ~50 assertions that assigned a literal to a plain Python
attribute on a never-persisted instance and read it back. Nothing but Python's
attribute protocol ran: defaulting ``DMCATakedown.reason`` and
``.content_id`` to attacker-controlled values left this file green, because no
``nullable=False`` was ever enforced, no column default ever applied, and no
index was ever created.

The tests below run against a real aiosqlite database, so they exercise the
three things a model actually contributes: column defaults applied on INSERT,
NOT NULL / NULL-ability as declared, and the indexes the queue queries depend
on.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.dmca import DMCATakedown
from app.models.review_queue import ReviewModeration


@pytest_asyncio.fixture
async def session():
    """One database for both model modules.

    ``DMCATakedown`` and ``ReviewModeration`` each declare their own
    declarative ``Base``, so both tables are created explicitly.
    """
    from app.models.dmca import Base as DmcaBase
    from app.models.review_queue import Base as ReviewBase

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(DmcaBase.metadata.create_all)
        await conn.run_sync(ReviewBase.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


def _dmca(**overrides) -> DMCATakedown:
    defaults = dict(
        content_id=uuid4(),
        reporter_email="reporter@example.com",
        reason="copyright infringement",
    )
    defaults.update(overrides)
    return DMCATakedown(**defaults)


def _review(**overrides) -> ReviewModeration:
    defaults = dict(review_id=uuid4(), content_id=uuid4())
    defaults.update(overrides)
    return ReviewModeration(**defaults)


# ---------------------------------------------------------------------------
# Column defaults: applied by the INSERT, not by __init__
# ---------------------------------------------------------------------------


class TestColumnDefaults:
    async def test_a_new_takedown_is_pending_with_zero_repeat_infringers(self, session):
        session.add(_dmca())
        await session.commit()

        row = (await session.execute(select(DMCATakedown))).scalar_one()
        assert row.status == "pending"
        assert row.repeat_infringer_count == 0
        assert row.counter_notice is None

    async def test_a_new_review_is_pending_and_not_auto_flagged(self, session):
        session.add(_review())
        await session.commit()

        row = (await session.execute(select(ReviewModeration))).scalar_one()
        assert row.status == "pending"
        assert row.auto_flagged is False
        assert row.moderator_id is None
        assert row.reason is None

    async def test_timestamps_are_populated_on_insert(self, session):
        before = datetime.now(UTC)
        session.add(_dmca())
        await session.commit()

        row = (await session.execute(select(DMCATakedown))).scalar_one()
        assert isinstance(row.created_at, datetime)
        assert isinstance(row.updated_at, datetime)
        # SQLite drops tzinfo; compare against the naive-UTC representation.
        created = row.created_at.replace(tzinfo=UTC)
        assert created >= before.replace(microsecond=0)

    async def test_updated_at_advances_on_a_counter_notice(self, session):
        row = _dmca()
        session.add(row)
        await session.commit()
        original = row.updated_at

        row.counter_notice = "fair use"
        row.status = "countered"
        await session.commit()

        assert row.updated_at != original


# ---------------------------------------------------------------------------
# NOT NULL / NULL-ability, which __init__ cannot enforce
# ---------------------------------------------------------------------------


class TestNullability:
    @pytest.mark.parametrize("field", ["content_id", "reporter_email", "reason"])
    async def test_a_takedown_without_a_required_field_is_rejected(self, session, field):
        session.add(_dmca(**{field: None}))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    @pytest.mark.parametrize("field", ["review_id", "content_id"])
    async def test_a_review_without_a_required_field_is_rejected(self, session, field):
        session.add(_review(**{field: None}))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async def test_counter_notice_is_nullable(self, session):
        # counter_notice is the only DMCA field that may be absent at intake;
        # making it NOT NULL would break intake for every untargeted takedown.
        session.add(_dmca(counter_notice=None))
        await session.commit()
        assert (await session.execute(select(DMCATakedown))).scalar_one().counter_notice is None


# ---------------------------------------------------------------------------
# Repeat-infringer accounting: the only mutable counter on the row
# ---------------------------------------------------------------------------


class TestRepeatInfringerCount:
    async def test_it_persists_across_sessions_style_rereads(self, session):
        row = _dmca()
        session.add(row)
        await session.commit()

        row.repeat_infringer_count += 1
        await session.commit()

        reloaded = (await session.execute(select(DMCATakedown))).scalar_one()
        assert reloaded.repeat_infringer_count == 1


class TestPersistedOverrides:
    """Supplied values must survive the round-trip, not just the instance."""

    async def test_an_auto_flagged_review_is_read_back_from_the_row(self, session):
        # The Boolean column, not a Python attribute: this replaces the old
        # `ReviewModeration(auto_flagged=True); assert rec.auto_flagged is True`,
        # which never touched the database.
        session.add(_review(auto_flagged=True))
        await session.commit()

        row = (await session.execute(select(ReviewModeration))).scalar_one()
        assert row.auto_flagged is True

    async def test_an_explicit_status_overrides_the_column_default(self, session):
        session.add(_dmca(status="rejected"))
        await session.commit()

        row = (await session.execute(select(DMCATakedown))).scalar_one()
        assert row.status == "rejected"


# ---------------------------------------------------------------------------
# Indexes: the queue and takedown lookups depend on these existing
# ---------------------------------------------------------------------------


class TestIndexes:
    def _index_columns(self, table_name: str) -> set[tuple[str, ...]]:
        table = DMCATakedown.metadata.tables[table_name]
        return {tuple(sorted(col.name for col in index.columns)) for index in table.indexes}

    def test_dmca_indexes_content_and_status(self):
        columns = self._index_columns("dmca_takedowns")
        assert ("content_id",) in columns
        assert ("status",) in columns

    def test_the_review_queue_status_index_exists(self):
        table = ReviewModeration.metadata.tables["review_moderation"]
        columns = {tuple(sorted(col.name for col in index.columns)) for index in table.indexes}
        assert ("status",) in columns


# ---------------------------------------------------------------------------
# Status column width: a value longer than String(20) is truncated or rejected
# by Postgres, which is a runtime error rather than a silent write.
# ---------------------------------------------------------------------------


class TestStatusColumnWidth:
    def test_status_is_declared_with_a_twenty_character_limit(self):
        for column in (DMCATakedown.__table__.c.status, ReviewModeration.__table__.c.status):
            assert column.type.length == 20

    async def test_the_declaration_is_what_postgres_enforces(self, session):
        # SQLite does not enforce VARCHAR length, so this asserts the DDL the
        # Postgres deployment runs rather than pretending SQLite caught it.
        from sqlalchemy.schema import CreateTable
        from sqlalchemy.dialects import postgresql

        ddl = str(CreateTable(DMCATakedown.__table__).compile(dialect=postgresql.dialect()))
        assert "VARCHAR(20)" in ddl
