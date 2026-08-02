from typing import Any

"""Pipeline stages: a ``Stage`` port + registry + the concrete stages.

Each stage is an independently-retryable, callable unit of work. Stages are
pure-ish functions of a job's accumulated ``context`` dict: they read what
prior stages wrote and append their own outputs. This makes them:

    * composable   — wired into a list in PIPELINE_STAGES_ORDER,
    * retryable    — the orchestrator wraps each in the retry/DLQ policy,
    * testable     — call a stage directly with a context dict,
    * swappable    — a stage with a heavy external dependency (ffmpeg, clamav,
      S3) is a thin wrapper around a port, so the impl can be stubbed.

Stage port
----------
A ``Stage`` is any callable ``async (ctx) -> ctx`` plus metadata. We model it
as a small class so each stage can carry a name, the event topic it emits on
success, and whether it is "critical" (failure should fail the job rather than
be skipped). The registry maps stage name -> Stage instance.

Event topics per stage
----------------------
See ``app/core/events.py`` docstring and PRODUCT_VISION §7. Each stage emits
one success event; failures are logged and surfaced via the job's stage log and,
on retry exhaustion, the ``content.pipeline.failed`` DLQ event.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage port.
# ---------------------------------------------------------------------------


class Stage(ABC):
    """A single, retryable pipeline stage.

    A stage mutates and returns the job's ``context`` dict. Raising any
    exception marks the stage as failed for that attempt; the orchestrator
    applies the retry/DLQ policy.
    """

    name: str = ""
    """Stable stage name (must match the key used in the registry)."""

    success_event: str = ""
    """Event topic emitted on success (``''`` = no event)."""

    critical: bool = True
    """If True, a failure at this stage can fail the job; if False, the
    orchestrator may choose to skip and continue (best-effort stages)."""

    @abstractmethod
    async def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Execute the stage against ``ctx`` and return the updated ctx."""
        raise NotImplementedError


# A stage may also be a plain async callable; we normalize via ``as_stage``.


def as_stage(
    name: str,
    success_event: str = "",
    critical: bool = True,
) -> Callable[[Callable], _CallableStage]:
    """Decorate an async ``(ctx) -> ctx`` function into a ``Stage``."""

    def deco(fn: Callable) -> _CallableStage:
        return _CallableStage(
            name=name, success_event=success_event, critical=critical, fn=fn
        )

    return deco


class _CallableStage(Stage):
    def __init__(
        self,
        name: str,
        success_event: str,
        critical: bool,
        fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> None:
        self.name = name
        self.success_event = success_event
        self.critical = critical
        self._fn = fn

    async def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return await self._fn(ctx)


# ---------------------------------------------------------------------------
# Registry.
# ---------------------------------------------------------------------------


class StageRegistry:
    """Ordered registry of stages.

    The orchestrator walks ``order`` and looks each stage up by name. Keeping
    the order explicit (rather than dict-insertion order) makes the pipeline
    definition readable and easy to reconfigure per environment.
    """

    def __init__(self) -> None:
        self._stages: dict[str, Stage] = {}
        self.order: list[str] = []

    def register(self, stage: Stage) -> None:
        if stage.name in self._stages:
            raise ValueError(f"stage {stage.name!r} already registered")
        self._stages[stage.name] = stage
        self.order.append(stage.name)

    def get(self, name: str) -> Stage:
        if name not in self._stages:
            raise KeyError(f"stage {name!r} not registered")
        return self._stages[name]

    def reset(self) -> None:
        self._stages.clear()
        self.order.clear()


# Process-wide registry singleton.
registry = StageRegistry()


# ---------------------------------------------------------------------------
# Ports for stages that touch the outside world.
#
# Each is an ABC with a sensible in-process stub. Real adapters (ffmpeg
# subprocess, clamav, S3, CDN) are wired in via dependency injection in the
# service layer / selected by settings, exactly like the uploads-service
# storage port. Stubs keep the pipeline runnable and testable with no
# binaries or network.
# ---------------------------------------------------------------------------


class VirusScanner(ABC):
    """Port: scan bytes for malware (clamav-like)."""

    @abstractmethod
    async def scan(self, path: str) -> bool:
        """Return True if ``path`` is clean, False if infected."""
        raise NotImplementedError


class StubVirusScanner(VirusScanner):
    """Always-clean scanner (default)."""

    async def scan(self, path: str) -> bool:
        return True


class ClamavScanner(VirusScanner):
    """Real clamd-backed scanner. Lazy-imports clamd."""

    def __init__(self, socket_path: str = "/var/run/clamav/clamd.ctl") -> None:
        self.socket_path = socket_path

    async def scan(self, path: str) -> bool:
        import clamd  # type: ignore

        cd = clamd.ClamdUnixSocket(self.socket_path)
        result = await asyncio.to_thread(cd.scan, path)
        if result is None:
            return True
        # result == {path: ('FOUND', 'Virus.Name')} when infected.
        for status in result.values():
            if status and status[0] == "FOUND":
                return False
        return True


class MetadataExtractor(ABC):
    """Port: extract media metadata (ffprobe-like)."""

    @abstractmethod
    async def extract(self, path: str) -> dict[str, Any]:
        raise NotImplementedError


class StubMetadataExtractor(MetadataExtractor):
    async def extract(self, path: str) -> dict[str, Any]:
        return {
            "duration_seconds": 0,
            "width": 0,
            "height": 0,
            "video_codec": "unknown",
            "audio_codecs": [],
            "has_subtitle_streams": False,
        }


class ThumbnailGenerator(ABC):
    """Port: generate poster/thumbnails (ffmpeg-like)."""

    @abstractmethod
    async def generate(self, path: str, out_dir: str) -> list[str]:
        raise NotImplementedError


class StubThumbnailGenerator(ThumbnailGenerator):
    async def generate(self, path: str, out_dir: str) -> list[str]:
        return [f"{out_dir}/poster.jpg"]


class MultiBitrateEncoder(ABC):
    """Port: ffmpeg multi-bitrate encode."""

    @abstractmethod
    async def encode(
        self, path: str, out_dir: str, bitrates: list[int]
    ) -> dict[int, str]:
        """Return bitrate_kbps -> output path."""
        raise NotImplementedError


class StubMultiBitrateEncoder(MultiBitrateEncoder):
    async def encode(
        self, path: str, out_dir: str, bitrates: list[int]
    ) -> dict[int, str]:
        return {br: f"{out_dir}/v_{br}.mp4" for br in bitrates}


class Packager(ABC):
    """Port: package encoded outputs into HLS / DASH."""

    @abstractmethod
    async def package_hls(self, inputs: dict[int, str], out_dir: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def package_dash(self, inputs: dict[int, str], out_dir: str) -> str:
        raise NotImplementedError


class StubPackager(Packager):
    async def package_hls(self, inputs: dict[int, str], out_dir: str) -> str:
        return f"{out_dir}/master.m3u8"

    async def package_dash(self, inputs: dict[int, str], out_dir: str) -> str:
        return f"{out_dir}/manifest.mpd"


class ObjectStorage(ABC):
    """Port: upload packaged artifacts to object storage (S3-like)."""

    @abstractmethod
    async def upload(self, local_path: str, storage_key: str) -> str:
        """Return the storage URI."""
        raise NotImplementedError


class StubObjectStorage(ObjectStorage):
    async def upload(self, local_path: str, storage_key: str) -> str:
        return f"s3://wildframe-media/{storage_key}"


class CDN(ABC):
    """Port: invalidate a CDN path."""

    @abstractmethod
    async def invalidate(self, path: str) -> None:
        raise NotImplementedError


class StubCDN(CDN):
    async def invalidate(self, path: str) -> None:
        logger.info("stub CDN invalidate: %s", path)


# ---------------------------------------------------------------------------
# Concrete stages.
#
# Each is a plain async ``(ctx) -> ctx`` function decorated with ``as_stage``.
# They read inputs from ``ctx`` (populated by prior stages) and write their
# outputs back. External work goes through the ports above, which are read from
# ``ctx`` (injected by the orchestrator) so tests can pass stubs.
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@as_stage(name="quarantine_store", success_event="content.quarantined")
async def quarantine_store(ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 1: move the uploaded bytes into an isolated quarantine area.

    Input:  ctx["storage_key"]  — key the uploads-service stored the object at.
    Output: ctx["quarantine_path"] — local path the bytes now live at.
    """
    storage_key = ctx["storage_key"]
    quarantine_root = ctx.get("quarantine_root", "/tmp/wildframe/quarantine")
    quarantine_path = f"{quarantine_root}/{ctx['job_id']}/{storage_key}"
    # In a real impl this copies the object from the uploads bucket into an
    # isolated prefix. Here we record the path; the bytes are assumed present.
    ctx["quarantine_path"] = quarantine_path
    logger.info("quarantined %s -> %s", storage_key, quarantine_path)
    return ctx


@as_stage(name="virus_scan", success_event="content.scanned")
async def virus_scan(ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 2: malware-scan the quarantined bytes.

    Uses the ``virus_scanner`` port from ctx. Raises if infected (which the
    orchestrator treats as a non-retryable failure -> DLQ).
    """
    scanner: VirusScanner = ctx["virus_scanner"]
    clean = await scanner.scan(ctx["quarantine_path"])
    ctx["scan_clean"] = clean
    if not clean:
        raise RuntimeError(
            f"virus detected in {ctx['quarantine_path']} (job {ctx['job_id']})"
        )
    return ctx


@as_stage(name="metadata_extract", success_event="content.metadata_extracted")
async def metadata_extract(ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 3: extract technical metadata (resolution, codecs, duration)."""
    extractor: MetadataExtractor = ctx["metadata_extractor"]
    ctx["metadata"] = await extractor.extract(ctx["quarantine_path"])
    return ctx


@as_stage(
    name="thumbnail_generate",
    success_event="content.thumbnailed",
    critical=False,
)
async def thumbnail_generate(ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 4: generate poster/thumbnails (best-effort)."""
    gen: ThumbnailGenerator = ctx["thumbnail_generator"]
    out_dir = f"/tmp/wildframe/work/{ctx['job_id']}/thumbs"
    ctx["thumbnails"] = await gen.generate(ctx["quarantine_path"], out_dir)
    return ctx


@as_stage(
    name="audio_extract",
    success_event="content.audio_extracted",
    critical=False,
)
async def audio_extract(ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 5: extract audio tracks (best-effort)."""
    ctx["audio_tracks"] = [f"/tmp/wildframe/work/{ctx['job_id']}/audio_en.m4a"]
    return ctx


@as_stage(
    name="subtitle_extract",
    success_event="content.subtitle_extracted",
    critical=False,
)
async def subtitle_extract(ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 6: extract subtitle tracks (best-effort)."""
    ctx["subtitle_tracks"] = [f"/tmp/wildframe/work/{ctx['job_id']}/subs_en.vtt"]
    return ctx


@as_stage(name="ffmpeg_multi_bitrate_encode", success_event="content.encoded")
async def ffmpeg_multi_bitrate_encode(ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 7: multi-bitrate encode via ffmpeg.

    Output: ctx["encoded"] = {bitrate_kbps: path}.
    """
    encoder: MultiBitrateEncoder = ctx["encoder"]
    out_dir = f"/tmp/wildframe/work/{ctx['job_id']}/encoded"
    bitrates = ctx.get("bitrates", [400, 800, 1200, 2400, 4800])
    ctx["encoded"] = await encoder.encode(ctx["quarantine_path"], out_dir, bitrates)
    return ctx


@as_stage(name="hls_package", success_event="content.packaged")
async def hls_package(ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 8a: package encoded outputs into HLS."""
    packager: Packager = ctx["packager"]
    out_dir = f"/tmp/wildframe/work/{ctx['job_id']}/hls"
    ctx["hls_url"] = await packager.package_hls(ctx["encoded"], out_dir)
    return ctx


@as_stage(name="dash_package", success_event="")
async def dash_package(ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 8b: package encoded outputs into DASH.

    No separate success event — packaging is reported as a single
    ``content.packaged`` event emitted by the orchestrator once both manifests
    exist.
    """
    packager: Packager = ctx["packager"]
    out_dir = f"/tmp/wildframe/work/{ctx['job_id']}/dash"
    ctx["dash_url"] = await packager.package_dash(ctx["encoded"], out_dir)
    return ctx


@as_stage(name="s3_upload", success_event="content.uploaded_to_storage")
async def s3_upload(ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 9: upload HLS + DASH artifacts to object storage."""
    storage: ObjectStorage = ctx["object_storage"]
    job_id = ctx["job_id"]
    hls_key = await storage.upload(ctx["hls_url"], f"media/{job_id}/hls/master.m3u8")
    dash_key = await storage.upload(
        ctx["dash_url"], f"media/{job_id}/dash/manifest.mpd"
    )
    ctx["storage"] = {"hls": hls_key, "dash": dash_key}
    return ctx


@as_stage(name="cdn_invalidate", success_event="content.cdn_invalidated")
async def cdn_invalidate(ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 10: purge CDN caches so the new media is served."""
    cdn: CDN = ctx["cdn"]
    await cdn.invalidate(f"/media/{ctx['job_id']}/*")
    ctx["cdn_invalidated_at"] = _now_iso()
    return ctx


# ---------------------------------------------------------------------------
# Default wiring: register the canonical stage order into the registry.
# ---------------------------------------------------------------------------

DEFAULT_STAGE_ORDER: list[str] = [
    "quarantine_store",
    "virus_scan",
    "metadata_extract",
    "thumbnail_generate",
    "audio_extract",
    "subtitle_extract",
    "ffmpeg_multi_bitrate_encode",
    "hls_package",
    "dash_package",
    "s3_upload",
    "cdn_invalidate",
]


def install_default_stages() -> None:
    """Register the canonical stages into the process-wide registry.

    Idempotent: clears first so re-calls (e.g. in tests) don't double-register.
    """
    registry.reset()
    registry.register(quarantine_store)
    registry.register(virus_scan)
    registry.register(metadata_extract)
    registry.register(thumbnail_generate)
    registry.register(audio_extract)
    registry.register(subtitle_extract)
    registry.register(ffmpeg_multi_bitrate_encode)
    registry.register(hls_package)
    registry.register(dash_package)
    registry.register(s3_upload)
    registry.register(cdn_invalidate)


# Auto-install at import time so the orchestrator can rely on the registry
# being populated. Tests can call ``install_default_stages()`` again to reset.
install_default_stages()
