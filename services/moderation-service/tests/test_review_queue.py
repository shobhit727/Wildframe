"""Tests for review queue."""

from uuid import uuid4
from app.models.review_queue import ReviewModeration


def test_review_mod_model():
    rec = ReviewModeration(review_id=uuid4(), content_id=uuid4(), status="pending")
    assert rec.status == "pending"


def test_review_mod_auto():
    rec = ReviewModeration(review_id=uuid4(), content_id=uuid4(), auto_flagged=True)
    assert rec.auto_flagged is True
