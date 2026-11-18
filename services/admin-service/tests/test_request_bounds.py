"""Request schemas must reject values wider than their database columns.

PostgreSQL raises ``22001 string_data_right_truncation`` when an over-long
string reaches a ``VARCHAR(n)`` column. The service's catch-all turns that
into a 500, so the API boundary must reject the value with 422 first
(issue #1050).
"""

import pytest
from pydantic import ValidationError

from app.schemas.admin import (
    ContentModerationRequest,
    SystemAlertRequest,
    SystemConfigRequest,
    UserModerationRequest,
)

# (model, field, column width, other valid fields)
BOUNDED_FIELDS = [
    (UserModerationRequest, "user_id", 255, {"status": "suspended"}),
    (ContentModerationRequest, "content_id", 255, {"content_type": "movie", "status": "flagged"}),
    (SystemAlertRequest, "alert_type", 50, {"severity": "info", "message": "m", "service": "s"}),
    (SystemAlertRequest, "service", 255, {"alert_type": "a", "severity": "info", "message": "m"}),
    (SystemConfigRequest, "key", 255, {"value": "v", "config_type": "string"}),
]


@pytest.mark.parametrize("model, field, width, extra", BOUNDED_FIELDS)
def test_request_rejects_a_value_longer_than_its_column(model, field, width, extra):
    payload = {**extra, field: "x" * (width + 1)}
    with pytest.raises(ValidationError):
        model(**payload)


@pytest.mark.parametrize("model, field, width, extra", BOUNDED_FIELDS)
def test_request_accepts_a_value_exactly_at_its_column_width(model, field, width, extra):
    payload = {**extra, field: "x" * width}
    model(**payload)
