"""Storage port: pre-signed upload URL generation.

The service never talks to object storage directly; it asks a ``StoragePort``
for a pre-signed PUT URL (and the stable storage key the object will live at).
This keeps storage swappable and testable via dependency injection:

    * ``StubStoragePort``  — deterministic, in-process. Default for dev/test.
    * ``S3StoragePort``    — real AWS S3 / MinIO via ``boto3`` presigned URLs.
      Only instantiated when ``settings.STORAGE_BACKEND == "s3"``.
"""
from __future__ import annotations

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


class StoragePort(ABC):
    """Port (interface) for generating pre-signed upload URLs."""

    @abstractmethod
    async def create_upload(
        self,
        *,
        session_id: str,
        filename: str,
        mime: str,
        chunk_index: int | None = None,
    ) -> PresignedUpload:
        """Return a pre-signed URL for a chunk (or the whole object)."""
        raise NotImplementedError


class StubStoragePort(StoragePort):
    """Deterministic in-process storage port (default).

    Produces a fake but well-formed URL so the upload flow is fully exercisable
    without S3. The "upload" is considered to have happened the moment the URL
    is handed out — ``complete_session`` still enforces the real invariant
    (all chunks registered + checksum) before emitting ``content.uploaded``.
    """

    def __init__(self, bucket: str = "wildframe-uploads") -> None:
        self.bucket = bucket

    async def create_upload(
        self,
        *,
        session_id: str,
        filename: str,
        mime: str,
        chunk_index: int | None = None,
    ) -> PresignedUpload:
        safe_name = filename.replace(" ", "_")
        if chunk_index is None:
            storage_key = f"uploads/{session_id}/{safe_name}"
        else:
            storage_key = f"uploads/{session_id}/chunks/{chunk_index:05d}-{safe_name}"
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
        )


class S3StoragePort(StoragePort):
    """S3-backed pre-signed URL generation (boto3).

    Used in production / staging. Imported lazily so the ``boto3`` dependency
    is only required when this adapter is actually selected.
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
    ) -> None:
        self.region = region
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.ttl_seconds = ttl_seconds

        import boto3

        self._client = boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            endpoint_url=endpoint_url or None,
        )

    async def create_upload(
        self,
        *,
        session_id: str,
        filename: str,
        mime: str,
        chunk_index: int | None = None,
    ) -> PresignedUpload:
        safe_name = filename.replace(" ", "_")
        if chunk_index is None:
            storage_key = f"uploads/{session_id}/{safe_name}"
        else:
            storage_key = f"uploads/{session_id}/chunks/{chunk_index:05d}-{safe_name}"

        # boto3 is synchronous; run it off the event loop thread.
        import asyncio

        url = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._client.generate_presigned_url(
                "put_object",
                Params={"Bucket": self.bucket, "Key": storage_key, "ContentType": mime},
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
        )
    return StubStoragePort(bucket=settings.S3_BUCKET)
