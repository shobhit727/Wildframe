"""Tests for the DMCA request schemas (``app/schemas/dmca.py``).

The previous version of this file asserted ``data.reason == "copy"`` on a
schema it had just constructed from that same literal, and
``rec.status == "pending"`` on a model instance it had just assigned. Neither
runs a validator, so the file could not fail for any change to the schemas.

What follows pins the boundary the schemas actually draw. Note the router is
not mounted in production -- see ``test_unmounted_routers.py`` and the owner
decision in ``oner-task.md`` -- so this is the contract that *would* apply if
it were.
"""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.schemas.dmca import CounterNoticeCreate, TakedownCreate


class TestTakedownCreate:
    def test_accepts_a_well_formed_body(self):
        content_id = uuid4()
        payload = TakedownCreate(
            content_id=str(content_id), reporter_email="reporter@example.com", reason="piracy"
        )

        # The UUID field is parsed, not passed through as a string.
        assert payload.content_id == content_id
        assert isinstance(payload.content_id, UUID)
        assert payload.reporter_email == "reporter@example.com"
        assert payload.reason == "piracy"

    @pytest.mark.parametrize("missing", ["content_id", "reporter_email", "reason"])
    def test_rejects_a_body_missing_a_required_field(self, missing):
        body = {
            "content_id": str(uuid4()),
            "reporter_email": "reporter@example.com",
            "reason": "piracy",
        }
        del body[missing]

        with pytest.raises(ValidationError) as excinfo:
            TakedownCreate(**body)

        assert missing in str(excinfo.value)

    def test_rejects_a_non_uuid_content_id(self):
        # The route turns this into a 422. Before the UUID was typed the field
        # would have accepted any string and written it to a UUID column.
        with pytest.raises(ValidationError):
            TakedownCreate(content_id="c-1", reporter_email="reporter@example.com", reason="piracy")

    def test_reporter_email_is_not_validated_as_an_address(self):
        """KNOWN GAP: the reporter address is a bare ``str``.

        ``reporter_email`` is typed ``str``, so an address that no mail system
        could deliver to is accepted and (if the router were mounted) written to
        a ``String(255)`` column. Pinned here so the gap is visible rather than
        assumed: today it is harmless only because ``POST /dmca/takedown`` is
        not served. Deciding whether to add ``EmailStr`` belongs with the
        mount-or-delete decision in ``oner-task.md``.
        """
        payload = TakedownCreate(content_id=uuid4(), reporter_email="not-an-email", reason="piracy")
        assert payload.reporter_email == "not-an-email"

    def test_reporter_email_is_not_length_bounded_in_the_schema(self):
        # The 255 limit lives on the column, not here, so SQLite and a
        # mis-configured deployment differ. Documented rather than asserted as
        # a guarantee the schema does not make.
        payload = TakedownCreate(content_id=uuid4(), reporter_email="a" * 5000, reason="x" * 5000)
        assert len(payload.reporter_email) == 5000


class TestCounterNoticeCreate:
    def test_accepts_a_well_formed_body(self):
        takedown_id = uuid4()
        payload = CounterNoticeCreate(takedown_id=str(takedown_id), counter_reason="fair use")

        assert payload.takedown_id == takedown_id
        assert payload.counter_reason == "fair use"

    def test_rejects_a_non_uuid_takedown_id(self):
        with pytest.raises(ValidationError):
            CounterNoticeCreate(takedown_id="x", counter_reason="fair use")

    def test_rejects_a_body_missing_the_counter_reason(self):
        with pytest.raises(ValidationError) as excinfo:
            CounterNoticeCreate(takedown_id=str(uuid4()))

        assert "counter_reason" in str(excinfo.value)
