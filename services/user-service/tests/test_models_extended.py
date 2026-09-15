import pytest
from uuid import uuid4
from datetime import datetime, timedelta, UTC
from app.models import UserDevice, UserPreference
from app.models.dsar import DSARRequest
from app.models.child_account import ChildAccount
from app.models.privacy import UserConsentRecord
from .test_models import db_session

# Reuse existing db_session fixture from test_models.py


def test_user_device_status_transitions(db_session):
    device = UserDevice(
        user_id=uuid4(),
        device_id="dev-123",
        device_name="Phone",
        device_type="android",
    )
    db_session.add(device)
    db_session.commit()
    db_session.refresh(device)
    # defaults
    assert device.is_active is True
    assert device.is_trusted is False
    assert device.can_stream is True
    assert device.can_download is False
    # transition
    device.is_active = False
    device.is_trusted = True
    device.can_stream = False
    device.can_download = True
    db_session.commit()
    db_session.refresh(device)
    assert device.is_active is False
    assert device.is_trusted is True
    assert device.can_stream is False
    assert device.can_download is True


def test_user_device_unique_constraint(db_session):
    uid = uuid4()
    d1 = UserDevice(user_id=uid, device_id="dup-1", device_name="A", device_type="web")
    db_session.add(d1)
    db_session.commit()
    d2 = UserDevice(user_id=uid, device_id="dup-1", device_name="B", device_type="web")
    db_session.add(d2)
    with pytest.raises(Exception):  # IntegrityError or similar
        db_session.commit()


def test_user_preference_updates(db_session):
    pref = UserPreference(user_id=uuid4())
    db_session.add(pref)
    db_session.commit()
    db_session.refresh(pref)
    # default
    assert pref.allow_explicit_content is True
    # change conflict: disable explicit content but set rating to "R"
    pref.allow_explicit_content = False
    pref.content_rating = "R"
    db_session.commit()
    db_session.refresh(pref)


def test_dsar_request_defaults_and_sla(db_session):
    user = uuid4()
    dr = DSARRequest(
        user_id=user,
        request_type="access",
        status="pending",
        data_categories='["profile"]',
        sla_deadline=datetime.now(UTC) + timedelta(days=30),
    )
    db_session.add(dr)
    db_session.commit()
    db_session.refresh(dr)
    assert dr.user_id == user
    assert dr.request_type == "access"
    now = datetime.utcnow()
    delta = dr.sla_deadline - now
    assert 29 <= delta.days <= 31
    record = UserConsentRecord(
        user_id=uuid4(),
        consent_type="marketing",
        jurisdiction="US",
        granted=True,
        version="1.0",
    )
    db_session.add(record)
    db_session.commit()
    db_session.refresh(record)
    assert record.granted is True
    assert record.granted_at is None  # not auto-set
    # withdraw
    record.granted = False
    record.withdrawn_at = datetime.now(UTC)
    db_session.commit()
    db_session.refresh(record)
    assert record.granted is False
    assert record.withdrawn_at is not None
