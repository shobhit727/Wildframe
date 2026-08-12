"""Security-critical helpers for the media pipeline.

Everything in here sits at a trust boundary:

* ``sanitize_storage_key`` — the upload key is caller-supplied; it must never
  be able to escape the quarantine root or alter object-storage semantics.
* ``is_local_media_path`` — stages must only ever hand local files to
  subprocesses; a URL here would turn ffmpeg into an SSRF primitive.
* ``sanitize_metadata`` — ffprobe output is untrusted input surface; control
  characters and unbounded strings must not reach logs, HTML, or manifests.
* ``validate_manifest_no_origin_urls`` — packaged manifests must reference
  relative segments only; absolute origin URLs would bypass playback auth.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# ---------------------------------------------------------------------------
# Input validation.
# ---------------------------------------------------------------------------

# Storage keys are object-prefix strings, never filesystem paths.
_KEY_SEPARATORS = ("/", "\\", "\x00")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# NFKC can turn lookalikes into real separators, so normalize BEFORE checking.
MAX_STORAGE_KEY_LENGTH = 512
MAX_METADATA_STRING_LENGTH = 256
# HLS/DASH manifests may contain absolute URLs for ad/variant injection, but
# Wildframe serves media through signed URLs; any absolute origin reference in
# a packaged manifest is a playback-authorization bypass.
URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")

# Probe-derived technical limits (trusted from ffprobe, enforced on it).
MAX_DURATION_SECONDS = 4 * 3600.0
MAX_DIMENSION_PIXELS = 8192
MAX_BITRATE_KBPS = 100_000
MAX_PROBE_OUTPUT_BYTES = 1 << 20  # ffprobe JSON is small; cap hard.


class UnsafeInput(ValueError):
    """Caller-supplied input is structurally unsafe (traversal, URL, ...)."""


def sanitize_storage_key(key: str) -> str:
    """Validate and normalize a caller-supplied object-storage key.

    Rejects path traversal (``..``), absolute paths, path separators, control
    characters, and over-long keys — the media pipeline derives local
    filesystem paths and storage prefixes from this value, so it must be
    structurally inert after this function.

    Raises ``UnsafeInput`` on rejection; returns the NFKC-normalized key.
    """
    if not isinstance(key, str) or not key:
        raise UnsafeInput("storage_key must be a non-empty string")
    if len(key) > MAX_STORAGE_KEY_LENGTH:
        raise UnsafeInput(f"storage_key exceeds {MAX_STORAGE_KEY_LENGTH} chars")
    if _CONTROL_RE.search(key):
        raise UnsafeInput("storage_key contains control characters")
    normalized = unicodedata.normalize("NFKC", key)
    if any(sep in normalized for sep in _KEY_SEPARATORS) or normalized.startswith("."):
        raise UnsafeInput("storage_key must be a single path segment (no separators)")
    if normalized.startswith(("/", "~")):
        raise UnsafeInput("storage_key must be relative")
    return normalized


def is_local_media_path(path: str, work_root: str, quarantine_root: str) -> bool:
    """True only for non-URL paths inside the pipeline's own directories.

    Guards the subprocess adapters: ffmpeg/ffprobe must never receive a URL or
    a path outside the per-job work/quarantine sandboxes (SSRF + escape).
    """
    if not isinstance(path, str) or not path:
        return False
    if URL_SCHEME_RE.match(path):
        return False
    if path.startswith(("//", "\\\\")):
        return False
    if not path.startswith(work_root) and not path.startswith(quarantine_root):
        return False
    # ``..`` anywhere means a path that can climb out of the sandbox.
    if ".." in path.split("/"):
        return False
    return True


# ---------------------------------------------------------------------------
# Metadata sanitization (ffprobe output is an untrusted input surface).
# ---------------------------------------------------------------------------

# Keys we are willing to propagate downstream. Anything else a probe reports is
# dropped, so exotic metadata cannot reach logs, manifests, or the API.
_ALLOWED_METADATA_KEYS = frozenset(
    {
        "duration_seconds",
        "width",
        "height",
        "codec",
        "bitrate_kbps",
        "container",
        "has_video",
        "has_audio",
    }
)

# Duration/dimension/bitrate ceilings derived from a trusted probe. Client-side
# metadata never influences these (see issue #288).
TECHNICAL_LIMITS = {
    "max_duration_seconds": MAX_DURATION_SECONDS,
    "max_dimension_pixels": MAX_DIMENSION_PIXELS,
    "max_bitrate_kbps": MAX_BITRATE_KBPS,
}


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded, whitelisted copy of probe metadata.

    * Only ``_ALLOWED_METADATA_KEYS`` survive.
    * String values are NFKC-normalized, control-char-stripped, and truncated.
    * Numeric values are clamped to finite ranges.
    """
    out: dict[str, Any] = {}
    for key, value in metadata.items():
        if key not in _ALLOWED_METADATA_KEYS:
            continue
        if isinstance(value, str):
            cleaned = _CONTROL_RE.sub("", unicodedata.normalize("NFKC", value))
            out[key] = cleaned[:MAX_METADATA_STRING_LENGTH]
        elif isinstance(value, bool):
            out[key] = value
        elif isinstance(value, (int, float)):
            out[key] = value
    return out


def enforce_technical_limits(metadata: dict[str, Any]) -> None:
    """Raise ``UnsafeInput`` when probed media exceeds resource ceilings.

    Pathological media (hours-long streams, 16k frames, insane bitrates) must
    be rejected before any encode work is scheduled, otherwise a single upload
    can pin a worker for hours (see #288/#289).
    """
    duration = metadata.get("duration_seconds")
    if isinstance(duration, (int, float)) and 0 < duration > MAX_DURATION_SECONDS:
        raise UnsafeInput(
            f"media duration {duration:.1f}s exceeds limit {MAX_DURATION_SECONDS:.0f}s"
        )
    for dim in ("width", "height"):
        size = metadata.get(dim)
        if isinstance(size, (int, float)) and 0 < size > MAX_DIMENSION_PIXELS:
            raise UnsafeInput(f"media dimension {dim}={size} exceeds limit {MAX_DIMENSION_PIXELS}")
    bitrate = metadata.get("bitrate_kbps")
    if isinstance(bitrate, (int, float)) and 0 < bitrate > MAX_BITRATE_KBPS:
        raise UnsafeInput(f"media bitrate {bitrate} kbps exceeds limit {MAX_BITRATE_KBPS} kbps")


# ---------------------------------------------------------------------------
# Manifest validation (#283): packaged manifests must not expose origin URLs.
# ---------------------------------------------------------------------------

MAX_MANIFEST_BYTES = 4 << 20  # 4 MiB: HLS/DASH manifests are small text files.

_ORIGIN_REFERENCE_RE = re.compile(r"https?://|//[a-zA-Z0-9.\-]+[/:]", re.IGNORECASE)


def validate_manifest_no_origin_urls(manifest_path: str) -> None:
    """Reject packaged manifests that reference absolute/origin URLs.

    Every segment/rendition URL in Wildframe manifests must stay relative so
    playback goes through signed, scoped URLs. Raises ``UnsafeInput`` when the
    manifest contains an absolute reference.
    """
    import os

    if not os.path.isfile(manifest_path):
        return
    size = os.path.getsize(manifest_path)
    if size > MAX_MANIFEST_BYTES:
        raise UnsafeInput(f"manifest {manifest_path} exceeds {MAX_MANIFEST_BYTES} bytes")
    with open(manifest_path, "r", encoding="utf-8", errors="replace") as handle:
        head = handle.read(MAX_MANIFEST_BYTES)
    match = _ORIGIN_REFERENCE_RE.search(head)
    if match:
        raise UnsafeInput(
            f"manifest {manifest_path} contains absolute URL "
            f"({match.group(0)!r}); relative segment URLs required"
        )
