"""The sanitiser's truncation limits and the columns they must fit into.

``send_notification`` truncates user-controlled title/message to
``TITLE_MAX_LENGTH`` / ``MESSAGE_MAX_LENGTH`` before the insert, precisely so a
long value cannot reach a ``VARCHAR(n)`` column it does not fit: PostgreSQL
rejects one with ``StringDataRightTruncation``, which would turn an ordinary
notification into a 500 on every send.

The two numbers used to be written out twice -- ``String(255)`` / ``String(1000)``
in ``app.models``, ``TITLE_MAX_LENGTH = 255`` / ``MESSAGE_MAX_LENGTH = 1000`` in
``app.services`` -- with nothing tying them together. Raising the service limits
tenfold left all 302 tests green and a 900-character title surviving to the
insert. The columns are now declared from the same constants; these tests are
what keep it that way.

Nothing here asserts a length by repeating a literal back: the expectations are
read back out of ``Notification.__table__``, so a test fails when the sanitiser
and the schema disagree rather than when both are quietly wrong together.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import String

from app.models import Notification, NotificationPreference
from app.services import MESSAGE_MAX_LENGTH, TITLE_MAX_LENGTH
from app.services import NotificationService

#: A title longer than the column by a wide margin. 900 is the length the audit
#: used, and it is also longer than the column by 3.5x, so a sanitiser that
#: stopped truncating at all, or truncated at some third limit, is caught.
OVERLONG_TITLE = "T" * 900

OVERLONG_MESSAGE = "M" * 5000


def _column_width(column_name: str) -> int:
    column = Notification.__table__.c[column_name]
    assert isinstance(column.type, String), f"{column_name} must stay a bounded VARCHAR"
    assert column.type.length is not None, f"{column_name} must declare a width"
    return column.type.length


def _service() -> tuple[NotificationService, MagicMock]:
    """A service whose repository records the exact values handed to the insert."""
    repo = MagicMock()
    repo.session = MagicMock()
    repo.session.commit = AsyncMock()
    # Column defaults are applied at INSERT, not at construction, so the flags
    # have to be set here or ``_preference_allows`` reads ``None`` and the send
    # short-circuits to "skipped" before the insert these tests are watching.
    repo.get_preference = AsyncMock(
        return_value=NotificationPreference(
            user_id=uuid4(),
            in_app_enabled=True,
            email_enabled=True,
            push_enabled=True,
            sms_enabled=True,
        )
    )

    created: dict[str, object] = {}

    async def create(user_id, title, message, channel, event_id):
        created.update(user_id=user_id, title=title, message=message)
        notif = MagicMock()
        notif.delivery_status = "sent"
        return notif, True

    repo.create = create
    return NotificationService(repo), created


class TestSanitiserLimitsMatchTheColumns:
    """The two numbers are the same numbers."""

    def test_the_title_column_width_equals_the_title_sanitiser_limit(self):
        assert _column_width("title") == TITLE_MAX_LENGTH

    def test_the_message_column_width_equals_the_message_sanitiser_limit(self):
        assert _column_width("message") == MESSAGE_MAX_LENGTH

    def test_the_limits_are_the_ones_the_schema_was_migrated_for(self):
        """Widening either is a schema change, not a tuning knob.

        Both the sanitiser limit and the ``VARCHAR`` width move together when
        this constant moves, so this assertion is the thing that stops the move:
        it turns "raise the limit tenfold" from a silent edit into a failure
        that says a migration and a review are required.
        """
        assert TITLE_MAX_LENGTH == 255
        assert MESSAGE_MAX_LENGTH == 1000

    def test_the_service_module_does_not_carry_its_own_copy_of_the_limits(self):
        """A second definition is the defect, so there must not be one.

        This is a source check rather than a behavioural one, and deliberately
        so: the property being protected is "there is only one place to edit",
        which no runtime assertion can observe. It is backed by the behavioural
        tests in the other class -- if someone reintroduces a literal here and
        the columns still follow ``app.models``, those still hold, and only this
        one fires.
        """
        import inspect

        import app.services as services_mod

        source = inspect.getsource(services_mod)
        assert "TITLE_MAX_LENGTH = " not in source
        assert "MESSAGE_MAX_LENGTH = " not in source


class TestLongInputIsTruncatedToTheColumn:
    """The end-to-end property: what is stored always fits what is written."""

    @pytest.mark.asyncio
    async def test_a_900_character_title_is_cut_to_the_title_column_width(self):
        service, created = _service()

        await service.send_notification(uuid4(), OVERLONG_TITLE, "short")

        assert len(created["title"]) == _column_width("title")

    @pytest.mark.asyncio
    async def test_an_overlong_message_is_cut_to_the_message_column_width(self):
        service, created = _service()

        await service.send_notification(uuid4(), "short", OVERLONG_MESSAGE)

        assert len(created["message"]) == _column_width("message")

    @pytest.mark.asyncio
    async def test_nothing_the_service_stores_exceeds_either_column(self):
        """Assert the inequality, not a copy of the limits."""
        service, created = _service()

        await service.send_notification(uuid4(), OVERLONG_TITLE, OVERLONG_MESSAGE)

        assert len(created["title"]) <= _column_width("title")
        assert len(created["message"]) <= _column_width("message")

    @pytest.mark.asyncio
    async def test_escaping_expansion_cannot_push_a_title_past_the_column(self):
        """``sanitize_text`` escapes before truncating, so the output is capped.

        ``"<"`` becomes ``"&lt;"`` -- four characters for one. A truncator that
        ran on the *unescaped* input would return up to 4x the column width for
        an input of markup, which is the shape of value that reaches a VARCHAR
        as a truncation error.
        """
        service, created = _service()

        await service.send_notification(uuid4(), "<" * 900, "short")

        assert len(created["title"]) <= _column_width("title")

    @pytest.mark.asyncio
    async def test_a_value_exactly_at_the_column_width_is_untouched(self):
        service, created = _service()
        title = "T" * TITLE_MAX_LENGTH

        await service.send_notification(uuid4(), title, "short")

        assert created["title"] == title

    @pytest.mark.asyncio
    async def test_one_character_past_the_column_width_is_dropped(self):
        """Pins the boundary: ``max_length`` is inclusive, not exclusive."""
        service, created = _service()

        await service.send_notification(uuid4(), "T" * (TITLE_MAX_LENGTH + 1), "short")

        assert created["title"] == "T" * TITLE_MAX_LENGTH
