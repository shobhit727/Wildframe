"""SQL-level coverage for the three uncovered ``PipelineJobRepository`` queries.

``lock()``, ``get_by_idempotency_key()`` and ``list_stale()`` build
PostgreSQL-specific statements (``FOR UPDATE SKIP LOCKED``, JSONB/UUID columns
from the postgresql dialect), so they cannot be executed against the in-memory
SQLite the rest of the suite uses. Instead each query is **compiled against the
PostgreSQL dialect** and asserted on: the emitted SQL text, the bound parameter
values, and the row mapping. Every assertion here fails if the filter, the
locking clause or the argument threading regresses.

Covered: ``repositories.py:34-40`` (lock), ``49-52`` (get_by_idempotency_key),
``69-77`` (list_stale).
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import ClauseElement

from app.models import PipelineJobStatus
from app.repositories import PipelineJobRepository

pytestmark = pytest.mark.asyncio


class RecordingSession:
    """Captures the statement handed to ``execute`` and replays a canned result.

    Only the pieces the repository actually uses are implemented, so an
    unexpected call (e.g. a second query) surfaces as an AttributeError rather
    than being silently absorbed.
    """

    def __init__(self, *, scalar=None, rows=()):
        self._scalar = scalar
        self._rows = list(rows)
        self.statements: list[ClauseElement] = []
        self.flushed = 0

    async def execute(self, statement: ClauseElement):
        self.statements.append(statement)
        return _Result(self._scalar, self._rows)

    async def flush(self) -> None:
        self.flushed += 1


class _Result:
    def __init__(self, scalar, rows):
        self._scalar = scalar
        self._rows = rows

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


def compile_pg(statement: ClauseElement) -> str:
    """Render the statement exactly as it would be sent to PostgreSQL."""
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def bound_params(statement: ClauseElement) -> dict:
    return dict(statement.compile(dialect=postgresql.dialect()).params)


def where_of(statement: ClauseElement) -> str:
    """Return just the WHERE clause.

    The SELECT projection already lists every column (including ``status``), so
    asserting on the whole statement cannot distinguish a filter from a
    projection. Only the WHERE tail tells us whether a predicate was applied.
    """
    sql = compile_pg(statement)
    return sql.split("WHERE", 1)[1] if "WHERE" in sql else ""


def parameterised(statement: ClauseElement) -> str:
    """Render the statement with bound params left as placeholders."""
    return str(statement.compile(dialect=postgresql.dialect()))


class TestLock:
    """``lock()`` is the lease primitive: SELECT ... FOR UPDATE SKIP LOCKED."""

    async def test_lock_takes_a_skip_locked_row_lock_and_returns_the_job(self):
        job_id = uuid4()
        job = object()
        session = RecordingSession(scalar=job)

        assert await PipelineJobRepository(session).lock(job_id) is job

        sql = compile_pg(session.statements[0])
        assert "FOR UPDATE SKIP LOCKED" in sql
        assert f"pipeline_jobs.id = '{job_id}'" in sql

    async def test_lock_refreshes_any_cached_column_state(self):
        """``populate_existing`` must be set or a resumed worker reads stale columns."""
        session = RecordingSession(scalar=object())
        await PipelineJobRepository(session).lock(uuid4())
        options = session.statements[0].get_execution_options()
        assert options["populate_existing"] is True

    async def test_lock_returns_none_when_the_row_is_gone(self):
        session = RecordingSession(scalar=None)
        assert await PipelineJobRepository(session).lock(uuid4()) is None

    async def test_lock_and_get_differ_in_the_locking_clause(self):
        """``get()`` must stay lock-free; only ``lock()`` may block other workers."""
        locked = RecordingSession()
        plain = RecordingSession()
        await PipelineJobRepository(locked).lock(uuid4())
        await PipelineJobRepository(plain).get(uuid4())
        assert "FOR UPDATE" in compile_pg(locked.statements[0])
        assert "FOR UPDATE" not in compile_pg(plain.statements[0])


class TestGetByIdempotencyKey:
    async def test_it_filters_on_the_supplied_key(self):
        key = "upload-abc-123"
        job = object()
        session = RecordingSession(scalar=job)

        assert await PipelineJobRepository(session).get_by_idempotency_key(key) is job

        sql = compile_pg(session.statements[0])
        assert "idempotency_key" in sql
        assert f"= '{key}'" in sql

    async def test_an_unknown_key_resolves_to_none(self):
        session = RecordingSession(scalar=None)
        assert await PipelineJobRepository(session).get_by_idempotency_key("nope") is None

    async def test_keys_are_bound_as_parameters_not_interpolated(self):
        """A quote-bearing key must survive as data, never as statement text."""
        session = RecordingSession()
        hostile = "k'); DROP TABLE pipeline_jobs;--"
        await PipelineJobRepository(session).get_by_idempotency_key(hostile)

        statement = session.statements[0]
        template = parameterised(statement)
        # The SQL text carries a placeholder, not the caller's string ...
        assert hostile not in template
        assert "idempotency_key = " in template
        assert "%(" in template, "the value must be a bound parameter, not inlined"
        # ... and the value arrives through the parameter map.
        assert hostile in bound_params(statement).values()


class TestListStale:
    """The stale-lease sweeper's query: leased, and the lease has expired."""

    async def test_it_requires_a_lease_and_an_expired_lease_timestamp(self):
        cutoff = datetime(2026, 3, 1, 12, 0, 0)
        session = RecordingSession(rows=[])

        assert await PipelineJobRepository(session).list_stale(cutoff) == []

        sql = compile_pg(session.statements[0])
        assert "leased_at IS NOT NULL" in sql
        assert f"leased_at < '{cutoff}'" in sql

    async def test_it_claims_the_stale_rows_with_skip_locked(self):
        """Two workers must never claim the same stale job."""
        session = RecordingSession(rows=[])
        await PipelineJobRepository(session).list_stale(datetime.now(UTC))
        assert "FOR UPDATE SKIP LOCKED" in compile_pg(session.statements[0])

    async def test_a_status_filter_is_omitted_when_not_requested(self):
        session = RecordingSession(rows=[])
        await PipelineJobRepository(session).list_stale(datetime.now(UTC))
        assert "status" not in where_of(session.statements[0])

    async def test_a_status_filter_is_added_when_requested(self):
        session = RecordingSession(rows=[])
        await PipelineJobRepository(session).list_stale(
            datetime.now(UTC), status=PipelineJobStatus.RUNNING
        )
        where = where_of(session.statements[0])
        assert "status" in where
        assert "RUNNING" in where
        # The status clause must not displace the lease predicates.
        assert "leased_at IS NOT NULL" in where
        assert "leased_at <" in where
        assert "FOR UPDATE SKIP LOCKED" in compile_pg(session.statements[0])

    async def test_a_settled_status_filter_really_is_threaded_through(self):
        """The sweeper only ever asks for RUNNING; the repo must not second-guess it."""
        session = RecordingSession(rows=[])
        await PipelineJobRepository(session).list_stale(
            datetime.now(UTC), status=PipelineJobStatus.COMPLETED
        )
        assert "COMPLETED" in where_of(session.statements[0])

    async def test_the_returned_rows_are_passed_through_in_order(self):
        rows = [object(), object(), object()]
        session = RecordingSession(rows=rows)
        result = await PipelineJobRepository(session).list_stale(datetime.now(UTC))
        assert result == rows

    async def test_an_empty_sweep_is_an_empty_list_not_none(self):
        session = RecordingSession(rows=[])
        result = await PipelineJobRepository(session).list_stale(datetime.now(UTC))
        assert result == [] and result is not None

    async def test_the_cutoff_is_threaded_through_untouched(self):
        """The sweeper computes ``now - lease_seconds``; we must not re-derive it."""
        cutoff = datetime.now(UTC) - timedelta(seconds=42)
        session = RecordingSession(rows=[])
        await PipelineJobRepository(session).list_stale(cutoff)
        assert cutoff in bound_params(session.statements[0]).values()
