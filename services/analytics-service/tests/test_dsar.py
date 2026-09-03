"""Tests for analytics dsar."""
from uuid import uuid4
from app.models.dsar import AnalyticsDSARExport
from app.schemas.dsar import AnalyticsExportResponse
def test_analytics_export_model():
    rec = AnalyticsDSARExport(user_id=uuid4(), dsar_id=uuid4(), data="[]", export_format="json")
    assert rec.export_format == "json"
def test_analytics_export_sla():
    rec = AnalyticsDSARExport(user_id=uuid4(), dsar_id=uuid4(), retention_days=365, data="[]", sla_compliant=True)
    assert rec.sla_compliant is True
