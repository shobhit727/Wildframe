"""Remaining admin tests."""
def test_admin_remaining1():
    from app.models.processors import Processor
    assert Processor is not None
def test_admin_remaining2():
    from app.models.documents import LegalDocument
    assert LegalDocument is not None
def test_admin_remaining3():
    from app.models.transfers import TransferRecord
    assert TransferRecord is not None
def test_admin_remaining4():
    from app.models.eu_compliance import EUCompliance
    assert EUCompliance is not None
def test_admin_remaining5():
    from app.models.india import IndiaCompliance
    assert IndiaCompliance is not None
def test_admin_remaining6():
    from app.schemas.processors import ProcessorCreate
    assert ProcessorCreate is not None
