def test_auth_final2_a():
    from app.core.event_consumer import run_user_moderation_consumer

    assert run_user_moderation_consumer is not None


def test_auth_final2_b():
    from app.api import router

    assert router is not None


def test_auth_final2_c():
    from app.repositories import PrivacyNoticeRepository

    assert PrivacyNoticeRepository is not None


def test_auth_final2_d():
    from app.models import Base

    assert Base is not None
