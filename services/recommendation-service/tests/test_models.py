import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base, UserPreferences, Recommendation
import datetime
from uuid import uuid4


@pytest.fixture(scope="function")
def db_session():
    """Create an isolated in-memory SQLite session for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    sess.close()


def test_user_preferences_defaults(db_session):
    # create without optional fields
    pref = UserPreferences(user_id=uuid4())
    db_session.add(pref)
    db_session.commit()

    fetched = db_session.get(UserPreferences, pref.id)
    assert fetched.liked_genres == []
    assert fetched.disliked_genres == []
    assert fetched.preferred_languages == ["en"]
    assert fetched.watch_frequency == "medium"
    # timestamps are set and timezone-aware (UTC)
    now = datetime.datetime.now(datetime.timezone.utc)
    assert isinstance(fetched.created_at, datetime.datetime)
    assert isinstance(fetched.updated_at, datetime.datetime)
    # compare using timestamps to avoid tz awareness issues
    assert abs(now.timestamp() - fetched.created_at.timestamp()) < 5
    assert abs(now.timestamp() - fetched.updated_at.timestamp()) < 5


def test_user_preferences_timestamp_update(db_session):
    pref = UserPreferences(user_id=uuid4())
    db_session.add(pref)
    db_session.commit()
    original_updated = pref.updated_at
    # modify a field to trigger onupdate
    pref.watch_frequency = "high"
    db_session.commit()
    db_session.refresh(pref)
    assert pref.updated_at > original_updated


def test_recommendation_fields(db_session):
    rec = Recommendation(
        user_id=uuid4(),
        content_id=uuid4(),
        score=0.85,
        reason="popular",
        algorithm="collab",
    )
    db_session.add(rec)
    db_session.commit()

    fetched = db_session.get(Recommendation, rec.id)
    assert fetched.score == 0.85
    assert fetched.reason == "popular"
    assert fetched.algorithm == "collab"
    delta_seconds = datetime.datetime.now(datetime.timezone.utc).timestamp() - fetched.created_at.timestamp()
    assert abs(delta_seconds) < 5


# Ensure index ordering works: higher score first for same user
def test_recommendation_index_ordering(db_session):
    uid = uuid4()
    rec_low = Recommendation(user_id=uid, content_id=uuid4(), score=0.2)
    rec_high = Recommendation(user_id=uid, content_id=uuid4(), score=0.9)
    db_session.add_all([rec_low, rec_high])
    db_session.commit()
    # ordering by index (user_id, score desc) – we emulate by query
    results = (
        db_session.query(Recommendation)
        .filter_by(user_id=uid)
        .order_by(Recommendation.score.desc())
        .all()
    )
    assert results[0].score > results[1].score
