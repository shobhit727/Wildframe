"""Elasticsearch index lifecycle, alias cutover and cursor integrity.

The Elasticsearch client is always injected (never a real cluster): the dev
stack pins ES 8.10 while the installed client is 9.x, and a real handshake
fails at the transport layer with HTTP 406 before any code under test runs.

The HTTP surface that wraps the index lifecycle is exercised here too, because
the ES-backed routes are part of the same contract: the shared client
lifecycle, the reindex error mapping and the integrity-protected search
cursor.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import wildframe_auth
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.search_routes as search_routes
import app.core.security as security_module
import app.services as services_module
from app.core.security import decode_cursor, encode_cursor
from app.core.settings import settings
from app.main import app
from app.repositories import SearchIndexRepository, SearchQueryRepository
from tests._test_jwks import JWKS, PRIVATE_PEM
from wildframe_auth.verifier import clear_jwks_cache


class _Endpoint:
    """The auth service's JWKS endpoint, as the routes see it."""

    def __init__(self) -> None:
        self.jwks = JWKS
        self.raises: Exception | None = None

    async def fetch(self, url: str):
        if self.raises is not None:
            raise self.raises
        return self.jwks


@pytest.fixture(autouse=True)
def _stub_jwks_endpoint(monkeypatch):
    """Serve the test JWKS and clear the SDK cache around every test.

    Autouse, because ``verify_token`` is on the path of most of the identity and
    route tests in this module and a real outbound fetch would otherwise be
    attempted.
    """
    ep = _Endpoint()
    monkeypatch.setattr(wildframe_auth.verifier, "fetch_jwks", ep.fetch)
    clear_jwks_cache()
    yield ep
    clear_jwks_cache()


from app.services import (
    CONTENT_INDEX,
    CONTENT_INDEX_MAPPING,
    CatalogFetchError,
    IndexingError,
    ReindexResult,
    SearchService,
)

from .test_service import no_alias_error


@pytest.fixture
def es_mock():
    """Mock Elasticsearch client with properly nested indices namespace."""
    mock = MagicMock()
    mock.indices = MagicMock()
    mock.indices.exists = AsyncMock()
    mock.indices.create = AsyncMock()
    mock.indices.put_alias = AsyncMock()
    mock.indices.get_alias = AsyncMock()
    mock.indices.get = AsyncMock()
    mock.cluster = MagicMock()
    mock.indices.update_aliases = AsyncMock()
    mock.indices.delete = AsyncMock()
    mock.indices.refresh = AsyncMock()
    mock.cluster.put_settings = AsyncMock()
    mock.bulk = AsyncMock(return_value={"errors": False, "items": []})
    return mock


@pytest.fixture
def service(es_mock):
    return SearchService(es_mock, MagicMock(), MagicMock(upsert=AsyncMock(), delete=AsyncMock()))


def published_item(title: str = "X") -> dict:
    return {"id": str(uuid4()), "title": title, "description": "d", "content_type": "movie"}


# ----------------------------------------------------------------------
# ensure_index — bootstrap, adoption and refusal
# ----------------------------------------------------------------------


class TestEnsureIndex:
    @pytest.mark.asyncio
    async def test_returns_existing_alias_target_without_touching_anything(self, es_mock, service):
        es_mock.indices.get_alias = AsyncMock(return_value={"content_v4": {}})

        assert await service.ensure_index() == "content_v4"

        es_mock.indices.create.assert_not_awaited()
        es_mock.indices.put_alias.assert_not_awaited()
        es_mock.cluster.put_settings.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_picks_the_highest_sorting_alias_key(self, es_mock, service):
        es_mock.indices.get_alias = AsyncMock(return_value={"content_v10": {}, "content_v2": {}})

        assert await service.ensure_index() == "content_v2"

    @pytest.mark.asyncio
    async def test_empty_alias_response_is_treated_as_absent(self, es_mock, service):
        es_mock.indices.get_alias = AsyncMock(return_value={})
        es_mock.indices.exists = AsyncMock(return_value=False)

        assert await service.ensure_index() == "content_v1"

    @pytest.mark.asyncio
    async def test_legacy_concrete_index_blocks_bootstrap(self, es_mock, service):
        """A pre-alias deployment must be migrated offline, never silently adopted."""
        es_mock.indices.get_alias = AsyncMock(side_effect=no_alias_error())
        es_mock.indices.exists = AsyncMock(return_value=True)

        with pytest.raises(IndexingError, match="requires an offline migration"):
            await service.ensure_index()

        es_mock.indices.create.assert_not_awaited()
        es_mock.indices.put_alias.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_version_one_with_canonical_mapping(self, es_mock, service):
        es_mock.indices.get_alias = AsyncMock(side_effect=no_alias_error())
        es_mock.indices.exists = AsyncMock(return_value=False)

        name = await service.ensure_index()

        assert name == "content_v1"
        es_mock.indices.create.assert_awaited_once_with(
            index="content_v1", body=CONTENT_INDEX_MAPPING
        )
        es_mock.indices.put_alias.assert_awaited_once_with(index="content_v1", name=CONTENT_INDEX)
        es_mock.cluster.put_settings.assert_awaited_once_with(
            body={"persistent": {"indices.id_field_data.enabled": True}}
        )

    @pytest.mark.asyncio
    async def test_id_field_data_failure_is_only_a_warning(self, es_mock, service):
        """A cluster that refuses put_settings must not block startup."""
        es_mock.indices.get_alias = AsyncMock(side_effect=no_alias_error())
        es_mock.indices.exists = AsyncMock(return_value=False)
        es_mock.cluster.put_settings = AsyncMock(side_effect=RuntimeError("read-only cluster"))

        name = await service.ensure_index()

        assert name == "content_v1"
        es_mock.indices.put_alias.assert_awaited_once()


# ----------------------------------------------------------------------
# _alias_target / _versioned_indices / _next_version
# ----------------------------------------------------------------------


class TestAliasAndVersionHelpers:
    @pytest.mark.asyncio
    async def test_alias_target_swallows_not_found(self, es_mock, service):
        es_mock.indices.get_alias = AsyncMock(side_effect=no_alias_error())

        assert await service._alias_target() is None

    @pytest.mark.asyncio
    async def test_alias_target_propagates_transport_failure(self, es_mock, service):
        es_mock.indices.get_alias = AsyncMock(side_effect=RuntimeError("es down"))

        with pytest.raises(RuntimeError, match="es down"):
            await service._alias_target()

    @pytest.mark.asyncio
    async def test_versioned_indices_are_sorted(self, es_mock, service):
        es_mock.indices.get = AsyncMock(
            return_value={"content_v10": {}, "content_v2": {}, "content_v1": {}}
        )

        assert await service._versioned_indices() == [
            "content_v1",
            "content_v10",
            "content_v2",
        ]
        es_mock.indices.get.assert_awaited_once_with(index="content_v*", ignore_unavailable=True)

    @pytest.mark.asyncio
    async def test_versioned_indices_swallows_failures(self, es_mock, service):
        es_mock.indices.get = AsyncMock(side_effect=RuntimeError("es down"))

        assert await service._versioned_indices() == []

    @pytest.mark.asyncio
    async def test_next_version_starts_at_one_without_indices(self, es_mock, service):
        es_mock.indices.get = AsyncMock(side_effect=RuntimeError("es down"))

        assert await service._next_version(None) == 1

    @pytest.mark.asyncio
    async def test_next_version_ignores_a_non_versioned_current(self, es_mock, service):
        es_mock.indices.get = AsyncMock(return_value={})

        assert await service._next_version("content") == 1

    @pytest.mark.asyncio
    async def test_next_version_uses_the_current_alias_target(self, es_mock, service):
        es_mock.indices.get = AsyncMock(return_value={})

        assert await service._next_version("content_v7") == 8

    @pytest.mark.asyncio
    async def test_next_version_takes_the_max_over_sibling_indices(self, es_mock, service):
        es_mock.indices.get = AsyncMock(
            return_value={"content_v3": {}, "content_v9": {}, "unrelated": {}}
        )

        assert await service._next_version("content_v2") == 10

    @pytest.mark.asyncio
    async def test_next_version_falls_back_to_siblings_when_current_is_none(self, es_mock, service):
        es_mock.indices.get = AsyncMock(return_value={"content_v4": {}})

        assert await service._next_version(None) == 5


# ----------------------------------------------------------------------
# reindex_catalog — atomic alias cutover
# ----------------------------------------------------------------------


def _catalog(items: list[dict]):
    catalog = MagicMock()
    catalog.fetch_published = AsyncMock(return_value=items)
    catalog.aclose = AsyncMock()
    return catalog


# ----------------------------------------------------------------------
# Token identity (app/core/security.py) — the guard on index-mutating routes
# ----------------------------------------------------------------------


def _request(headers: dict[str, str]):
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/search/reindex",
            "headers": raw,
            "query_string": b"",
        }
    )


def _token(**claims) -> str:
    """Mint a real RS256 access token signed by the test JWKS key.

    Signed with RS256 over the in-memory RSA key from ``tests/_test_jwks.py``,
    because the service no longer accepts a shared-secret HS256 token at all.
    """
    import time

    from jose import jwt

    base = {
        "sub": str(uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "av": 0,
        "iat": int(time.time()),
        "exp": int(time.time()) + 900,
    }
    base.update(claims)
    # A ``None`` claim is dropped rather than encoded as JSON null, because
    # python-jose rejects a non-string ``sub`` at decode time.
    return jwt.encode(
        {k: v for k, v in base.items() if v is not None},
        PRIVATE_PEM,
        algorithm="RS256",
        headers={"kid": "k1"},
    )


class TestTokenIdentity:
    async def test_missing_header_is_anonymous(self):
        from app.core.security import verify_token

        assert await verify_token(_request({})) is None

    @pytest.mark.parametrize("header", ["Basic abc", "Bearer", "token abc"])
    async def test_non_bearer_schemes_are_anonymous(self, header):
        from app.core.security import verify_token

        assert await verify_token(_request({"Authorization": header})) is None

    async def test_refresh_tokens_are_never_accepted_as_access(self):
        """Token-type separation (#221): a refresh token is anonymous.

        Now enforced inside the shared verifier via ``expected_type="access"``
        rather than by a hand-written ``type`` check here.
        """
        from app.core.security import verify_token

        token = _token(type="refresh")

        assert await verify_token(_request({"Authorization": f"Bearer {token}"})) is None

    @pytest.mark.parametrize("claims", [{"sub": None, "user_id": None}])
    async def test_token_without_a_subject_is_anonymous(self, claims):
        """``sub`` is a required claim, so this is refused during verification."""
        from app.core.security import verify_token

        token = _token(**claims)

        assert await verify_token(_request({"Authorization": f"Bearer {token}"})) is None

    async def test_user_id_claim_is_no_longer_accepted_as_a_fallback(self):
        """The shared verifier requires ``sub``, so the old ``user_id`` fallback is unreachable.

        Under the deleted HS256 path a token carrying only ``user_id`` was
        accepted and ``user_id or sub`` supplied the identity. The SDK verifier
        requires ``sub`` as a claim, so such a token is now anonymous outright
        rather than silently reinterpreting a different claim as the identity.
        """
        from app.core.security import verify_token

        user_id = uuid4()
        token = _token(sub=None, user_id=str(user_id))

        assert await verify_token(_request({"Authorization": f"Bearer {token}"})) is None

    async def test_the_user_id_fallback_still_resolves_when_the_verifier_allows_it(
        self, monkeypatch
    ):
        """The ``user_id or sub`` branch is retained, so it stays covered.

        Unreachable through the real verifier, so ``verify_token_with_jwks`` is
        stubbed. Deleting the branch instead would be a second, unrelated change;
        keeping it covered means a future refactor cannot silently drop the
        fallback.
        """
        import app.core.security as security

        user_id = uuid4()

        async def _stub(token: str, **kwargs) -> dict:
            return {"user_id": str(user_id)}

        monkeypatch.setattr(security, "verify_token_with_jwks", _stub)

        identity = await security.verify_token(_request({"Authorization": "Bearer anything"}))

        assert identity is not None
        assert identity.user_id == user_id

    async def test_valid_token_yields_role_and_arv(self):
        from app.core.security import verify_token

        token = _token(role="admin", arv=3)

        identity = await verify_token(_request({"Authorization": f"Bearer {token}"}))

        assert identity is not None
        assert identity.role == "admin"
        assert identity.arv == 3
        assert identity.is_admin is True
        # role_current compares against the live ADMIN_ROLE_VERSION, not a
        # hard-coded value.
        assert identity.role_current is (3 == settings.ADMIN_ROLE_VERSION)

    @pytest.mark.parametrize(
        "header",
        [
            "Bearer not-a-jwt",
            "Bearer ",
        ],
    )
    async def test_malformed_tokens_are_anonymous(self, header):
        from app.core.security import verify_token

        assert await verify_token(_request({"Authorization": header})) is None

    async def test_expired_token_is_anonymous(self):
        """Expired by 1h, not 10s: the shared verifier allows ~60s of clock skew."""
        import time

        from jose import jwt

        from app.core.security import verify_token

        token = jwt.encode(
            {
                "sub": str(uuid4()),
                "type": "access",
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
                "av": 0,
                "iat": int(time.time()) - 3600,
                "exp": int(time.time()) - 3600,
            },
            PRIVATE_PEM,
            algorithm="RS256",
            headers={"kid": "k1"},
        )

        assert await verify_token(_request({"Authorization": f"Bearer {token}"})) is None

    async def test_token_signed_by_another_key_is_anonymous(self):
        """An RS256 token from an RSA key that is not in the JWKS is anonymous."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from jose import jwt

        from app.core.security import verify_token

        intruder = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = intruder.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        token = jwt.encode(
            {
                "sub": str(uuid4()),
                "type": "access",
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
                "av": 0,
                "iat": 9999999999,
                "exp": 9999999999,
            },
            pem,
            algorithm="RS256",
            headers={"kid": "k1"},
        )

        assert await verify_token(_request({"Authorization": f"Bearer {token}"})) is None

    async def test_token_without_exp_is_rejected(self):
        """``exp`` is a required claim, replacing the old ``require_exp`` option."""
        import time

        from jose import jwt

        from app.core.security import verify_token

        token = jwt.encode(
            {
                "sub": str(uuid4()),
                "type": "access",
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
                "av": 0,
                "iat": int(time.time()),
            },
            PRIVATE_PEM,
            algorithm="RS256",
            headers={"kid": "k1"},
        )

        assert await verify_token(_request({"Authorization": f"Bearer {token}"})) is None

    @pytest.mark.asyncio
    async def test_optional_identity_mirrors_verify_token(self):
        from app.core.security import get_optional_identity

        assert await get_optional_identity(_request({})) is None

    @pytest.mark.asyncio
    async def test_required_identity_401s_for_anonymous_callers(self):
        from fastapi import HTTPException

        from app.core.security import get_required_identity

        with pytest.raises(HTTPException) as excinfo:
            await get_required_identity(_request({}))

        assert excinfo.value.status_code == 401
        assert excinfo.value.headers == {"WWW-Authenticate": "Bearer"}
        assert excinfo.value.detail["message"] == "Authentication required"

    @pytest.mark.asyncio
    async def test_admin_identity_returns_the_identity(self):
        from app.core.security import get_admin_identity

        token = _token(role="admin")

        identity = await get_admin_identity(_request({"Authorization": f"Bearer {token}"}))

        assert identity.is_admin is True

    @pytest.mark.asyncio
    async def test_admin_identity_403s_a_non_admin(self):
        from fastapi import HTTPException

        from app.core.security import get_admin_identity

        token = _token(role="user")

        with pytest.raises(HTTPException) as excinfo:
            await get_admin_identity(_request({"Authorization": f"Bearer {token}"}))

        assert excinfo.value.status_code == 403
        assert excinfo.value.detail["message"] == "Administrator privileges required"


class TestReindexAliasCutover:
    @pytest.mark.asyncio
    async def test_alias_switch_issues_exact_add_and_remove_actions(self, es_mock, service):
        es_mock.indices.get_alias = AsyncMock(return_value={"content_v2": {}})
        es_mock.indices.get = AsyncMock(return_value={"content_v1": {}, "content_v2": {}})

        result = await service.reindex_catalog(_catalog([published_item()]))

        assert result == ReindexResult(count=1, index_name="content_v3", switched=True)
        es_mock.indices.update_aliases.assert_awaited_once_with(
            body={
                "actions": [
                    {"add": {"index": "content_v3", "alias": CONTENT_INDEX}},
                    {"remove": {"index": "content_v2", "alias": CONTENT_INDEX}},
                ]
            }
        )
        es_mock.indices.create.assert_awaited_once_with(
            index="content_v3", body=CONTENT_INDEX_MAPPING
        )
        es_mock.indices.refresh.assert_awaited_once_with(index="content_v3")
        es_mock.indices.delete.assert_awaited_once_with(index="content_v2")

    @pytest.mark.asyncio
    async def test_alias_switch_adds_without_remove_when_target_is_not_versioned(
        self, es_mock, service
    ):
        """A legacy (non-versioned) target is never removed by the alias swap."""
        es_mock.indices.get_alias = AsyncMock(return_value={"legacy-content": {}})
        es_mock.indices.get = AsyncMock(return_value={})

        await service.reindex_catalog(_catalog([published_item()]))

        actions = es_mock.indices.update_aliases.await_args.kwargs["body"]["actions"]
        assert actions == [{"add": {"index": "content_v1", "alias": CONTENT_INDEX}}]
        es_mock.indices.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_alias_switch_never_deletes_the_new_index(self, es_mock, service):
        """An ambiguous alias response must not trigger a destructive cleanup."""
        es_mock.indices.get_alias = AsyncMock(return_value={"content_v1": {}})
        es_mock.indices.get = AsyncMock(return_value={"content_v1": {}})
        es_mock.indices.update_aliases = AsyncMock(side_effect=RuntimeError("timeout"))

        with pytest.raises(RuntimeError, match="timeout"):
            await service.reindex_catalog(_catalog([published_item()]))

        es_mock.indices.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_old_index_delete_failure_does_not_fail_the_reindex(self, es_mock, service):
        es_mock.indices.get_alias = AsyncMock(return_value={"content_v1": {}})
        es_mock.indices.get = AsyncMock(return_value={"content_v1": {}})

        async def delete(index, **kwargs):
            if index == "content_v1":
                raise RuntimeError("index in use")

        es_mock.indices.delete = AsyncMock(side_effect=delete)

        result = await service.reindex_catalog(_catalog([published_item()]))

        assert result.switched is True
        assert result.index_name == "content_v2"
        es_mock.indices.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bulk_failure_cleans_up_the_half_built_index(self, es_mock, service):
        es_mock.indices.get_alias = AsyncMock(return_value={"content_v1": {}})
        es_mock.indices.get = AsyncMock(return_value={"content_v1": {}})
        es_mock.bulk = AsyncMock(
            side_effect=[
                {
                    "errors": True,
                    "items": [{"index": {"_id": "z", "error": {"type": "t"}}}],
                },
                {"errors": True, "items": [{"index": {"_id": "z", "error": {"type": "t"}}}]},
            ]
        )

        with pytest.raises(IndexingError):
            await service.reindex_catalog(_catalog([{"id": "z", "title": "Z"}]))

        es_mock.indices.delete.assert_awaited_once_with(index="content_v2", ignore_unavailable=True)
        es_mock.indices.update_aliases.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_catalog_reports_the_surviving_index(self, es_mock, service):
        es_mock.indices.get_alias = AsyncMock(return_value={"content_v5": {}})
        es_mock.indices.get = AsyncMock(return_value={"content_v5": {}})

        result = await service.reindex_catalog(_catalog([]))

        assert result == ReindexResult(count=0, index_name="content_v5", switched=False)
        es_mock.indices.delete.assert_awaited_once_with(index="content_v6", ignore_unavailable=True)
        es_mock.indices.update_aliases.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_owns_and_closes_a_self_built_catalog_client(self, es_mock, service, monkeypatch):
        """No injected catalog -> the service builds one and must close it."""
        es_mock.indices.get_alias = AsyncMock(return_value={"content_v1": {}})
        es_mock.indices.get = AsyncMock(return_value={"content_v1": {}})

        created = _catalog([published_item()])
        monkeypatch.setattr(
            services_module, "ContentCatalogClient", MagicMock(return_value=created)
        )

        result = await service.reindex_catalog()

        assert result.switched is True
        services_module.ContentCatalogClient.assert_called_once()
        created.fetch_published.assert_awaited_once()
        created.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_injected_catalog_is_not_closed_by_the_caller(self, es_mock, service):
        """The caller owns an injected client; the service must not close it."""
        es_mock.indices.get_alias = AsyncMock(return_value={"content_v1": {}})
        es_mock.indices.get = AsyncMock(return_value={"content_v1": {}})
        catalog = _catalog([published_item()])

        await service.reindex_catalog(catalog)

        catalog.aclose.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_self_built_catalog_is_closed_even_when_reindex_fails(
        self, es_mock, service, monkeypatch
    ):
        es_mock.indices.get_alias = AsyncMock(return_value={"content_v1": {}})
        es_mock.indices.get = AsyncMock(return_value={"content_v1": {}})
        created = _catalog([published_item()])
        created.fetch_published = AsyncMock(side_effect=CatalogFetchError("down"))
        monkeypatch.setattr(
            services_module, "ContentCatalogClient", MagicMock(return_value=created)
        )

        with pytest.raises(CatalogFetchError):
            await service.reindex_catalog()

        created.aclose.assert_awaited_once()


# ----------------------------------------------------------------------
# Shared ES client lifecycle (app/api/search_routes.py)
# ----------------------------------------------------------------------


@pytest.fixture
def restore_es_client():
    original = search_routes._es_client
    search_routes._es_client = None
    yield
    search_routes._es_client = original


class TestSharedEsClient:
    def test_client_is_created_lazily_and_cached(self, restore_es_client):
        created = MagicMock()
        with patch.object(search_routes, "AsyncElasticsearch", return_value=created) as factory:
            first = search_routes.es_client()
            second = search_routes.es_client()

        assert first is created
        assert second is created
        factory.assert_called_once()

    def test_pre_existing_client_is_reused(self, restore_es_client):
        sentinel = MagicMock()
        search_routes._es_client = sentinel

        assert search_routes.es_client() is sentinel

    @pytest.mark.asyncio
    async def test_close_resets_the_cached_client(self, restore_es_client):
        client = MagicMock()
        client.close = AsyncMock()
        search_routes._es_client = client

        await search_routes.close_es_client()

        client.close.assert_awaited_once()
        assert search_routes._es_client is None

    @pytest.mark.asyncio
    async def test_close_is_a_noop_without_a_client(self, restore_es_client):
        assert search_routes._es_client is None
        await search_routes.close_es_client()  # must not raise

    @pytest.mark.asyncio
    async def test_get_search_service_wires_repos_and_the_shared_client(self, restore_es_client):
        client = MagicMock()
        search_routes._es_client = client
        db = MagicMock(spec=AsyncSession)

        service = await search_routes.get_search_service(db)

        assert isinstance(service, SearchService)
        assert service.es is client
        assert isinstance(service.query_repo, SearchQueryRepository)
        assert isinstance(service.index_repo, SearchIndexRepository)
        assert service.query_repo.session is db
        assert service.index_repo.session is db


# ----------------------------------------------------------------------
# Integrity-protected pagination cursor (app/core/security.py)
# ----------------------------------------------------------------------


def _sign(payload: dict, key: bytes | str | None = None) -> str:
    """Build a correctly-signed cursor for an arbitrary payload.

    ``key`` defaults to the real cursor key so callers cannot accidentally
    produce a cursor the service would not itself have minted. Pass an explicit
    key to forge one under a different secret.
    """
    if key is None:
        key = settings.SEARCH_CURSOR_SECRET
    if isinstance(key, str):
        key = key.encode()
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(key, raw, hashlib.sha256).digest()
    return (
        base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        + "."
        + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    )


class TestCursorIntegrity:
    def test_round_trip_returns_the_sort_values(self):
        cursor = encode_cursor("action", "movie", 20, [9.5, "doc-1"])

        assert decode_cursor(cursor, "action", "movie", 20) == [9.5, "doc-1"]

    def test_round_trip_without_a_content_type(self):
        cursor = encode_cursor("action", None, 5, [1.0, "a"])

        assert decode_cursor(cursor, "action", None, 5) == [1.0, "a"]

    def test_tampered_signature_is_rejected(self):
        cursor = encode_cursor("action", None, 20, [9.5, "doc-1"])
        raw, sig = cursor.rsplit(".", 1)
        flipped = ("A" if sig[0] != "A" else "B") + sig[1:]

        with pytest.raises(ValueError, match="invalid cursor"):
            decode_cursor(f"{raw}.{flipped}", "action", None, 20)

    def test_tampered_payload_is_rejected(self):
        raw, sig = encode_cursor("action", None, 20, [9.5, "doc-1"]).rsplit(".", 1)
        forged = (
            base64.urlsafe_b64encode(
                json.dumps({"scope": "x", "sort": [0.0, "evil"]}, separators=(",", ":")).encode()
            )
            .rstrip(b"=")
            .decode()
        )

        with pytest.raises(ValueError, match="invalid cursor"):
            decode_cursor(f"{forged}.{sig}", "action", None, 20)

    @pytest.mark.parametrize(
        "scope",
        [("different", None, 20), ("action", "show", 20), ("action", None, 21)],
    )
    def test_cursor_cannot_be_replayed_against_another_scope(self, scope):
        cursor = encode_cursor("action", None, 20, [9.5, "doc-1"])

        with pytest.raises(ValueError, match="invalid cursor"):
            decode_cursor(cursor, *scope)

    def test_signed_cursor_with_a_non_list_sort_is_rejected(self):
        from app.core.security import _scope_hash

        cursor = _sign({"scope": _scope_hash("action", None, 20), "sort": "nope"})

        with pytest.raises(ValueError, match="invalid cursor"):
            decode_cursor(cursor, "action", None, 20)

    def test_structurally_broken_cursor_is_rejected(self):
        for bad in ("", "no-dot", "a.b.c", "!!!.???"):
            with pytest.raises(ValueError, match="invalid cursor"):
                decode_cursor(bad, "action", None, 20)

    def test_cursor_from_a_foreign_cursor_secret_is_rejected(self, monkeypatch):
        cursor = encode_cursor("action", None, 20, [9.5, "doc-1"])
        monkeypatch.setattr(settings, "SEARCH_CURSOR_SECRET", "a-completely-different-secret-value")

        with pytest.raises(ValueError, match="invalid cursor"):
            decode_cursor(cursor, "action", None, 20)


class TestCursorKeyIsNotTheJwtSecret:
    """The cursor HMAC must not be keyed on JWT_SECRET_KEY.

    Two defects motivated the split, and each needs its own assertion: cursors
    were defended by a value committed to the repository, and the two keys were
    rotationally coupled. A rename alone would satisfy neither.
    """

    def test_a_cursor_signed_with_the_jwt_secret_is_rejected(self):
        """The regression that matters: the key really changed, it was not renamed."""
        from app.core.security import _scope_hash

        forged = _sign(
            {"scope": _scope_hash("action", None, 20), "sort": [9.5, "doc-1"]},
            key=settings.JWT_SECRET_KEY,
        )

        with pytest.raises(ValueError, match="invalid cursor"):
            decode_cursor(forged, "action", None, 20)

    def test_rotating_the_jwt_secret_leaves_existing_cursors_valid(self, monkeypatch):
        """The lifecycle half: the two keys rotate independently."""
        cursor = encode_cursor("action", None, 20, [9.5, "doc-1"])
        monkeypatch.setattr(settings, "JWT_SECRET_KEY", "a-totally-rotated-jwt-secret")

        assert decode_cursor(cursor, "action", None, 20) == [9.5, "doc-1"]

    def test_rotating_the_cursor_secret_invalidates_existing_cursors(self, monkeypatch):
        """The converse, so the previous test is not vacuous."""
        cursor = encode_cursor("action", None, 20, [9.5, "doc-1"])
        monkeypatch.setattr(settings, "SEARCH_CURSOR_SECRET", "a-rotated-cursor-secret")

        with pytest.raises(ValueError, match="invalid cursor"):
            decode_cursor(cursor, "action", None, 20)

    def test_cursor_format_is_byte_identical_apart_from_the_key(self):
        """Only the key changed: the payload segment and the algorithm are untouched.

        Guards the promise that cursors stay wire-compatible -- the previous
        construction is reproduced here line for line, with the new key, and the
        two strings must match exactly.
        """
        from app.core.security import _scope_hash

        raw = json.dumps(
            {"scope": _scope_hash("action", None, 20), "sort": [9.5, "doc-1"]},
            separators=(",", ":"),
        ).encode()
        signature = hmac.new(settings.SEARCH_CURSOR_SECRET.encode(), raw, hashlib.sha256).digest()
        expected = (
            base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
            + "."
            + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
        )

        assert encode_cursor("action", None, 20, [9.5, "doc-1"]) == expected

    def test_only_the_signature_segment_differs_from_the_old_construction(self):
        """Same payload bytes, same shape, same length -- a different signature."""
        from app.core.security import _scope_hash

        payload = {"scope": _scope_hash("action", None, 20), "sort": [9.5, "doc-1"]}
        raw = json.dumps(payload, separators=(",", ":")).encode()

        old_sig = hmac.new(settings.JWT_SECRET_KEY.encode(), raw, hashlib.sha256).digest()
        old = (
            base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
            + "."
            + base64.urlsafe_b64encode(old_sig).rstrip(b"=").decode()
        )
        new = encode_cursor("action", None, 20, [9.5, "doc-1"])

        assert old.count(".") == new.count(".") == 1
        assert old.rsplit(".", 1)[0] == new.rsplit(".", 1)[0]
        assert old.rsplit(".", 1)[1] != new.rsplit(".", 1)[1]
        assert len(old) == len(new)


class TestUnsetCursorSecret:
    """An unset key must degrade availability, never raise and never weaken.

    Asserting "does not raise" alone would pass for a fallback to the committed
    dev default, which is precisely the property that must not hold. Each case
    below pins one part of the chosen behaviour: a per-process random key.
    """

    @pytest.mark.parametrize("unset", ["", None])
    def test_signer_and_verifier_do_not_raise(self, monkeypatch, unset):
        monkeypatch.setattr(settings, "SEARCH_CURSOR_SECRET", unset)

        cursor = encode_cursor("action", None, 20, [9.5, "doc-1"])

        assert decode_cursor(cursor, "action", None, 20) == [9.5, "doc-1"]

    def test_no_configured_signing_key_raises_nothing(self, monkeypatch):
        """Defect #2 pinned without assuming which attribute the signer reads.

        Both candidate attributes are emptied when they are declared, so this
        runs against the pre-fix code too: there it dies with
        ``RuntimeError("JWT_SECRET_KEY is not configured")``, which the search
        route does not map and which therefore surfaces as a 500. Here it
        round-trips. A 400-shaped degradation is the point -- a hard failure on
        a read-only endpoint is not.
        """
        candidates = ("SEARCH_CURSOR_SECRET", "JWT_SECRET_KEY")
        emptied = [a for a in candidates if a in type(settings).model_fields]
        assert "JWT_SECRET_KEY" in emptied
        for attribute in emptied:
            monkeypatch.setattr(settings, attribute, "")

        cursor = encode_cursor("action", None, 20, [9.5, "doc-1"])

        assert decode_cursor(cursor, "action", None, 20) == [9.5, "doc-1"]

    @pytest.mark.parametrize("unset", ["", None])
    def test_fallback_key_is_random_and_not_any_committed_value(self, monkeypatch, unset):
        from app.core.settings import DEV_DEFAULTS

        monkeypatch.setattr(settings, "SEARCH_CURSOR_SECRET", unset)

        key = security_module._cursor_secret()

        assert len(key) == 32
        assert key.decode(errors="ignore") not in DEV_DEFAULTS.values()
        assert key != settings.JWT_SECRET_KEY.encode()
        assert key != DEV_DEFAULTS["SEARCH_CURSOR_SECRET"].encode()

    def test_fallback_does_not_fall_back_to_the_dev_default(self, monkeypatch):
        """A client holding the committed dev default must not be able to forge."""
        from app.core.security import _scope_hash
        from app.core.settings import DEV_DEFAULTS

        monkeypatch.setattr(settings, "SEARCH_CURSOR_SECRET", "")
        forged = _sign(
            {"scope": _scope_hash("action", None, 20), "sort": [9.5, "doc-1"]},
            key=DEV_DEFAULTS["SEARCH_CURSOR_SECRET"],
        )

        with pytest.raises(ValueError, match="invalid cursor"):
            decode_cursor(forged, "action", None, 20)

    def test_a_client_signing_with_the_jwt_secret_still_cannot_forge(self, monkeypatch):
        from app.core.security import _scope_hash

        monkeypatch.setattr(settings, "SEARCH_CURSOR_SECRET", "")
        forged = _sign(
            {"scope": _scope_hash("action", None, 20), "sort": [9.5, "doc-1"]},
            key=settings.JWT_SECRET_KEY,
        )

        with pytest.raises(ValueError, match="invalid cursor"):
            decode_cursor(forged, "action", None, 20)

    def test_the_fallback_is_stable_within_a_process(self, monkeypatch):
        """Otherwise every cursor would be invalid the moment it was minted."""
        monkeypatch.setattr(settings, "SEARCH_CURSOR_SECRET", "")

        assert security_module._cursor_secret() == security_module._cursor_secret()

    def test_cursors_signed_before_the_key_was_unset_stop_verifying(self, monkeypatch):
        """The documented cost of the fallback, stated as behaviour not folklore."""
        cursor = encode_cursor("action", None, 20, [9.5, "doc-1"])
        monkeypatch.setattr(settings, "SEARCH_CURSOR_SECRET", "")

        with pytest.raises(ValueError, match="invalid cursor"):
            decode_cursor(cursor, "action", None, 20)

    def test_an_unusable_key_raises_value_error_not_runtime_error(self, monkeypatch):
        """The mapping the route depends on: ValueError -> 4xx, RuntimeError -> 500."""
        monkeypatch.setattr(settings, "SEARCH_CURSOR_SECRET", "")

        with pytest.raises(ValueError) as excinfo:
            decode_cursor("not-even-a-cursor", "action", None, 20)

        assert not isinstance(excinfo.value, RuntimeError)


class TestCursorSecretIsNeverDisclosed:
    """The key must not reach a log record or a client-visible error."""

    def test_the_fallback_warning_omits_the_key(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "SEARCH_CURSOR_SECRET", "")
        monkeypatch.setattr(security_module, "_ephemeral_cursor_secret_warned", False)

        with caplog.at_level(logging.WARNING, logger="app.core.security"):
            encode_cursor("action", None, 20, [9.5, "doc-1"])

        assert any("SEARCH_CURSOR_SECRET" in r.getMessage() for r in caplog.records)
        assert all(
            security_module._EPHEMERAL_CURSOR_SECRET.decode(errors="ignore") not in r.getMessage()
            for r in caplog.records
        )

    def test_no_value_ever_appears_in_a_log_record(self, monkeypatch, caplog):
        """Exercise every cursor path while recording, then scan the lot."""
        from app.core.settings import DEV_DEFAULTS

        secrets_ = {
            DEV_DEFAULTS["SEARCH_CURSOR_SECRET"],
            DEV_DEFAULTS["JWT_SECRET_KEY"],
            "a-very-distinctive-cursor-secret-value-for-scanning",
        }
        monkeypatch.setattr(security_module, "_ephemeral_cursor_secret_warned", False)
        monkeypatch.setattr(settings, "SEARCH_CURSOR_SECRET", "")

        with caplog.at_level(logging.DEBUG):
            encode_cursor("action", None, 20, [9.5, "doc-1"])
            for bad in ("", "no-dot", "a.b.c", "!!!.???"):
                with pytest.raises(ValueError):
                    decode_cursor(bad, "action", None, 20)

        monkeypatch.setattr(
            settings, "SEARCH_CURSOR_SECRET", "a-very-distinctive-cursor-secret-value-for-scanning"
        )
        with caplog.at_level(logging.DEBUG):
            encode_cursor("action", None, 20, [9.5, "doc-1"])
            with pytest.raises(ValueError):
                decode_cursor("garbage", "action", None, 20)

        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert caplog.records, "expected the fallback warning to have been recorded"
        for secret in secrets_:
            assert secret not in blob

    def test_error_messages_never_echo_the_key(self, monkeypatch):
        from app.core.settings import DEV_DEFAULTS

        key = "a-very-distinctive-cursor-secret-value-for-scanning"
        monkeypatch.setattr(settings, "SEARCH_CURSOR_SECRET", key)
        raw, sig = encode_cursor("action", None, 20, [9.5, "doc-1"]).rsplit(".", 1)
        tampered = f"{raw}.{('A' if sig[0] != 'A' else 'B')}{sig[1:]}"

        for bad in (tampered, "garbage", "", "a.b.c", "!!!.???"):
            with pytest.raises(ValueError) as excinfo:
                decode_cursor(bad, "action", None, 20)
            rendered = (
                f"{excinfo.value} {excinfo.value.__cause__!r} {excinfo.value.__traceback__!r}"
            )
            assert key not in rendered
            assert DEV_DEFAULTS["SEARCH_CURSOR_SECRET"] not in rendered

    def test_the_fallback_warning_is_emitted_once_per_process(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "SEARCH_CURSOR_SECRET", "")
        monkeypatch.setattr(security_module, "_ephemeral_cursor_secret_warned", False)

        with caplog.at_level(logging.WARNING, logger="app.core.security"):
            for _ in range(3):
                encode_cursor("action", None, 20, [9.5, "doc-1"])

        warnings = [r for r in caplog.records if "SEARCH_CURSOR_SECRET" in r.getMessage()]
        assert len(warnings) == 1


# ----------------------------------------------------------------------
# HTTP surface over the index lifecycle
# ----------------------------------------------------------------------


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    yield TestClient(app, base_url="http://localhost")
    app.dependency_overrides.clear()


@pytest.fixture
def route_service():
    mock = MagicMock()
    mock.reindex_catalog = AsyncMock(
        return_value=ReindexResult(count=1, index_name="content_v2", switched=True)
    )
    mock.delete_index = AsyncMock()
    mock.search = AsyncMock(
        return_value=MagicMock(results=[{"title": "A"}], next_sort=[9.0, "doc-1"])
    )
    return mock


def _admin():
    from app.core.security import Identity

    return AsyncMock(return_value=Identity(user_id=uuid4(), role="admin"))


def _use(mock):
    app.dependency_overrides[search_routes.get_search_service] = lambda: mock


class TestReindexRouteErrorMapping:
    def test_catalog_outage_maps_to_502(self, client, route_service):
        route_service.reindex_catalog = AsyncMock(
            side_effect=CatalogFetchError("content-service down")
        )
        _use(route_service)

        with patch("app.api.search_routes.get_admin_identity", new=_admin()):
            response = client.post("/api/v1/search/reindex")

        assert response.status_code == 502
        assert "Content service unavailable" in response.json()["detail"]["message"]

    def test_indexing_failure_maps_to_502(self, client, route_service):
        route_service.reindex_catalog = AsyncMock(side_effect=IndexingError("3 documents failed"))
        _use(route_service)

        with patch("app.api.search_routes.get_admin_identity", new=_admin()):
            response = client.post("/api/v1/search/reindex")

        assert response.status_code == 502
        assert "Bulk indexing failed" in response.json()["detail"]["message"]

    def test_unexpected_failure_maps_to_opaque_500(self, client, route_service):
        route_service.reindex_catalog = AsyncMock(side_effect=MemoryError("boom"))
        _use(route_service)

        with patch("app.api.search_routes.get_admin_identity", new=_admin()):
            response = client.post("/api/v1/search/reindex")

        assert response.status_code == 500
        body = response.json()
        assert body["detail"]["message"] == "Reindex failed"
        # The internal error text must never reach the caller.
        assert "boom" not in response.text

    def test_reindex_lock_is_released_after_a_failure(self, client, route_service):
        route_service.reindex_catalog = AsyncMock(side_effect=CatalogFetchError("down"))
        _use(route_service)

        with patch("app.api.search_routes.get_admin_identity", new=_admin()):
            assert client.post("/api/v1/search/reindex").status_code == 502
        assert not search_routes._reindex_lock.locked()

        route_service.reindex_catalog = AsyncMock(
            return_value=ReindexResult(count=0, index_name="content_v1", switched=True)
        )
        with patch("app.api.search_routes.get_admin_identity", new=_admin()):
            assert client.post("/api/v1/search/reindex").status_code == 200


class TestDeleteIndexRoute:
    def test_safeguard_violation_maps_to_400(self, client, route_service):
        route_service.delete_index = AsyncMock(
            side_effect=ValueError("refusing to delete the search alias")
        )
        _use(route_service)

        with patch("app.api.search_routes.get_admin_identity", new=_admin()):
            response = client.delete("/api/v1/search/index/content", params={"confirm": "true"})

        assert response.status_code == 400
        assert "refusing to delete" in response.json()["detail"]["message"]

    def test_non_versioned_name_maps_to_400(self, client, route_service):
        route_service.delete_index = AsyncMock(
            side_effect=ValueError("only versioned indices (content_v<N>) can be deleted")
        )
        _use(route_service)

        with patch("app.api.search_routes.get_admin_identity", new=_admin()):
            response = client.delete("/api/v1/search/index/random", params={"confirm": "true"})

        assert response.status_code == 400
        assert "only versioned indices" in response.json()["detail"]["message"]


class TestSearchCursorRoundTrip:
    def test_a_full_page_returns_a_resumable_cursor(self, client, route_service):
        _use(route_service)

        first = client.get("/api/v1/search/query", params={"q": "action", "limit": 1})
        assert first.status_code == 200
        cursor = first.json()["next_cursor"]
        assert cursor is not None

        second = client.get(
            "/api/v1/search/query", params={"q": "action", "limit": 1, "cursor": cursor}
        )
        assert second.status_code == 200
        assert route_service.search.await_args.kwargs["search_after"] == [9.0, "doc-1"]

    def test_a_replayed_cursor_is_refused_with_422(self, client, route_service):
        _use(route_service)
        cursor = encode_cursor("action", None, 1, [9.0, "doc-1"])

        response = client.get(
            "/api/v1/search/query", params={"q": "other", "limit": 1, "cursor": cursor}
        )

        assert response.status_code == 422
        route_service.search.assert_not_awaited()

    def test_an_unset_cursor_secret_does_not_500_a_search(self, client, route_service, monkeypatch):
        """The reported defect, end to end.

        With no SEARCH_CURSOR_SECRET configured, both the cursor page and a
        fresh search must still answer. The old code raised RuntimeError out of
        the signer, which the route does not map, so this was a 500 on a
        read-only endpoint.
        """
        monkeypatch.setattr(settings, "SEARCH_CURSOR_SECRET", "")
        _use(route_service)

        paged = client.get(
            "/api/v1/search/query",
            params={"q": "action", "limit": 1, "cursor": "not-even-a-cursor"},
        )
        first = client.get("/api/v1/search/query", params={"q": "action", "limit": 1})

        assert paged.status_code == 422
        assert first.status_code == 200
        assert first.json()["next_cursor"] is not None
        route_service.search.assert_awaited()

    def test_service_level_query_error_maps_to_422(self, client, route_service):
        route_service.search = AsyncMock(side_effect=ValueError("query too long"))
        _use(route_service)

        response = client.get("/api/v1/search/query", params={"q": "action"})

        assert response.status_code == 422
        assert "query too long" in response.json()["detail"]["message"]


class TestReindexCancellationSafety:
    @pytest.mark.asyncio
    async def test_cancellation_mid_fetch_still_cleans_up(self, es_mock, service):
        """A cancelled reindex must not leave a half-built index behind."""
        es_mock.indices.get_alias = AsyncMock(return_value={"content_v1": {}})
        es_mock.indices.get = AsyncMock(return_value={"content_v1": {}})

        catalog = MagicMock()
        catalog.fetch_published = AsyncMock(side_effect=asyncio.CancelledError())
        catalog.aclose = AsyncMock()

        with pytest.raises(asyncio.CancelledError):
            await service.reindex_catalog(catalog)

        es_mock.indices.delete.assert_awaited_once_with(index="content_v2", ignore_unavailable=True)
        es_mock.indices.update_aliases.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ensure_index_failure_aborts_before_creating(self, es_mock, service):
        es_mock.indices.get_alias = AsyncMock(side_effect=no_alias_error())
        es_mock.indices.exists = AsyncMock(return_value=True)

        with pytest.raises(IndexingError):
            await service.reindex_catalog(_catalog([published_item()]))

        es_mock.indices.create.assert_not_awaited()
        es_mock.indices.update_aliases.assert_not_awaited()
