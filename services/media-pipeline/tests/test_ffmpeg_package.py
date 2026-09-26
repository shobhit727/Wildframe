"""Thumbnail + packaging adapters, and the ``run_process`` guard rails.

Covers the three remaining ``FFmpeg*`` adapters (``FFmpegThumbnailGenerator``,
``FFmpegPackager``) plus every branch of the shared subprocess driver that the
probe/encoder tests do not reach: argv validation, spawn failure, the pipe
capture cap, the per-job disk quota, and the cancellation-safe cleanup path.

No real ffmpeg/ffprobe is executed — ``asyncio.create_subprocess_exec`` is
patched and the fakes are reused from ``test_pipeline_security_hardening``.
"""

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

from app.core.ffmpeg import (
    MAX_PIPE_CAPTURE_BYTES,
    CommandFailure,
    CommandTimeout,
    FFmpegPackager,
    FFmpegThumbnailGenerator,
    OutputLimitExceeded,
    _child_rlimit,
    _read_capped,
    run_process,
)
from app.core.security import UnsafeInput
from tests.test_ffmpeg_probes import ByteStream, ChunkedStream, DataProcess, ExplodingStream
from tests.test_pipeline_security_hardening import FakeProcess


class StuckProcess(DataProcess):
    """A child that never exits, but reaps instantly once killed."""

    def __init__(self) -> None:
        super().__init__(returncode=0)
        self.returncode = None

    async def wait(self) -> int:
        return -9


@pytest.fixture(autouse=True)
def _never_touch_the_real_process_group():
    """Guard: ``cleanup()`` must never SIGKILL a real process group.

    ``run_process`` calls ``os.killpg(proc.pid, SIGKILL)`` on any failure; with
    faked processes the pid is meaningless, so an unpatched call would aim at
    the pytest runner's own process group.
    """
    with patch("os.killpg") as killpg, patch("resource.setrlimit") as setrlimit:
        yield {"killpg": killpg, "setrlimit": setrlimit}


def _media_file(root, name: str = "in.mp4", data: bytes = b"media") -> str:
    path = os.path.join(str(root), "job-1", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _touch(path: str, data: bytes = b"x") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _spawn(process) -> AsyncMock:
    return AsyncMock(return_value=process)


# ---------------------------------------------------------------------------
# FFmpegThumbnailGenerator.
# ---------------------------------------------------------------------------


async def test_thumbnail_generate_writes_a_poster_and_pins_argv(tmp_path):
    work = tmp_path / "work"
    media = _media_file(work)
    out_dir = work / "job-1" / "thumbs"
    poster = _touch(str(out_dir / "poster.jpg"), b"jpeg-bytes")
    gen = FFmpegThumbnailGenerator(ffmpeg_bin="ffmpeg", work_root=str(work))

    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())) as spawn:
        result = await gen.generate(media, str(out_dir))

    assert result == [poster]
    argv = spawn.await_args.args
    assert argv == (
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-threads",
        "1",
        "-t",
        "1",
        "-i",
        media,
        "-frames:v",
        "1",
        "-vf",
        "scale=640:-2",
        poster,
    )
    assert "shell" not in spawn.await_args.kwargs
    assert spawn.await_args.kwargs["start_new_session"] is True


async def test_thumbnail_generate_uses_the_per_call_timeout_override(tmp_path):
    work = tmp_path / "work"
    media = _media_file(work)
    out_dir = work / "job-1" / "thumbs"
    _touch(str(out_dir / "poster.jpg"))
    gen = FFmpegThumbnailGenerator(timeout=900.0, work_root=str(work))

    with patch(
        "app.core.ffmpeg.run_process", new=AsyncMock(return_value=(0, b"", b"", 0, 0))
    ) as run:
        await gen.generate(media, str(out_dir), timeout=3.5)

    assert run.await_args.kwargs["timeout"] == 3.5


async def test_thumbnail_generate_reports_nonzero_exit(tmp_path):
    work = tmp_path / "work"
    media = _media_file(work)
    out_dir = work / "job-1" / "thumbs"
    gen = FFmpegThumbnailGenerator(work_root=str(work))

    with patch(
        "asyncio.create_subprocess_exec",
        new=_spawn(DataProcess(returncode=1, stderr=b"No such filter: scale")),
    ):
        with pytest.raises(CommandFailure, match="ffmpeg thumbnail failed"):
            await gen.generate(media, str(out_dir))


async def test_thumbnail_generate_rejects_a_missing_poster(tmp_path):
    """Exit 0 but no artifact on disk is still a failure, never a success."""
    work = tmp_path / "work"
    media = _media_file(work)
    out_dir = work / "job-1" / "thumbs"
    gen = FFmpegThumbnailGenerator(work_root=str(work))

    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())):
        with pytest.raises(OutputLimitExceeded, match="produced no file"):
            await gen.generate(media, str(out_dir))


async def test_thumbnail_generate_rejects_a_zero_byte_poster(tmp_path):
    work = tmp_path / "work"
    media = _media_file(work)
    out_dir = work / "job-1" / "thumbs"
    _touch(str(out_dir / "poster.jpg"), b"")
    gen = FFmpegThumbnailGenerator(work_root=str(work))

    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())):
        with pytest.raises(OutputLimitExceeded, match="produced no file"):
            await gen.generate(media, str(out_dir))


async def test_thumbnail_generate_refuses_urls(tmp_path):
    gen = FFmpegThumbnailGenerator(work_root=str(tmp_path / "work"))
    with pytest.raises(UnsafeInput):
        await gen.generate("https://cdn.example.com/a.mp4", str(tmp_path / "out"))


# ---------------------------------------------------------------------------
# FFmpegPackager._inputs validation.
# ---------------------------------------------------------------------------


async def test_inputs_rejects_empty_and_non_positive_bitrates(tmp_path):
    packager = FFmpegPackager(work_root=str(tmp_path / "work"))
    for bad in ({}, {0: "/work/job-1/a.mp4"}, {-1: "/work/job-1/a.mp4"}):
        with pytest.raises(UnsafeInput, match="positive integer rendition bitrates"):
            packager._inputs(bad)


async def test_inputs_rejects_non_integer_bitrates(tmp_path):
    work = tmp_path / "work"
    media = _media_file(work)
    packager = FFmpegPackager(work_root=str(work))
    for bad in ({"800": media}, {800.0: media}, {True: media}):
        with pytest.raises(UnsafeInput, match="positive integer rendition bitrates"):
            packager._inputs(bad)


async def test_inputs_rejects_non_local_sources_and_sorts_by_bitrate(tmp_path):
    work = tmp_path / "work"
    low = _media_file(work, "low.mp4")
    high = _media_file(work, "high.mp4")
    packager = FFmpegPackager(work_root=str(work))

    with pytest.raises(UnsafeInput):
        packager._inputs({800: "https://cdn.example.com/a.mp4"})

    assert packager._inputs({4800: high, 400: low}) == [(400, low), (4800, high)]


# ---------------------------------------------------------------------------
# FFmpegPackager.package_hls.
# ---------------------------------------------------------------------------


async def test_package_hls_writes_a_master_playlist_with_peak_bandwidths(tmp_path):
    work = tmp_path / "work"
    low = _media_file(work, "low.mp4")
    high = _media_file(work, "high.mp4")
    out_dir = work / "job-1" / "hls"
    out_dir.mkdir(parents=True)
    for index in (0, 1):
        _touch(str(out_dir / f"index_{index}.m3u8"), b"#EXTM3U\nseg_000000.ts\n")
    packager = FFmpegPackager(work_root=str(work))

    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())) as spawn:
        master = await packager.package_hls({400: low, 4800: high}, str(out_dir))

    assert master == str(out_dir / "master.m3u8")
    with open(master, encoding="utf-8") as handle:
        body = handle.read()
    assert body.splitlines() == [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-STREAM-INF:BANDWIDTH={(400 + 128) * 1200}",
        "index_0.m3u8",
        f"#EXT-X-STREAM-INF:BANDWIDTH={(4800 + 128) * 1200}",
        "index_1.m3u8",
    ]
    # One ffmpeg invocation per rendition, each pinned to a single thread.
    assert spawn.await_count == 2
    first_argv = spawn.await_args_list[0].args
    assert first_argv[0] == "ffmpeg"
    assert first_argv[1:6] == ("-y", "-v", "error", "-threads", "1")
    assert "-f" in first_argv and first_argv[first_argv.index("-f") + 1] == "hls"
    assert first_argv[first_argv.index("-hls_time") + 1] == "6"
    assert first_argv[first_argv.index("-hls_playlist_type") + 1] == "vod"
    assert first_argv[first_argv.index("-c") + 1] == "copy"


async def test_package_hls_forwards_the_timeout_override(tmp_path):
    work = tmp_path / "work"
    source = _media_file(work)
    out_dir = work / "job-1" / "hls"
    _touch(str(out_dir / "index_0.m3u8"))
    packager = FFmpegPackager(timeout=7200.0, work_root=str(work))

    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())):
        await packager.package_hls({400: source}, str(out_dir))

    with patch(
        "app.core.ffmpeg.run_process", new=AsyncMock(return_value=(0, b"", b"", 0, 0))
    ) as run:
        await packager.package_hls({400: source}, str(out_dir), timeout=11.0)
    assert run.await_args.kwargs["timeout"] == 11.0


async def test_package_hls_reports_packaging_failure(tmp_path):
    work = tmp_path / "work"
    source = _media_file(work)
    out_dir = work / "job-1" / "hls"
    _touch(str(out_dir / "index_0.m3u8"))
    packager = FFmpegPackager(work_root=str(work))

    with patch(
        "asyncio.create_subprocess_exec",
        new=_spawn(DataProcess(returncode=1, stderr=b"Invalid data found")),
    ):
        with pytest.raises(CommandFailure, match="Invalid data found"):
            await packager.package_hls({400: source}, str(out_dir))


async def test_package_hls_rejects_a_missing_media_playlist(tmp_path):
    work = tmp_path / "work"
    source = _media_file(work)
    out_dir = work / "job-1" / "hls"
    packager = FFmpegPackager(work_root=str(work))

    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())):
        with pytest.raises(OutputLimitExceeded, match="produced no manifest"):
            await packager.package_hls({400: source}, str(out_dir))


async def test_package_hls_rejects_an_origin_url_in_the_playlist(tmp_path):
    """A playlist referencing an absolute origin URL is a playback-auth bypass."""
    work = tmp_path / "work"
    source = _media_file(work)
    out_dir = work / "job-1" / "hls"
    _touch(str(out_dir / "index_0.m3u8"), b"#EXTM3U\nhttps://cdn.example.com/seg.ts\n")
    packager = FFmpegPackager(work_root=str(work))

    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())):
        with pytest.raises(UnsafeInput, match="absolute URL"):
            await packager.package_hls({400: source}, str(out_dir))


# ---------------------------------------------------------------------------
# FFmpegPackager.package_dash.
# ---------------------------------------------------------------------------


async def test_package_dash_maps_every_rendition_and_returns_the_mpd(tmp_path):
    work = tmp_path / "work"
    low = _media_file(work, "low.mp4")
    high = _media_file(work, "high.mp4")
    out_dir = work / "job-1" / "dash"
    out_dir.mkdir(parents=True)
    manifest = _touch(str(out_dir / "manifest.mpd"), b"<MPD/>")
    packager = FFmpegPackager(work_root=str(work))

    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())) as spawn:
        result = await packager.package_dash({400: low, 4800: high}, str(out_dir))

    assert result == manifest
    argv = spawn.await_args.args
    assert argv[0] == "ffmpeg"
    assert argv[1:6] == ("-y", "-v", "error", "-threads", "1")
    # Inputs appear in bitrate order, one -i each, then one -map per rendition.
    inputs = [argv[i + 1] for i, token in enumerate(argv) if token == "-i"]
    assert inputs == [low, high]
    maps = [argv[i + 1] for i, token in enumerate(argv) if token == "-map"]
    assert maps == ["0:v:0", "1:v:0", "0:a:0?"]
    assert argv[-1] == manifest
    assert argv[argv.index("-f") + 1] == "dash"
    assert argv[argv.index("-seg_duration") + 1] == "6"
    assert argv[argv.index("-use_template") + 1] == "1"
    assert argv[argv.index("-use_timeline") + 1] == "1"


async def test_package_dash_rejects_a_missing_manifest(tmp_path):
    work = tmp_path / "work"
    source = _media_file(work)
    out_dir = work / "job-1" / "dash"
    packager = FFmpegPackager(work_root=str(work))

    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())):
        with pytest.raises(OutputLimitExceeded, match="produced no manifest"):
            await packager.package_dash({400: source}, str(out_dir))


async def test_package_dash_rejects_an_origin_url_in_the_manifest(tmp_path):
    work = tmp_path / "work"
    source = _media_file(work)
    out_dir = work / "job-1" / "dash"
    _touch(str(out_dir / "manifest.mpd"), b"<MPD><BaseURL>https://cdn.example.com/</BaseURL></MPD>")
    packager = FFmpegPackager(work_root=str(work))

    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())):
        with pytest.raises(UnsafeInput, match="absolute URL"):
            await packager.package_dash({400: source}, str(out_dir))


async def test_package_dash_refuses_urls(tmp_path):
    packager = FFmpegPackager(work_root=str(tmp_path / "work"))
    with pytest.raises(UnsafeInput):
        await packager.package_dash({400: "https://cdn.example.com/a.mp4"}, str(tmp_path / "o"))


# ---------------------------------------------------------------------------
# run_process(): argv / resource-ceiling validation before any spawn.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "timeout", "kwargs", "message"),
    [
        ([], 1.0, {}, "nonempty argv and positive timeout required"),
        ([None], 1.0, {}, "nonempty argv and positive timeout required"),
        ([1234], 1.0, {}, "nonempty argv and positive timeout required"),
        (["ffmpeg"], 0, {}, "nonempty argv and positive timeout required"),
        (["ffmpeg"], -1.0, {}, "nonempty argv and positive timeout required"),
        (["ffmpeg"], 1.0, {"max_file_bytes": -1}, "resource ceilings cannot be negative"),
        (["ffmpeg"], 1.0, {"disk_quota_bytes": -1}, "resource ceilings cannot be negative"),
    ],
)
async def test_run_process_rejects_invalid_limits_without_spawning(argv, timeout, kwargs, message):
    spawn = AsyncMock()
    with patch("asyncio.create_subprocess_exec", new=spawn):
        with pytest.raises(CommandFailure, match=message):
            await run_process(argv, timeout=timeout, **kwargs)
    spawn.assert_not_awaited()


async def test_run_process_wraps_a_spawn_oserror_as_command_failure():
    with patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=OSError(2, "No such file or directory")),
    ):
        with pytest.raises(CommandFailure, match="failed to start ffmpeg"):
            await run_process(["ffmpeg", "-version"], timeout=1.0)


async def test_run_process_passes_env_and_cwd_through():
    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())) as spawn:
        await run_process(["true"], timeout=1.0, env={"A": "1"}, cwd="/tmp")
    kwargs = spawn.await_args.kwargs
    assert kwargs["env"] == {"A": "1"}
    assert kwargs["cwd"] == "/tmp"
    assert kwargs["stdin"] is not None and kwargs["stdout"] is not None


# ---------------------------------------------------------------------------
# _read_capped: the pipe capture cap.
# ---------------------------------------------------------------------------


async def test_read_capped_returns_short_streams_untouched():
    captured, total = await _read_capped(ByteStream(b"short"), MAX_PIPE_CAPTURE_BYTES)
    assert captured == b"short"
    assert total == 5


async def test_read_capped_counts_every_byte_of_an_over_limit_stream():
    """The reported total is the true volume, not the retained volume."""
    captured, total = await _read_capped(ByteStream(b"abcdefghij" * 10), limit=8)
    assert total == 100
    # BUG (app/core/ffmpeg.py:78-82): the read that trips the cap is dropped on
    # the floor. `chunks` is replaced by the join of the *previous* chunks only,
    # so when a single read already exceeds the limit nothing is retained at
    # all. Documented here as-is; production code is not modified.
    assert captured == b""


async def test_read_capped_retains_the_cap_only_for_granular_reads():
    """With per-read granularity the cap does hold — and the count stays honest."""
    stream = ChunkedStream(b"abcdefghij" * 10, chunk=4)
    captured, total = await _read_capped(stream, limit=8)
    assert captured == b"abcdefgh"
    assert total == 100
    assert stream.reads > 1, "exercised the multi-chunk collapse path"


async def test_read_capped_under_retains_when_a_read_straddles_the_cap():
    """BUG (app/core/ffmpeg.py:78-82): retention stops short of the cap.

    The first 100 bytes are available, but 60 are kept: after the collapse the
    stream is never appended to again, so the retained tail is frozen.
    """
    captured, total = await _read_capped(ChunkedStream(b"y" * 200, chunk=60), limit=100)
    assert total == 200
    assert len(captured) == 60


@pytest.mark.parametrize("exc", [BrokenPipeError("gone"), ConnectionResetError("reset")])
async def test_read_capped_tolerates_a_broken_upstream(exc):
    captured, total = await _read_capped(ExplodingStream(exc), MAX_PIPE_CAPTURE_BYTES)
    assert captured == b""
    assert total == 0


async def test_run_process_caps_both_pipes_and_counts_the_raw_volume():
    noisy = b"x" * 4096
    process = DataProcess()
    process.stdout = ChunkedStream(noisy, chunk=8)
    process.stderr = ChunkedStream(b"err", chunk=8)
    with patch("asyncio.create_subprocess_exec", new=_spawn(process)):
        rc, out, err, out_total, err_total = await run_process(
            ["ffmpeg"], timeout=1.0, max_pipe_bytes=16
        )
    assert rc == 0
    assert out == b"x" * 16 and out_total == 4096
    assert err == b"err" and err_total == 3


# ---------------------------------------------------------------------------
# check_disk: the per-job disk quota.
# ---------------------------------------------------------------------------


async def test_run_process_passes_the_disk_quota_check_under_the_limit(tmp_path):
    _touch(str(tmp_path / "seg_000000.ts"), b"x" * 10)
    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())):
        rc, *_ = await run_process(
            ["ffmpeg"], timeout=1.0, disk_root=str(tmp_path), disk_quota_bytes=1 << 20
        )
    assert rc == 0


async def test_run_process_fails_the_job_when_the_disk_quota_is_reached(tmp_path):
    _touch(str(tmp_path / "hls" / "seg_000000.ts"), b"x" * 4096)
    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())) as spawn:
        with pytest.raises(OutputLimitExceeded, match="job disk quota reached"):
            await run_process(["ffmpeg"], timeout=1.0, disk_root=str(tmp_path), disk_quota_bytes=1)
    # Over-quota is a failure, so the process group is still reaped.
    assert spawn.await_count == 1


async def test_thumbnail_generate_surfaces_the_disk_quota_through_the_adapter(tmp_path):
    work = tmp_path / "work"
    media = _media_file(work)
    out_dir = work / "job-1" / "thumbs"
    _touch(str(out_dir / "poster.jpg"))
    gen = FFmpegThumbnailGenerator(disk_quota_bytes=1, work_root=str(work))

    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())):
        with pytest.raises(OutputLimitExceeded, match="disk quota"):
            await gen.generate(media, str(out_dir))


# ---------------------------------------------------------------------------
# cleanup(): the failure path must reap without masking the original error.
# ---------------------------------------------------------------------------


async def test_timeout_kills_the_process_group_then_raises_command_timeout():
    with patch("asyncio.create_subprocess_exec", new=_spawn(StuckProcess())):
        with pytest.raises(CommandTimeout, match="wall-clock budget"):
            await run_process(["ffmpeg"], timeout=0.05)


async def test_cleanup_survives_a_failing_killpg():
    with (
        patch("asyncio.create_subprocess_exec", new=_spawn(StuckProcess())),
        patch("os.killpg", side_effect=OSError("permission denied")),
    ):
        with pytest.raises(CommandTimeout):
            await run_process(["ffmpeg"], timeout=0.05)


async def test_cleanup_reawaits_the_child_even_if_the_shield_is_cancelled():
    """A cancellation racing the cleanup must not orphan the reader tasks.

    ``asyncio.shield`` is what makes ``await cleanup_task`` uncancellable; when
    the surrounding task is cancelled anyway the handler must re-await the
    cleanup task directly and still surface the original CommandTimeout.
    """
    with (
        patch("asyncio.create_subprocess_exec", new=_spawn(StuckProcess())),
        patch("os.killpg") as killpg,
        patch("asyncio.shield", side_effect=asyncio.CancelledError),
    ):
        with pytest.raises(CommandTimeout, match="wall-clock budget"):
            await run_process(["ffmpeg"], timeout=0.05)
    assert killpg.called


# ---------------------------------------------------------------------------
# _child_rlimit: the preexec closure the child would run.
# ---------------------------------------------------------------------------


def test_child_rlimit_applies_both_ceilings(monkeypatch):
    apply = _child_rlimit(1 << 30, 1 << 20)
    with patch("resource.setrlimit") as setrlimit:
        apply()
    assert setrlimit.call_count == 2


def test_child_rlimit_skips_the_memory_ceiling_when_unset():
    apply = _child_rlimit(None, 1 << 20)
    with patch("resource.setrlimit") as setrlimit:
        apply()
    assert setrlimit.call_count == 1


def test_child_rlimit_is_none_when_no_ceiling_is_requested():
    assert _child_rlimit(None, 0) is None
    assert _child_rlimit(0, 0) is None


# ---------------------------------------------------------------------------
# The encoder's own error branches, reached through its real subprocess path.
# ---------------------------------------------------------------------------


async def test_encode_reports_a_nonzero_exit(tmp_path):
    from app.core.ffmpeg import FFmpegMultiBitrateEncoder

    work = tmp_path / "work"
    media = _media_file(work)
    out_dir = work / "job-1" / "encoded"
    _touch(str(out_dir / "v_400.mp4"))
    encoder = FFmpegMultiBitrateEncoder(work_root=str(work))

    with patch(
        "asyncio.create_subprocess_exec",
        new=_spawn(DataProcess(returncode=1, stderr=b"Conversion failed")),
    ):
        with pytest.raises(CommandFailure, match="Conversion failed"):
            await encoder.encode(media, str(out_dir), [400])


async def test_encode_rejects_a_missing_rendition_file(tmp_path):
    from app.core.ffmpeg import FFmpegMultiBitrateEncoder

    work = tmp_path / "work"
    media = _media_file(work)
    out_dir = work / "job-1" / "encoded"
    encoder = FFmpegMultiBitrateEncoder(work_root=str(work))

    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())):
        with pytest.raises(OutputLimitExceeded, match="produced no output file"):
            await encoder.encode(media, str(out_dir), [400])


async def test_encode_enforces_the_output_size_cap(tmp_path):
    from app.core.ffmpeg import FFmpegMultiBitrateEncoder

    work = tmp_path / "work"
    media = _media_file(work)
    out_dir = work / "job-1" / "encoded"
    _touch(str(out_dir / "v_400.mp4"), b"x" * 2048)
    encoder = FFmpegMultiBitrateEncoder(max_output_bytes=1024, work_root=str(work))

    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())):
        with pytest.raises(OutputLimitExceeded, match="exceeds 1024 byte output cap"):
            await encoder.encode(media, str(out_dir), [400])


async def test_encode_takes_the_tighter_of_the_two_output_caps(tmp_path):
    from app.core.ffmpeg import FFmpegMultiBitrateEncoder

    work = tmp_path / "work"
    media = _media_file(work)
    out_dir = work / "job-1" / "encoded"
    _touch(str(out_dir / "v_400.mp4"), b"x" * 100)
    encoder = FFmpegMultiBitrateEncoder(max_output_bytes=1 << 20, work_root=str(work))

    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())):
        outputs = await encoder.encode(media, str(out_dir), [400], max_output_bytes=4096)
    assert outputs == {400: str(out_dir / "v_400.mp4")}


@pytest.mark.parametrize("bitrates", [[], [0], [-400], ["800"], [800.0]])
async def test_encode_refuses_invalid_bitrate_plans(tmp_path, bitrates):
    from app.core.ffmpeg import FFmpegMultiBitrateEncoder

    work = tmp_path / "work"
    media = _media_file(work)
    encoder = FFmpegMultiBitrateEncoder(work_root=str(work))
    with pytest.raises(UnsafeInput, match="positive integer rendition bitrates"):
        await encoder.encode(media, str(work / "job-1" / "encoded"), bitrates)


async def test_encode_produces_one_output_per_bitrate(tmp_path):
    from app.core.ffmpeg import FFmpegMultiBitrateEncoder

    work = tmp_path / "work"
    media = _media_file(work)
    out_dir = work / "job-1" / "encoded"
    for bitrate in (400, 800):
        _touch(str(out_dir / f"v_{bitrate}.mp4"), b"x" * 4)
    encoder = FFmpegMultiBitrateEncoder(work_root=str(work))

    with patch("asyncio.create_subprocess_exec", new=_spawn(DataProcess())) as spawn:
        outputs = await encoder.encode(media, str(out_dir), [400, 800])

    assert outputs == {
        400: str(out_dir / "v_400.mp4"),
        800: str(out_dir / "v_800.mp4"),
    }
    assert spawn.await_count == 2


def test_fake_process_is_still_the_shared_harness():
    """The fakes these tests rely on are the ones the hardening suite uses."""
    assert FakeProcess().returncode == 0
    assert issubclass(DataProcess, FakeProcess)
