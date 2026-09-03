"""Tests for documents."""

from app.models.documents import LegalDocument
from app.schemas.documents import DocumentCreate


def test_doc_create():
    data = DocumentCreate(title="TOS", version="1.0.0", content="test")
    assert data.title == "TOS"


def test_doc_model():
    rec = LegalDocument(title="Privacy", version="1.0.0", content="x")
    assert rec.version == "1.0.0"
