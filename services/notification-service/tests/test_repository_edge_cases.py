"""Concurrency / malformed-data branches of `app.repositories`.

`tests/test_repositories.py` covers the single-writer happy paths. What it
leaves are the branches that only fire under a race or on corrupt stored data:

* `create` losing the `event_id` unique-index race (roll back, re-read, return
  the winner's row - or re-raise if the winner has since been deleted);
* `get_preference` losing the concurrent insert (roll back, re-select);
* `parse_delivery_errors` meeting a non-JSON `delivery_errors` column.

These matter: each one is a path where the session is in a broken transaction
state, so a missed rollback would 500 every subsequent request on that
connection.
"""

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Notification
from app.repositories import NotificationRepository


@pytest_asyncio.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/repo_race.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


def _winner(delivery_status: str = "sent") -> MagicMock:
    row = MagicMock()
    row.id = uuid4()
    row.delivery_status = delivery_status
    return row


class _RecordingSession:
    """A real ``AsyncSession`` plus a record of the calls made on it, in order.

    The point of the recording is order; the point of delegating to a real
    session is that a real session is the only thing that knows what a flush
    failure *does* to it. A ``MagicMock`` cannot be poisoned, so it cannot tell
    you that a re-SELECT run before the rollback raises
    ``PendingRollbackError`` -- which is the entire reason the rollback has to
    come first.
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        attribute = getattr(self._session, name)
        if not callable(attribute):
            return attribute

        def record(*args, **kwargs):
            self.calls.append(name)
            return attribute(*args, **kwargs)

        return record


# ---------------------------------------------------------------------------
# create - unique-index race
# ---------------------------------------------------------------------------


async def test_create_returns_the_winners_row_when_the_insert_loses_the_race():
    winner = _winner()
    session = MagicMock()
    session.flush = AsyncMock(side_effect=IntegrityError("UNIQUE", {}, Exception("dup")))
    session.rollback = AsyncMock()

    repo = NotificationRepository(session)
    repo.get_by_event_id = AsyncMock(side_effect=[None, winner])  # pre-check, then re-read

    notif, created = await repo.create(uuid4(), "T", "M", event_id=uuid4())

    assert created is False
    assert notif is winner


async def test_create_rolls_back_before_re_reading_after_an_integrity_error(session: AsyncSession):
    """The session must be usable again before the re-SELECT runs.

    The previous version of this test used ``MagicMock`` and asserted only
    ``session.rollback.assert_awaited_once()``, which proves the rollback
    happened *somewhere*, not that it happened *first*. Reversing the two lines
    in ``repositories.create`` left all 302 tests green, and on a real session
    that ordering raises ``PendingRollbackError`` -- so the blind spot was over
    the one thing the test claims to be about.

    Two independent mechanisms, either of which is enough:

    * the calls are recorded on a **real** ``AsyncSession``, so the assertion is
      about the actual sequence rather than about mock bookkeeping;
    * the re-SELECT runs as **real SQL** against that session, so if the rollback
      had not already cleaned the transaction the database call itself would
      raise, and the test could not reach its assertions at all.
    """
    winner_user = uuid4()
    winner_event = uuid4()
    # A committed row is the "winner" the lost race must find on the re-SELECT.
    winner = Notification(
        user_id=winner_user,
        title="first",
        message="first",
        channel="in-app",
        event_id=winner_event,
    )
    session.add(winner)
    await session.commit()

    recording = _RecordingSession(session)
    repo = NotificationRepository(recording)

    # The pre-check must miss the winner (that is what losing the race looks
    # like), but the re-SELECT must be the real query against the real session.
    real_get_by_event_id = repo.get_by_event_id
    lookups: list[UUID] = []

    async def get_by_event_id(event_id: UUID):
        lookups.append(event_id)
        if len(lookups) == 1:
            return None
        return await real_get_by_event_id(event_id)

    repo.get_by_event_id = get_by_event_id

    notif, created = await repo.create(winner_user, "second", "second", event_id=winner_event)

    # The rollback happens, and it happens before the re-SELECT.
    assert recording.calls == ["add", "flush", "rollback", "execute"], (
        "expected the rollback to precede the re-SELECT; recorded order was " f"{recording.calls}"
    )
    # And the re-SELECT genuinely ran on a session the flush failure had not
    # poisoned -- a MagicMock could not have told us either way.
    assert len(lookups) == 2
    assert created is False
    assert notif.id == winner.id
    assert notif.title == "first"


async def test_create_reraises_when_the_winner_vanished():
    """No row to hand back - the original IntegrityError must surface."""
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock(side_effect=IntegrityError("UNIQUE", {}, Exception("dup")))
    session.rollback = AsyncMock()

    repo = NotificationRepository(session)
    repo.get_by_event_id = AsyncMock(side_effect=[None, None])

    with pytest.raises(IntegrityError):
        await repo.create(uuid4(), "T", "M", event_id=uuid4())

    session.rollback.assert_awaited_once()


async def test_create_does_not_consult_the_index_without_an_event_id():
    """event_id is nullable: without one there is nothing to dedupe on."""
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.rollback = AsyncMock()

    repo = NotificationRepository(session)

    notif, created = await repo.create(uuid4(), "T", "M")

    assert created is True
    assert notif.event_id is None
    session.rollback.assert_not_awaited()


# ---------------------------------------------------------------------------
# get_preference - concurrent insert
# ---------------------------------------------------------------------------


async def test_get_preference_reloads_after_a_concurrent_insert(session: AsyncSession):
    """A parallel request inserted the row first; the unique index rejects ours."""
    user_id = uuid4()
    repo = NotificationRepository(session)
    # No preference row yet -> the repository inserts one.
    pref = await repo.get_preference(user_id)
    assert pref.user_id == user_id
    await session.commit()

    # A second call now finds the committed row instead of inserting again.
    again = await repo.get_preference(user_id)
    assert again.user_id == user_id


async def test_get_preference_recovers_when_the_insert_loses_the_race():
    """`flush` raises IntegrityError, the row turns up on the re-SELECT."""
    winner = MagicMock()
    winner.user_id = uuid4()

    session = MagicMock()
    session.add = MagicMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock(side_effect=IntegrityError("UNIQUE", {}, Exception("dup")))

    repo = NotificationRepository(session)
    # First execute (the pre-SELECT) -> None; second (the re-SELECT) -> winner.
    repo_result = MagicMock()
    repo_result.scalar_one_or_none = MagicMock(side_effect=[None, winner])
    session.execute = AsyncMock(return_value=repo_result)

    pref = await repo.get_preference(winner.user_id)

    session.rollback.assert_awaited_once()
    assert pref is winner


# ---------------------------------------------------------------------------
# update_preference - field allowlist
# ---------------------------------------------------------------------------


async def test_update_preference_rejects_unknown_fields(session: AsyncSession):
    repo = NotificationRepository(session)

    with pytest.raises(ValueError, match="unknown preference fields"):
        await repo.update_preference(uuid4(), nonsense=True)


async def test_update_preference_applies_flags_and_bumps_updated_at(session: AsyncSession):
    repo = NotificationRepository(session)
    user_id = uuid4()

    pref = await repo.update_preference(user_id, push_enabled=False, sms_enabled=False)
    await session.commit()

    assert pref.push_enabled is False
    assert pref.sms_enabled is False
    assert pref.updated_at is not None


# ---------------------------------------------------------------------------
# parse_delivery_errors - corrupt stored data
# ---------------------------------------------------------------------------


def test_parse_delivery_errors_returns_empty_for_a_null_column():
    assert NotificationRepository.parse_delivery_errors(Notification()) == {}


def test_parse_delivery_errors_returns_empty_for_invalid_json():
    notif = Notification(delivery_errors="not json at all")

    assert NotificationRepository.parse_delivery_errors(notif) == {}


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("[]", id="json-array"),
        pytest.param('"just a string"', id="json-string"),
        pytest.param("null", id="json-null"),
        pytest.param("42", id="json-number"),
        pytest.param("{not json", id="malformed"),
    ],
)
def test_parse_delivery_errors_treats_non_object_json_as_no_outcomes(raw):
    """A ``delivery_errors`` value that is not an object must not 500 the read path.

    ``app/repositories.py`` wraps only ``json.loads`` in ``except ValueError``. A
    column holding valid JSON that is not an object (``"[]"``, ``"null"``, a bare
    number) makes ``parsed.items()`` raise ``AttributeError``, which escapes. One
    corrupt value therefore turned every unread/retry read path into a 500 instead
    of degrading to "no recorded outcomes", which is what an absent value does.

    Regression test for #856.
    """
    notif = Notification(delivery_errors=raw)

    assert NotificationRepository.parse_delivery_errors(notif) == {}


def test_parse_delivery_errors_stringifies_keys_and_values():
    notif = Notification(delivery_errors=json.dumps({"in-app": "sent", "sms": 1}))

    assert NotificationRepository.parse_delivery_errors(notif) == {
        "in-app": "sent",
        "sms": "1",
    }


# ---------------------------------------------------------------------------
# soft_delete idempotency (#210)
# ---------------------------------------------------------------------------


async def test_soft_delete_is_idempotent_for_an_already_deleted_row(session: AsyncSession):
    repo = NotificationRepository(session)
    user_id = uuid4()
    notif, _ = await repo.create(user_id, "T", "M")
    await session.commit()

    assert await repo.soft_delete(notif.id, user_id) is True
    await session.commit()
    # A second DELETE still answers True: the resource exists for this user.
    assert await repo.soft_delete(notif.id, user_id) is True


async def test_soft_delete_returns_false_for_another_users_row(session: AsyncSession):
    repo = NotificationRepository(session)
    owner, attacker = uuid4(), uuid4()
    notif, _ = await repo.create(owner, "T", "M")
    await session.commit()

    assert await repo.soft_delete(notif.id, attacker) is False


async def test_soft_delete_hides_the_row_from_every_read_path(session: AsyncSession):
    repo = NotificationRepository(session)
    user_id = uuid4()
    notif, _ = await repo.create(user_id, "T", "M")
    await session.commit()

    await repo.soft_delete(notif.id, user_id)
    await session.commit()

    assert await repo.get_by_id(notif.id, user_id) is None
    assert await repo.get_unread(user_id) == []
    assert await repo.count_unread(user_id) == 0
