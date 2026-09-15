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


def test_dmca_model_full_flow():
    dmca = DMCATakedown(
        content_id=uuid.uuid4(),
        reporter_email="author@example.com",
        reason="copyright infringement",
    )
    # defaults are None before DB persistence
    assert dmca.status is None
    assert dmca.counter_notice is None
    assert dmca.repeat_infringer_count is None
    assert dmca.created_at is None
    assert dmca.updated_at is None
    # set initial status
    dmca.status = "pending"
    dmca.repeat_infringer_count = 0
    assert dmca.status == "pending"
    assert dmca.repeat_infringer_count == 0
    # approval flow
    dmca.status = "approved"
    assert dmca.status == "approved"
    # counter-notice flow
    dmca.status = "countered"
    dmca.counter_notice = "Counter claim text"
    assert dmca.status == "countered"
    assert dmca.counter_notice == "Counter claim text"
    # repeat infringer increment
    dmca.repeat_infringer_count += 1
    assert dmca.repeat_infringer_count == 1
def test_review_moderation_model_defaults():
    review = ReviewModeration(
        review_id=uuid.uuid4(),
        content_id=uuid.uuid4(),
    )
    assert review.status is None
    assert review.auto_flagged is None
    assert review.created_at is None
    # set initial values
    review.status = "pending"
    review.auto_flagged = False
    assert review.status == "pending"
    assert review.auto_flagged is False
def test_review_moderation_full_flow():
    review = ReviewModeration(
        review_id=uuid.uuid4(),
        content_id=uuid.uuid4(),
    )
    # defaults not set without DB
    assert review.status is None
    assert review.auto_flagged is None
    assert review.created_at is None
    # set initial values
    review.status = "pending"
    review.auto_flagged = False
    assert review.status == "pending"
    assert review.auto_flagged is False
    # auto-flag scenario
    review.auto_flagged = True
    review.status = "escalated"
    review.moderator_id = uuid.uuid4()
    review.reason = "Potential policy violation"
    assert review.auto_flagged is True
    assert review.status == "escalated"
    assert review.moderator_id is not None
    assert review.reason == "Potential policy violation"
    review.status = "pending"
    assert review.status == "pending"
