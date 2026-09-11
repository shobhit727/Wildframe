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
