import pytest
import uuid
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import SearchQuery, SearchIndex


# Helper to get a fresh in‑memory DB and session
@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    # Create tables
    SearchQuery.metadata.create_all(engine)
    SearchIndex.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        yield s
        s.rollback()


def test_search_query_defaults(session):
    uid = uuid.uuid4()
    sq = SearchQuery(user_id=uid, query_text="test query")
    session.add(sq)
    session.commit()
    # result_count defaults to 0
    assert sq.result_count == 0
    # created_at is set and is naive UTC (tzinfo stripped)
    assert isinstance(sq.created_at, datetime)
    assert sq.created_at.tzinfo is None
    # stored values match input
    assert sq.user_id == uid
    assert sq.query_text == "test query"


def test_search_index_defaults_and_update(session):
    cid = uuid.uuid4()
    si = SearchIndex(
        content_id=cid,
        title="Title",
        content_type="movie",
    )
    session.add(si)
    session.commit()
    # indexed_at set, updated_at equals indexed_at initially
    assert isinstance(si.indexed_at, datetime)
    assert isinstance(si.updated_at, datetime)
    # updated_at set at creation; may differ by a few microseconds
    assert abs((si.updated_at - si.indexed_at).total_seconds()) < 1
    # modify and commit, updated_at should change
    old_updated = si.updated_at
    si.title = "New Title"
    session.commit()
    assert si.updated_at > old_updated


def test_search_index_unique_content_id(session):
    cid = uuid.uuid4()
    si1 = SearchIndex(content_id=cid, title="First", content_type="movie")
    si2 = SearchIndex(content_id=cid, title="Second", content_type="movie")
    session.add(si1)
    session.commit()
    session.add(si2)
    with pytest.raises(Exception):  # IntegrityError or similar
        session.commit()
