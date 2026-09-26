"""Stage ports: the ABC contract, the in-process stubs, and the real adapters.

Every stage in ``app/core/stages.py`` is a thin wrapper around a port, so the
ports are where the trust boundary lives. This module pins:

* the ABC surface — each port's abstract method must refuse to run.
* the deterministic stubs — the default wiring when ``MEDIA_PIPELINE_ADAPTERS``
  is ``stub``; no binaries, no network, byte-identical paths.
* ``ClamavScanner`` and ``CloudFrontCDN`` — the two real adapters, driven with
  injected fake ``clamd`` / ``boto3`` modules (neither is installed here, and
  both are imported lazily inside the adapter on purpose).
* the context helpers (``_work_dir`` / ``_quarantine_path`` / ``_cleanup_job``)
  and the manifest re-validation the packaging stages perform.
"""

import os
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.security import UnsafeInput
from app.core.stages import (
    CDN,
    DEFAULT_STAGE_ORDER,
    MetadataExtractor,
    MultiBitrateEncoder,
    ObjectStorage,
    Packager,
    Stage,
    StageInputError,
    StageRegistry,
    StubCDN,
    StubMetadataExtractor,
    StubMultiBitrateEncoder,
    StubObjectStorage,
    StubPackager,
    StubThumbnailGenerator,
    StubVirusScanner,
    ThumbnailGenerator,
    VirusScanner,
    ClamavScanner,
    CloudFrontCDN,
    _cleanup_job,
    _quarantine_path,
    _work_dir,
    as_stage,
    audio_extract,
    cdn_invalidate,
    dash_package,
    hls_package,
    install_default_stages,
    metadata_extract,
    quarantine_store,
    registry as process_registry,
    s3_upload,
    subtitle_extract,
    thumbnail_generate,
    virus_scan,
)

# ---------------------------------------------------------------------------
# The port contract: abstract methods must never silently succeed.
# ---------------------------------------------------------------------------


async def test_stage_run_is_abstract():
    with pytest.raises(NotImplementedError):
        await Stage.run(object(), {})


async def test_virus_scanner_scan_is_abstract():
    with pytest.raises(NotImplementedError):
        await VirusScanner.scan(object(), "/tmp/x")


async def test_metadata_extractor_extract_is_abstract():
    with pytest.raises(NotImplementedError):
        await MetadataExtractor.extract(object(), "/tmp/x")


async def test_thumbnail_generator_generate_is_abstract():
    with pytest.raises(NotImplementedError):
        await ThumbnailGenerator.generate(object(), "/tmp/in", "/tmp/out")


async def test_multi_bitrate_encoder_encode_is_abstract():
    with pytest.raises(NotImplementedError):
        await MultiBitrateEncoder.encode(object(), "/tmp/in", "/tmp/out", [400])


async def test_packager_ports_are_abstract():
    with pytest.raises(NotImplementedError):
        await Packager.package_hls(object(), {}, "/tmp/out")
    with pytest.raises(NotImplementedError):
        await Packager.package_dash(object(), {}, "/tmp/out")


async def test_object_storage_upload_is_abstract():
    with pytest.raises(NotImplementedError):
        await ObjectStorage.upload(object(), "/tmp/a", "key")


async def test_cdn_invalidate_is_abstract():
    with pytest.raises(NotImplementedError):
        await CDN.invalidate(object(), "/media/x")


def test_stage_input_error_is_a_plain_exception():
    assert issubclass(StageInputError, Exception)


# ---------------------------------------------------------------------------
# StageRegistry.
# ---------------------------------------------------------------------------


def test_registry_rejects_duplicate_stage_names():
    reg = StageRegistry()
    stage = StubVirusScannerStage("scan")
    reg.register(stage)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(StubVirusScannerStage("scan"))
    assert reg.order == ["scan"]


def test_registry_get_raises_for_an_unknown_stage():
    reg = StageRegistry()
    with pytest.raises(KeyError, match="not registered"):
        reg.get("nope")


def test_registry_get_returns_the_registered_instance():
    reg = StageRegistry()
    stage = StubVirusScannerStage("scan")
    reg.register(stage)
    assert reg.get("scan") is stage


def test_registry_reset_clears_stages_and_order():
    reg = StageRegistry()
    reg.register(StubVirusScannerStage("scan"))
    reg.reset()
    assert reg.order == []
    with pytest.raises(KeyError):
        reg.get("scan")


def test_install_default_stages_registers_the_canonical_order_idempotently():
    install_default_stages()
    install_default_stages()  # idempotent: resets first
    assert process_registry.order == DEFAULT_STAGE_ORDER
    assert len(process_registry.order) == len(set(process_registry.order))


def test_as_stage_wraps_a_bare_async_callable():
    @as_stage(name="custom", success_event="content.custom", critical=False)
    async def _custom(ctx: dict[str, Any]) -> dict[str, Any]:
        ctx["custom_done"] = True
        return ctx

    assert isinstance(_custom, Stage)
    assert _custom.name == "custom"
    assert _custom.success_event == "content.custom"
    assert _custom.critical is False


# ---------------------------------------------------------------------------
# The in-process stubs (the default adapter selection).
# ---------------------------------------------------------------------------


async def test_stub_virus_scanner_always_reports_clean():
    scanner = StubVirusScanner()
    assert await scanner.scan("/tmp/anything.bin") is True
    assert await scanner.scan("/nonexistent") is True


async def test_stub_metadata_extractor_returns_a_zeroed_envelope():
    metadata = await StubMetadataExtractor().extract("/tmp/in.mp4", timeout=5.0)
    assert metadata == {
        "duration_seconds": 0,
        "width": 0,
        "height": 0,
        "video_codec": "unknown",
        "audio_codecs": [],
        "has_subtitle_streams": False,
    }


async def test_stub_thumbnail_generator_derives_the_poster_path():
    out_dir = "/tmp/wildframe/work/job-1/thumbs"
    assert await StubThumbnailGenerator().generate("/tmp/in.mp4", out_dir) == [
        f"{out_dir}/poster.jpg"
    ]


async def test_stub_encoder_maps_every_requested_bitrate():
    out_dir = "/tmp/wildframe/work/job-1/encoded"
    outputs = await StubMultiBitrateEncoder().encode(
        "/tmp/in.mp4", out_dir, [400, 1200], timeout=1.0, cpu_threads=4, max_output_bytes=99
    )
    assert outputs == {400: f"{out_dir}/v_400.mp4", 1200: f"{out_dir}/v_1200.mp4"}


async def test_stub_encoder_with_no_bitrates_returns_nothing():
    assert await StubMultiBitrateEncoder().encode("/tmp/in.mp4", "/out", []) == {}


async def test_stub_packager_returns_manifest_paths():
    packager = StubPackager()
    assert await packager.package_hls({400: "/a.mp4"}, "/out") == "/out/master.m3u8"
    assert await packager.package_dash({400: "/a.mp4"}, "/out") == "/out/manifest.mpd"


async def test_stub_object_storage_namespaces_the_key():
    assert await StubObjectStorage().upload("/local/master.m3u8", "media/job-1/hls") == (
        "s3://wildframe-media/media/job-1/hls"
    )


async def test_stub_cdn_invalidate_is_a_no_op(caplog):
    with caplog.at_level("INFO", logger="app.core.stages"):
        assert await StubCDN().invalidate("/media/job-1/*") is None
    assert "stub CDN invalidate" in caplog.text


# ---------------------------------------------------------------------------
# ClamavScanner — the real malware-scan adapter.
# ---------------------------------------------------------------------------


class _FakeClamd:
    """Stand-in for the ``clamd`` python-clamd module."""

    instances: list["_FakeClamd"] = []
    result: object = None

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self.scanned: list[str] = []
        _FakeClamd.instances.append(self)

    def scan(self, path: str):
        self.scanned.append(path)
        return _FakeClamd.result


@pytest.fixture
def fake_clamd():
    _FakeClamd.instances = []
    _FakeClamd.result = None
    module = types.ModuleType("clamd")
    module.ClamdUnixSocket = _FakeClamd
    with patch.dict(sys.modules, {"clamd": module}):
        yield _FakeClamd
    _FakeClamd.instances = []


async def test_clamav_scanner_defaults_to_clean_when_daemon_returns_nothing(fake_clamd):
    fake_clamd.result = None
    scanner = ClamavScanner(socket_path="/run/clamav.sock")
    assert scanner.socket_path == "/run/clamav.sock"
    assert await scanner.scan("/tmp/sample.bin") is True
    assert fake_clamd.instances[0].socket_path == "/run/clamav.sock"
    assert fake_clamd.instances[0].scanned == ["/tmp/sample.bin"]


async def test_clamav_scanner_detects_an_infected_sample(fake_clamd):
    fake_clamd.result = {"/tmp/sample.bin": ("FOUND", "Eicar-Test-Signature")}
    assert await ClamavScanner().scan("/tmp/sample.bin") is False


async def test_clamav_scanner_treats_ok_and_empty_statuses_as_clean(fake_clamd):
    fake_clamd.result = {"/tmp/sample.bin": ("OK",)}
    assert await ClamavScanner().scan("/tmp/sample.bin") is True
    fake_clamd.result = {"/tmp/sample.bin": (None, None)}
    assert await ClamavScanner().scan("/tmp/sample.bin") is True


async def test_clamav_scanner_uses_the_default_socket_path(fake_clamd):
    assert ClamavScanner().socket_path == "/var/run/clamav/clamd.ctl"
    await ClamavScanner().scan("/tmp/sample.bin")
    assert fake_clamd.instances[0].socket_path == "/var/run/clamav/clamd.ctl"


# ---------------------------------------------------------------------------
# CloudFrontCDN — the real invalidation adapter.
# ---------------------------------------------------------------------------


class _FakeBoto3:
    def __init__(self, client: MagicMock) -> None:
        self.client_obj = client
        self.calls: list[tuple] = []

    def client(self, service: str, region_name: str | None = None):
        self.calls.append((service, region_name))
        return self.client_obj


@pytest.fixture
def fake_boto3():
    client = MagicMock()
    module = types.ModuleType("boto3")
    holder = _FakeBoto3(client)
    module.client = holder.client
    with patch.dict(sys.modules, {"boto3": module}):
        yield holder, client
    client.create_invalidation.reset_mock()


def test_cloudfront_cdn_defaults_to_us_east_1():
    cdn = CloudFrontCDN(distribution_id="D1")
    assert cdn.distribution_id == "D1"
    assert cdn.region == "us-east-1"
    assert CloudFrontCDN(distribution_id="D1", region="eu-west-1").region == "eu-west-1"
    assert CloudFrontCDN().distribution_id is None


async def test_cloudfront_cdn_skips_when_no_distribution_is_configured(caplog):
    cdn = CloudFrontCDN(distribution_id=None)
    with caplog.at_level("WARNING", logger="app.core.stages"):
        assert await cdn.invalidate("/media/job-1/*") is None
    assert "not configured" in caplog.text


async def test_cloudfront_cdn_creates_an_invalidation_with_a_leading_slash(fake_boto3):
    holder, client = fake_boto3
    cdn = CloudFrontCDN(distribution_id="DIST123", region="eu-west-1")

    await cdn.invalidate("media/job-1/*")

    assert holder.calls == [("cloudfront", "eu-west-1")]
    kwargs = client.create_invalidation.call_args.kwargs
    assert kwargs["DistributionId"] == "DIST123"
    batch = kwargs["InvalidationBatch"]
    assert batch["Paths"] == {"Quantity": 1, "Items": ["/media/job-1/*"]}
    assert batch["CallerReference"].startswith("wildframe-/media/job-1/*-")


async def test_cloudfront_cdn_keeps_an_already_rooted_path(fake_boto3):
    _holder, client = fake_boto3
    await CloudFrontCDN(distribution_id="DIST123").invalidate("/media/x")
    items = client.create_invalidation.call_args.kwargs["InvalidationBatch"]["Paths"]["Items"]
    assert items == ["/media/x"]


async def test_cloudfront_cdn_propagates_aws_failures(fake_boto3, caplog):
    _holder, client = fake_boto3
    client.create_invalidation.side_effect = RuntimeError("AccessDenied")
    cdn = CloudFrontCDN(distribution_id="DIST123")
    with caplog.at_level("ERROR", logger="app.core.stages"):
        with pytest.raises(RuntimeError, match="AccessDenied"):
            await cdn.invalidate("/media/x")
    assert "CloudFront invalidation failed" in caplog.text


# ---------------------------------------------------------------------------
# Context helpers.
# ---------------------------------------------------------------------------


def test_work_dir_defaults_to_the_job_root():
    ctx = {"job_id": "job-1"}
    assert _work_dir(ctx) == os.path.join("/tmp/wildframe/work", "job-1")


def test_work_dir_nests_under_a_custom_root_with_parts():
    ctx = {"job_id": "job-1", "work_root": "/srv/work"}
    assert _work_dir(ctx, "encoded", "deep") == "/srv/work/job-1/encoded/deep"


def test_quarantine_path_uses_the_key_basename_only():
    ctx = {"job_id": "job-1", "quarantine_root": "/srv/q"}
    assert _quarantine_path(ctx, "clip.mp4") == "/srv/q/job-1/clip.mp4"
    # A traversal attempt collapses to its basename, never a nested path.
    assert _quarantine_path(ctx, "../../etc/passwd") == "/srv/q/job-1/passwd"


def test_quarantine_path_falls_back_when_the_key_has_no_basename():
    ctx = {"job_id": "job-1"}
    assert _quarantine_path(ctx, "") == "/tmp/wildframe/quarantine/job-1/source"
    assert _quarantine_path(ctx, "/") == "/tmp/wildframe/quarantine/job-1/source"


def test_cleanup_job_removes_the_work_and_quarantine_directories(tmp_path):
    work = tmp_path / "work" / "job-1"
    quarantine = tmp_path / "quarantine" / "job-1"
    work.mkdir(parents=True)
    quarantine.mkdir(parents=True)
    (work / "partial.mp4").write_bytes(b"x")
    (quarantine / "source.mp4").write_bytes(b"x")

    _cleanup_job(
        {
            "job_id": "job-1",
            "work_root": str(tmp_path / "work"),
            "quarantine_root": str(tmp_path / "quarantine"),
        }
    )

    assert not work.exists()
    assert not quarantine.exists()


def test_cleanup_job_is_a_noop_without_a_job_id(tmp_path):
    _cleanup_job({"work_root": str(tmp_path)})  # no job_id -> nothing removed
    _cleanup_job({"job_id": "", "work_root": str(tmp_path)})


# ---------------------------------------------------------------------------
# The concrete stages that read from / write to ctx.
# ---------------------------------------------------------------------------


def _ctx(tmp_path, **overrides) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "job_id": "job-1",
        "work_root": str(tmp_path / "work"),
        "quarantine_root": str(tmp_path / "quarantine"),
        "storage_key": "clip.mp4",
        # Set by ``quarantine_store``; every later stage reads it.
        "quarantine_path": str(tmp_path / "quarantine" / "job-1" / "clip.mp4"),
    }
    ctx.update(overrides)
    return ctx


async def test_quarantine_store_records_the_local_path(tmp_path, caplog):
    ctx = _ctx(tmp_path)
    with caplog.at_level("INFO", logger="app.core.stages"):
        out = await quarantine_store.run(ctx)
    assert out["quarantine_path"] == str(tmp_path / "quarantine" / "job-1" / "clip.mp4")
    assert "quarantined" in caplog.text
    assert quarantine_store.success_event == "content.quarantined"


async def test_virus_scan_records_cleanliness(tmp_path):
    out = await virus_scan.run(_ctx(tmp_path, virus_scanner=StubVirusScanner()))
    assert out["scan_clean"] is True


async def test_virus_scan_fails_the_job_on_an_infected_sample(tmp_path):
    class Infected(VirusScanner):
        async def scan(self, path: str) -> bool:
            return False

    ctx = _ctx(tmp_path, virus_scanner=Infected())
    with pytest.raises(RuntimeError, match="virus detected"):
        await virus_scan.run(ctx)
    assert ctx["scan_clean"] is False


async def test_metadata_extract_sanitizes_the_probe_output(tmp_path):
    extractor = StubMetadataExtractor()
    out = await metadata_extract.run(
        _ctx(tmp_path, metadata_extractor=extractor, stage_timeout=3.0)
    )
    # video_codec/audio_codecs are not on the allowlist, so they are dropped.
    assert out["metadata"] == {"duration_seconds": 0, "width": 0, "height": 0}


async def test_thumbnail_generate_derives_its_output_directory(tmp_path):
    gen = StubThumbnailGenerator()
    out = await thumbnail_generate.run(_ctx(tmp_path, thumbnail_generator=gen, stage_timeout=2.0))
    assert out["thumbnails"] == [str(tmp_path / "work" / "job-1" / "thumbs" / "poster.jpg")]
    assert thumbnail_generate.critical is False


async def test_audio_and_subtitle_extract_derive_track_paths(tmp_path):
    out = await audio_extract.run(_ctx(tmp_path))
    assert out["audio_tracks"] == [str(tmp_path / "work" / "job-1" / "audio_en.m4a")]
    out = await subtitle_extract.run(_ctx(tmp_path))
    assert out["subtitle_tracks"] == [str(tmp_path / "work" / "job-1" / "subs_en.vtt")]


async def test_dash_package_emits_no_success_event_of_its_own(tmp_path):
    out = await dash_package.run(_ctx(tmp_path, encoded={}, packager=StubPackager()))
    assert out["dash_url"] == str(tmp_path / "work" / "job-1" / "dash" / "manifest.mpd")
    assert dash_package.success_event == ""


async def test_s3_upload_publishes_both_artifacts(tmp_path):
    storage = StubObjectStorage()
    out = await s3_upload.run(
        _ctx(
            tmp_path,
            hls_url="/local/master.m3u8",
            dash_url="/local/manifest.mpd",
            object_storage=storage,
        )
    )
    assert out["storage"] == {
        "hls": "s3://wildframe-media/media/job-1/hls/master.m3u8",
        "dash": "s3://wildframe-media/media/job-1/dash/manifest.mpd",
    }


async def test_cdn_invalidate_purges_the_job_prefix_and_stamps_a_time(tmp_path):
    cdn = MagicMock(spec=CDN)
    cdn.invalidate = AsyncMock()
    out = await cdn_invalidate.run(_ctx(tmp_path, cdn=cdn))
    cdn.invalidate.assert_awaited_once_with("/media/job-1/*")
    assert out["cdn_invalidated_at"].endswith("+00:00")


# ---------------------------------------------------------------------------
# Packaging stages re-validate the manifest the adapter produced (#283).
# ---------------------------------------------------------------------------


class _FixedPackager(Packager):
    """A packager that returns a caller-supplied manifest path."""

    def __init__(self, manifest: str) -> None:
        self.manifest = manifest

    async def package_hls(self, inputs, out_dir, *, timeout=None) -> str:
        return self.manifest

    async def package_dash(self, inputs, out_dir, *, timeout=None) -> str:
        return self.manifest


async def test_hls_package_skips_validation_when_the_stub_wrote_nothing(tmp_path):
    out = await hls_package.run(_ctx(tmp_path, encoded={}, packager=StubPackager()))
    assert out["hls_url"].endswith("hls/master.m3u8")
    assert not os.path.exists(out["hls_url"])


async def test_hls_package_validates_a_real_manifest(tmp_path):
    manifest = tmp_path / "master.m3u8"
    manifest.write_text("#EXTM3U\nseg_000000.ts\n", encoding="utf-8")
    out = await hls_package.run(_ctx(tmp_path, encoded={}, packager=_FixedPackager(str(manifest))))
    assert out["hls_url"] == str(manifest)


async def test_hls_package_rejects_a_manifest_with_origin_urls(tmp_path):
    manifest = tmp_path / "master.m3u8"
    manifest.write_text("#EXTM3U\nhttps://cdn.example.com/seg.ts\n", encoding="utf-8")
    with pytest.raises(UnsafeInput, match="absolute URL"):
        await hls_package.run(_ctx(tmp_path, encoded={}, packager=_FixedPackager(str(manifest))))


async def test_dash_package_validates_a_real_manifest(tmp_path):
    manifest = tmp_path / "manifest.mpd"
    manifest.write_text("<MPD/>", encoding="utf-8")
    out = await dash_package.run(_ctx(tmp_path, encoded={}, packager=_FixedPackager(str(manifest))))
    assert out["dash_url"] == str(manifest)


async def test_dash_package_rejects_a_manifest_with_origin_urls(tmp_path):
    manifest = tmp_path / "manifest.mpd"
    manifest.write_text("<MPD><BaseURL>//cdn.example.com/</BaseURL></MPD>", encoding="utf-8")
    with pytest.raises(UnsafeInput, match="absolute URL"):
        await dash_package.run(_ctx(tmp_path, encoded={}, packager=_FixedPackager(str(manifest))))


# ---------------------------------------------------------------------------
# Local helper.
# ---------------------------------------------------------------------------


class StubVirusScannerStage(Stage):
    """A minimal concrete Stage used to exercise the registry."""

    name = "scan"
    success_event = "content.scanned"
    critical = True

    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return ctx
