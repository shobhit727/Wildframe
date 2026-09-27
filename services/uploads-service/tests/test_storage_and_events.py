"""Focused coverage for authoritative upload storage and domain events."""

import pytest

from app.core.events import Event, InMemoryEventPublisher
from app.core.storage import StorageError, StubStoragePort, clamp_ttl, storage_key_for


def test_storage_key_and_ttl_bounds():
    assert storage_key_for("session", None) == "uploads/session/final"
    assert storage_key_for("session", 7) == "uploads/session/chunks/00007"
    assert clamp_ttl(0, 60) == 1
    assert clamp_ttl(120, 60) == 60
    assert clamp_ttl(30, 60) == 30


@pytest.mark.asyncio
async def test_stub_upload_metadata_is_authoritative():
    storage = StubStoragePort(bucket="test", ttl_seconds=10, max_ttl_seconds=5)
    upload = await storage.create_upload(
        session_id="session",
        filename="video.mp4",
        mime="video/mp4",
        chunk_index=None,
    )
    assert upload.storage_key == "uploads/session/final"
    assert upload.expires_in_seconds == 5

    storage.upload_bytes(upload.storage_key, b"abc", "video/mp4")
    metadata = await storage.get_object_metadata(storage_key=upload.storage_key)
    assert metadata is not None
    assert metadata.size_bytes == 3
    assert metadata.mime == "video/mp4"
    assert metadata.checksum_sha256 is not None


@pytest.mark.asyncio
async def test_stub_chunk_completion_assembles_and_consumes_parts():
    storage = StubStoragePort()
    first = await storage.create_upload(
        session_id="session", filename="video", mime="video/mp4", chunk_index=0
    )
    second = await storage.create_upload(
        session_id="session", filename="video", mime="video/mp4", chunk_index=1
    )
    storage.upload_bytes(first.storage_key, b"ab", "video/mp4")
    storage.upload_bytes(second.storage_key, b"cd", "video/mp4")

    result = await storage.complete_upload(
        session_id="session",
        chunk_keys=[first.storage_key, second.storage_key],
        final_key=storage_key_for("session", None),
        size_bytes=4,
        mime="video/mp4",
    )
    assert result.size_bytes == 4
    assert result.mime == "video/mp4"
    assert await storage.get_object_metadata(storage_key=first.storage_key) is None
    final = await storage.get_object_metadata(storage_key=result.storage_key)
    assert final is not None
    assert final.checksum_sha256 == result.checksum_sha256


@pytest.mark.asyncio
async def test_stub_completion_rejects_missing_mime_and_size_mismatches():
    storage = StubStoragePort()
    key = storage_key_for("session", 0)
    storage.upload_bytes(key, b"abc", "video/webm")

    with pytest.raises(StorageError, match="content type mismatch"):
        await storage.complete_upload(
            session_id="session",
            chunk_keys=[key],
            final_key=storage_key_for("session", None),
            size_bytes=3,
            mime="video/mp4",
        )

    storage.upload_bytes(key, b"abc", "video/mp4")
    with pytest.raises(StorageError, match="assembled size mismatch"):
        await storage.complete_upload(
            session_id="session",
            chunk_keys=[key],
            final_key=storage_key_for("session", None),
            size_bytes=4,
            mime="video/mp4",
        )

    with pytest.raises(StorageError, match="missing"):
        await storage.complete_upload(
            session_id="session",
            chunk_keys=["uploads/session/chunks/99999"],
            final_key=storage_key_for("session", None),
            size_bytes=1,
            mime="video/mp4",
        )


@pytest.mark.asyncio
async def test_single_chunk_metadata_uses_final_object():
    storage = StubStoragePort()
    final = storage_key_for("session", None)
    storage.upload_bytes(final, b"payload", "video/mp4")
    metadata = await storage.get_chunk_metadata(
        session_id="session", index=0, total_chunks=1
    )
    assert metadata is not None
    assert metadata.storage_key == final


@pytest.mark.asyncio
async def test_in_memory_event_publisher_records_serializable_events():
    event = Event(topic="content.uploaded", key="session", payload={"size": 4})
    publisher = InMemoryEventPublisher()
    await publisher.publish(event)

    assert publisher.sent == [event]
    data = event.to_dict()
    assert data["topic"] == "content.uploaded"
    assert data["key"] == "session"
    assert data["payload"] == {"size": 4}
    assert data["event_id"] == event.event_id
