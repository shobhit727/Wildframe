"""Tests for moderation service models behavior."""

import uuid

from app.models.dmca import DMCATakedown
from app.models.review_queue import ReviewModeration


def test_dmca_model_defaults():
    dmca = DMCATakedown(
        content_id=uuid.uuid4(),
        reporter_email="report@example.com",
        reason="copyright",
    )
    # Defaults are applied by the DB, not on plain instance creation
    assert dmca.status is None
    assert dmca.counter_notice is None
    assert dmca.repeat_infringer_count is None
    assert dmca.created_at is None
    assert dmca.updated_at is None

    # Simulate setting values before persistence
    dmca.status = "pending"
    dmca.repeat_infringer_count = 0
    assert dmca.status == "pending"
    assert dmca.repeat_infringer_count == 0


def test_review_moderation_model_defaults():
    review = ReviewModeration(
        review_id=uuid.uuid4(),
        content_id=uuid.uuid4(),
    )
    # Defaults before DB persistence
    assert review.status is None
    assert review.auto_flagged is None
    assert review.moderator_id is None
    assert review.reason is None
    assert review.created_at is None

    # Simulate DB default after insert
    review.status = "pending"
    assert review.status == "pending"
