"""What the platform will accept as an uploaded object.

This file previously held two tests that could not fail:

    def test_uploads_health():
        assert True

    def test_uploads_rights():
        assert "rights" in ["rights", "license"]

Neither touched production code — the first asserted a literal, the second
asserted that a string is a member of a two-element list written inline
alongside it. They named a concern ("rights" — what a user is permitted to put
in the bucket) without testing any of it, which is worse than no test: a reader
scanning the file sees compliance coverage that does not exist.

The concern they gestured at is real and does have a production referent here:
``settings.ALLOWED_UPLOAD_MIME_TYPES`` is the gate in
``UploadService.validate_mime``, and nothing in the suite constrained its
*contents*. The only two assertions anywhere were that ``video/mp4`` is present
and ``application/octet-stream`` is absent (``test_settings_validation.py``), so
adding an active-content or executable media type — the actual upload-attack
surface for a streaming platform, and a rights question in the most direct sense
— would have shipped green.

The two vacuous tests are therefore removed, not skipped: neither asserted
anything, so nothing is lost, and the coverage they implied is supplied below
against code that can actually fail.
"""

import re

import pytest

from app.core.settings import settings
from app.services import UploadError, UploadService

#: Media types that must never be accepted. Grouped by why they are dangerous.
#:
#: * **Active content** — rendered by a browser in a user's session. A stored
#:   XSS payload here becomes script execution against a logged-in viewer, served
#:   back to the same user who uploaded it.
#: * **Executable content** — run by the operating system when opened. Irrelevant
#:   to streaming video and never a legitimate upload.
#: * **Opaque catch-alls** — accept any bytes with no type guarantee, which turns
#:   the allowlist into a no-op.
DANGEROUS_MEDIA_TYPES = [
    pytest.param("text/html", id="active-content-html"),
    pytest.param("application/xhtml+xml", id="active-content-xhtml"),
    pytest.param("image/svg+xml", id="active-content-svg"),
    pytest.param("application/javascript", id="active-content-js"),
    pytest.param("text/javascript", id="active-content-js-text"),
    pytest.param("application/x-msdownload", id="executable-windows"),
    pytest.param("application/x-sh", id="executable-shell"),
    pytest.param("application/x-msdos-program", id="executable-dos"),
    pytest.param("application/octet-stream", id="opaque-catch-all"),
]

#: Refused one gate earlier. ``validate_mime`` applies ``_MIME_RE`` before the
#: allowlist, and a wildcard is not a ``type/subtype`` at all, so these never
#: reach the allowlist comparison. They are pinned separately because that is
#: the actual reason they are refused, and a message that named the allowlist
#: instead would be misleading.
SYNTACTICALLY_INVALID = [
    pytest.param("*/*", id="wildcard"),
    pytest.param("video/*", id="wildcard-video"),
    pytest.param("video", id="no-subtype"),
    pytest.param("video/", id="empty-subtype"),
]


@pytest.mark.parametrize("dangerous", [*DANGEROUS_MEDIA_TYPES, *SYNTACTICALLY_INVALID])
def test_the_upload_allowlist_excludes_active_and_executable_types(dangerous):
    """The allowlist is the rights boundary; this is what holds it.

    Comparing against a denylist rather than an exact expected list on purpose:
    the set of legitimate streaming, audio, image and document types will change
    as the catalogue grows, and this test should not have to be rewritten each
    time. It should only fire when a genuinely dangerous type joins the list.
    """
    assert dangerous not in settings.ALLOWED_UPLOAD_MIME_TYPES


@pytest.mark.parametrize("dangerous", DANGEROUS_MEDIA_TYPES)
def test_a_dangerous_media_type_is_refused_by_the_allowlist(dangerous):
    """The setting is enforced, not merely declared.

    ``test_the_upload_allowlist_excludes_active_and_executable_types`` reads the
    setting; this one drives the code path that reads it, so a refactor that
    stopped consulting ``ALLOWED_UPLOAD_MIME_TYPES`` fails here.
    """
    with pytest.raises(UploadError, match="not allowed"):
        UploadService.validate_mime(dangerous)


@pytest.mark.parametrize("malformed", SYNTACTICALLY_INVALID)
def test_a_malformed_media_type_is_refused_before_the_allowlist(malformed):
    """A wildcard is not a media type, so it never reaches the allowlist.

    Asserted against the specific message because "refused for the wrong reason"
    is how a silently-disabled gate looks: a caller told the type is merely
    not-allowed would not know the type was never even parsed.
    """
    with pytest.raises(UploadError, match="invalid media type"):
        UploadService.validate_mime(malformed)


def test_every_allowed_media_type_is_a_well_formed_lowercase_media_type():
    """No wildcards, no parameters, no case tricks in the list itself.

    ``validate_mime`` matches by exact lowercase string, so an entry written as
    ``"Video/MP4"`` or ``"video/mp4; codecs=avc1"`` would be dead configuration:
    unreachable from every request, which is a defect that reads as working
    configuration.
    """
    for allowed in settings.ALLOWED_UPLOAD_MIME_TYPES:
        assert re.fullmatch(
            r"[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*", allowed
        ), f"{allowed!r} is not a bare lowercase type/subtype and can never match"


def test_the_allowlist_is_not_empty_and_has_no_duplicates():
    """A duplicate entry is harmless at runtime but hides a copy-paste edit."""
    allowed = settings.ALLOWED_UPLOAD_MIME_TYPES

    assert allowed, "an empty allowlist would reject every upload"
    assert len(allowed) == len(set(allowed)), "duplicate entry in ALLOWED_UPLOAD_MIME_TYPES"


def test_the_platform_still_accepts_the_media_types_it_stores():
    """The complement of the denylist: a gate that rejects everything is broken too."""
    for expected in ("video/mp4", "video/webm", "audio/mpeg", "image/jpeg"):
        assert UploadService.validate_mime(expected) == expected
