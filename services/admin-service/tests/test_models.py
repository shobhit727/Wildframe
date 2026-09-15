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


def test_legal_document_model_defaults():
    from app.models.documents import LegalDocument

    doc = LegalDocument(title="Doc", version="1.0", content="text")
    # defaults not set on init
    assert doc.acceptance_required is None


def test_eu_compliance_model_defaults():
    from app.models.eu_compliance import EUCompliance

    eu = EUCompliance()
    assert eu.avms_enabled is None
    assert eu.dsa_enabled is None
    assert eu.dma_enabled is None


def test_india_compliance_model_defaults():
    from app.models.india import IndiaCompliance

    india = IndiaCompliance(grievance_officer="Officer")
    assert india.ott_registered is None
    assert india.grievance_officer == "Officer"
    # default tier set at DB level; model init leaves None
    assert india.tier is None


def test_processor_model_defaults():
    from app.models.processors import Processor

    proc = Processor(name="Proc")
    assert proc.dpa_url is None
    assert proc.vendor_change_status is None


def test_transfer_record_model_defaults():
    from app.models.transfers import TransferRecord

    tr = TransferRecord(source_region="EU", target_region="IN")
    assert tr.mechanism is None
    assert tr.adequacy is None
    assert tr.source_region == "EU"
    assert tr.target_region == "IN"
    with pytest.raises(ValidationError):
        ProcessorCreate()  # missing name
    with pytest.raises(ValidationError):
        TransferCreate(source_region="EU")  # missing target_region


def test_legal_document_assignments():
    from app.models.documents import LegalDocument

    doc = LegalDocument(title="Doc", version="1.0", content="text", acceptance_required=False)
    assert doc.acceptance_required is False


def test_eu_compliance_assignments():
    from app.models.eu_compliance import EUCompliance

    eu = EUCompliance(avms_enabled=False, dsa_enabled=False, dma_enabled=True)
    assert eu.avms_enabled is False
    assert eu.dsa_enabled is False
    assert eu.dma_enabled is True


def test_india_compliance_assignments():
    from app.models.india import IndiaCompliance

    india = IndiaCompliance(grievance_officer="Off", ott_registered=False, tier="tier2")
    assert india.ott_registered is False
    assert india.grievance_officer == "Off"
    assert india.tier == "tier2"


def test_processor_assignments():
    from app.models.processors import Processor

    proc = Processor(name="Proc", dpa_url="http://example.com", vendor_change_status="active")
    assert proc.dpa_url == "http://example.com"
    assert proc.vendor_change_status == "active"


def test_transfer_record_assignments():
    from app.models.transfers import TransferRecord

    tr = TransferRecord(source_region="EU", target_region="IN", mechanism="Custom", adequacy=True)
    assert tr.mechanism == "Custom"
    assert tr.adequacy is True
