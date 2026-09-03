"""Integration tests for content rights."""
from datetime import UTC, datetime
from uuid import uuid4
from app.models.rights import RightsHolder, TerritorialLicense

def test_rights_holder_create():
    holder = RightsHolder(name="Studio", type="studio", contact="a@b.com")
    assert holder.name == "Studio"

def test_territorial_license():
    lic = TerritorialLicense(content_id=uuid4(), rights_holder_id=uuid4(), territory="US", exclusive=True, avail_start=datetime.now(UTC), avail_end=datetime.now(UTC))
    assert lic.territory == "US"
