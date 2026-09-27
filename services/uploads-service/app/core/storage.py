"""Storage port: pre-signed upload URLs + authoritative object verification.

The service never talks to object storage directly; it asks a ``StoragePort``
for pre-signed PUT/part URLs (and the stable storage key each object will live
at), then verifies completion against *authoritative* storage metadata (size,
content type, server-computed digest) instead of trusting the client.

    * ``StubStoragePort``  — deterministic, in-process, holds real bytes.
                            Default for dev/test.
    * ``S3StoragePort``    — real AWS S3 / MinIO via ``boto3``. Uses native
      multipart semantics (create_multipart_upload -> presigned upload_part ->
      complete_multipart_upload). Only instantiated when
      ``settings.STORAGE_BACKEND == "s3"``.

Storage keys are derived exclusively from the unguessable session UUID — the
client-controlled filename never influences the storage path (it is display
metadata only). That eliminates path traversal, Unicode-normalization
collisions, and cross-session overwrites by construction.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PresignedUpload:
    """A pre-signed PUT URL plus the key the object will be stored under."""

    storage_key: str
    upload_url: str
    # HTTP method the client should use (PUT for single, POST/part for multi).
    method: str = "PUT"
    # Optional headers the client must send (e.g. Content-Type, x-amz-acl).
    headers: dict | None = None
    expires_in_seconds: int = 3600


@dataclass
class StorageObjectMetadata:
    """Authoritative metadata read back from object storage."""

    storage_key: str
    size_bytes: int
    # Server-computed SHA-256 of the object bytes; None when the object is
    # larger than the verification budget (storage-side integrity metadata,
    # e.g. multipart ETags, still applies).
    checksum_sha256: str | None
    mime: str | None


class StorageError(Exception):
    """Object-storage failure (missing object, size/content-type mismatch)."""


def _raise_storage_error(message: str) -> str:
    """Narrow a Optional storage return by raising instead (#105 follow-up)."""
    raise StorageError(message)


def storage_key_for(session_id: str, chunk_index: int | None) -> str:
    """Derive the storage key for a session chunk (or the final object).

    Keys contain only the unguessable session UUID: the client filename never
    influences the storage path. ``chunk_index is None`` yields the final
    assembled-object key.
    """
    if chunk_index is None:
        return f"uploads/{session_id}/final"
    return f"uploads/{session_id}/chunks/{chunk_index:05d}"


def clamp_ttl(ttl_seconds: int, max_ttl_seconds: int) -> int:
    """Bound a pre-signed URL lifetime to a hard ceiling (min 1 second)."""
    return max(1, min(ttl_seconds, max_ttl_seconds))


class StoragePort(ABC):
    """Port (interface) for the upload/storage lifecycle."""

    @abstractmethod
    async def begin_upload(self, *, session_id: str, mime: str) -> str | None:
        """Open a multipart upload for a chunked session.

        Returns the provider upload id, or None for backends without
        multipart semantics. The id is persisted on the session so chunk
        registration, completion and cleanup all address the same upload.
        """
        raise NotImplementedError

    @abstractmethod
    async def create_upload(
        self,
        *,
        session_id: str,
        filename: str,
        mime: str,
        chunk_index: int | None = None,
        upload_id: str | None = None,
    ) -> PresignedUpload:
        """Return a pre-signed URL for a chunk (or the whole object)."""
        raise NotImplementedError

    @abstractmethod
    async def get_object_metadata(self, *, storage_key: str) -> StorageObjectMetadata | None:
        """Return authoritative metadata for an object, or None if missing."""
        raise NotImplementedError

    @abstractmethod
    async def get_chunk_metadata(
        self,
        *,
        session_id: str,
        index: int,
        total_chunks: int,
        upload_id: str | None = None,
    ) -> StorageObjectMetadata | None:
        """Return authoritative metadata for one chunk, or None if missing.

        ``index`` is the chunk's position in the session's chunk plan; a
        single-chunk session is stored under the final object key.
        """
        raise NotImplementedError

    @abstractmethod
    async def complete_upload(
        self,
        *,
        session_id: str,
        chunk_keys: list[str],
        final_key: str,
        size_bytes: int,
        mime: str,
        upload_id: str | None = None,
    ) -> StorageObjectMetadata:
        """Assemble/commit chunks into the final object and verify it.

        Raises ``StorageError`` when any part is missing, sizes do not match
        the declared session size, or the stored content type differs from the
        session MIME. Returns authoritative metadata of the final object.
        """
        raise NotImplementedError

    @abstractmethod
    async def cleanup_upload(
        self,
        *,
        session_id: str,
        chunk_keys: list[str],
        final_key: str,
        upload_id: str | None = None,
    ) -> None:
        """Delete chunk/final objects and cancel multipart state.

        Must be idempotent and tolerate already-missing objects.
        """
        raise NotImplementedError


class StubStoragePort(StoragePort):
    """Deterministic in-process storage port (default).

    Holds real bytes: tests PUT data with ``upload_bytes`` and the port's
    metadata (size, sha256, content type) is computed from those bytes, so the
    service always verifies authoritative state.
    """

    def __init__(
        self,
        bucket: str = "wildframe-uploads",
        ttl_seconds: int = 3600,
        max_ttl_seconds: int = 3600,
    ) -> None:
        self.bucket = bucket
        self.ttl_seconds = clamp_ttl(ttl_seconds, max_ttl_seconds)
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}

    def upload_bytes(self, key: str, data: bytes, mime: str = "") -> None:
        """Simulate a client PUTting bytes to ``key`` (test helper)."""
        self.objects[key] = data
        self.content_types[key] = mime

    async def begin_upload(self, *, session_id: str, mime: str) -> str | None:
        """The stub stores chunks as plain objects: no multipart state."""
        return None

    async def create_upload(
        self,
        *,
        session_id: str,
        filename: str,
        mime: str,
        chunk_index: int | None = None,
        upload_id: str | None = None,
    ) -> PresignedUpload:
        storage_key = storage_key_for(session_id, chunk_index)
        upload_url = (
            f"https://storage.local/{self.bucket}/{storage_key}"
            f"?x-upload-session={session_id}&x-mime={mime}"
        )
        logger.info(
            "stub presigned URL issued: session=%s key=%s chunk=%s",
            session_id,
            storage_key,
            chunk_index,
        )
        return PresignedUpload(
            storage_key=storage_key,
            upload_url=upload_url,
            method="PUT",
            headers={"Content-Type": mime},
            expires_in_seconds=self.ttl_seconds,
        )

    async def get_object_metadata(self, *, storage_key: str) -> StorageObjectMetadata | None:
        data = self.objects.get(storage_key)
        if data is None:
            return None
        return StorageObjectMetadata(
            storage_key=storage_key,
            size_bytes=len(data),
            checksum_sha256=_sha256(data),
            mime=self.content_types.get(storage_key),
        )

    async def get_chunk_metadata(
        self,
        *,
        session_id: str,
        index: int,
        total_chunks: int,
        upload_id: str | None = None,
    ) -> StorageObjectMetadata | None:
        storage_key = storage_key_for(session_id, index if total_chunks > 1 else None)
        return await self.get_object_metadata(storage_key=storage_key)

    async def complete_upload(
        self,
        *,
        session_id: str,
        chunk_keys: list[str],
        final_key: str,
        size_bytes: int,
        mime: str,
        upload_id: str | None = None,
    ) -> StorageObjectMetadata:
        parts = []
        for key in chunk_keys:
            data = self.objects.get(key)
            if data is None:
                raise StorageError(f"part {key} missing")
            parts.append(data)
        # Server-side media verification: the session-declared MIME must match
        # what was actually stored (stub records the MIME passed at PUT time).
        for key in chunk_keys:
            stored_mime = self.content_types.get(key, "")
            if stored_mime and mime and stored_mime != mime:
                raise StorageError(
                    f"content type mismatch for {key}: stored {stored_mime!r} != session {mime!r}"
                )
        assembled = b"".join(parts)
        if len(assembled) != size_bytes:
            raise StorageError(
                f"assembled size mismatch: {len(assembled)} bytes != declared {size_bytes}"
            )
        self.objects[final_key] = assembled
        self.content_types[final_key] = mime
        # Parts are consumed by assembly: drop them so they cannot be reused.
        for key in chunk_keys:
            if key != final_key:
                self.objects.pop(key, None)
                self.content_types.pop(key, None)
        return StorageObjectMetadata(
            storage_key=final_key,
            size_bytes=len(assembled),
            checksum_sha256=_sha256(assembled),
            mime=mime,
        )

    async def cleanup_upload(
        self,
        *,
        session_id: str,
        chunk_keys: list[str],
        final_key: str,
        upload_id: str | None = None,
    ) -> None:
        for key in [*chunk_keys, final_key]:
            self.objects.pop(key, None)
            self.content_types.pop(key, None)


class S3StoragePort(StoragePort):
    """S3-backed pre-signed URL generation + multipart completion (boto3).

    Used in production / staging. Imported lazily so the ``boto3`` dependency
    is only required when this adapter is actually selected. Synchronous boto3
    calls run in the executor, off the event loop.
    """

    def __init__(
        self,
        *,
        region: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        endpoint_url: str = "",
        ttl_seconds: int = 3600,
        max_ttl_seconds: int = 3600,
        checksum_verify_max_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self.region = region
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.ttl_seconds = clamp_ttl(ttl_seconds, max_ttl_seconds)
        self.checksum_verify_max_bytes = checksum_verify_max_bytes
        # Multipart UploadIds are NOT cached here: the session row is the single
        # source of truth (see ``UploadSession.multipart_upload_id``) so any
        # worker can complete or abort an upload created by another process.

        import boto3

        self._client = boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            endpoint_url=endpoint_url or None,
        )

    async def begin_upload(self, *, session_id: str, mime: str) -> str:
        """Create the multipart upload for ``final_key`` and return its UploadId.

        The caller persists the id on the session row; nothing about an active
        upload lives in this process, so a restart or a second worker can still
        sign parts, complete and abort.
        """
        created = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._client.create_multipart_upload(
                Bucket=self.bucket,
                Key=storage_key_for(session_id, None),
                ContentType=mime,
            )["UploadId"],
        )
        if created is None:
            _raise_storage_error("S3 create_multipart_upload returned no UploadId")
        return created

    async def create_upload(
        self,
        *,
        session_id: str,
        filename: str,
        mime: str,
        chunk_index: int | None = None,
        upload_id: str | None = None,
    ) -> PresignedUpload:
        final_key = storage_key_for(session_id, None)
        if chunk_index is None:
            # Single-object upload: one signed PUT against the final key.
            storage_key = final_key
            url = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.generate_presigned_url(
                    "put_object",
                    Params={"Bucket": self.bucket, "Key": storage_key, "ContentType": mime},
                    ExpiresIn=self.ttl_seconds,
                ),
            )
        else:
            if upload_id is None:
                # Never sign a part against an upload we do not know: the
                # UploadId is created once (begin_upload) and persisted.
                _raise_storage_error("multipart part URL requested without an UploadId")
            # Multipart parts are addressed by the key the UploadId was created
            # against — the final object key — plus a PartNumber. Signing any
            # other key produces an UploadPart that S3 rejects outright.
            storage_key = final_key
            url = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.generate_presigned_url(
                    "upload_part",
                    Params={
                        "Bucket": self.bucket,
                        "Key": final_key,
                        "UploadId": upload_id,
                        "PartNumber": chunk_index + 1,
                    },
                    ExpiresIn=self.ttl_seconds,
                ),
            )
        logger.info(
            "s3 presigned URL issued: session=%s key=%s chunk=%s",
            session_id,
            storage_key,
            chunk_index,
        )
        return PresignedUpload(
            storage_key=storage_key,
            upload_url=url,
            method="PUT",
            headers={"Content-Type": mime},
            expires_in_seconds=self.ttl_seconds,
        )

    async def get_object_metadata(self, *, storage_key: str) -> StorageObjectMetadata | None:
        try:
            head = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.head_object(
                    Bucket=self.bucket, Key=storage_key, ChecksumMode="ENABLED"
                ),
            )
        except Exception as exc:  # noqa: BLE001 - botocore 404 surface
            if _is_missing_resource(exc):
                return None
            raise

        # Prefer the provider-computed digest when the object carries one: it
        # is authoritative and, unlike a full read, not bounded by size.
        checksum: str | None = head.get("ChecksumSHA256")
        if checksum is None and head["ContentLength"] <= self.checksum_verify_max_bytes:
            body = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.get_object(Bucket=self.bucket, Key=storage_key)["Body"].read(),
            )
            checksum = _sha256(body)
        return StorageObjectMetadata(
            storage_key=storage_key,
            size_bytes=head["ContentLength"],
            checksum_sha256=checksum,
            mime=head.get("ContentType"),
        )

    async def get_chunk_metadata(
        self,
        *,
        session_id: str,
        index: int,
        total_chunks: int,
        upload_id: str | None = None,
    ) -> StorageObjectMetadata | None:
        storage_key = storage_key_for(session_id, index if total_chunks > 1 else None)
        if upload_id is None:
            return await self.get_object_metadata(storage_key=storage_key)
        # Multipart parts are not objects: read their authoritative byte count
        # from the provider's part listing instead of a HEAD request.
        parts = await self._list_all_parts(
            final_key=storage_key_for(session_id, None), upload_id=upload_id
        )
        for part in parts:
            if part.get("PartNumber") == index + 1:
                return StorageObjectMetadata(
                    storage_key=storage_key,
                    size_bytes=part["Size"],
                    # S3 exposes no SHA-256 for parts of a multipart upload that
                    # was not created with a checksum algorithm; part integrity
                    # is covered by the size check here and the ETag
                    # verification in complete_upload.
                    checksum_sha256=None,
                    mime=None,
                )
            if part.get("PartNumber", 0) > index + 1:
                break  # the listing is ascending: this part was never uploaded
        return None

    async def complete_upload(
        self,
        *,
        session_id: str,
        chunk_keys: list[str],
        final_key: str,
        size_bytes: int,
        mime: str,
        upload_id: str | None = None,
    ) -> StorageObjectMetadata:
        if upload_id is not None:
            try:
                listed = await self._list_all_parts(final_key=final_key, upload_id=upload_id)
            except Exception as exc:  # noqa: BLE001
                if not _is_missing_upload(exc):
                    raise
                # NoSuchUpload: a previous attempt already completed the
                # multipart upload and the DB commit then failed. Fall through
                # to the final-object verification so the retry converges.
                listed = None
            if listed is not None:
                # Provider semantics: parts must be exactly 1..N, no gaps/dups.
                expected = list(range(1, len(chunk_keys) + 1))
                actual = [p["PartNumber"] for p in listed]
                if actual != expected:
                    raise StorageError(
                        f"multipart part list incomplete/malformed: {actual} != {expected}"
                    )
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._client.complete_multipart_upload(
                        Bucket=self.bucket,
                        Key=final_key,
                        UploadId=upload_id,
                        MultipartUpload={
                            "Parts": [
                                {"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in listed
                            ]
                        },
                    ),
                )

        metadata = await self.get_object_metadata(storage_key=final_key)
        if metadata is None:
            raise StorageError(f"final object {final_key} missing after completion")
        if metadata.size_bytes != size_bytes:
            raise StorageError(
                f"final size mismatch: {metadata.size_bytes} != declared {size_bytes}"
            )
        if metadata.mime and mime and metadata.mime != mime:
            raise StorageError(
                f"content type mismatch: stored {metadata.mime!r} != session {mime!r}"
            )
        return metadata

    async def _list_all_parts(self, *, final_key: str, upload_id: str) -> list[dict]:
        """Every uploaded part of ``upload_id``, following S3's pagination.

        ``ListParts`` returns at most 1,000 parts per response while a session
        may declare up to ``MAX_CHUNKS_PER_SESSION`` chunks, so a single call
        would silently under-report the plan. Parts come back in ascending
        PartNumber order, which is what ``complete_upload``'s 1..N check needs.
        """
        parts: list[dict] = []
        marker: int | str | None = None
        while True:
            params: dict = {
                "Bucket": self.bucket,
                "Key": final_key,
                "UploadId": upload_id,
                "MaxParts": 1000,
            }
            if marker is not None:
                params["PartNumberMarker"] = marker
            page = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda p=params: self._client.list_parts(**p),
            )
            parts.extend(page.get("Parts", []))
            if not page.get("IsTruncated"):
                return parts
            marker = page.get("NextPartNumberMarker")
            if marker is None:
                return parts

    async def cleanup_upload(
        self,
        *,
        session_id: str,
        chunk_keys: list[str],
        final_key: str,
        upload_id: str | None = None,
    ) -> None:
        """Delete the session's objects and cancel its multipart upload.

        Only *confirmed missing* resources count as success. Any other failure
        propagates so the caller keeps ``storage_cleaned_at`` unset and the
        reaper retries; swallowing it would mark the session cleaned while the
        bytes stayed in the bucket forever.
        """
        loop = asyncio.get_event_loop()
        for key in dict.fromkeys([*chunk_keys, final_key]):
            try:
                await loop.run_in_executor(
                    None,
                    lambda k=key: self._client.delete_object(Bucket=self.bucket, Key=k),
                )
            except Exception as exc:  # noqa: BLE001
                if not _is_missing_resource(exc):
                    raise
        if upload_id is not None:
            try:
                await loop.run_in_executor(
                    None,
                    lambda: self._client.abort_multipart_upload(
                        Bucket=self.bucket, Key=final_key, UploadId=upload_id
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                if not _is_missing_upload(exc):
                    raise


def _s3_error_code(exc: BaseException) -> str:
    """The botocore error code for an exception, or "" when it has none."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return ""
    return str(response.get("Error", {}).get("Code", "") or "")


def _s3_status(exc: BaseException) -> int:
    """The HTTP status botocore reported for an exception (0 when unknown)."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return 0
    return int(response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) or 0)


def _is_missing_resource(exc: BaseException) -> bool:
    """True only for a provider response proving the object is already gone."""
    return _s3_error_code(exc) in ("NoSuchKey", "NoSuchBucket", "NotFound", "404") or (
        _s3_status(exc) == 404
    )


def _is_missing_upload(exc: BaseException) -> bool:
    """True only for a provider response proving the multipart upload is gone."""
    return _s3_error_code(exc) == "NoSuchUpload" or _is_missing_resource(exc)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Process-wide storage port singleton (dependency-injected into services).
# ---------------------------------------------------------------------------

_storage: StoragePort | None = None


def get_storage() -> StoragePort:
    """Return the process-wide storage port, constructing the default if needed."""
    global _storage
    if _storage is None:
        _storage = _build_storage()
    return _storage


def set_storage(storage: StoragePort) -> None:
    """Override the process-wide storage port (used by tests)."""
    global _storage
    _storage = storage


def _build_storage() -> StoragePort:
    """Construct the storage port selected by ``settings.STORAGE_BACKEND``."""
    from app.core.settings import settings

    if settings.STORAGE_BACKEND == "s3":
        return S3StoragePort(
            region=settings.S3_REGION,
            bucket=settings.S3_BUCKET,
            access_key_id=settings.S3_ACCESS_KEY_ID,
            secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            endpoint_url=settings.S3_ENDPOINT_URL,
            ttl_seconds=settings.S3_PRESIGNED_URL_TTL_SECONDS,
            max_ttl_seconds=settings.PRESIGNED_URL_MAX_TTL_SECONDS,
            checksum_verify_max_bytes=settings.CHECKSUM_VERIFY_MAX_BYTES,
        )
    return StubStoragePort(
        bucket=settings.S3_BUCKET,
        ttl_seconds=settings.S3_PRESIGNED_URL_TTL_SECONDS,
        max_ttl_seconds=settings.PRESIGNED_URL_MAX_TTL_SECONDS,
    )
