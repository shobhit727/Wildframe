"""Storage port: the in-process stub and the boto3-backed S3 adapter.

``app/core/storage.py`` is where the service decides what the client is allowed
to do and how the *authoritative* result is verified, so both ports are pinned
here:

* ``StubStoragePort`` — deterministic, holds real bytes; the dev/test default.
* ``S3StoragePort`` — real multipart S3 semantics (``create_multipart_upload``
  → presigned ``upload_part`` → ``complete_multipart_upload``), driven against
  an injected fake ``boto3`` module. ``boto3`` is not installed here and the
  adapter imports it lazily on purpose, so the fake is the only way in.

Every boto3 call is asserted exactly: bucket, key, upload id, part numbers,
part ETags, content types and the presign TTL. The security-relevant branches
are pinned too: a part that is missing, a size that disagrees with the
declared total, and a stored content type that disagrees with the session MIME.
"""

import hashlib
import sys
import types
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.core.settings import settings
from app.core.storage import (
    PresignedUpload,
    S3StoragePort,
    StorageError,
    StorageObjectMetadata,
    StoragePort,
    StubStoragePort,
    _build_storage,
    _raise_storage_error,
    _sha256,
    clamp_ttl,
    get_storage,
    set_storage,
    storage_key_for,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class FakeClientError(Exception):
    """Shaped like ``botocore.exceptions.ClientError`` (only ``response`` is read)."""

    def __init__(self, status_code: int, message: str = "error") -> None:
        super().__init__(message)
        self.response = {
            "Error": {"Code": str(status_code), "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status_code},
        }


def _not_found() -> FakeClientError:
    return FakeClientError(404, "Not Found")


@pytest.fixture
def s3_client():
    """An injected fake boto3 module + the MagicMock client it hands out."""
    client = MagicMock()
    created: list[dict] = []

    def _client(service: str, **kwargs):
        created.append({"service": service, **kwargs})
        return client

    module = types.ModuleType("boto3")
    module.client = _client
    with patch.dict(sys.modules, {"boto3": module}):
        yield client, created


def _s3_port(client=None, **kwargs) -> S3StoragePort:
    defaults = {
        "region": "us-east-1",
        "bucket": "wildframe-uploads",
        "access_key_id": "AKIA-TEST",
        "secret_access_key": "shh",
        "endpoint_url": "http://minio:9000",
    }
    defaults.update(kwargs)
    return S3StoragePort(**defaults)


# ---------------------------------------------------------------------------
# Pure helpers.
# ---------------------------------------------------------------------------


def test_storage_key_for_the_final_object_uses_only_the_session_uuid():
    session_id = str(uuid4())
    key = storage_key_for(session_id, None)
    assert key == f"uploads/{session_id}/final"
    # A hostile filename never reaches the key: the key is UUID-only.
    assert "../../etc/passwd" not in key
    assert ".." not in key.split("/")


def test_storage_key_for_a_chunk_is_zero_padded():
    session_id = str(uuid4())
    assert storage_key_for(session_id, 0) == f"uploads/{session_id}/chunks/00000"
    assert storage_key_for(session_id, 42) == f"uploads/{session_id}/chunks/00042"
    assert storage_key_for(session_id, 12345) == f"uploads/{session_id}/chunks/12345"


@pytest.mark.parametrize(
    ("ttl", "ceiling", "expected"),
    [(3600, 3600, 3600), (99999, 3600, 3600), (0, 3600, 1), (-5, 3600, 1), (30, 3600, 30)],
)
def test_clamp_ttl_bounds_the_presign_lifetime(ttl, ceiling, expected):
    assert clamp_ttl(ttl, ceiling) == expected


def test_raise_storage_error_narrows_an_optional_return_by_raising():
    with pytest.raises(StorageError, match="no UploadId"):
        _raise_storage_error("no UploadId")


def test_sha256_matches_hashlib():
    assert _sha256(b"abc") == hashlib.sha256(b"abc").hexdigest()


# ---------------------------------------------------------------------------
# The port contract.
# ---------------------------------------------------------------------------


async def test_every_storage_port_method_is_abstract():
    """The ABC is a real contract: no port method may silently do nothing."""
    with pytest.raises(NotImplementedError):
        await StoragePort.begin_upload(None, session_id="s", mime="video/mp4")
    with pytest.raises(NotImplementedError):
        await StoragePort.create_upload(None, session_id="s", filename="f", mime="video/mp4")
    with pytest.raises(NotImplementedError):
        await StoragePort.get_object_metadata(None, storage_key="k")
    with pytest.raises(NotImplementedError):
        await StoragePort.get_chunk_metadata(None, session_id="s", index=0, total_chunks=1)
    with pytest.raises(NotImplementedError):
        await StoragePort.complete_upload(
            None, session_id="s", chunk_keys=[], final_key="k", size_bytes=0, mime="video/mp4"
        )
    with pytest.raises(NotImplementedError):
        await StoragePort.cleanup_upload(None, session_id="s", chunk_keys=[], final_key="k")


def test_a_port_cannot_be_instantiated_without_implementing_every_method():
    class Partial(StoragePort):
        async def begin_upload(self, *, session_id: str, mime: str):
            return None

    with pytest.raises(TypeError, match="abstract"):
        Partial()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# StubStoragePort.
# ---------------------------------------------------------------------------


def test_the_stub_clamps_its_ttl_on_construction():
    port = StubStoragePort(ttl_seconds=99999, max_ttl_seconds=3600)
    assert port.ttl_seconds == 3600
    assert StubStoragePort(ttl_seconds=0).ttl_seconds == 1
    assert StubStoragePort().bucket == "wildframe-uploads"


async def test_stub_begin_upload_has_no_multipart_state():
    assert await StubStoragePort().begin_upload(session_id="s", mime="video/mp4") is None


async def test_stub_create_upload_issues_a_put_url_bound_to_the_session_and_mime():
    session_id = str(uuid4())
    port = StubStoragePort(bucket="bkt", ttl_seconds=120)
    presigned = await port.create_upload(
        session_id=session_id, filename="../../evil.mp4", mime="video/mp4", chunk_index=3
    )

    assert isinstance(presigned, PresignedUpload)
    assert presigned.storage_key == storage_key_for(session_id, 3)
    assert presigned.upload_url == (
        f"https://storage.local/bkt/{storage_key_for(session_id, 3)}"
        f"?x-upload-session={session_id}&x-mime=video/mp4"
    )
    assert presigned.method == "PUT"
    assert presigned.headers == {"Content-Type": "video/mp4"}
    assert presigned.expires_in_seconds == 120
    # The client filename is display metadata only: it is not in the key.
    assert "evil" not in presigned.storage_key


async def test_stub_create_upload_for_a_single_shot_object_uses_the_final_key():
    session_id = str(uuid4())
    presigned = await StubStoragePort().create_upload(
        session_id=session_id, filename="clip.mp4", mime="video/webm"
    )
    assert presigned.storage_key == storage_key_for(session_id, None)
    assert "chunks" not in presigned.storage_key


async def test_stub_get_object_metadata_reports_authoritative_bytes():
    port = StubStoragePort()
    port.upload_bytes("k", b"hello", mime="video/mp4")
    meta = await port.get_object_metadata(storage_key="k")
    assert meta == StorageObjectMetadata(
        storage_key="k", size_bytes=5, checksum_sha256=_sha("hello"), mime="video/mp4"
    )


async def test_stub_get_object_metadata_returns_none_for_a_missing_key():
    assert await StubStoragePort().get_object_metadata(storage_key="absent") is None


async def test_stub_get_object_metadata_reports_the_empty_mime_of_a_typeless_put():
    """``upload_bytes`` always records a content type; the default is "".

    That matters downstream: ``complete_upload`` treats a falsy stored mime as
    "unknown" and therefore does not reject the object.
    """
    port = StubStoragePort()
    port.upload_bytes("k", b"x")  # no mime given
    assert (await port.get_object_metadata(storage_key="k")).mime == ""
    assert port.content_types["k"] == ""


async def test_stub_get_chunk_metadata_uses_the_chunk_key_for_multipart_sessions():
    session_id = str(uuid4())
    port = StubStoragePort()
    port.upload_bytes(storage_key_for(session_id, 1), b"chunk-1", mime="video/mp4")
    meta = await port.get_chunk_metadata(session_id=session_id, index=1, total_chunks=3)
    assert meta is not None
    assert meta.storage_key == storage_key_for(session_id, 1)
    assert meta.size_bytes == 7


async def test_stub_get_chunk_metadata_uses_the_final_key_for_single_chunk_sessions():
    session_id = str(uuid4())
    port = StubStoragePort()
    port.upload_bytes(storage_key_for(session_id, None), b"whole", mime="video/mp4")
    meta = await port.get_chunk_metadata(session_id=session_id, index=0, total_chunks=1)
    assert meta is not None
    assert meta.storage_key == storage_key_for(session_id, None)


async def test_stub_complete_upload_assembles_parts_and_drops_them():
    session_id = str(uuid4())
    port = StubStoragePort()
    part_a, part_b = storage_key_for(session_id, 0), storage_key_for(session_id, 1)
    final = storage_key_for(session_id, None)
    port.upload_bytes(part_a, b"AAAA", mime="video/mp4")
    port.upload_bytes(part_b, b"BBBB", mime="video/mp4")

    meta = await port.complete_upload(
        session_id=session_id,
        chunk_keys=[part_a, part_b],
        final_key=final,
        size_bytes=8,
        mime="video/mp4",
    )

    assert meta == StorageObjectMetadata(
        storage_key=final, size_bytes=8, checksum_sha256=_sha("AAAABBBB"), mime="video/mp4"
    )
    assert port.objects[final] == b"AAAABBBB"
    # Parts are consumed by assembly and can never be replayed.
    assert part_a not in port.objects
    assert part_b not in port.objects
    assert part_a not in port.content_types


async def test_stub_complete_upload_keeps_a_part_that_is_also_the_final_key():
    """A single-chunk session stores the part *at* the final key."""
    session_id = str(uuid4())
    port = StubStoragePort()
    final = storage_key_for(session_id, None)
    port.upload_bytes(final, b"whole-object", mime="video/mp4")

    meta = await port.complete_upload(
        session_id=session_id,
        chunk_keys=[final],
        final_key=final,
        size_bytes=12,
        mime="video/mp4",
    )
    assert meta.size_bytes == 12
    assert port.objects[final] == b"whole-object"


async def test_stub_complete_upload_rejects_a_missing_part():
    session_id = str(uuid4())
    port = StubStoragePort()
    present = storage_key_for(session_id, 0)
    absent = storage_key_for(session_id, 1)
    port.upload_bytes(present, b"AAAA", mime="video/mp4")

    with pytest.raises(StorageError, match=f"part {absent} missing"):
        await port.complete_upload(
            session_id=session_id,
            chunk_keys=[present, absent],
            final_key=storage_key_for(session_id, None),
            size_bytes=8,
            mime="video/mp4",
        )
    assert storage_key_for(session_id, None) not in port.objects


async def test_stub_complete_upload_rejects_a_content_type_mismatch():
    session_id = str(uuid4())
    port = StubStoragePort()
    part = storage_key_for(session_id, 0)
    port.upload_bytes(part, b"AAAA", mime="video/webm")

    with pytest.raises(StorageError, match="content type mismatch"):
        await port.complete_upload(
            session_id=session_id,
            chunk_keys=[part],
            final_key=storage_key_for(session_id, None),
            size_bytes=4,
            mime="video/mp4",
        )


async def test_stub_complete_upload_rejects_a_size_mismatch():
    session_id = str(uuid4())
    port = StubStoragePort()
    part = storage_key_for(session_id, 0)
    port.upload_bytes(part, b"AAAA", mime="video/mp4")

    with pytest.raises(StorageError, match="assembled size mismatch: 4 bytes != declared 99"):
        await port.complete_upload(
            session_id=session_id,
            chunk_keys=[part],
            final_key=storage_key_for(session_id, None),
            size_bytes=99,
            mime="video/mp4",
        )


async def test_stub_complete_upload_tolerates_parts_stored_without_a_mime():
    """A part with no recorded content type cannot contradict the session MIME."""
    session_id = str(uuid4())
    port = StubStoragePort()
    part = storage_key_for(session_id, 0)
    port.upload_bytes(part, b"AAAA")  # no mime

    meta = await port.complete_upload(
        session_id=session_id,
        chunk_keys=[part],
        final_key=storage_key_for(session_id, None),
        size_bytes=4,
        mime="video/mp4",
    )
    assert meta.mime == "video/mp4"


async def test_stub_cleanup_upload_removes_every_part_and_the_final_object():
    session_id = str(uuid4())
    port = StubStoragePort()
    part = storage_key_for(session_id, 0)
    final = storage_key_for(session_id, None)
    port.upload_bytes(part, b"A", mime="video/mp4")
    port.upload_bytes(final, b"A", mime="video/mp4")

    await port.cleanup_upload(session_id=session_id, chunk_keys=[part], final_key=final)

    assert port.objects == {}
    assert port.content_types == {}


async def test_stub_cleanup_upload_is_idempotent_for_missing_objects():
    session_id = str(uuid4())
    port = StubStoragePort()
    await port.cleanup_upload(
        session_id=session_id, chunk_keys=["gone"], final_key="also-gone"
    )  # no raise


# ---------------------------------------------------------------------------
# S3StoragePort — construction.
# ---------------------------------------------------------------------------


def test_s3_port_builds_its_client_from_settings_style_arguments(s3_client):
    client, created = s3_client
    port = _s3_port()
    assert port._client is client
    assert created == [
        {
            "service": "s3",
            "region_name": "us-east-1",
            "aws_access_key_id": "AKIA-TEST",
            "aws_secret_access_key": "shh",
            "endpoint_url": "http://minio:9000",
        }
    ]


def test_s3_port_passes_none_endpoint_url_when_unset(s3_client):
    _client, created = s3_client
    _s3_port(endpoint_url="")
    assert created[0]["endpoint_url"] is None


def test_s3_port_clamps_the_presign_ttl_and_starts_with_no_upload_ids(s3_client):
    port = _s3_port(ttl_seconds=99999, max_ttl_seconds=3600)
    assert port.ttl_seconds == 3600
    assert port._upload_ids == {}


# ---------------------------------------------------------------------------
# S3StoragePort — begin_upload.
# ---------------------------------------------------------------------------


async def test_begin_upload_creates_a_multipart_upload_once_per_session(s3_client):
    client, _ = s3_client
    client.create_multipart_upload.return_value = {"UploadId": "upload-1"}
    port = _s3_port()
    session_id = str(uuid4())

    assert await port.begin_upload(session_id=session_id, mime="video/mp4") == "upload-1"
    # A second call is memoized, not a second multipart upload.
    assert await port.begin_upload(session_id=session_id, mime="video/mp4") == "upload-1"
    client.create_multipart_upload.assert_called_once_with(
        Bucket="wildframe-uploads",
        Key=storage_key_for(session_id, None),
        ContentType="video/mp4",
    )
    assert port._upload_ids == {session_id: "upload-1"}


async def test_begin_upload_raises_when_s3_returns_no_upload_id(s3_client):
    client, _ = s3_client
    client.create_multipart_upload.return_value = {"UploadId": None}
    port = _s3_port()
    with pytest.raises(StorageError, match="returned no UploadId"):
        await port.begin_upload(session_id=str(uuid4()), mime="video/mp4")
    assert port._upload_ids == {}


# ---------------------------------------------------------------------------
# S3StoragePort — create_upload.
# ---------------------------------------------------------------------------


async def test_create_upload_presigns_a_put_for_a_single_shot_object(s3_client):
    client, _ = s3_client
    client.generate_presigned_url.return_value = "https://s3/presigned"
    port = _s3_port(ttl_seconds=600)
    session_id = str(uuid4())

    presigned = await port.create_upload(
        session_id=session_id, filename="clip.mp4", mime="video/mp4"
    )

    assert presigned.storage_key == storage_key_for(session_id, None)
    assert presigned.upload_url == "https://s3/presigned"
    assert presigned.method == "PUT"
    assert presigned.headers == {"Content-Type": "video/mp4"}
    assert presigned.expires_in_seconds == 600
    client.generate_presigned_url.assert_called_once_with(
        "put_object",
        Params={
            "Bucket": "wildframe-uploads",
            "Key": storage_key_for(session_id, None),
            "ContentType": "video/mp4",
        },
        ExpiresIn=600,
    )
    client.create_multipart_upload.assert_not_called()


async def test_create_upload_presigns_a_part_when_the_upload_id_is_known(s3_client):
    client, _ = s3_client
    client.generate_presigned_url.return_value = "https://s3/part"
    port = _s3_port()
    session_id = str(uuid4())
    port._upload_ids[session_id] = "upload-1"

    presigned = await port.create_upload(
        session_id=session_id,
        filename="clip.mp4",
        mime="video/mp4",
        chunk_index=4,
        upload_id="upload-explicit",
    )

    # Part numbers are 1-based; chunk_index 4 is part 5.
    assert presigned.storage_key == storage_key_for(session_id, 4)
    client.generate_presigned_url.assert_called_once_with(
        "upload_part",
        Params={
            "Bucket": "wildframe-uploads",
            "Key": storage_key_for(session_id, 4),
            "UploadId": "upload-explicit",
            "PartNumber": 5,
        },
        ExpiresIn=port.ttl_seconds,
    )
    client.create_multipart_upload.assert_not_called()


async def test_create_upload_starts_a_multipart_upload_when_none_is_known(s3_client):
    client, _ = s3_client
    client.generate_presigned_url.return_value = "https://s3/part"
    client.create_multipart_upload.return_value = {"UploadId": "upload-lazy"}
    port = _s3_port()
    session_id = str(uuid4())

    await port.create_upload(
        session_id=session_id, filename="clip.mp4", mime="video/mp4", chunk_index=0
    )

    # The multipart upload is created against the FINAL key, not the part key.
    client.create_multipart_upload.assert_called_once_with(
        Bucket="wildframe-uploads",
        Key=storage_key_for(session_id, None),
        ContentType="video/mp4",
    )
    assert port._upload_ids == {session_id: "upload-lazy"}
    assert client.generate_presigned_url.call_args.args == ("upload_part",)
    assert client.generate_presigned_url.call_args.kwargs["Params"]["UploadId"] == ("upload-lazy")


async def test_create_upload_raises_when_the_lazy_multipart_upload_has_no_id(s3_client):
    client, _ = s3_client
    client.create_multipart_upload.return_value = {"UploadId": None}
    port = _s3_port()
    with pytest.raises(StorageError, match="returned no UploadId"):
        await port.create_upload(
            session_id=str(uuid4()),
            filename="clip.mp4",
            mime="video/mp4",
            chunk_index=0,
        )
    assert port._upload_ids == {}


# ---------------------------------------------------------------------------
# S3StoragePort — get_object_metadata.
# ---------------------------------------------------------------------------


async def test_get_object_metadata_computes_sha256_under_the_verification_budget(s3_client):
    client, _ = s3_client
    client.head_object.return_value = {"ContentLength": 5, "ContentType": "video/mp4"}
    client.get_object.return_value = {"Body": MagicMock(read=lambda: b"hello")}

    meta = await _s3_port(checksum_verify_max_bytes=1024).get_object_metadata(
        storage_key="uploads/s/final"
    )

    assert meta == StorageObjectMetadata(
        storage_key="uploads/s/final",
        size_bytes=5,
        checksum_sha256=_sha("hello"),
        mime="video/mp4",
    )
    client.head_object.assert_called_once_with(Bucket="wildframe-uploads", Key="uploads/s/final")
    client.get_object.assert_called_once_with(Bucket="wildframe-uploads", Key="uploads/s/final")


async def test_get_object_metadata_skips_the_download_for_huge_objects(s3_client):
    client, _ = s3_client
    client.head_object.return_value = {"ContentLength": 10**12, "ContentType": "video/mp4"}

    meta = await _s3_port(checksum_verify_max_bytes=1024).get_object_metadata(storage_key="k")

    # No SHA-256 is claimed for an object too large to verify.
    assert meta.checksum_sha256 is None
    assert meta.size_bytes == 10**12
    client.get_object.assert_not_called()


async def test_get_object_metadata_returns_none_on_a_404(s3_client):
    client, _ = s3_client
    client.head_object.side_effect = _not_found()
    assert await _s3_port().get_object_metadata(storage_key="gone") is None


async def test_get_object_metadata_propagates_non_404_errors(s3_client):
    client, _ = s3_client
    client.head_object.side_effect = FakeClientError(403, "AccessDenied")
    with pytest.raises(FakeClientError, match="AccessDenied"):
        await _s3_port().get_object_metadata(storage_key="k")


async def test_get_object_metadata_propagates_a_response_without_a_status(s3_client):
    """A non-botocore exception has no ``response``; it must still surface."""
    client, _ = s3_client
    client.head_object.side_effect = OSError("connection reset")
    with pytest.raises(OSError, match="connection reset"):
        await _s3_port().get_object_metadata(storage_key="k")


async def test_get_object_metadata_tolerates_a_missing_content_type(s3_client):
    client, _ = s3_client
    client.head_object.return_value = {"ContentLength": 0}
    client.get_object.return_value = {"Body": MagicMock(read=lambda: b"")}
    meta = await _s3_port().get_object_metadata(storage_key="k")
    assert meta.mime is None
    assert meta.checksum_sha256 == _sha("")


# ---------------------------------------------------------------------------
# S3StoragePort — get_chunk_metadata.
# ---------------------------------------------------------------------------


async def test_get_chunk_metadata_reads_the_part_listing_for_multipart_parts(s3_client):
    client, _ = s3_client
    client.list_parts.return_value = {
        "Parts": [
            {"PartNumber": 1, "Size": 10, "ETag": '"e1"'},
            {"PartNumber": 2, "Size": 20, "ETag": '"e2"'},
        ]
    }
    port = _s3_port()
    session_id = str(uuid4())

    meta = await port.get_chunk_metadata(
        session_id=session_id, index=1, total_chunks=2, upload_id="upload-1"
    )

    assert meta == StorageObjectMetadata(
        storage_key=storage_key_for(session_id, 1),
        size_bytes=20,
        # S3 exposes no SHA-256 for a part of a non-checksummed multipart upload.
        checksum_sha256=None,
        mime=None,
    )
    # The listing is addressed at the final key + upload id, not the part key.
    client.list_parts.assert_called_once_with(
        Bucket="wildframe-uploads",
        Key=storage_key_for(session_id, None),
        UploadId="upload-1",
    )
    client.head_object.assert_not_called()


async def test_get_chunk_metadata_returns_none_for_an_uploaded_part_gap(s3_client):
    client, _ = s3_client
    client.list_parts.return_value = {"Parts": [{"PartNumber": 1, "Size": 10, "ETag": '"e"'}]}
    meta = await _s3_port().get_chunk_metadata(
        session_id=str(uuid4()), index=4, total_chunks=5, upload_id="upload-1"
    )
    assert meta is None


async def test_get_chunk_metadata_handles_an_empty_part_listing(s3_client):
    client, _ = s3_client
    client.list_parts.return_value = {}
    meta = await _s3_port().get_chunk_metadata(
        session_id=str(uuid4()), index=0, total_chunks=1, upload_id="upload-1"
    )
    assert meta is None


async def test_get_chunk_metadata_falls_back_to_head_without_an_upload_id(s3_client):
    client, _ = s3_client
    client.head_object.return_value = {"ContentLength": 7, "ContentType": "video/mp4"}
    client.get_object.return_value = {"Body": MagicMock(read=lambda: b"payload")}
    session_id = str(uuid4())

    meta = await _s3_port().get_chunk_metadata(session_id=session_id, index=0, total_chunks=2)

    assert meta is not None
    assert meta.storage_key == storage_key_for(session_id, 0)
    assert meta.checksum_sha256 == _sha("payload")
    client.list_parts.assert_not_called()


async def test_get_chunk_metadata_for_a_single_chunk_session_heads_the_final_key(s3_client):
    client, _ = s3_client
    client.head_object.return_value = {"ContentLength": 3, "ContentType": "video/mp4"}
    client.get_object.return_value = {"Body": MagicMock(read=lambda: b"abc")}
    session_id = str(uuid4())

    meta = await _s3_port().get_chunk_metadata(session_id=session_id, index=0, total_chunks=1)
    assert meta is not None
    assert meta.storage_key == storage_key_for(session_id, None)


# ---------------------------------------------------------------------------
# S3StoragePort — complete_upload.
# ---------------------------------------------------------------------------


def _listed_parts(count: int) -> dict:
    return {
        "Parts": [{"PartNumber": i + 1, "Size": 4, "ETag": f'"etag-{i + 1}"'} for i in range(count)]
    }


async def test_complete_upload_completes_the_multipart_upload_in_part_order(s3_client):
    client, _ = s3_client
    # S3 lists parts out of order; the adapter must sort before completing.
    client.list_parts.return_value = {
        "Parts": [
            {"PartNumber": 2, "Size": 4, "ETag": '"etag-2"'},
            {"PartNumber": 1, "Size": 4, "ETag": '"etag-1"'},
        ]
    }
    client.head_object.return_value = {"ContentLength": 8, "ContentType": "video/mp4"}
    client.get_object.return_value = {"Body": MagicMock(read=lambda: b"AAAABBBB")}
    port = _s3_port()
    session_id = uuid4()
    final = storage_key_for(session_id, None)
    port._upload_ids[session_id] = "upload-1"

    meta = await port.complete_upload(
        session_id=session_id,
        chunk_keys=["a", "b"],
        final_key=final,
        size_bytes=8,
        mime="video/mp4",
    )

    assert meta.size_bytes == 8 and meta.checksum_sha256 == _sha("AAAABBBB")
    client.list_parts.assert_called_once_with(
        Bucket="wildframe-uploads", Key=final, UploadId="upload-1"
    )
    client.complete_multipart_upload.assert_called_once_with(
        Bucket="wildframe-uploads",
        Key=final,
        UploadId="upload-1",
        MultipartUpload={
            "Parts": [
                {"PartNumber": 1, "ETag": '"etag-1"'},
                {"PartNumber": 2, "ETag": '"etag-2"'},
            ]
        },
    )
    # The upload id is consumed so a later cleanup cannot abort a finished upload.
    assert port._upload_ids == {}


async def test_complete_upload_rejects_a_gapped_or_malformed_part_list(s3_client):
    client, _ = s3_client
    client.list_parts.return_value = {"Parts": [{"PartNumber": 1, "Size": 4, "ETag": '"e"'}]}
    port = _s3_port()
    session_id = str(uuid4())

    with pytest.raises(StorageError, match=r"part list incomplete/malformed: \[1\] != \[1, 2\]"):
        await port.complete_upload(
            session_id=session_id,
            chunk_keys=["a", "b"],
            final_key=storage_key_for(session_id, None),
            size_bytes=8,
            mime="video/mp4",
            upload_id="upload-1",
        )
    client.complete_multipart_upload.assert_not_called()


async def test_complete_upload_rejects_duplicate_part_numbers(s3_client):
    client, _ = s3_client
    client.list_parts.return_value = {
        "Parts": [
            {"PartNumber": 1, "Size": 4, "ETag": '"a"'},
            {"PartNumber": 1, "Size": 4, "ETag": '"b"'},
        ]
    }
    with pytest.raises(StorageError, match="part list incomplete/malformed"):
        await _s3_port().complete_upload(
            session_id=str(uuid4()),
            chunk_keys=["a", "b"],
            final_key="k",
            size_bytes=8,
            mime="video/mp4",
            upload_id="upload-1",
        )


async def test_complete_upload_rejects_an_empty_part_listing(s3_client):
    client, _ = s3_client
    client.list_parts.return_value = {"Parts": []}
    with pytest.raises(StorageError, match=r"part list incomplete/malformed: \[\] != \[1\]"):
        await _s3_port().complete_upload(
            session_id=str(uuid4()),
            chunk_keys=["a"],
            final_key="k",
            size_bytes=4,
            mime="video/mp4",
            upload_id="upload-1",
        )


async def test_complete_upload_without_an_upload_id_skips_multipart_entirely(s3_client):
    client, _ = s3_client
    client.head_object.return_value = {"ContentLength": 4, "ContentType": "video/mp4"}
    client.get_object.return_value = {"Body": MagicMock(read=lambda: b"AAAA")}
    port = _s3_port()
    final = storage_key_for(str(uuid4()), None)

    meta = await port.complete_upload(
        session_id=str(uuid4()),
        chunk_keys=["a"],
        final_key=final,
        size_bytes=4,
        mime="video/mp4",
    )
    assert meta.size_bytes == 4
    client.list_parts.assert_not_called()
    client.complete_multipart_upload.assert_not_called()


async def test_complete_upload_uses_the_memoized_upload_id_when_none_is_passed(s3_client):
    client, _ = s3_client
    client.list_parts.return_value = _listed_parts(1)
    client.head_object.return_value = {"ContentLength": 4, "ContentType": "video/mp4"}
    client.get_object.return_value = {"Body": MagicMock(read=lambda: b"AAAA")}
    port = _s3_port()
    session_id = str(uuid4())
    port._upload_ids[session_id] = "upload-memo"

    await port.complete_upload(
        session_id=session_id,
        chunk_keys=["a"],
        final_key=storage_key_for(session_id, None),
        size_bytes=4,
        mime="video/mp4",
    )
    assert client.list_parts.call_args.kwargs["UploadId"] == "upload-memo"


async def test_complete_upload_rejects_a_final_object_that_is_missing(s3_client):
    client, _ = s3_client
    client.head_object.side_effect = _not_found()
    final = storage_key_for(str(uuid4()), None)
    with pytest.raises(StorageError, match="missing after completion"):
        await _s3_port().complete_upload(
            session_id=str(uuid4()),
            chunk_keys=["a"],
            final_key=final,
            size_bytes=4,
            mime="video/mp4",
        )


async def test_complete_upload_rejects_a_final_size_mismatch(s3_client):
    client, _ = s3_client
    client.head_object.return_value = {"ContentLength": 3, "ContentType": "video/mp4"}
    client.get_object.return_value = {"Body": MagicMock(read=lambda: b"AAA")}
    with pytest.raises(StorageError, match="final size mismatch: 3 != declared 4"):
        await _s3_port().complete_upload(
            session_id=str(uuid4()),
            chunk_keys=["a"],
            final_key="k",
            size_bytes=4,
            mime="video/mp4",
        )


async def test_complete_upload_rejects_a_content_type_mismatch(s3_client):
    client, _ = s3_client
    client.head_object.return_value = {"ContentLength": 4, "ContentType": "video/webm"}
    client.get_object.return_value = {"Body": MagicMock(read=lambda: b"AAAA")}
    with pytest.raises(StorageError, match="content type mismatch: stored 'video/webm'"):
        await _s3_port().complete_upload(
            session_id=str(uuid4()),
            chunk_keys=["a"],
            final_key="k",
            size_bytes=4,
            mime="video/mp4",
        )


async def test_complete_upload_tolerates_an_object_stored_without_a_content_type(s3_client):
    client, _ = s3_client
    client.head_object.return_value = {"ContentLength": 4}
    client.get_object.return_value = {"Body": MagicMock(read=lambda: b"AAAA")}
    meta = await _s3_port().complete_upload(
        session_id=str(uuid4()),
        chunk_keys=["a"],
        final_key="k",
        size_bytes=4,
        mime="video/mp4",
    )
    assert meta.mime is None


# ---------------------------------------------------------------------------
# S3StoragePort — cleanup_upload.
# ---------------------------------------------------------------------------


async def test_cleanup_upload_deletes_every_part_and_the_final_object(s3_client):
    client, _ = s3_client
    port = _s3_port()
    deleted: list[str] = []
    client.delete_object.side_effect = lambda Bucket, Key: deleted.append(Key)

    await port.cleanup_upload(session_id=str(uuid4()), chunk_keys=["p0", "p1"], final_key="final")

    assert deleted == ["p0", "p1", "final"]
    assert all(
        call.kwargs["Bucket"] == "wildframe-uploads" for call in client.delete_object.call_args_list
    )
    client.abort_multipart_upload.assert_not_called()


async def test_cleanup_upload_tolerates_a_missing_object(s3_client):
    client, _ = s3_client
    client.delete_object.side_effect = _not_found()
    port = _s3_port()  # must not raise
    await port.cleanup_upload(session_id=str(uuid4()), chunk_keys=["gone"], final_key="f")


async def test_cleanup_upload_aborts_the_multipart_upload_it_memoized(s3_client):
    client, _ = s3_client
    port = _s3_port()
    session_id = str(uuid4())
    port._upload_ids[session_id] = "upload-1"

    await port.cleanup_upload(session_id=session_id, chunk_keys=["p"], final_key="final")

    client.abort_multipart_upload.assert_called_once_with(
        Bucket="wildframe-uploads", Key="final", UploadId="upload-1"
    )
    assert port._upload_ids == {}, "the memoized upload id is released"


async def test_cleanup_upload_prefers_an_explicit_upload_id(s3_client):
    client, _ = s3_client
    port = _s3_port()
    session_id = str(uuid4())
    port._upload_ids[session_id] = "memoized"

    await port.cleanup_upload(
        session_id=session_id, chunk_keys=[], final_key="final", upload_id="explicit"
    )
    assert client.abort_multipart_upload.call_args.kwargs["UploadId"] == "explicit"


async def test_cleanup_upload_tolerates_a_failing_abort(s3_client):
    client, _ = s3_client
    client.abort_multipart_upload.side_effect = FakeClientError(404, "NoSuchUpload")
    port = _s3_port()
    await port.cleanup_upload(
        session_id=str(uuid4()), chunk_keys=[], final_key="final", upload_id="upload-1"
    )  # must not raise


async def test_cleanup_upload_with_nothing_to_abort(s3_client):
    client, _ = s3_client
    await _s3_port().cleanup_upload(session_id=str(uuid4()), chunk_keys=[], final_key="f")
    client.abort_multipart_upload.assert_not_called()


# ---------------------------------------------------------------------------
# The process-wide port singleton.
# ---------------------------------------------------------------------------


def test_get_storage_memoizes_the_default_port():
    set_storage(None)  # type: ignore[arg-type]
    port = get_storage()
    assert isinstance(port, StubStoragePort)
    assert get_storage() is port
    # Leave the process-wide port clean for the rest of the suite.
    set_storage(port)


def test_set_storage_overrides_the_process_wide_port():
    original = get_storage()
    try:
        replacement = StubStoragePort(bucket="other")
        set_storage(replacement)
        assert get_storage() is replacement
    finally:
        set_storage(original)


def test_build_storage_returns_the_stub_for_the_stub_backend():
    with patch.object(settings, "STORAGE_BACKEND", "stub"):
        port = _build_storage()
    assert isinstance(port, StubStoragePort)
    assert port.bucket == settings.S3_BUCKET
    assert port.ttl_seconds == settings.S3_PRESIGNED_URL_TTL_SECONDS


def test_build_storage_returns_the_s3_adapter_for_the_s3_backend(s3_client):
    _client, created = s3_client
    with (
        patch.object(settings, "STORAGE_BACKEND", "s3"),
        patch.object(settings, "S3_BUCKET", "prod-uploads"),
        patch.object(settings, "S3_REGION", "eu-west-1"),
        patch.object(settings, "S3_ACCESS_KEY_ID", "AKIA-PROD"),
        patch.object(settings, "S3_SECRET_ACCESS_KEY", "prod-secret"),
        patch.object(settings, "S3_ENDPOINT_URL", ""),
    ):
        port = _build_storage()
    assert isinstance(port, S3StoragePort)
    assert port.bucket == "prod-uploads"
    assert port.checksum_verify_max_bytes == settings.CHECKSUM_VERIFY_MAX_BYTES
    assert created[0]["region_name"] == "eu-west-1"
    assert created[0]["aws_access_key_id"] == "AKIA-PROD"
    assert created[0]["endpoint_url"] is None
