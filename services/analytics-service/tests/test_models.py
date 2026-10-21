"""Model behavior tests for analytics service.

Covers the Event and ContentViewEvent write-boundary contract: every
timestamp column is TIMESTAMP WITHOUT TIME ZONE, so the values Python
hands to asyncpg must be naive UTC.
"""

from datetime import UTC, datetime
from uuid import uuid4

from app.models import ContentViewEvent, Event


def test_event_defaults_to_naive_utc_now():
    """New Event timestamps are naive UTC (asyncpg rejects tz-aware values)."""
    evt = Event(user_id=uuid4(), event_type="play", content_id=uuid4())
    assert evt.timestamp.tzinfo is None
    assert evt.created_at.tzinfo is None
    now = datetime.now(UTC).replace(tzinfo=None)
    assert abs((now - evt.timestamp).total_seconds()) < 2


def test_view_event_created_at_defaults_to_naive_utc():
    """The declared column default must be naive UTC.

    Column defaults are applied at INSERT, not at construction, so asserting on
    a freshly built (never flushed) instance would only read ``None``.
    """
    default = ContentViewEvent.__table__.c.created_at.default
    # SQLAlchemy wraps a zero-arg callable so it receives the execution context.
    assert default.arg(None).tzinfo is None


def test_event_partitioning_index():
    """Event model defines composite index on user_id and event_type."""
    indexes = [idx.name for idx in Event.__table_args__]
    assert "idx_events_user_type" in indexes
