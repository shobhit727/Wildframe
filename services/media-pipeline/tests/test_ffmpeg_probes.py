"""FFprobe adapter + the security trust boundary it sits on.

``FFprobeMetadataExtractor`` is the only place untrusted probe output enters
the pipeline, so these tests cover three layers:

1. ``FFprobeMetadataExtractor.extract`` — the subprocess boundary: fixed argv,
   non-zero exit handling, and the oversized-probe-output guard.
2. ``FFprobeMetadataExtractor._parse`` — a pure function over probe JSON:
   missing ``format``, duration only on the video stream, non-float
   duration/bit_rate, no streams at all, and invalid JSON.
3. ``app.core.security`` — the normalizer/limiter/manifest validators those two
   layers depend on (``sanitize_metadata``, ``enforce_technical_limits``,
   ``sanitize_storage_key``, ``is_local_media_path``,
   ``validate_manifest_no_origin_urls``).

No real ffprobe is ever executed: the subprocess seam is
``asyncio.create_subprocess_exec`` and the fakes come from
``test_pipeline_security_hardening``.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.core.ffmpeg import (
    CommandFailure,
    FFprobeMetadataExtractor,
    _output_job_root,
    _require_local_input,
)
from app.core.security import (
    MAX_BITRATE_KBPS,
    MAX_DIMENSION_PIXELS,
    MAX_DURATION_SECONDS,
    MAX_METADATA_STRING_LENGTH,
    MAX_PROBE_OUTPUT_BYTES,
    UnsafeInput,
    enforce_technical_limits,
    is_local_media_path,
    sanitize_metadata,
    sanitize_storage_key,
    validate_manifest_no_origin_urls,
)
from tests.test_pipeline_security_hardening import FakeProcess


class ByteStream:
    """A fake pipe that serves ``payload`` in ``read(n)``-sized slices."""

    def __init__(self, payload: bytes) -> None:
        self._remaining = payload
        self.reads = 0

    async def read(self, n: int) -> bytes:
        self.reads += 1
        if not self._remaining:
            return b""
        take = self._remaining[:n]
        self._remaining = self._remaining[n:]
        return take


class ChunkedStream:
    """A fake pipe that mimics real 64 KiB pipe reads at a fixed granularity."""

    def __init__(self, payload: bytes, chunk: int) -> None:
        self._remaining = payload
        self._chunk = chunk
        self.reads = 0

    async def read(self, n: int) -> bytes:
        self.reads += 1
        if not self._remaining:
            return b""
        take = self._remaining[: min(n, self._chunk)]
        self._remaining = self._remaining[min(n, self._chunk) :]
        return take


class ExplodingStream:
    """A fake pipe whose reader dies with ``exc`` (broken upstream)."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def read(self, n: int) -> bytes:
        raise self._exc


class DataProcess(FakeProcess):
    """``FakeProcess`` carrying real stdout/stderr payloads and a pid."""

    def __init__(self, *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.pid = 31337
        self.stdout = ByteStream(stdout)
        self.stderr = ByteStream(stderr)

    async def wait(self) -> int:
        return self.returncode


@pytest.fixture(autouse=True)
def _never_touch_the_real_process_group():
    """Guard: ``cleanup()`` must never SIGKILL a real process group here.

    ``run_process`` calls ``os.killpg(proc.pid, SIGKILL)`` on any failure. With
    faked processes the pid is meaningless, so an unpatched call would aim at
    the pytest runner's own process group.
    """
    with patch("os.killpg") as killpg, patch("resource.setrlimit") as setrlimit:
        yield {"killpg": killpg, "setrlimit": setrlimit}


def _media_file(tmp_path, name: str = "in.mp4", data: bytes = b"media-bytes") -> str:
    path = tmp_path / "job-1" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _spawn(process) -> AsyncMock:
    return AsyncMock(return_value=process)


def _probe(stdout: bytes, returncode: int = 0, stderr: bytes = b"") -> DataProcess:
    return DataProcess(returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# extract(): the subprocess boundary.
# ---------------------------------------------------------------------------


async def test_extract_returns_sanitized_metadata_for_a_healthy_probe(tmp_path):
    media = _media_file(tmp_path)
    payload = json.dumps(
        {
            "format": {"format_name": "mov,mp4", "duration": "12.5", "bit_rate": "1500000"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }
    ).encode()
    extractor = FFprobeMetadataExtractor(work_root=str(tmp_path))

    with patch("asyncio.create_subprocess_exec", new=_spawn(_probe(payload))) as spawn:
        metadata = await extractor.extract(media)

    assert metadata == {
        "duration_seconds": 12.5,
        "width": 1920,
        "height": 1080,
        "container": "mov,mp4",
        "codec": "h264",
        "bitrate_kbps": 1500.0,
        "has_video": True,
        "has_audio": True,
    }
    # Fixed argv: probe is exec'd with a JSON print format, never a shell string.
    assert spawn.await_args.args == (
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        media,
    )
    assert "shell" not in spawn.await_args.kwargs
    assert spawn.await_args.kwargs["start_new_session"] is True
    assert spawn.await_args.kwargs["preexec_fn"] is None


async def test_extract_uses_configured_binary_and_timeout_override(tmp_path):
    media = _media_file(tmp_path)
    extractor = FFprobeMetadataExtractor(
        ffprobe_bin="/opt/bin/ffprobe", timeout=99.0, work_root=str(tmp_path)
    )

    with patch("asyncio.create_subprocess_exec", new=_spawn(_probe(b"{}"))):
        await extractor.extract(media, timeout=0.5)

    spawn = AsyncMock(return_value=_probe(b"{}"))
    with patch("asyncio.create_subprocess_exec", new=spawn) as spawn_ref:
        await extractor.extract(media)
    assert spawn_ref.await_args.args[0] == "/opt/bin/ffprobe"


async def test_extract_raises_command_failure_on_nonzero_exit(tmp_path):
    media = _media_file(tmp_path)
    extractor = FFprobeMetadataExtractor(work_root=str(tmp_path))

    with patch(
        "asyncio.create_subprocess_exec",
        new=_spawn(_probe(b"", returncode=1, stderr=b"[mov,mp4] moov atom not found")),
    ):
        with pytest.raises(CommandFailure) as exc:
            await extractor.extract(media)

    assert "ffprobe failed (exit 1)" in str(exc.value)
    assert "moov atom not found" in str(exc.value)


async def test_extract_rejects_probe_output_over_the_capture_limit(tmp_path):
    """A pathological probe emitting >1 MiB is refused, not parsed."""
    media = _media_file(tmp_path)
    extractor = FFprobeMetadataExtractor(work_root=str(tmp_path))
    chatty = b"x" * ((MAX_PROBE_OUTPUT_BYTES + 4096) * 2)

    with patch("asyncio.create_subprocess_exec", new=_spawn(_probe(chatty))):
        with pytest.raises(CommandFailure, match="exceeded capture limit"):
            await extractor.extract(media)


@pytest.mark.parametrize(
    "bad_path",
    [
        "https://cdn.example.com/movie.mp4",
        "ftp://example.com/movie.mp4",
        "/etc/passwd",
        "relative/movie.mp4",
        "",
    ],
)
async def test_extract_refuses_non_local_or_url_inputs(tmp_path, bad_path):
    extractor = FFprobeMetadataExtractor(work_root=str(tmp_path))
    with pytest.raises(UnsafeInput):
        await extractor.extract(bad_path)


async def test_extract_refuses_empty_or_missing_media_file(tmp_path):
    job_dir = tmp_path / "job-empty"
    job_dir.mkdir(parents=True, exist_ok=True)
    empty = job_dir / "empty.mp4"
    empty.write_bytes(b"")
    missing = job_dir / "gone.mp4"
    extractor = FFprobeMetadataExtractor(work_root=str(tmp_path))

    with pytest.raises(UnsafeInput, match="missing or empty media input"):
        await extractor.extract(str(empty))
    with pytest.raises(UnsafeInput, match="missing or empty media input"):
        await extractor.extract(str(missing))


# ---------------------------------------------------------------------------
# _parse(): pure probe-JSON interpretation.
# ---------------------------------------------------------------------------


def _parse(payload: bytes) -> dict:
    return FFprobeMetadataExtractor(work_root="/tmp")._parse(payload)


def test_parse_empty_output_yields_zeroed_envelope():
    """``b""`` decodes as ``{}`` — nothing known, nothing invented."""
    assert _parse(b"") == {
        "duration_seconds": 0.0,
        "container": "",
        "codec": "",
        "bitrate_kbps": 0.0,
        "has_video": False,
        "has_audio": False,
    }


def test_parse_rejects_invalid_json():
    with pytest.raises(CommandFailure, match="invalid JSON"):
        _parse(b"<html>404 not json</html>")


def test_parse_falls_back_to_video_stream_duration_when_format_lacks_it():
    payload = json.dumps(
        {
            "streams": [
                {"codec_type": "video", "duration": "42.25", "codec_name": "vp9"},
                {"codec_type": "audio"},
            ]
        }
    ).encode()
    metadata = _parse(payload)
    assert metadata["duration_seconds"] == 42.25
    assert metadata["bitrate_kbps"] == 0.0
    assert metadata["container"] == ""
    assert metadata["codec"] == "vp9"
    assert metadata["has_video"] is True and metadata["has_audio"] is True


def test_parse_prefers_format_duration_over_stream_duration():
    payload = json.dumps(
        {
            "format": {"duration": "10", "bit_rate": "2000000"},
            "streams": [{"codec_type": "video", "duration": "999"}],
        }
    ).encode()
    metadata = _parse(payload)
    assert metadata["duration_seconds"] == 10.0
    assert metadata["bitrate_kbps"] == 2000.0


@pytest.mark.parametrize("bad", ["not-a-number", None, {"nested": 1}, [1, 2]])
def test_parse_swallows_non_numeric_duration_and_bitrate(bad):
    payload = json.dumps({"format": {"duration": bad, "bit_rate": bad}, "streams": []}).encode()
    metadata = _parse(payload)
    assert metadata["duration_seconds"] == 0.0
    assert metadata["bitrate_kbps"] == 0.0


def test_parse_zero_bitrate_is_not_scaled():
    payload = json.dumps({"format": {"bit_rate": 0}, "streams": []}).encode()
    assert _parse(payload)["bitrate_kbps"] == 0.0


def test_parse_without_any_stream_drops_dimensions():
    payload = json.dumps(
        {"format": {"format_name": "mp4", "duration": "7"}, "streams": []}
    ).encode()
    metadata = _parse(payload)
    assert "width" not in metadata
    assert "height" not in metadata
    assert metadata["has_video"] is False and metadata["has_audio"] is False
    assert metadata["container"] == "mp4"


def test_parse_drops_unknown_probe_keys():
    """Only the whitelisted envelope survives sanitization."""
    payload = json.dumps(
        {
            "format": {"format_name": "mp4", "tags": {"title": "leak-me"}},
            "streams": [{"codec_type": "video", "width": 640, "height": 360, "tags": {}}],
        }
    ).encode()
    metadata = _parse(payload)
    assert set(metadata) <= {
        "duration_seconds",
        "width",
        "height",
        "container",
        "codec",
        "bitrate_kbps",
        "has_video",
        "has_audio",
    }
    assert "tags" not in metadata


def test_parse_strips_control_characters_from_probe_strings():
    payload = json.dumps(
        {"format": {"format_name": "mp4\x00\x07"}, "streams": [{"codec_type": "video"}]}
    ).encode()
    assert _parse(payload)["container"] == "mp4"


def test_parse_truncates_overlong_probe_strings():
    payload = json.dumps(
        {"format": {}, "streams": [{"codec_type": "video", "codec_name": "c" * 5000}]}
    ).encode()
    assert len(_parse(payload)["codec"]) == MAX_METADATA_STRING_LENGTH


# ---------------------------------------------------------------------------
# _parse() -> enforce_technical_limits: the resource ceilings.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("width", 99999, "exceeds limit"),
        ("height", 99999, "exceeds limit"),
        ("bitrate_kbps", 999999, "exceeds limit"),
        ("duration_seconds", 99999.0, "exceeds limit"),
    ],
)
def test_parse_raises_unsafe_input_above_technical_limits(field, value, message):
    payload = json.dumps(
        {
            "format": {"format_name": "mp4"},
            "streams": [{"codec_type": "video", "width": 640, "height": 360}],
        }
    ).encode()
    base = _parse(payload)
    assert base["width"] == 640  # sanity: the baseline probe is acceptable

    probe_stream = {"codec_type": "video", "width": 640, "height": 360}
    if field in ("width", "height"):
        probe_stream[field] = value
    if field == "duration_seconds":
        probe_stream["duration"] = str(value)
    if field == "bitrate_kbps":
        payload = json.dumps({"format": {"bit_rate": str(value * 1000)}, "streams": []}).encode()
    else:
        payload = json.dumps({"format": {}, "streams": [probe_stream]}).encode()

    with pytest.raises(UnsafeInput, match=message):
        _parse(payload)


def test_parse_allows_values_exactly_at_the_ceilings():
    payload = json.dumps(
        {
            "format": {
                "format_name": "mp4",
                "duration": str(MAX_DURATION_SECONDS),
                "bit_rate": str(MAX_BITRATE_KBPS * 1000),
            },
            "streams": [
                {
                    "codec_type": "video",
                    "width": MAX_DIMENSION_PIXELS,
                    "height": MAX_DIMENSION_PIXELS,
                }
            ],
        }
    ).encode()
    metadata = _parse(payload)
    assert metadata["width"] == MAX_DIMENSION_PIXELS
    assert metadata["duration_seconds"] == MAX_DURATION_SECONDS


# ---------------------------------------------------------------------------
# app.core.security — sanitize_metadata / enforce_technical_limits directly.
# ---------------------------------------------------------------------------


def test_sanitize_metadata_keeps_bools_numbers_and_cleans_strings():
    out = sanitize_metadata(
        {
            "has_video": True,
            "width": 1920,
            "bitrate_kbps": 1.5,
            "codec": "ｈ264\x1b[0m",
            "unknown_key": "dropped",
            "none_value": None,
        }
    )
    assert out["has_video"] is True
    assert out["width"] == 1920
    assert out["bitrate_kbps"] == 1.5
    # NFKC folds the fullwidth 'ｈ' to 'h'; control chars are stripped.
    assert out["codec"] == "h264[0m"
    assert "unknown_key" not in out and "none_value" not in out


def test_sanitize_metadata_returns_an_empty_envelope_for_junk():
    assert sanitize_metadata({"nope": 1, "also_nope": object()}) == {}


def test_enforce_technical_limits_ignores_zero_and_non_numeric_values():
    enforce_technical_limits(
        {
            "duration_seconds": 0,
            "width": 0,
            "height": -5,
            "bitrate_kbps": "huge",
        }
    )


def test_enforce_technical_limits_rejects_each_ceiling_independently():
    with pytest.raises(UnsafeInput, match="duration"):
        enforce_technical_limits({"duration_seconds": MAX_DURATION_SECONDS + 1})
    with pytest.raises(UnsafeInput, match="width"):
        enforce_technical_limits({"width": MAX_DIMENSION_PIXELS + 1})
    with pytest.raises(UnsafeInput, match="height"):
        enforce_technical_limits({"height": MAX_DIMENSION_PIXELS + 1})
    with pytest.raises(UnsafeInput, match="bitrate"):
        enforce_technical_limits({"bitrate_kbps": MAX_BITRATE_KBPS + 1})


# ---------------------------------------------------------------------------
# app.core.security — sanitize_storage_key (object-prefix trust boundary).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["clip.mp4", "a" * 512, "café.mkv", "ns:key", "a b c.mp4"])
def test_sanitize_storage_key_accepts_single_relative_segments(key):
    assert sanitize_storage_key(key) == key


def test_sanitize_storage_key_normalizes_unicode_before_validating():
    # NFKC turns the fullwidth solidus into a real separator, which must be
    # rejected *after* normalization, not before.
    with pytest.raises(UnsafeInput, match="single path segment"):
        sanitize_storage_key("clip／evil.mp4")


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("", "non-empty string"),
        (None, "non-empty string"),
        (123, "non-empty string"),
        ("a" * 513, "exceeds 512 chars"),
        ("clip\x00.mp4", "control characters"),
        ("clip\n.mp4", "control characters"),
        ("clip\x7f.mp4", "control characters"),
        ("nested/clip.mp4", "single path segment"),
        ("nested\\clip.mp4", "single path segment"),
        (".hidden", "single path segment"),
        ("/abs/clip.mp4", "single path segment"),
        # NOTE: the "/"-half of the "must be relative" check is unreachable —
        # any key containing "/" is already rejected as a multi-segment key.
        ("~clip.mp4", "must be relative"),
    ],
)
def test_sanitize_storage_key_rejects_unsafe_keys(key, message):
    with pytest.raises(UnsafeInput, match=message):
        sanitize_storage_key(key)


# ---------------------------------------------------------------------------
# app.core.security — is_local_media_path (SSRF / escape guard).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [None, "", 42])
def test_is_local_media_path_rejects_non_strings(path):
    assert is_local_media_path(path, "/work", "/quarantine") is False


@pytest.mark.parametrize(
    "path",
    [
        "https://cdn.example.com/a.mp4",
        "HTTPS://cdn.example.com/a.mp4",
        "file:///etc/passwd",
        "//evil.example.com/a.mp4",
        "\\\\evil.example.com/a.mp4",
    ],
)
def test_is_local_media_path_rejects_urls_and_unc_paths(path):
    assert is_local_media_path(path, "/work", "/quarantine") is False


def test_is_local_media_path_rejects_paths_outside_the_sandboxes():
    assert is_local_media_path("/tmp/elsewhere/a.mp4", "/work", "/quarantine") is False


def test_is_local_media_path_rejects_dotdot_segments():
    assert is_local_media_path("/work/job/../../etc/passwd", "/work", "/q") is False


def test_is_local_media_path_accepts_work_and_quarantine_paths():
    assert is_local_media_path("/work/job/a.mp4", "/work", "/quarantine") is True
    assert is_local_media_path("/quarantine/job/b.mp4", "/work", "/quarantine") is True
    # A '..' that is not a whole path segment is not an escape.
    assert is_local_media_path("/work/job/a..b.mp4", "/work", "/quarantine") is True


# ---------------------------------------------------------------------------
# app.core.security — validate_manifest_no_origin_urls.
# ---------------------------------------------------------------------------


def test_validate_manifest_ignores_a_missing_file(tmp_path):
    assert validate_manifest_no_origin_urls(str(tmp_path / "absent.m3u8")) is None


def test_validate_manifest_accepts_relative_segment_urls(tmp_path):
    manifest = tmp_path / "master.m3u8"
    manifest.write_text("#EXTM3U\n#EXTINF:6,\nseg_000000.ts\n", encoding="utf-8")
    assert validate_manifest_no_origin_urls(str(manifest)) is None


@pytest.mark.parametrize(
    "body",
    [
        "#EXTM3U\nhttps://cdn.example.com/seg.ts\n",
        "#EXTM3U\n//cdn.example.com/seg.ts\n",
    ],
)
def test_validate_manifest_rejects_absolute_segment_urls(tmp_path, body):
    manifest = tmp_path / "master.m3u8"
    manifest.write_text(body, encoding="utf-8")
    with pytest.raises(UnsafeInput, match="absolute URL"):
        validate_manifest_no_origin_urls(str(manifest))


def test_validate_manifest_rejects_oversized_manifests(tmp_path):
    manifest = tmp_path / "master.m3u8"
    manifest.write_text("x" * 4096, encoding="utf-8")
    with patch("app.core.security.MAX_MANIFEST_BYTES", 128):
        with pytest.raises(UnsafeInput, match="exceeds"):
            validate_manifest_no_origin_urls(str(manifest))


# ---------------------------------------------------------------------------
# The two guard helpers the probe adapter's siblings share.
# ---------------------------------------------------------------------------


def test_require_local_input_accepts_a_real_file_inside_the_work_root(tmp_path):
    media = _media_file(tmp_path)
    _require_local_input(media, str(tmp_path), str(tmp_path / "q"))  # no raise


def test_output_job_root_creates_a_private_job_directory(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    out_dir = work / "job-42" / "encoded"
    job_root = _output_job_root(str(out_dir), str(work))
    assert job_root == str(work / "job-42")
    assert out_dir.is_dir()


@pytest.mark.parametrize("candidate", ["shared-work-root", "/tmp/elsewhere"])
def test_output_job_root_refuses_the_shared_root_and_escapes(tmp_path, candidate):
    work = tmp_path / "work"
    work.mkdir()
    shared = work if candidate == "shared-work-root" else tmp_path / "elsewhere"
    shared.mkdir(parents=True, exist_ok=True)
    with pytest.raises(UnsafeInput, match="escapes work root"):
        _output_job_root(str(shared), str(work))
