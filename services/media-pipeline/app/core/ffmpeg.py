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


def _child_rlimit(memory_limit_bytes: int | None, max_file_bytes: int = 0):
    """Apply kernel-enforced address-space and per-file output ceilings."""
    import resource

    def _apply() -> None:
        if memory_limit_bytes:
            resource.setrlimit(resource.RLIMIT_AS, (memory_limit_bytes, memory_limit_bytes))
        if max_file_bytes:
            resource.setrlimit(resource.RLIMIT_FSIZE, (max_file_bytes, max_file_bytes))

    return _apply if memory_limit_bytes or max_file_bytes else None


async def run_process(
    argv: list[str],
    *,
    timeout: float,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    max_pipe_bytes: int = MAX_PIPE_CAPTURE_BYTES,
    memory_limit_bytes: int | None = None,
    max_file_bytes: int = 0,
    disk_root: str | None = None,
    disk_quota_bytes: int = 0,
) -> tuple[int, bytes, bytes, int, int]:
    """Run bounded pipes/process tree; kill and reap before draining on failure."""
    if not argv or not isinstance(argv[0], str) or timeout <= 0:
        raise CommandFailure("nonempty argv and positive timeout required")
    if max_file_bytes < 0 or disk_quota_bytes < 0:
        raise CommandFailure("resource ceilings cannot be negative")
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
            start_new_session=True,
            preexec_fn=_child_rlimit(memory_limit_bytes, max_file_bytes),
        )
    except OSError as exc:
        raise CommandFailure(f"failed to start {argv[0]}: {exc}") from exc
    assert proc.stdout is not None and proc.stderr is not None
    readers = [
        asyncio.create_task(_read_capped(stream, max_pipe_bytes))
        for stream in (proc.stdout, proc.stderr)
    ]

    def check_disk() -> None:
        if disk_root and disk_quota_bytes:
            size = sum(
                os.path.getsize(os.path.join(root, name))
                for root, _, files in os.walk(disk_root)
                for name in files
            )
            if size >= disk_quota_bytes:
                raise OutputLimitExceeded("job disk quota reached")

    async def collect() -> tuple[int, bytes, bytes, int, int]:
        while proc.returncode is None:
            check_disk()
            await asyncio.sleep(0.1)
        check_disk()
        stdout, stderr = await asyncio.gather(*readers)
        return proc.returncode, stdout[0], stderr[0], stdout[1], stderr[1]

    async def cleanup() -> None:
        # Kill the group even when its leader exited: descendants may hold pipes.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        await proc.wait()

    try:
        return await asyncio.wait_for(collect(), timeout=timeout)
    except BaseException as exc:
        cleanup_task = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            await cleanup_task
        if isinstance(exc, asyncio.TimeoutError):
            raise CommandTimeout(f"command exceeded {timeout:g}s wall-clock budget") from exc
        raise


def _require_local_input(path: str, work_root: str, quarantine_root: str) -> None:
    """Refuse URLs and any path outside the job sandboxes (SSRF/escape guard)."""
    if not is_local_media_path(path, work_root, quarantine_root):
        raise UnsafeInput(
            f"refusing non-local media input {path!r}: only files inside the "
            "job work/quarantine directories are accepted"
        )
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise UnsafeInput(f"missing or empty media input: {path!r}")


def _output_job_root(out_dir: str, work_root: str) -> str:
    """Require outputs below a job directory, not the shared work root."""
    root = os.path.realpath(work_root)
    target = os.path.realpath(out_dir)
    if os.path.commonpath([root, target]) != root or target == root:
        raise UnsafeInput("output directory escapes work root")
    job_root = os.path.join(root, os.path.relpath(target, root).split(os.sep)[0])
    os.makedirs(target, mode=0o700, exist_ok=True)
    return job_root


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
        if _st_total > MAX_PROBE_OUTPUT_BYTES:
            raise CommandFailure("ffprobe output exceeded capture limit")
        return self._parse(_stdout)

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
        memory_limit_bytes: int | None = None,
        work_root: str = "/tmp/wildframe/work",
        quarantine_root: str = "/tmp/wildframe/quarantine",
        disk_quota_bytes: int = 0,
    ) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.timeout = timeout
        self.cpu_threads = cpu_threads
        self.max_output_bytes = max_output_bytes
        self.max_duration_seconds = max_duration_seconds
        self.memory_limit_bytes = memory_limit_bytes
        self.work_root = work_root
        self.quarantine_root = quarantine_root
        self.disk_quota_bytes = disk_quota_bytes

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
        job_root = _output_job_root(out_dir, self.work_root)
        if not bitrates or any(type(br) is not int or br <= 0 for br in bitrates):
            raise UnsafeInput("positive integer rendition bitrates required")
        # The adapter's configured thread count is the hard ceiling: a caller
        # (or ctx) can never raise it above the per-job cap (#218).
        threads = min(cpu_threads or self.cpu_threads, self.cpu_threads)
        caps = [cap for cap in (max_output_bytes, self.max_output_bytes) if cap]
        size_cap = min(caps) if caps else 0
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
                "-map",
                "0:a:0?",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-c:v",
                "libx264",
                "-b:v",
                f"{bitrate}k",
                "-preset",
                "veryfast",
                "-threads",
                str(threads),
                "-force_key_frames",
                "expr:gte(t,n_forced*6)",
                out_path,
            ]
            returncode, _stdout, stderr, _st, _err = await run_process(
                argv,
                timeout=timeout or self.timeout,
                memory_limit_bytes=self.memory_limit_bytes,
                max_file_bytes=size_cap,
                disk_root=job_root,
                disk_quota_bytes=self.disk_quota_bytes,
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
        memory_limit_bytes: int | None = None,
        work_root: str = "/tmp/wildframe/work",
        quarantine_root: str = "/tmp/wildframe/quarantine",
        disk_quota_bytes: int = 0,
    ) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.timeout = timeout
        self.memory_limit_bytes = memory_limit_bytes
        self.work_root = work_root
        self.quarantine_root = quarantine_root
        self.disk_quota_bytes = disk_quota_bytes

    async def generate(self, path: str, out_dir: str, *, timeout: float | None = None) -> list[str]:
        _require_local_input(path, self.work_root, self.quarantine_root)
        job_root = _output_job_root(out_dir, self.work_root)
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
            argv,
            timeout=timeout or self.timeout,
            memory_limit_bytes=self.memory_limit_bytes,
            disk_root=job_root,
            disk_quota_bytes=self.disk_quota_bytes,
        )
        if returncode != 0:
            raise CommandFailure(
                f"ffmpeg thumbnail failed (exit {returncode}): "
                f"{stderr.decode(errors='replace')[:400]}"
            )
        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            raise OutputLimitExceeded(f"thumbnail generation produced no file {out_path!r}")
        return [out_path]


class FFmpegPackager(Packager):
    """Package every encoded video and its optional audio as HLS and DASH."""

    def __init__(
        self,
        *,
        ffmpeg_bin: str = "ffmpeg",
        timeout: float = 3600.0,
        memory_limit_bytes: int | None = None,
        work_root: str = "/tmp/wildframe/work",
        quarantine_root: str = "/tmp/wildframe/quarantine",
        disk_quota_bytes: int = 0,
    ) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.timeout = timeout
        self.memory_limit_bytes = memory_limit_bytes
        self.work_root = work_root
        self.quarantine_root = quarantine_root
        self.disk_quota_bytes = disk_quota_bytes

    def _inputs(self, inputs: dict[int, str]) -> list[tuple[int, str]]:
        if not inputs or any(type(br) is not int or br <= 0 for br in inputs):
            raise UnsafeInput("positive integer rendition bitrates required")
        for path in inputs.values():
            _require_local_input(path, self.work_root, self.quarantine_root)
        return sorted(inputs.items())

    async def _package(
        self, args: list[str], manifest: str, job_root: str, timeout: float | None
    ) -> None:
        from app.core.stages import require_artifact

        code, _, stderr, _, _ = await run_process(
            [self.ffmpeg_bin, "-y", "-v", "error", "-threads", "1", *args],
            timeout=timeout or self.timeout,
            memory_limit_bytes=self.memory_limit_bytes,
            disk_root=job_root,
            disk_quota_bytes=self.disk_quota_bytes,
        )
        if code:
            raise CommandFailure(
                f"ffmpeg packaging failed: {stderr.decode(errors='replace')[:400]}"
            )
        require_artifact(manifest)
        from app.core.security import validate_manifest_no_origin_urls

        validate_manifest_no_origin_urls(manifest)

    async def package_hls(
        self, inputs: dict[int, str], out_dir: str, *, timeout: float | None = None
    ) -> str:
        renditions = self._inputs(inputs)
        job_root = _output_job_root(out_dir, self.work_root)
        master_lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
        for index, (bitrate, source) in enumerate(renditions):
            playlist = os.path.join(out_dir, f"index_{index}.m3u8")
            await self._package(
                [
                    "-i",
                    source,
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-c",
                    "copy",
                    "-f",
                    "hls",
                    "-hls_time",
                    "6",
                    "-hls_playlist_type",
                    "vod",
                    "-hls_segment_filename",
                    os.path.join(out_dir, f"seg_{index}_%06d.ts"),
                    playlist,
                ],
                playlist,
                job_root,
                timeout,
            )
            # Conservative peak allowance includes mux overhead and AAC audio.
            master_lines += [
                f"#EXT-X-STREAM-INF:BANDWIDTH={(bitrate + 128) * 1200}",
                os.path.basename(playlist),
            ]
        master = os.path.join(out_dir, "master.m3u8")
        with open(master, "w", encoding="utf-8") as handle:
            handle.write("\n".join(master_lines) + "\n")
        return master

    async def package_dash(
        self, inputs: dict[int, str], out_dir: str, *, timeout: float | None = None
    ) -> str:
        renditions = self._inputs(inputs)
        job_root = _output_job_root(out_dir, self.work_root)
        manifest = os.path.join(out_dir, "manifest.mpd")
        args: list[str] = []
        for _, source in renditions:
            args += ["-i", source]
        for index in range(len(renditions)):
            args += ["-map", f"{index}:v:0"]
        args += [
            "-map",
            "0:a:0?",
            "-c",
            "copy",
            "-f",
            "dash",
            "-seg_duration",
            "6",
            "-use_template",
            "1",
            "-use_timeline",
            "1",
            manifest,
        ]
        await self._package(args, manifest, job_root, timeout)
        return manifest
