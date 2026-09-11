import pytest
from pydantic import ValidationError
from app.schemas.documents import DocumentCreate
from app.schemas.eu import EUCreate
from app.schemas.india import IndiaCreate
from app.schemas.processors import ProcessorCreate
from app.schemas.transfers import TransferCreate

def test_document_create_defaults():
    doc = DocumentCreate(title="Doc", version="1.0", content="text")
    assert doc.acceptance_required is True

def test_eu_create_defaults():
    eu = EUCreate()
    assert eu.avms_enabled is True
    assert eu.dsa_enabled is True
    assert eu.dma_enabled is False

def test_india_create_requires_fields():
    india = IndiaCreate(grievance_officer="Officer")
    assert india.ott_registered is True
    assert india.tier == "tier1"

def test_processor_create_defaults():
    proc = ProcessorCreate(name="Proc")
    assert proc.dpa_url is None
    assert proc.vendor_change_status == "pending"

def test_transfer_create_defaults():
    tr = TransferCreate(source_region="EU", target_region="IN")
    assert tr.mechanism == "SCC"
    assert tr.adequacy is False

def test_missing_required_fields_raise():
    with pytest.raises(ValidationError):
        DocumentCreate(title="Doc", version="1.0")  # missing content
    with pytest.raises(ValidationError):
        IndiaCreate()  # missing grievance_officer
    with pytest.raises(ValidationError):
        ProcessorCreate()  # missing name
    with pytest.raises(ValidationError):
        TransferCreate(source_region="EU")  # missing target_region
