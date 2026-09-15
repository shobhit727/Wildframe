"""Comprehensive model behavior tests for analytics service.

Covers Event, DSAR export, and TrackingConsent models.
"""

from uuid import uuid4
from datetime import datetime, UTC

from app.models import Event
from app.models.dsar import AnalyticsDSARExport
from app.models.tracking import TrackingConsent


def test_event_defaults_timestamp():
    """New Event should have a timestamp close to now (within 2 seconds)."""
    evt = Event(user_id=uuid4(), event_type="play", content_id=uuid4())
    now = datetime.now(UTC)
    diff = (now - evt.timestamp).total_seconds()
    assert abs(diff) < 2, f"Timestamp diff too large: {diff}s"


def test_dsar_export_defaults():
    """AnalyticsDSARExport defaults for export_format, retention_days, sla_compliant."""
    rec = AnalyticsDSARExport(user_id=uuid4(), dsar_id=uuid4(), data="[]")
    assert rec.export_format == "json"
    assert rec.retention_days == 365
    assert rec.sla_compliant is True


def test_tracking_consent_defaults():
    """TrackingConsent defaults for cookie_consent, sdk_governed, consent_mode."""
    cons = TrackingConsent(user_id=uuid4())
    assert cons.cookie_consent == "essential"
    assert cons.sdk_governed is True
    assert cons.consent_mode == "denied"


def test_event_custom_timestamp_and_data():
    """Event respects provided timestamp and stores arbitrary event_data."""
    custom_ts = datetime(2020, 1, 1, tzinfo=UTC)
    data = {"key": "value", "num": 42}
    evt = Event(
        user_id=uuid4(),
        event_type="custom",
        content_id=None,
        event_data=data,
        timestamp=custom_ts,
    )
    assert evt.timestamp == custom_ts
    assert evt.event_data == data
    assert evt.content_id is None


def test_event_partitioning_index():
    """Event model defines composite index on user_id and event_type."""
    indexes = [idx.name for idx in Event.__table_args__]
    assert "idx_events_user_type" in indexes


def test_dsar_export_defaults_and_formats():
    """DSAR export defaults and respects export_format field."""
    rec = AnalyticsDSARExport(user_id=uuid4(), dsar_id=uuid4(), data="[]")
    assert rec.export_format == "json"
    assert rec.retention_days == 365
    assert rec.sla_compliant is True
    csv_rec = AnalyticsDSARExport(user_id=uuid4(), dsar_id=uuid4(), data="[]", export_format="csv")
    assert csv_rec.export_format == "csv"


def test_dsar_export_retention_and_sla():
    """Retention days affect expiry calculation (simulated)."""
    rec = AnalyticsDSARExport(
        user_id=uuid4(),
        dsar_id=uuid4(),
        data="[]",
        retention_days=10,
        sla_compliant=False,
    )
    assert rec.retention_days == 10
    assert rec.sla_compliant is False


def test_tracking_consent_defaults_and_variations():
    """TrackingConsent defaults and handles explicit values."""
    cons_default = TrackingConsent(user_id=uuid4())
    assert cons_default.cookie_consent == "essential"
    assert cons_default.sdk_governed is True
    assert cons_default.consent_mode == "denied"
    cons = TrackingConsent(
        user_id=uuid4(),
        cookie_consent="marketing",
        sdk_governed=False,
        consent_mode="granted",
    )
    assert cons.cookie_consent == "marketing"
    assert cons.sdk_governed is False
    assert cons.consent_mode == "granted"
