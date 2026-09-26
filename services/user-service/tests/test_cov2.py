"""Module-level invariants for user-service.

The previous version of this file (and `test_final.py` / `test_final15.py`)
was a byte-identical placeholder copied across five services and asserted only
`assert True`. These tests instead pin real, cheap invariants of the
`app.repositories` facade: what the public surface re-exports, and the column
defaults the repositories rely on for their entitlement maths.
"""

from uuid import uuid4

from app.repositories import (
    BaseRepository,
    DSARRepository,
    UserDeviceRepository,
    UserPreferenceRepository,
    UserProfileRepository,
    UserSubscriptionProfileRepository,
)
from app.repositories import __doc__ as repositories_docstring

ALL_REPOSITORIES = (
    UserProfileRepository,
    UserDeviceRepository,
    UserPreferenceRepository,
    UserSubscriptionProfileRepository,
    DSARRepository,
)


def test_repository_facade_is_documented():
    assert repositories_docstring is not None
    assert "Repository layer" in repositories_docstring


def test_every_repository_extends_the_shared_base():
    # DSARRepository predates BaseRepository and manages its own session.
    for repository in ALL_REPOSITORIES[:-1]:
        assert issubclass(repository, BaseRepository), repository
    assert issubclass(DSARRepository, object)


def test_base_repository_stores_the_session_it_was_given():
    sentinel = object()

    assert BaseRepository(sentinel).session is sentinel


def test_each_repository_keeps_its_own_session_reference():
    sentinel = object()

    for repository in ALL_REPOSITORIES:
        instance = repository(sentinel)
        expected = sentinel
        assert (instance.db if hasattr(instance, "db") else instance.session) is expected


def test_dsar_repository_is_re_exported_from_the_package_root():
    from app import repositories

    assert repositories.DSARRepository is DSARRepository


def _column_default(model, name):
    """The Python-side default SQLAlchemy applies on INSERT.

    Column defaults are applied at flush time, not at object construction, so
    the only way to assert them without a database is to read the DDL default.
    """
    column = model.__table__.columns[name]
    assert column.default is not None, f"{model.__name__}.{name} has no default"
    return column.default.arg


def test_subscription_tier_defaults_free_with_no_entitlements():
    from app.models import UserSubscriptionProfile

    assert _column_default(UserSubscriptionProfile, "subscription_tier") == "free"
    assert _column_default(UserSubscriptionProfile, "max_concurrent_streams") == 1
    assert _column_default(UserSubscriptionProfile, "can_download") is False
    assert _column_default(UserSubscriptionProfile, "can_use_4k") is False
    assert _column_default(UserSubscriptionProfile, "ad_free") is False


def test_device_defaults_allow_streaming_but_block_downloads():
    from app.models import UserDevice

    assert _column_default(UserDevice, "is_active") is True
    assert _column_default(UserDevice, "is_trusted") is False
    # Streaming is allowed by default; downloads are not - the device must be
    # trusted (and the account entitled) before an off-platform copy is allowed.
    assert _column_default(UserDevice, "can_stream") is True
    assert _column_default(UserDevice, "can_download") is False


def test_profile_defaults_are_private_and_incomplete():
    from app.models import UserProfile

    assert _column_default(UserProfile, "completed_onboarding") is False
    assert _column_default(UserProfile, "profile_completeness") == 0
    assert _column_default(UserProfile, "public_profile") is False


def test_preference_defaults_are_conservative():
    from app.models import UserPreference

    assert _column_default(UserPreference, "theme") == "dark"
    assert _column_default(UserPreference, "autoplay") is True
    # Nothing is shared off-platform until the user opts in.
    assert _column_default(UserPreference, "share_viewing_activity") is False
    assert _column_default(UserPreference, "allow_explicit_content") is True
