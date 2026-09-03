"""Integration tests for admin."""
from app.models.processors import Processor
from app.models.documents import LegalDocument
from app.models.transfers import TransferRecord

def test_processor_create():
    rec = Processor(name="Vendor", dpa_url="https://example.com/dpa")
    assert rec.name == "Vendor"

def test_document_version():
    doc = LegalDocument(title="TOS", version="2.0.0", content="terms")
    assert doc.version == "2.0.0"

def test_transfer_scc():
    rec = TransferRecord(source_region="EU", target_region="US", mechanism="SCC")
    assert rec.mechanism == "SCC"
