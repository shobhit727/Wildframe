"""Compliance-surface invariants for the notification service.

The previous version of this file was a byte-identical placeholder copied
across five services (`assert True` / `assert "marketing" in [...]`).

`NotificationPreference` is where the GDPR/CCPA consent signal actually lives
for this service: the four `*_enabled` flags are read by
`NotificationService._preference_allows` on every send, so their *column*
defaults decide whether a user receives anything at all before they have
touched a preference. These tests pin those defaults (which are only applied on
INSERT, so they are asserted at the DDL level) plus the structural invariants
the repositories and the dispatch loop rely on.
"""

import pytest

from app.models import Base, Notification, NotificationPreference, utcnow_naive


# ---------------------------------------------------------------------------
# Preference defaults
# ---------------------------------------------------------------------------


def _column_default(model, name):
    column = model.__table__.columns[name]
    assert column.default is not None, f"{model.__name__}.{name} has no default"
    return column.default.arg


@pytest.mark.parametrize("flag", ["in_app_enabled", "email_enabled", "push_enabled", "sms_enabled"])
def test_every_channel_is_enabled_by_default(flag):
    """No channel is opt-in: an untouched account still gets notifications."""
    assert _column_default(NotificationPreference, flag) is True


def test_updated_at_defaults_to_now_utc_naive():
    column = NotificationPreference.__table__.columns["updated_at"]
    assert callable(column.default.arg)
    value = column.default.arg(None)
    # Repo convention: all timestamp columns are naive UTC.
    assert value.tzinfo is None


def test_preference_user_id_is_the_primary_key():
    assert list(NotificationPreference.__table__.primary_key.columns.keys()) == ["user_id"]


# ---------------------------------------------------------------------------
# Notification defaults and constraints
# ---------------------------------------------------------------------------


def test_a_new_notification_starts_pending_and_unread():
    assert _column_default(Notification, "delivery_status") == "pending"
    assert _column_default(Notification, "is_read") is False


def test_created_at_defaults_to_naive_utc():
    assert Notification.__table__.columns["created_at"].default is not None
    assert utcnow_naive().tzinfo is None


def test_event_id_is_nullable_so_dedup_is_optional():
    assert Notification.__table__.columns["event_id"].nullable is True


def test_event_id_has_a_unique_index_for_deduplication():
    indexes = {tuple(c.name for c in idx.columns) for idx in Notification.__table__.indexes}
    assert ("event_id",) in indexes
    unique = [idx for idx in Notification.__table__.indexes if tuple(c.name for c in idx.columns) == ("event_id",)]
    assert unique[0].unique is True


def test_user_and_read_state_are_indexed_for_the_unread_query():
    indexes = {tuple(c.name for c in idx.columns) for idx in Notification.__table__.indexes}
    assert ("user_id", "is_read") in indexes


def test_soft_delete_columns_are_nullable():
    for column in ("deleted_at", "read_at", "delivered_at", "delivery_errors"):
        assert Notification.__table__.columns[column].nullable is True


def test_the_table_names_match_the_migration_ddl():
    assert Notification.__tablename__ == "notifications"
    assert NotificationPreference.__tablename__ == "notification_preferences"


# ---------------------------------------------------------------------------
# Metadata sanity
# ---------------------------------------------------------------------------


def test_both_models_share_the_declarative_base():
    assert issubclass(Notification, Base)
    assert issubclass(NotificationPreference, Base)


def test_the_metadata_holds_exactly_the_two_service_tables():
    assert set(Base.metadata.tables) == {"notifications", "notification_preferences"}
