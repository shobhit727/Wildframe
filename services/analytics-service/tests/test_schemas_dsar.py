"""Tests for ``analytics-service/app/schemas/dsar.py``.

``AnalyticsExportResponse`` had no coverage. It is the response model behind the
``/dsar/export`` stub, so its field contract, UUID/datetime parsing and
``from_attributes`` behaviour are pinned here.

The sibling request models in ``app/schemas/__init__.py`` use
``extra="forbid"``; this response model does not, which is pinned as well.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas.dsar import AnalyticsExportResponse


def make(**overrides) -> dict:
    base = {
        "id": uuid4(),
        "user_id": uuid4(),
        "dsar_id": uuid4(),
        "export_format": "json",
        "retention_days": 365,
        "sla_compliant": True,
        "data": "[]",
        "created_at": datetime(2026, 5, 6, 7, 8, 9),
    }
    base.update(overrides)
    return base


@pytest.mark.unit
def test_valid_payload_round_trips():
    """Every declared field is required and preserved verbatim."""
    payload = make()
    model = AnalyticsExportResponse(**payload)

    assert model.id == payload["id"]
    assert model.user_id == payload["user_id"]
    assert model.dsar_id == payload["dsar_id"]
    assert model.export_format == "json"
    assert model.retention_days == 365
    assert model.sla_compliant is True
    assert model.data == "[]"
    assert model.created_at == payload["created_at"]


@pytest.mark.unit
def test_declares_exactly_the_documented_fields():
    """The field set is the DSAR response contract."""
    assert set(AnalyticsExportResponse.model_fields) == {
        "id",
        "user_id",
        "dsar_id",
        "export_format",
        "retention_days",
        "sla_compliant",
        "data",
        "created_at",
    }


@pytest.mark.unit
@pytest.mark.parametrize("field", ["id", "user_id", "dsar_id"])
def test_uuid_fields_are_required(field):
    """The three identity fields have no default and must be supplied."""
    payload = make()
    del payload[field]
    with pytest.raises(ValidationError) as exc:
        AnalyticsExportResponse(**payload)
    assert field in str(exc.value)


@pytest.mark.unit
@pytest.mark.parametrize(
    "field", ["export_format", "retention_days", "sla_compliant", "data", "created_at"]
)
def test_remaining_fields_are_required(field):
    """No field on this model is optional -- a partial record is a 422."""
    payload = make()
    del payload[field]
    with pytest.raises(ValidationError) as exc:
        AnalyticsExportResponse(**payload)
    assert field in str(exc.value)


@pytest.mark.unit
def test_uuid_strings_are_coerced():
    """A JSON body carries strings; they must become UUIDs."""
    raw = str(uuid4())
    model = AnalyticsExportResponse(**make(id=raw, user_id=raw, dsar_id=raw))

    assert isinstance(model.id, UUID)
    assert str(model.id) == raw
    assert str(model.user_id) == raw
    assert str(model.dsar_id) == raw


@pytest.mark.unit
def test_malformed_uuid_strings_are_rejected():
    """A non-UUID identity is a validation error, not a 500 downstream."""
    with pytest.raises(ValidationError):
        AnalyticsExportResponse(**make(dsar_id="not-a-uuid"))


@pytest.mark.unit
def test_iso_datetime_strings_are_coerced():
    """created_at arrives as an ISO string over HTTP."""
    model = AnalyticsExportResponse(**make(created_at="2026-05-06T07:08:09Z"))
    assert isinstance(model.created_at, datetime)
    assert model.created_at.tzinfo is not None
    assert model.created_at.astimezone(UTC) == datetime(2026, 5, 6, 7, 8, 9, tzinfo=UTC)


@pytest.mark.unit
def test_malformed_datetime_string_is_rejected():
    """A non-datetime created_at is a validation error."""
    with pytest.raises(ValidationError):
        AnalyticsExportResponse(**make(created_at="the day before yesterday"))


@pytest.mark.unit
def test_naive_datetime_is_accepted_as_supplied():
    """A naive timestamp is kept naive; the model does not force UTC."""
    model = AnalyticsExportResponse(**make(created_at=datetime(2026, 5, 6, 7, 8, 9)))
    assert model.created_at.tzinfo is None
    assert model.created_at == datetime(2026, 5, 6, 7, 8, 9)


@pytest.mark.unit
def test_sla_compliant_accepts_both_boolean_values():
    """The flag is a real bool, not a truthy coercion of a string."""
    assert AnalyticsExportResponse(**make(sla_compliant=False)).sla_compliant is False
    assert AnalyticsExportResponse(**make(sla_compliant=True)).sla_compliant is True


@pytest.mark.unit
def test_sla_compliant_rejects_a_non_boolean():
    """An uninterpretable string is rejected."""
    with pytest.raises(ValidationError):
        AnalyticsExportResponse(**make(sla_compliant="perhaps"))


@pytest.mark.unit
def test_retention_days_accepts_zero_and_negative_values():
    """No bound is declared, so the model does not invent one.

    The ``/dsar/retention-check`` endpoint applies the 2555 policy; this
    response model is a plain carrier.
    """
    assert AnalyticsExportResponse(**make(retention_days=0)).retention_days == 0
    assert AnalyticsExportResponse(**make(retention_days=-1)).retention_days == -1


@pytest.mark.unit
def test_retention_days_rejects_a_non_integer():
    """A fractional or textual retention is rejected."""
    with pytest.raises(ValidationError):
        AnalyticsExportResponse(**make(retention_days="365 days"))


@pytest.mark.unit
def test_unknown_extra_fields_are_ignored():
    """Unlike the request models, this response model does not forbid extras."""
    model = AnalyticsExportResponse(**make(unexpected="ignored"))
    assert not hasattr(model, "unexpected")
    assert "unexpected" not in model.model_dump()


@pytest.mark.unit
def test_model_validates_from_attributes_on_an_orm_like_object():
    """``from_attributes=True`` lets the model read an ORM/dataclass row."""
    payload = make()
    row = SimpleNamespace(**payload)

    model = AnalyticsExportResponse.model_validate(row)

    assert model.id == payload["id"]
    assert model.user_id == payload["user_id"]
    assert model.sla_compliant is True


@pytest.mark.unit
def test_model_validate_from_attributes_reports_a_missing_attribute():
    """A row missing a column produces a clear validation error."""
    payload = make()
    del payload["dsar_id"]
    with pytest.raises(ValidationError) as exc:
        AnalyticsExportResponse.model_validate(SimpleNamespace(**payload))
    assert "dsar_id" in str(exc.value)


@pytest.mark.unit
def test_model_dump_json_is_serialisable():
    """The /dsar/export response is serialised to JSON by FastAPI."""
    import json

    payload = make()
    dumped = AnalyticsExportResponse(**payload).model_dump(mode="json")
    round_tripped = json.loads(json.dumps(dumped))

    assert round_tripped["id"] == str(payload["id"])
    assert round_tripped["user_id"] == str(payload["user_id"])
    assert round_tripped["created_at"] == "2026-05-06T07:08:09"


@pytest.mark.unit
def test_schema_is_a_pydantic_base_model():
    """Guard the base class so the public API surface cannot silently change."""
    assert issubclass(AnalyticsExportResponse, BaseModel)
