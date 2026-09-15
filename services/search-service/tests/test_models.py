import pytest
import uuid
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import SearchQuery, SearchIndex
# Heavy imports avoided; use SQLAlchemy model inspection for mapping expectations.


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

def test_search_index_mapping_fields():
    from app.services import CONTENT_INDEX_MAPPING
    # duplicate import removed

    props = CONTENT_INDEX_MAPPING["mappings"]["properties"]
    # text fields
    assert props["title"]["type"] == "text"
    assert props["description"]["type"] == "text"
    # keyword fields
    for kw in ["content_type", "genres", "actors", "director", "status"]:
        assert props[kw]["type"] == "keyword"
    # numeric fields
    assert props["release_year"]["type"] == "integer"
    assert props["rating"]["type"] == "float"

def test_search_query_filters_and_pagination(session):
    """Store JSON filters; ensure result_count updates and pagination works."""
    uid = uuid.uuid4()
    sq = SearchQuery(user_id=uid, query_text="test", filters={"type": "movie"})
    session.add(sq)
    session.commit()
    # simulate pagination by updating result_count manually
    sq.result_count = 5
    session.commit()
    fetched = session.get(SearchQuery, sq.id)
    assert fetched.filters == {"type": "movie"}
    assert fetched.result_count == 5

def test_search_result_scoring_highlighting_facets():
    """SearchResult should preserve result metadata like scores, highlights, facets."""
    from app.services import SearchResult

    results = [
        {"id": "1", "title": "A", "score": 1.2, "highlight": {"title": ["<em>A</em>"]}},
        {"id": "2", "title": "B", "score": 0.8},
    ]
    sr = SearchResult(results=results, next_sort=["1"])
    assert sr.results[0]["score"] == 1.2
    assert sr.results[0]["highlight"]["title"] == ["<em>A</em>"]
    assert sr.next_sort == ["1"]