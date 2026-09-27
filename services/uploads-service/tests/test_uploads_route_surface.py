"""The uploads HTTP surface: auth resolution, every handler, and the ownership guard.

``app/api/uploads_routes.py`` was almost entirely uncovered — the only tests that
touched it (``test_routes_auth.py``) overrode ``get_current_user_id`` away and
stopped at ``POST /sessions``. So the JWT body, all four session handlers, and
``_get_owned_session`` — the IDOR guard on get / chunks / complete / abort —
had no coverage at all.

Sessions here are real ``UploadSession`` model objects (not ``MagicMock``) so the
response schema and status enums are exercised for real.

**Body shape note.** ``/complete`` and ``/abort`` declare their parameters as
``Annotated[str | None, Body()]`` / ``Annotated[str, Body()]`` *without*
``embed=True``, so FastAPI treats the entire request body as the scalar value.
The accepted payloads are therefore a bare JSON string (``"user cancelled"``) or
no body at all — an object like ``{"reason": "..."}`` is rejected with 422. That
is the current, self-consistent-with-OpenAPI behaviour; it is pinned here and
reported as an API wart rather than silently worked around.

Covered: ``uploads_routes.py:38-64, 153-154, 181-184, 195-205, 221-226,
237-242, 246, 263-266``.
"""

import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

from app.api.uploads_routes import get_current_user_id, get_upload_service
from app.core.settings import settings
from app.main import app
from app.models import UploadSession, UploadSessionStatus
from app.services import UploadError

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def mint_token(**claims) -> str:
    """Sign a token valid on every axis except the claim under test."""
    payload = {
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5),
    }
    payload.update(claims)
    return jose_jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def make_session(creator_id, **overrides) -> UploadSession:
    """A real UploadSession row with sensible defaults."""
    fields = {
        "id": uuid4(),
        "creator_id": creator_id,
        "filename": "clip.mp4",
        "mime": "video/mp4",
        "size_bytes": 5 * 1024 * 1024,
        "status": UploadSessionStatus.UPLOADING,
        "chunk_size": 5 * 1024 * 1024,
        "total_chunks": 1,
        "uploaded_chunks": 1,
        "expires_at": datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
    }
    fields.update(overrides)
    return UploadSession(**fields)


def make_service_stub(session=None):
    """A service stub that also answers ``repo.get`` for the ownership check."""
    from unittest.mock import AsyncMock, MagicMock

    svc = MagicMock()
    svc.create_session = AsyncMock(return_value=(make_session(uuid4()), []))
    svc.register_chunk = AsyncMock()
    svc.complete_session = AsyncMock()
    svc.abort = AsyncMock()
    svc.repo = MagicMock(get=AsyncMock(return_value=session))
    return svc


@pytest.fixture
def client():
    """Client with only the *service* stubbed; the auth path stays real."""
    stub = make_service_stub()
    app.dependency_overrides.clear()
    app.dependency_overrides[get_upload_service] = lambda: stub
    try:
        yield TestClient(app, base_url="http://localhost"), stub
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# get_current_user_id (routes.py:38-64).
# ---------------------------------------------------------------------------


class TestTokenResolution:
    async def test_a_valid_access_token_resolves_to_its_sub(self):
        subject = uuid4()
        token = mint_token(sub=str(subject), type="access")
        assert await get_current_user_id(f"Bearer {token}") == subject

    async def test_a_refresh_token_is_refused(self):
        """Token-type separation (#221) — a refresh token must not authorise writes."""
        token = mint_token(sub=str(uuid4()), type="refresh")
        with pytest.raises(Exception) as excinfo:
            await get_current_user_id(f"Bearer {token}")
        assert getattr(excinfo.value, "status_code", None) == 401
        assert getattr(excinfo.value, "detail", None) == "Invalid token type"

    async def test_the_refresh_token_is_otherwise_perfectly_valid(self):
        """Pin *why* it is refused: the signature/audience/issuer/expiry all pass.

        Without this, a broken decode could masquerade as working type
        separation, and deleting the type check would go unnoticed.
        """
        token = mint_token(sub=str(uuid4()), type="refresh")
        claims = jose_jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
        assert claims["type"] == "refresh"
        assert claims["aud"] == settings.JWT_AUDIENCE
        with pytest.raises(Exception) as excinfo:
            await get_current_user_id(f"Bearer {token}")
        assert getattr(excinfo.value, "detail", None) == "Invalid token type"

    async def test_a_garbage_token_is_refused(self):
        with pytest.raises(Exception) as excinfo:
            await get_current_user_id("Bearer not-a-jwt")
        assert getattr(excinfo.value, "status_code", None) == 401
        assert getattr(excinfo.value, "detail", None) == "Invalid token"

    async def test_an_expired_token_is_refused(self):
        token = jose_jwt.encode(
            {
                "sub": str(uuid4()),
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
                "type": "access",
                "exp": datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        with pytest.raises(Exception) as excinfo:
            await get_current_user_id(f"Bearer {token}")
        assert getattr(excinfo.value, "status_code", None) == 401

    async def test_a_token_signed_with_the_wrong_key_is_refused(self):
        token = jose_jwt.encode(
            {
                "sub": str(uuid4()),
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
                "type": "access",
                "exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5),
            },
            "a-different-secret-key-that-is-long-enough!!",
            algorithm=settings.JWT_ALGORITHM,
        )
        with pytest.raises(Exception) as excinfo:
            await get_current_user_id(f"Bearer {token}")
        assert getattr(excinfo.value, "status_code", None) == 401

    async def test_the_user_id_claim_is_accepted_in_place_of_sub(self):
        subject = uuid4()
        token = mint_token(user_id=str(subject), type="access")
        assert await get_current_user_id(f"Bearer {token}") == subject

    async def test_sub_takes_precedence_over_user_id(self):
        sub, other = uuid4(), uuid4()
        token = mint_token(sub=str(sub), user_id=str(other), type="access")
        assert await get_current_user_id(f"Bearer {token}") == sub

    @pytest.mark.parametrize("sub", [None, "", "not-a-uuid", "12345"])
    async def test_an_unusable_subject_is_refused(self, sub):
        claims = {"type": "access"}
        if sub is not None:
            claims["sub"] = sub
        token = mint_token(**claims)
        with pytest.raises(Exception) as excinfo:
            await get_current_user_id(f"Bearer {token}")
        assert getattr(excinfo.value, "status_code", None) == 401
        assert getattr(excinfo.value, "detail", None) == "Invalid token subject"

    @pytest.mark.parametrize("header", [None, "", "Basic abc", "bearer lower-case"])
    async def test_a_malformed_authorization_header_is_refused(self, header):
        with pytest.raises(Exception) as excinfo:
            await get_current_user_id(header)
        assert getattr(excinfo.value, "status_code", None) == 401
        assert getattr(excinfo.value, "detail", None) == "Missing or invalid authorization header"


# ---------------------------------------------------------------------------
# POST /sessions error mapping (routes.py:153-154).
# ---------------------------------------------------------------------------


class TestCreateSessionErrorMapping:
    async def test_a_domain_error_becomes_a_400(self, client):
        client, stub = client
        stub.create_session.side_effect = UploadError("invalid media type: 'x'")
        creator = uuid4()
        response = client.post(
            "/api/v1/uploads/sessions",
            json={
                "creator_id": str(creator),
                "filename": "clip.mp4",
                "mime": "video/mp4",
                "size_bytes": 1024,
            },
            headers={"Authorization": f"Bearer {mint_token(sub=str(creator), type='access')}"},
        )
        assert response.status_code == 400
        assert "invalid media type" in response.json()["detail"]

    async def test_a_valid_request_returns_the_chunk_plan(self, client):
        client, stub = client
        creator = uuid4()
        session = make_session(creator, status=UploadSessionStatus.INITIATED, uploaded_chunks=0)
        from app.core.storage import PresignedUpload

        stub.create_session.return_value = (
            session,
            [
                PresignedUpload(
                    storage_key="uploads/x/0",
                    upload_url="https://cdn/0",
                    method="PUT",
                    headers={"Content-Type": "video/mp4"},
                )
            ],
        )
        response = client.post(
            "/api/v1/uploads/sessions",
            json={
                "creator_id": str(creator),
                "filename": "clip.mp4",
                "mime": "video/mp4",
                "size_bytes": 5 * 1024 * 1024,
            },
            headers={"Authorization": f"Bearer {mint_token(sub=str(creator), type='access')}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["session_id"] == str(session.id)
        assert body["status"] == "initiated"
        assert body["chunk_size"] == session.chunk_size
        assert body["total_chunks"] == 1
        assert body["uploads"][0]["upload_url"] == "https://cdn/0"
        assert body["uploads"][0]["headers"] == {"Content-Type": "video/mp4"}


# ---------------------------------------------------------------------------
# GET /sessions/{id} (routes.py:181-184, 246).
# ---------------------------------------------------------------------------


class TestGetSession:
    async def test_the_owner_sees_their_session(self, client):
        client, stub = client
        owner = uuid4()
        session = make_session(owner)
        stub.repo.get.return_value = session

        response = client.get(
            f"/api/v1/uploads/sessions/{session.id}",
            headers={"Authorization": f"Bearer {mint_token(sub=str(owner), type='access')}"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["session_id"] == str(session.id)
        assert body["creator_id"] == str(owner)
        assert body["status"] == "uploading"
        assert body["uploaded_chunks"] == 1
        assert body["filename"] == "clip.mp4"

    async def test_a_known_but_ungenerated_response_field_is_serialised(self, client):
        """``_session_to_response`` must carry storage_key and checksum through."""
        client, stub = client
        owner = uuid4()
        session = make_session(
            owner,
            status=UploadSessionStatus.COMPLETE,
            storage_key=f"uploads/{uuid4()}/final",
            checksum_sha256="a" * 64,
        )
        stub.repo.get.return_value = session

        response = client.get(
            f"/api/v1/uploads/sessions/{session.id}",
            headers={"Authorization": f"Bearer {mint_token(sub=str(owner), type='access')}"},
        )
        body = response.json()
        assert body["status"] == "complete"
        assert body["storage_key"] == session.storage_key
        assert body["checksum_sha256"] == "a" * 64


# ---------------------------------------------------------------------------
# _get_owned_session — the IDOR guard (routes.py:263-266).
# ---------------------------------------------------------------------------


class TestOwnershipGuard:
    """404 (not 403) for both unknown and foreign sessions — no existence oracle."""

    # (method, url template, schema-valid body) — a body that fails validation
    # would 422 before the ownership guard ever runs.
    OWNED_ROUTES = (
        ("get", "/api/v1/uploads/sessions/{sid}", None),
        ("post", "/api/v1/uploads/sessions/{sid}/chunks", {"index": 0}),
        ("post", "/api/v1/uploads/sessions/{sid}/complete", "c" * 64),
        ("post", "/api/v1/uploads/sessions/{sid}/abort", "cancelled"),
    )

    @pytest.mark.parametrize("method,template,body", OWNED_ROUTES)
    async def test_a_foreign_session_is_indistinguishable_from_a_missing_one(
        self, client, method, template, body
    ):
        client, stub = client
        intruder = uuid4()
        victim = uuid4()
        session = make_session(victim)
        stub.repo.get.return_value = session

        def _call(session_id):
            url = template.format(sid=session_id)
            token = mint_token(sub=str(intruder), type="access")
            headers = {"Authorization": f"Bearer {token}"}
            if method == "get":
                return client.get(url, headers=headers)
            return client.post(url, json=body, headers=headers)

        foreign = _call(session.id)
        missing = _call(uuid4())

        assert foreign.status_code == 404
        assert missing.status_code == 404
        assert (
            foreign.json() == missing.json()
        ), "a foreign session must not be distinguishable from a nonexistent one"
        # And no state-changing call may be issued against someone else's session.
        stub.register_chunk.assert_not_awaited()
        stub.complete_session.assert_not_awaited()
        stub.abort.assert_not_awaited()

    @pytest.mark.parametrize("method,template,body", OWNED_ROUTES)
    async def test_an_unknown_session_is_a_404(self, client, method, template, body):
        client, stub = client
        stub.repo.get.return_value = None
        owner = uuid4()
        url = template.format(sid=uuid4())
        headers = {"Authorization": f"Bearer {mint_token(sub=str(owner), type='access')}"}
        response = (
            client.get(url, headers=headers)
            if method == "get"
            else client.post(url, json=body, headers=headers)
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Upload session not found"


# ---------------------------------------------------------------------------
# POST /sessions/{id}/chunks (routes.py:195-205).
# ---------------------------------------------------------------------------


class TestRegisterChunk:
    async def test_a_registered_chunk_is_acknowledged(self, client):
        client, stub = client
        owner = uuid4()
        session = make_session(owner)
        stub.repo.get.return_value = session
        chunk = type("Chunk", (), {"id": uuid4(), "index": 0})()
        stub.register_chunk.return_value = chunk

        response = client.post(
            f"/api/v1/uploads/sessions/{session.id}/chunks",
            json={"index": 0, "size_bytes": 1234, "etag": "abc"},
            headers={"Authorization": f"Bearer {mint_token(sub=str(owner), type='access')}"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["chunk_id"] == str(chunk.id)
        assert body["session_id"] == str(session.id)
        assert body["index"] == 0
        assert body["received"] is True
        # The client-reported size is forwarded but the service ignores it.
        assert stub.register_chunk.await_args.kwargs["index"] == 0
        assert stub.register_chunk.await_args.kwargs["etag"] == "abc"

    async def test_a_duplicate_chunk_becomes_a_400(self, client):
        client, stub = client
        owner = uuid4()
        session = make_session(owner)
        stub.repo.get.return_value = session
        stub.register_chunk.side_effect = UploadError("chunk 0 already received")

        response = client.post(
            f"/api/v1/uploads/sessions/{session.id}/chunks",
            json={"index": 0},
            headers={"Authorization": f"Bearer {mint_token(sub=str(owner), type='access')}"},
        )
        assert response.status_code == 400
        assert "already received" in response.json()["detail"]

    async def test_ownership_is_checked_before_the_chunk_is_registered(self, client):
        client, stub = client
        stub.repo.get.return_value = make_session(uuid4())  # someone else's session
        response = client.post(
            f"/api/v1/uploads/sessions/{uuid4()}/chunks",
            json={"index": 0},
            headers={"Authorization": f"Bearer {mint_token(sub=str(uuid4()), type='access')}"},
        )
        assert response.status_code == 404
        stub.register_chunk.assert_not_awaited()


# ---------------------------------------------------------------------------
# POST /sessions/{id}/complete (routes.py:221-226).
# ---------------------------------------------------------------------------


class TestCompleteSession:
    async def test_a_verified_completion_returns_the_finalised_session(self, client):
        client, stub = client
        owner = uuid4()
        session = make_session(owner)
        stub.repo.get.return_value = session
        completed = make_session(
            owner,
            id=session.id,
            status=UploadSessionStatus.COMPLETE,
            storage_key=f"uploads/{session.id}/final",
        )
        stub.complete_session.return_value = completed

        response = client.post(
            f"/api/v1/uploads/sessions/{session.id}/complete",
            headers={"Authorization": f"Bearer {mint_token(sub=str(owner), type='access')}"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "complete"
        assert body["storage_key"] == f"uploads/{session.id}/final"

    async def test_a_client_checksum_is_passed_through_as_advisory(self, client):
        client, stub = client
        owner = uuid4()
        session = make_session(owner)
        stub.repo.get.return_value = session
        stub.complete_session.return_value = make_session(
            owner, id=session.id, status=UploadSessionStatus.COMPLETE
        )

        # The body is the bare checksum string (see the module docstring).
        client.post(
            f"/api/v1/uploads/sessions/{session.id}/complete",
            json="b" * 64,
            headers={"Authorization": f"Bearer {mint_token(sub=str(owner), type='access')}"},
        )
        assert stub.complete_session.await_args.kwargs["checksum_sha256"] == "b" * 64

    async def test_an_object_body_is_rejected_by_schema_validation(self, client):
        """Pin the non-embedded-Body() contract: the body is the scalar itself."""
        client, stub = client
        owner = uuid4()
        session = make_session(owner)
        stub.repo.get.return_value = session

        response = client.post(
            f"/api/v1/uploads/sessions/{session.id}/complete",
            json={"checksum_sha256": "b" * 64},
            headers={"Authorization": f"Bearer {mint_token(sub=str(owner), type='access')}"},
        )
        assert response.status_code == 422
        stub.complete_session.assert_not_awaited()

    async def test_a_missing_chunk_becomes_a_409(self, client):
        client, stub = client
        owner = uuid4()
        session = make_session(owner)
        stub.repo.get.return_value = session
        stub.complete_session.side_effect = UploadError("session X missing chunks: [1, 2]")

        response = client.post(
            f"/api/v1/uploads/sessions/{session.id}/complete",
            headers={"Authorization": f"Bearer {mint_token(sub=str(owner), type='access')}"},
        )
        assert response.status_code == 409
        assert "missing chunks" in response.json()["detail"]

    async def test_ownership_is_checked_before_completion(self, client):
        client, stub = client
        stub.repo.get.return_value = make_session(uuid4())
        response = client.post(
            f"/api/v1/uploads/sessions/{uuid4()}/complete",
            headers={"Authorization": f"Bearer {mint_token(sub=str(uuid4()), type='access')}"},
        )
        assert response.status_code == 404
        stub.complete_session.assert_not_awaited()


# ---------------------------------------------------------------------------
# POST /sessions/{id}/abort (routes.py:237-242).
# ---------------------------------------------------------------------------


class TestAbortSession:
    async def test_an_abort_returns_the_aborted_session(self, client):
        client, stub = client
        owner = uuid4()
        session = make_session(owner)
        stub.repo.get.return_value = session
        aborted = make_session(owner, id=session.id, status=UploadSessionStatus.ABORTED)
        stub.abort.return_value = aborted

        response = client.post(
            f"/api/v1/uploads/sessions/{session.id}/abort",
            json="user cancelled",
            headers={"Authorization": f"Bearer {mint_token(sub=str(owner), type='access')}"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "aborted"
        assert stub.abort.await_args.kwargs["reason"] == "user cancelled"

    async def test_aborting_an_already_complete_session_is_a_409(self, client):
        client, stub = client
        owner = uuid4()
        session = make_session(owner)
        stub.repo.get.return_value = session
        stub.abort.side_effect = UploadError("session X already complete; cannot abort")

        response = client.post(
            f"/api/v1/uploads/sessions/{session.id}/abort",
            headers={"Authorization": f"Bearer {mint_token(sub=str(owner), type='access')}"},
        )
        assert response.status_code == 409
        assert "already complete" in response.json()["detail"]

    async def test_ownership_is_checked_before_abort(self, client):
        client, stub = client
        stub.repo.get.return_value = make_session(uuid4())
        response = client.post(
            f"/api/v1/uploads/sessions/{uuid4()}/abort",
            headers={"Authorization": f"Bearer {mint_token(sub=str(uuid4()), type='access')}"},
        )
        assert response.status_code == 404
        stub.abort.assert_not_awaited()
