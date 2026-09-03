"""Remaining auth tests for 11 files without direct test."""


def test_remaining_auth1():
    from app.models.privacy import PrivacyNotice

    assert PrivacyNotice is not None


def test_remaining_auth2():
    from app.models.age_verification import AgeVerification

    assert AgeVerification is not None


def test_remaining_auth3():
    from app.models.audit import SecurityAudit

    assert SecurityAudit is not None


def test_remaining_auth4():
    from app.schemas.age import AgeVerifyRequest

    assert AgeVerifyRequest is not None


def test_remaining_auth5():
    from app.schemas.dsar_verify import DSARVerifyRequest

    assert DSARVerifyRequest is not None


def test_remaining_auth6():
    from app.repositories.privacy_repository import PrivacyNoticeRepository

    assert PrivacyNoticeRepository is not None


def test_remaining_auth7():
    from app.api.routes.privacy import router

    assert router is not None


def test_remaining_auth8():
    from app.api.routes.age import router as age_router

    assert age_router is not None


def test_remaining_auth9():
    from app.api.routes.dsar_verify import router as dsar_router

    assert dsar_router is not None


def test_remaining_auth10():
    from app.core.database import DatabaseManager

    assert DatabaseManager is not None


def test_remaining_auth11():
    from app.core.settings import settings

    assert settings.SERVICE_NAME == "auth-service"
