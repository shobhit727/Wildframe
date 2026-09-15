import os
import importlib.util

module_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app", "models.py"))
spec = importlib.util.spec_from_file_location("uploads_models", module_path)
uploads_models = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uploads_models)
UploadSession = uploads_models.UploadSession
UploadChunk = uploads_models.UploadChunk
UploadSessionStatus = uploads_models.UploadSessionStatus
from app.models import Base
from uuid import uuid4
from sqlalchemy import inspect
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import datetime
from datetime import timedelta


def test_upload_session_defaults():
    """UploadSession defaults on creation.
    - status INITIATED
    - uploaded_chunks 0
    - storage_cleaned_at None
    """
    session = UploadSession(
        id=uuid4(),
        creator_id=uuid4(),
        filename="example.txt",
        mime="text/plain",
        size_bytes=123,
        chunk_size=64,
        total_chunks=2,
        expires_at=UploadSession.expires_at.type.python_type.utcnow(),
        status=UploadSessionStatus.INITIATED,
    )
    assert session.uploaded_chunks in (0, None)
    assert session.storage_cleaned_at is None


def test_upload_chunk_unique_index_definition():
    """UploadChunk defines unique index on (session_id, index)."""
    insp = inspect(UploadChunk.__table__)
    index = next(
        (idx for idx in insp.indexes if idx.name == "idx_upload_chunk_session_index"), None
    )
    assert index is not None, "unique index idx_upload_chunk_session_index missing"
    assert set(index.columns.keys()) == {"session_id", "index"}
    assert index.unique


@pytest.fixture(scope="function")
def db_session():
    """In‑memory SQLite DB session for upload models."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    sess.close()


def test_upload_chunk_unique_index_enforced(db_session):
    """Duplicate (session_id, index) raises IntegrityError."""
    from sqlalchemy.exc import IntegrityError

    s = UploadSession(
        id=uuid4(),
        creator_id=uuid4(),
        filename="file.bin",
        mime="application/octet-stream",
        size_bytes=256,
        chunk_size=128,
        total_chunks=2,
        expires_at=UploadSession.expires_at.type.python_type.utcnow(),
    )
    db_session.add(s)
    db_session.commit()

    c1 = UploadChunk(session_id=s.id, index=0, size_bytes=128)
    db_session.add(c1)
    db_session.commit()

    c2 = UploadChunk(session_id=s.id, index=0, size_bytes=128)
    db_session.add(c2)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_upload_chunk_tracking_and_status_progression(db_session):
    """Chunk count and status change on first chunk."""
    s = UploadSession(
        id=uuid4(),
        creator_id=uuid4(),
        filename="data.bin",
        mime="application/octet-stream",
        size_bytes=256,
        chunk_size=128,
        total_chunks=2,
        expires_at=UploadSession.expires_at.type.python_type.utcnow(),
    )
    db_session.add(s)
    db_session.commit()

    assert s.uploaded_chunks == 0
    assert s.status == UploadSessionStatus.INITIATED

    c = UploadChunk(session_id=s.id, index=0, size_bytes=128)
    db_session.add(c)
    s.status = UploadSessionStatus.UPLOADING
    s.uploaded_chunks = 1
    db_session.commit()

    refreshed = db_session.get(UploadSession, s.id)
    assert refreshed.status == UploadSessionStatus.UPLOADING
    assert refreshed.uploaded_chunks == 1


def test_upload_session_status_enum_values():
    assert UploadSessionStatus.INITIATED.value == "initiated"
    assert UploadSessionStatus.UPLOADING.value == "uploading"
    assert UploadSessionStatus.COMPLETE.value == "complete"
    assert UploadSessionStatus.ABORTED.value == "aborted"


def test_upload_session_expiry_logic(db_session):
    """Session past expires_at is considered expired."""
    past = datetime.datetime.utcnow() - timedelta(hours=1)
    s = UploadSession(
        id=uuid4(),
        creator_id=uuid4(),
        filename="old.txt",
        mime="text/plain",
        size_bytes=10,
        chunk_size=10,
        total_chunks=1,
        expires_at=past,
        status=UploadSessionStatus.UPLOADING,
    )
    db_session.add(s)
    db_session.commit()

    assert datetime.datetime.utcnow() > s.expires_at
    fetched = db_session.get(UploadSession, s.id)
    assert fetched.status == UploadSessionStatus.UPLOADING
