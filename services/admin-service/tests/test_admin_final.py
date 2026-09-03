"""Final admin tests."""

from app.models.processors import Processor
from app.models.documents import LegalDocument


def test_admin_final1():
    assert Processor(name="P") is not None


def test_admin_final2():
    assert LegalDocument(title="T", version="1.0", content="c") is not None


def test_admin_final3():
    from app.models.transfers import TransferRecord

    assert TransferRecord(source_region="EU", target_region="US") is not None
