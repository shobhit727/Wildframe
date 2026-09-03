"""Moderation + analytics 80%."""

from uuid import uuid4
from app.models.dmca import DMCATakedown
from app.models.review_queue import ReviewModeration


def test_dmca_status():
    r = DMCATakedown(content_id=uuid4(), reporter_email="a@b.com", reason="x", status="countered")
    assert r.status == "countered"


def test_review_queue_status():
    r = ReviewModeration(review_id=uuid4(), content_id=uuid4(), status="approved")
    assert r.status == "approved"
