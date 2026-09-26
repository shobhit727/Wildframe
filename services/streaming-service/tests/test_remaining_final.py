"""Replaces the former placeholder file.

The old contents were::

    def test_streaming_final():
        import pathlib
        assert pathlib.Path("app/models/accessibility.py").exists()

-- a file-existence assertion that exercised no production code. This module
holds real behavioural tests instead:

* ``app/schemas/accessibility.py`` was at 0% coverage. It is a Pydantic model
  that nothing imports, so it is exercised directly through its own validation
  contract (defaults, coercion, rejection).
* ``app/models/__init__.py:121`` -- the ``VideoManifest.expires_at`` property
  had its ``started_at is None`` arm uncovered.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models import PlaybackSession, PlaybackSessionStatus
from app.schemas.accessibility import AccessibilityCreate


# ------------------------------------------- app/schemas/accessibility.py ---


@pytest.mark.unit
def test_accessibility_defaults_are_opt_out_not_opt_in():
    """Captions and keyboard nav default on; audio description defaults off."""
    payload = AccessibilityCreate(content_id=uuid4())
    assert payload.captions_enabled is True
    assert payload.keyboard_nav is True
    assert payload.audio_description is False


@pytest.mark.unit
def test_accessibility_accepts_an_explicitly_disabled_accessibility_set():
    """A fully opted-out profile must be representable."""
    content_id = uuid4()
    payload = AccessibilityCreate(
        content_id=content_id,
        captions_enabled=False,
        audio_description=True,
        keyboard_nav=False,
    )
    assert payload.content_id == content_id
    assert payload.captions_enabled is False
    assert payload.audio_description is True
    assert payload.keyboard_nav is False


@pytest.mark.unit
def test_accessibility_accepts_a_uuid_string_and_coerces_it():
    """A JSON body carries a string id; Pydantic must coerce it to UUID."""
    raw = str(uuid4())
    payload = AccessibilityCreate.model_validate({"content_id": raw})
    assert isinstance(payload.content_id, type(uuid4()))
    assert str(payload.content_id) == raw


@pytest.mark.unit
def test_accessibility_requires_a_content_id():
    """content_id is mandatory -- a profile with no asset is meaningless."""
    with pytest.raises(ValidationError) as exc:
        AccessibilityCreate()
    assert "content_id" in str(exc.value)


@pytest.mark.unit
def test_accessibility_rejects_a_malformed_content_id():
    """A non-UUID id is a validation error, not a downstream DB failure."""
    with pytest.raises(ValidationError) as exc:
        AccessibilityCreate(content_id="not-a-uuid")
    assert "content_id" in str(exc.value)


@pytest.mark.unit
def test_accessibility_rejects_an_uninterpretable_boolean_flag():
    """The flags are strictly bool; arbitrary text must not be coerced.

    Note: Pydantic's lax mode *does* accept "yes"/"no"/"on"/"off"/0/1, so the
    rejection is asserted with a value outside that vocabulary.
    """
    with pytest.raises(ValidationError):
        AccessibilityCreate(content_id=uuid4(), captions_enabled="maybe")


@pytest.mark.unit
def test_accessibility_exposes_exactly_the_documented_fields():
    """Field set is a contract the frontend relies on."""
    assert set(AccessibilityCreate.model_fields) == {
        "content_id",
        "captions_enabled",
        "audio_description",
        "keyboard_nav",
    }


# ------------------------------- app/models/__init__.py: expires_at ---------


def make_session(started_at) -> PlaybackSession:
    return PlaybackSession(
        id=uuid4(),
        user_id=uuid4(),
        content_id=uuid4(),
        episode_id=None,
        device_id="device-1",
        protocol="hls",
        resolution="1080p",
        bitrate_kbps=5000,
        total_duration_seconds=3600,
        status=PlaybackSessionStatus.ACTIVE,
        started_at=started_at,
    )


@pytest.mark.unit
def test_expires_at_is_none_when_started_at_is_none():
    """models/__init__.py:121 -- an unstarted session has no expiry."""
    assert make_session(None).expires_at is None


@pytest.mark.unit
def test_expires_at_is_two_hours_after_started_at():
    """The documented TTL is a fixed 2 hours from started_at."""
    started = datetime(2026, 3, 4, 5, 6, 7)
    assert make_session(started).expires_at == started + timedelta(hours=2)


@pytest.mark.unit
def test_expires_at_preserves_the_awareness_of_started_at():
    """The property adds a timedelta, so awareness is carried through.

    Callers must not mix naive and aware stamps; this pins that the helper does
    not silently normalise one into the other.
    """
    naive = make_session(datetime(2026, 3, 4, 5, 6, 7))
    aware = make_session(datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC))

    assert naive.expires_at.tzinfo is None
    assert naive.expires_at == datetime(2026, 3, 4, 7, 6, 7)
    assert aware.expires_at.tzinfo is not None
    assert aware.expires_at == datetime(2026, 3, 4, 7, 6, 7, tzinfo=UTC)
