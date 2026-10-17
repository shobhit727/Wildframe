"""Input-validation guards in ``UploadService`` and the S3 multipart limits.

Every one of these is a rejection path: a client must not be able to talk the
service into an unbounded chunk plan, a non-allowlisted media type, a
traversal-shaped filename, a forged checksum, or an S3 multipart layout that
S3 itself would reject.

Covered: ``services.py:92, 96, 104, 106, 118, 120, 122, 128, 154-157, 160``.
"""

from unittest.mock import patch
from uuid import uuid4

import pytest

from app.core.events import InMemoryEventPublisher, set_event_publisher
from app.core.settings import settings
from app.core.storage import StubStoragePort, set_storage
from app.services import UploadError, UploadService
from tests.test_upload_state_machine import FakeRepo

MIB = 1024 * 1024
GIB = 1024**3


@pytest.fixture
def service():
    set_event_publisher(InMemoryEventPublisher())
    set_storage(StubStoragePort())
    return UploadService(repo=FakeRepo())


# ---------------------------------------------------------------------------
# normalize_filename (services.py:92, 96).
# ---------------------------------------------------------------------------


class TestFilenameNormalization:
    @pytest.mark.parametrize("filename", ["", "   ", "///", "..", "\x00", "\x1f\x01"])
    async def test_a_filename_with_nothing_usable_in_it_is_rejected(self, service, filename):
        """Empty, separator-only, and control-character-only names are all refused."""
        with pytest.raises(UploadError, match="empty after normalization"):
            service.normalize_filename(filename)

    async def test_a_dot_only_basename_is_rejected(self, service):
        """``...`` survives splitting but is stripped to nothing by lstrip('.')."""
        with pytest.raises(UploadError, match="empty after normalization"):
            service.normalize_filename("....")

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("clip.mp4", "clip.mp4"),
            ("../../etc/passwd", "passwd"),
            ("..\\..\\windows\\system32\\cmd.exe", "cmd.exe"),
            ("  spaced.mp4  ", "spaced.mp4"),
            ("....hidden.mp4", "hidden.mp4"),
            ("dir/sub/clip.mp4", "clip.mp4"),
        ],
    )
    async def test_the_basename_is_what_survives(self, service, raw, expected):
        assert service.normalize_filename(raw) == expected

    async def test_the_name_is_length_bounded(self, service):
        assert len(service.normalize_filename("x" * 5000)) == 255

    async def test_unicode_is_nfkc_normalized(self, service):
        """A fullwidth 'clip' folds to ASCII under NFKC."""
        assert service.normalize_filename("ｃｌｉｐ.mp4") == "clip.mp4"


# ---------------------------------------------------------------------------
# validate_mime (services.py:104, 106).
# ---------------------------------------------------------------------------


class TestMimeValidation:
    @pytest.mark.parametrize(
        "mime",
        [
            "notamimetype",
            "video/",
            "/mp4",
            "video//mp4",
            "video/mp4; charset=utf-8",
            "video mp4",
        ],
    )
    async def test_a_syntactically_invalid_media_type_is_rejected(self, service, mime):
        with pytest.raises(UploadError, match="invalid media type"):
            service.validate_mime(mime)

    @pytest.mark.parametrize("mime", ["text/html", "application/x-msdownload", "image/svg+xml"])
    async def test_a_well_formed_but_non_allowlisted_type_is_rejected(self, service, mime):
        """A dangerous type must be refused even though it parses correctly."""
        with pytest.raises(UploadError, match="not allowed for upload"):
            service.validate_mime(mime)

    @pytest.mark.parametrize("mime", ["video/mp4", "audio/mpeg", "video/quicktime"])
    async def test_an_allowlisted_type_is_normalized_to_lowercase(self, service, mime):
        assert service.validate_mime(mime.upper()) == mime
        assert service.validate_mime(f"  {mime}  ") == mime

    async def test_surrounding_whitespace_is_stripped_not_rejected(self, service):
        """``validate_mime`` strips before matching, so padding never causes a 400."""
        assert service.validate_mime("  video/mp4\n") == "video/mp4"

    async def test_creation_rejects_a_non_allowlisted_type_end_to_end(self, service):
        with pytest.raises(UploadError, match="not allowed for upload"):
            await service.create_session(
                creator_id=uuid4(),
                filename="evil.html",
                mime="text/html",
                size_bytes=1024,
                chunk_size=1024,
            )


# ---------------------------------------------------------------------------
# compute_chunk_plan (services.py:118, 120, 122, 128).
# ---------------------------------------------------------------------------


class TestChunkPlan:
    @pytest.mark.parametrize("chunk_size", [0, -1, -MIB])
    async def test_a_non_positive_chunk_size_is_rejected(self, service, chunk_size):
        with pytest.raises(UploadError, match="chunk_size must be positive"):
            service.compute_chunk_plan(1024, chunk_size)

    @pytest.mark.parametrize("size_bytes", [0, -1, -MIB])
    async def test_a_non_positive_size_is_rejected(self, service, size_bytes):
        with pytest.raises(UploadError, match="size_bytes must be positive"):
            service.compute_chunk_plan(size_bytes, 1024)

    async def test_a_size_over_the_cap_is_rejected_and_names_both_numbers(self, service):
        too_big = settings.MAX_UPLOAD_SIZE_BYTES + 1
        with pytest.raises(UploadError) as excinfo:
            service.compute_chunk_plan(too_big, MIB)
        message = str(excinfo.value)
        assert "exceeds MAX_UPLOAD_SIZE_BYTES" in message
        assert str(settings.MAX_UPLOAD_SIZE_BYTES) in message
        assert str(too_big) in message

    async def test_a_size_exactly_at_the_cap_is_accepted(self, service):
        """The cap is inclusive — an off-by-one here would break large uploads."""
        chunk_size = settings.DEFAULT_CHUNK_SIZE_BYTES
        chosen, total = service.compute_chunk_plan(settings.MAX_UPLOAD_SIZE_BYTES, chunk_size)
        assert chosen == chunk_size
        assert total == settings.MAX_UPLOAD_SIZE_BYTES // chunk_size

    async def test_the_size_cap_and_the_chunk_cap_interact(self, service):
        """Max-size uploads need a large enough chunk size to stay under both caps.

        10 GiB at 1 MiB chunks asks for 10 240 chunks, over the 10 000 cap, so
        the chunk guard fires first. This pins the interaction between the two
        limits so changing either one does not silently make max-size uploads
        unplannable.
        """
        with pytest.raises(UploadError, match="exceeds MAX_CHUNKS_PER_SESSION"):
            service.compute_chunk_plan(settings.MAX_UPLOAD_SIZE_BYTES, MIB)

        # The smallest chunk size that plans a max-size upload cleanly.
        viable = -(-settings.MAX_UPLOAD_SIZE_BYTES // settings.MAX_CHUNKS_PER_SESSION)
        chosen, total = service.compute_chunk_plan(settings.MAX_UPLOAD_SIZE_BYTES, viable)
        assert chosen == viable
        assert total <= settings.MAX_CHUNKS_PER_SESSION

    async def test_a_plan_beyond_the_chunk_cap_is_rejected(self, service):
        """A tiny chunk size must not be able to mint an unbounded chunk list."""
        total = settings.MAX_CHUNKS_PER_SESSION + 1
        with pytest.raises(UploadError) as excinfo:
            service.compute_chunk_plan(total, 1)
        message = str(excinfo.value)
        assert "exceeds MAX_CHUNKS_PER_SESSION" in message
        assert str(total) in message

    async def test_a_plan_exactly_at_the_chunk_cap_is_accepted(self, service):
        _, total = service.compute_chunk_plan(settings.MAX_CHUNKS_PER_SESSION, 1)
        assert total == settings.MAX_CHUNKS_PER_SESSION

    @pytest.mark.parametrize(
        "size,chunk,expected",
        [(1, 1, 1), (1024, 1024, 1), (1025, 1024, 2), (2048, 1024, 2), (2049, 1024, 3)],
    )
    async def test_the_plan_rounds_up(self, service, size, chunk, expected):
        """total_chunks is a ceiling, so no byte is ever left without a chunk."""
        assert service.compute_chunk_plan(size, chunk) == (chunk, expected)


# ---------------------------------------------------------------------------
# S3 multipart limits (services.py:154-157).
# ---------------------------------------------------------------------------


class TestS3MultipartLimits:
    """S3 rejects sub-5 MiB and over-5 GiB parts; catch it before the round trip."""

    @pytest.fixture(autouse=True)
    def _s3_backend(self):
        with patch.object(settings, "STORAGE_BACKEND", "s3"):
            yield

    async def test_multipart_chunks_below_5_mib_are_refused(self, service):
        with pytest.raises(UploadError, match="at least 5 MiB"):
            await service.create_session(
                creator_id=uuid4(),
                filename="clip.mp4",
                mime="video/mp4",
                size_bytes=2 * MIB,
                chunk_size=1 * MIB,
            )

    async def test_a_single_part_below_5_mib_is_fine(self, service):
        """The 5 MiB floor is an S3 *multipart* rule; single PUTs are exempt."""
        session, _uploads = await service.create_session(
            creator_id=uuid4(),
            filename="clip.mp4",
            mime="video/mp4",
            size_bytes=2 * MIB,
            chunk_size=2 * MIB,
        )
        assert session.total_chunks == 1

    async def test_multipart_chunks_of_exactly_5_mib_are_accepted(self, service):
        session, _uploads = await service.create_session(
            creator_id=uuid4(),
            filename="clip.mp4",
            mime="video/mp4",
            size_bytes=10 * MIB,
            chunk_size=5 * MIB,
        )
        assert session.total_chunks == 2

    async def test_an_oversized_part_is_refused(self, service):
        with pytest.raises(UploadError, match="cannot exceed 5 GiB"):
            await service.create_session(
                creator_id=uuid4(),
                filename="clip.mp4",
                mime="video/mp4",
                size_bytes=6 * GIB,
                chunk_size=6 * GIB,
            )

    async def test_the_size_cap_is_checked_before_the_multipart_rules(self, service):
        """A 20 GiB request is over MAX_UPLOAD_SIZE_BYTES, not a multipart problem."""
        with pytest.raises(UploadError, match="exceeds MAX_UPLOAD_SIZE_BYTES"):
            await service.create_session(
                creator_id=uuid4(),
                filename="clip.mp4",
                mime="video/mp4",
                size_bytes=20 * GIB,
                chunk_size=6 * GIB,
            )

    async def test_the_multipart_limits_are_skipped_on_the_stub_backend(self, service):
        """The same 1 MiB multipart plan is fine when not talking to S3."""
        with patch.object(settings, "STORAGE_BACKEND", "stub"):
            session, _uploads = await service.create_session(
                creator_id=uuid4(),
                filename="clip.mp4",
                mime="video/mp4",
                size_bytes=2 * MIB,
                chunk_size=1 * MIB,
            )
        assert session.total_chunks == 2


# ---------------------------------------------------------------------------
# checksum_sha256 format (services.py:160).
# ---------------------------------------------------------------------------


class TestDeclaredChecksumFormat:
    @pytest.mark.parametrize(
        "checksum",
        [
            "not-a-digest",
            "a" * 63,
            "a" * 65,
            "g" * 64,  # 'g' is not a hex digit
            "A" * 63 + " ",
            "sha256:" + "a" * 64,
        ],
    )
    async def test_a_malformed_declared_checksum_is_refused(self, service, checksum):
        """A client must not be able to inject arbitrary text into the digest column."""
        with pytest.raises(UploadError, match="SHA-256 hex digest"):
            await service.create_session(
                creator_id=uuid4(),
                filename="clip.mp4",
                mime="video/mp4",
                size_bytes=1024,
                chunk_size=1024,
                checksum_sha256=checksum,
            )

    async def test_an_uppercase_digest_is_accepted_and_stored_lowercase(self, service):
        session, _uploads = await service.create_session(
            creator_id=uuid4(),
            filename="clip.mp4",
            mime="video/mp4",
            size_bytes=1024,
            chunk_size=1024,
            checksum_sha256="A" * 64,
        )
        assert session.checksum_sha256 == "a" * 64

    async def test_the_checksum_is_optional(self, service):
        session, _uploads = await service.create_session(
            creator_id=uuid4(),
            filename="clip.mp4",
            mime="video/mp4",
            size_bytes=1024,
            chunk_size=1024,
        )
        assert session.checksum_sha256 is None

    async def test_the_digest_is_never_derived_from_the_client_filename(self, service):
        """The storage identity is the session UUID alone — no client input leaks in."""
        creator = uuid4()
        session, uploads = await service.create_session(
            creator_id=creator,
            filename="../../../etc/passwd",
            mime="video/mp4",
            size_bytes=1024,
            chunk_size=1024,
        )
        assert session.filename == "passwd", "display metadata is sanitized"
        for upload in uploads:
            assert str(session.id) in upload.storage_key
            assert "passwd" not in upload.storage_key
            assert ".." not in upload.storage_key
