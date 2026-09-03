"""Analytics 80%."""
from uuid import uuid4
from app.models.tracking import TrackingConsent
from app.models.dsar import AnalyticsDSARExport
def test_tracking_modes():
    for mode in ["essential","analytics","advertising"]:
        c = TrackingConsent(user_id=uuid4(), cookie_consent=mode)
        assert c.cookie_consent == mode
def test_export_csv():
    e = AnalyticsDSARExport(user_id=uuid4(), dsar_id=uuid4(), export_format="csv", data="[]")
    assert e.export_format == "csv"
