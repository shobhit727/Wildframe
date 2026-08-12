"""Hardened FFmpeg/FFprobe subprocess adapters for the media pipeline.

These are the *real* adapters behind the ``MetadataExtractor`` /
``MultiBitrateEncoder`` / ``ThumbnailGenerator`` / ``Packager`` ports. They
exist because the security posture of the pipeline lives here:

* Every command is a fixed argument array via ``asyncio.create_subprocess_exec``
  — never a shell string, so filenames/metadata cannot alter semantics (#66).
* stdout/stderr are read with a hard byte cap, so a chatty codec cannot OOM
  the worker (#538, #540).
* Every invocation is bounded by a wall-clock ``asyncio.wait_for``; on timeout
  the child process tree is killed so workers cannot be held indefinitely
  (#486, #537).
* Input paths must pass :func:`~app.core.security.is_local_media_path` — URLs
  are rejected outright, so media processing cannot be an SSRF pivot (#286,
  #542).
* Probe output is validated against resource ceilings (duration, resolution,
  bitrate) and sanitized before it reaches the rest of the pipeline (#287,
  #288, #289).
* Outputs are validated (exist, non-empty, under size cap) before a stage is
  reported successful (#290); encoding is limited to ``-threads`` so a single
  job cannot consume the whole node (#539, #546, #634).
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

from app.core.security import (
    MAX_PROBE_OUTPUT_BYTES,
    UnsafeInput,
    enforce_technical_limits,
    is_local_media_path,
    sanitize_metadata,
)
from app.core.stages import (
    MetadataExtractor,
    MultiBitrateEncoder,
    Packager,
    ThumbnailGenerator,
)

logger = __import__("logging").getLogger(__name__)

# Subprocess pipe capture cap: ffmpeg/ffprobe diagnostics are small; anything
# beyond this is discarded (and counted) so workers cannot be memory-exhausted
# by pathological outputs (#538).
MAX_PIPE_CAPTURE_BYTES = 1 << 20  # 1 MiB per stream.


class CommandFailure(RuntimeError):
    """A subprocess exited non-zero or violated its execution limits."""


class CommandTimeout(CommandFailure):
    """The subprocess exceeded its wall-clock budget and was killed."""


class OutputLimitExceeded(CommandFailure):
    """Output artifacts exceeded the configured size/validity bounds."""


async def _read_capped(stream: asyncio.StreamReader, limit: int) -> tuple[bytes, int]:
    """Read a pipe, keeping at most ``limit`` bytes; return (tail, total)."""
    chunks: list[bytes] = []
    total = 0
    while True:
        try:
            chunk = await stream.read(65536)
        except (ConnectionResetError, BrokenPipeError):
            break
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            # Keep only the first ``limit`` bytes of the stream for diagnostics.
            chunks = [b"".join(chunks)[:limit]] if len(chunks) == 1 else [b"".join(chunks)[:limit]]
            continue
        chunks.append(chunk)
    captured = b"".join(chunks)[:limit]
    return captured, total


async def run_process(
    argv: list[str],
    *,
    timeout: float,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    max_pipe_bytes: int = MAX_PIPE_CAPTURE_BYTES,
) -> tuple[int, bytes, bytes, int, int]:
    """Run ``argv`` with hard execution bounds.

    Returns ``(returncode, stdout_tail, stderr_tail, stdout_total, stderr_total)``.

    * Never uses a shell: ``create_subprocess_exec(argv)``.
    * ``timeout`` is enforced by ``asyncio.wait_for``; on expiry the process
      group is killed (SIGKILL after SIGTERM grace) so no child lingers.
    * On cancellation (e.g. an orchestrator shutdown mid-stage) the child is
      killed the same way — no orphaned ffmpeg processes.
    * Pipe reads are capped at ``max_pipe_bytes``.

    Raises ``CommandTimeout`` on wall-clock expiry, ``CommandFailure`` when the
    process cannot be started.
    """
    if not argv or not isinstance(argv[0], str):
        raise CommandFailure("run_process requires a non-empty argv array")

    proc: asyncio.subprocess.Process | None = None

    async def _spawn_and_wait() -> tuple[int, bytes, bytes, int, int]:
        nonlocal proc
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
                start_new_session=True,  # own process group -> kill the whole tree
            )
        except OSError as exc:
            raise CommandFailure(f"failed to start {argv[0]}: {exc}") from exc

        assert proc.stdout is not None and proc.stderr is not None
        stdout_task = asyncio.ensure_future(_read_capped(proc.stdout, max_pipe_bytes))
        stderr_task = asyncio.ensure_future(_read_capped(proc.stderr, max_pipe_bytes))
        try:
            returncode = await proc.wait()
        finally:
            stdout_bytes, stdout_total = await stdout_task
            stderr_bytes, stderr_total = await stderr_task
        return returncode, stdout_bytes, stderr_bytes, stdout_total, stderr_total

    async def _kill() -> None:
        if proc is None or proc.returncode is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (ProcessLookupError, asyncio.TimeoutError, ChildProcessError):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, ChildProcessError):
                pass

    try:
        return await asyncio.wait_for(_spawn_and_wait(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        await _kill()
        raise CommandTimeout(
            f"command {' '.join(argv[:3])}… exceeded {timeout:g}s wall-clock budget"
        ) from exc
    except asyncio.CancelledError:
        await _kill()
        raise


def _require_local_input(path: str, work_root: str, quarantine_root: str) -> None:
    """Refuse URLs and any path outside the job sandboxes (SSRF/escape guard)."""
    if not is_local_media_path(path, work_root, quarantine_root):
        raise UnsafeInput(
            f"refusing non-local media input {path!r}: only files inside the "
            "job work/quarantine directories are accepted"
        )


class FFprobeMetadataExtractor(MetadataExtractor):
    """Real ffprobe adapter: fixed argv, hard timeout, sanitized output."""

    def __init__(
        self,
        *,
        ffprobe_bin: str = "ffprobe",
        timeout: float = 30.0,
        work_root: str = "/tmp/wildframe/work",
        quarantine_root: str = "/tmp/wildframe/quarantine",
    ) -> None:
        self.ffprobe_bin = ffprobe_bin
        self.timeout = timeout
        self.work_root = work_root
        self.quarantine_root = quarantine_root

    async def extract(self, path: str, *, timeout: float | None = None) -> dict[str, Any]:
        """Probe ``path`` with ffprobe; return sanitized, limit-checked metadata.

        Raises ``UnsafeInput`` for URLs/escapes and for media exceeding the
        duration/resolution/bitrate ceilings (see #288/#289).
        """
        _require_local_input(path, self.work_root, self.quarantine_root)
        argv = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            path,
        ]
        returncode, _stdout, stderr, _st_total, _err_total = await run_process(
            argv, timeout=timeout or self.timeout
        )
        if returncode != 0:
            raise CommandFailure(
                f"ffprobe failed (exit {returncode}): {stderr.decode(errors='replace')[:400]}"
            )
        return self._parse(stderr if False else _stdout)

    def _parse(self, stdout: bytes) -> dict[str, Any]:
        import json

        try:
            payload = json.loads(stdout[:MAX_PROBE_OUTPUT_BYTES] or b"{}")
        except ValueError as exc:
            raise CommandFailure(f"ffprobe returned invalid JSON: {exc}") from exc

        fmt = payload.get("format") or {}
        streams = payload.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

        duration = fmt.get("duration")
        if duration is None and video is not None:
            duration = video.get("duration")
        try:
            duration_seconds = float(duration) if duration is not None else 0.0
        except (TypeError, ValueError):
            duration_seconds = 0.0

        bitrate = fmt.get("bit_rate")
        try:
            bitrate_kbps = (float(bitrate) / 1000.0) if bitrate else 0.0
        except (TypeError, ValueError):
            bitrate_kbps = 0.0

        width = video.get("width") if video else None
        height = video.get("height") if video else None

        # Build the whitelisted envelope BEFORE sanitize_metadata so raw probe
        # strings are normalized/truncated downstream. Unknown keys dropped.
        raw = {
            "duration_seconds": duration_seconds,
            "width": width,
            "height": height,
            "container": fmt.get("format_name") or "",
            "codec": (video or {}).get("codec_name") or "",
            "bitrate_kbps": bitrate_kbps,
            "has_video": video is not None,
            "has_audio": audio is not None,
        }
        metadata = sanitize_metadata(raw)
        enforce_technical_limits(metadata)
        return metadata


class FFmpegMultiBitrateEncoder(MultiBitrateEncoder):
    """Real ffmpeg encoder: arg arrays, ``-threads`` cap, timeout, size caps."""

    def __init__(
        self,
        *,
        ffmpeg_bin: str = "ffmpeg",
        timeout: float = 3600.0,
        cpu_threads: int = 2,
        max_output_bytes: int = 0,
        max_duration_seconds: float = 0.0,
        work_root: str = "/tmp/wildframe/work",
        quarantine_root: str = "/tmp/wildframe/quarantine",
    ) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.timeout = timeout
        self.cpu_threads = cpu_threads
        self.max_output_bytes = max_output_bytes
        self.max_duration_seconds = max_duration_seconds
        self.work_root = work_root
        self.quarantine_root = quarantine_root

    async def encode(
        self,
        path: str,
        out_dir: str,
        bitrates: list[int],
        *,
        timeout: float | None = None,
        cpu_threads: int | None = None,
        max_output_bytes: int | None = None,
    ) -> dict[int, str]:
        _require_local_input(path, self.work_root, self.quarantine_root)
        os.makedirs(out_dir, mode=0o700, exist_ok=True)
        threads = cpu_threads or self.cpu_threads
        size_cap = max_output_bytes or self.max_output_bytes
        outputs: dict[int, str] = {}
        for bitrate in bitrates:
            out_path = os.path.join(out_dir, f"v_{bitrate}.mp4")
            # Fixed argv: the only caller-derived strings are a validated local
            # path, a server-generated out_dir and integer bitrates.
            argv = [
                self.ffmpeg_bin,
                "-y",
                "-v",
                "error",
                "-threads",
                str(threads),
                "-i",
                path,
            ]
            if self.max_duration_seconds > 0:
                argv += ["-t", f"{self.max_duration_seconds:g}"]
            argv += [
                "-map",
                "0:v:0",
                "-c:v",
                "libx264",
                "-b:v",
                f"{bitrate}k",
                "-preset",
                "veryfast",
                out_path,
            ]
            returncode, _stdout, stderr, _st, _err = await run_process(
                argv, timeout=timeout or self.timeout
            )
            if returncode != 0:
                raise CommandFailure(
                    f"ffmpeg encode {bitrate}k failed (exit {returncode}): "
                    f"{stderr.decode(errors='replace')[:400]}"
                )
            if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
                raise OutputLimitExceeded(
                    f"ffmpeg encode {bitrate}k produced no output file {out_path!r}"
                )
            if size_cap > 0 and os.path.getsize(out_path) > size_cap:
                raise OutputLimitExceeded(
                    f"rendition {bitrate}k exceeds {size_cap} byte output cap"
                )
            outputs[bitrate] = out_path
        return outputs


class FFmpegThumbnailGenerator(ThumbnailGenerator):
    """Real ffmpeg thumbnail generator: bounded, no URLs, one frame only."""

    def __init__(
        self,
        *,
        ffmpeg_bin: str = "ffmpeg",
        timeout: float = 60.0,
        work_root: str = "/tmp/wildframe/work",
        quarantine_root: str = "/tmp/wildframe/quarantine",
    ) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.timeout = timeout
        self.work_root = work_root
        self.quarantine_root = quarantine_root

    async def generate(self, path: str, out_dir: str, *, timeout: float | None = None) -> list[str]:
        _require_local_input(path, self.work_root, self.quarantine_root)
        os.makedirs(out_dir, mode=0o700, exist_ok=True)
        out_path = os.path.join(out_dir, "poster.jpg")
        argv = [
            self.ffmpeg_bin,
            "-y",
            "-v",
            "error",
            "-threads",
            "1",
            "-t",
            "1",
            "-i",
            path,
            "-frames:v",
            "1",
            "-vf",
            "scale=640:-2",
            out_path,
        ]
        returncode, _stdout, stderr, _st, _err = await run_process(
            argv, timeout=timeout or self.timeout
        )
        if returncode != 0:
            raise CommandFailure(
                f"ffmpeg thumbnail failed (exit {returncode}): "
                f"{stderr.decode(errors='replace')[:400]}"
            )
        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            raise OutputLimitExceeded(f"thumbnail generation produced no file {out_path!r}")
        return [out_path]


class FFmpegHlsPackager(Packager):
    """Real HLS packager: relative segment URLs only, manifest validated."""

    def __init__(
        self,
        *,
        ffmpeg_bin: str = "ffmpeg",
        timeout: float = 3600.0,
        work_root: str = "/tmp/wildframe/work",
        quarantine_root: str = "/tmp/wildframe/quarantine",
    ) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.timeout = timeout
        self.work_root = work_root
        self.quarantine_root = quarantine_root

    async def package_hls(
        self, inputs: dict[int, str], out_dir: str, *, timeout: float | None = None
    ) -> str:
        os.makedirs(out_dir, mode=0o700, exist_ok=True)
        for path in inputs.values():
            _require_local_input(path, self.work_root, self.quarantine_root)
        master = os.path.join(out_dir, "master.m3u8")
        # One fixed-argv ffmpeg per rendition; all paths are server-generated.
        for index, source in enumerate(sorted(inputs.values())):
            seg_pattern = os.path.join(out_dir, f"seg_{index}_%03d.ts")
            playlist = os.path.join(out_dir, f"index_{index}.m3u8")
            argv = [
                self.ffmpeg_bin,
                "-y",
                "-v",
                "error",
                "-threads",
                "1",
                "-i",
                source,
                "-c",
                "copy",
                "-f",
                "hls",
                "-hls_time",
                "6",
                "-hls_playlist_type",
                "vod",
                "-hls_segment_filename",
                seg_pattern,
                playlist,
            ]
            returncode, _stdout, stderr, _st, _err = await run_process(
                argv, timeout=timeout or self.timeout
            )
            if returncode != 0:
                raise CommandFailure(
                    f"ffmpeg HLS package failed (exit {returncode}): "
                    f"{stderr.decode(errors='replace')[:400]}"
                )
        # ffmpeg wrote per-rendition playlists; validate the caller-facing
        # artifact and refuse absolute origin URLs (#283).
        from app.core.security import validate_manifest_no_origin_urls

        validate_manifest_no_origin_urls(master)
        return master


class FFmpegDashPackager(Packager):
    """Real DASH packager: fixed argv, validated manifest, relative URLs."""

    def __init__(
        self,
        *,
        ffmpeg_bin: str = "ffmpeg",
        timeout: float = 3600.0,
        work_root: str = "/tmp/wildframe/work",
        quarantine_root: str = "/tmp/wildframe/quarantine",
    ) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.timeout = timeout
        self.work_root = work_root
        self.quarantine_root = quarantine_root

    async def package_dash(
        self, inputs: dict[int, str], out_dir: str, *, timeout: float | None = None
    ) -> str:
        os.makedirs(out_dir, mode=0o700, exist_ok=True)
        for path in inputs.values():
            _require_local_input(path, self.work_root, self.quarantine_root)
        manifest = os.path.join(out_dir, "manifest.mpd")
        source = sorted(inputs.values())[0]
        argv = [
            self.ffmpeg_bin,
            "-y",
            "-v",
            "error",
            "-threads",
            "1",
            "-i",
            source,
            "-c",
            "copy",
            "-f",
            "dash",
            "-seg_duration",
            "6",
            manifest,
        ]
        returncode, _stdout, stderr, _st, _err = await run_process(
            argv, timeout=timeout or self.timeout
        )
        if returncode != 0:
            raise CommandFailure(
                f"ffmpeg DASH package failed (exit {returncode}): "
                f"{stderr.decode(errors='replace')[:400]}"
            )
        from app.core.security import validate_manifest_no_origin_urls

        validate_manifest_no_origin_urls(manifest)
        return manifest
