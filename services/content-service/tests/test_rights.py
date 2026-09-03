"""Tests for content-service rights and reviews."""

from uuid import uuid4

from app.models.rights import RightsHolder, TerritorialLicense
from app.models.reviews import Review
from app.schemas.rights import RightsHolderCreate
from app.schemas.reviews import ReviewCreate


def test_rights_holder_create():
    data = RightsHolderCreate(name="Studio", type="studio")
    assert data.name == "Studio"


def test_review_create():
    data = ReviewCreate(content_id=uuid4(), user_id=uuid4(), rating=5, text="Great")
    assert data.rating == 5


def test_rights_holder_model():
    holder = RightsHolder(name="Creator", type="creator")
    assert holder.type == "creator"


def test_review_model():
    review = Review(content_id=uuid4(), user_id=uuid4(), rating=4, text="Good", verified_viewer=True)
    assert review.rating == 4
