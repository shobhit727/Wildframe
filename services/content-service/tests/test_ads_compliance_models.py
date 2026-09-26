import uuid
from datetime import datetime, timezone
import importlib.util
import pathlib


def _load_module(name, filename):
    path = pathlib.Path(__file__).resolve().parents[1] / "app" / "models" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Load required model modules
ads_mod = _load_module("ads", "ads.py")
ContentEU = _load_module("eu", "eu.py").ContentEU
ContentIndia = _load_module("india", "india.py").ContentIndia
ContentTransfer = _load_module("transfers", "transfers.py").ContentTransfer
RightsHolder = _load_module("rights", "rights.py").RightsHolder
TerritorialLicense = _load_module("rights", "rights.py").TerritorialLicense
Audit = _load_module("audit", "audit.py").ContentAudit


def _has_default(model, column_name):
    return model.__table__.c[column_name].default is not None


def test_ad_config_defaults():
    _ = ads_mod.AdConfig(content_id=uuid.uuid4())
    assert _has_default(ads_mod.AdConfig, "consent_gated")
    assert _has_default(ads_mod.AdConfig, "minor_safe")
    assert _has_default(ads_mod.AdConfig, "tcf_required")
    assert _has_default(ads_mod.AdConfig, "created_at")


def test_eu_compliance_defaults():
    _ = ContentEU(content_id=uuid.uuid4())
    assert _has_default(ContentEU, "avms_rating")
    assert _has_default(ContentEU, "created_at")


def test_india_compliance_defaults():
    _ = ContentIndia(content_id=uuid.uuid4())
    assert _has_default(ContentIndia, "grievance_tier")
    assert _has_default(ContentIndia, "created_at")


def test_content_transfer_residency():
    tr = ContentTransfer(content_id=uuid.uuid4(), residency="US")
    assert tr.residency == "US"
    assert _has_default(ContentTransfer, "created_at")


def test_rights_holder_and_territorial_license():
    holder = RightsHolder(name="Studio X", type="studio")
    assert holder.type == "studio"
    lic = TerritorialLicense(
        content_id=holder.id,
        rights_holder_id=holder.id,
        territory="US",
        exclusive=True,
        avail_start=datetime.now(timezone.utc),
        avail_end=datetime.now(timezone.utc),
    )
    assert lic.exclusive is True
    assert hasattr(lic, "royalty_rate")


def test_content_audit_defaults():
    _ = Audit(event_type="create", content_id=uuid.uuid4())
    assert _has_default(Audit, "encrypted")
    assert _has_default(Audit, "created_at")
