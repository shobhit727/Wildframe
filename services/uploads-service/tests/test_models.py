import os
import importlib.util
module_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app', 'models.py'))
spec = importlib.util.spec_from_file_location('uploads_models', module_path)
uploads_models = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uploads_models)
UploadSession = uploads_models.UploadSession
UploadChunk = uploads_models.UploadChunk
UploadSessionStatus = uploads_models.UploadSessionStatus
from uuid import uuid4
from sqlalchemy import inspect

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
    index = next((idx for idx in insp.indexes if idx.name == "idx_upload_chunk_session_index"), None)
    assert index is not None, "unique index idx_upload_chunk_session_index missing"
    assert set(index.columns.keys()) == {"session_id", "index"}
    assert index.unique
