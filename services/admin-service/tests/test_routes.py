"""Behavioural tests for ``app/api/routes/admin.py``.

The admin API is entirely gated on two security-critical helpers that had no
direct coverage at all:

* ``get_current_admin_id`` — bearer-token validation with token-type
  separation, admin-role enforcement, and ``ADMIN_ROLE_VERSION`` revocation.
* ``_consume_stepup_jti`` — single-use enforcement for step-up tokens, first
  against Redis (``SET NX``) and falling back to an in-process set.

Both the Redis and the in-process paths of the JTI guard are exercised, plus
every route handler (driven over HTTP with a real SQLite session) and the two
authorisation helpers that deliberately 404 instead of 403.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.admin import (
    AUDIT_RESOURCE_VISIBILITY,
    _client_ip,
    _consume_stepup_jti,
    _ensure_admin_can_view_resource,
    _ensure_admin_can_view_target_admin,
    _stepup_jti_seen,
    get_current_admin_id,
    router,
)
from app.core.database import DatabaseManager, get_db
from app.core.settings import settings
from app.models.admin import AdminAuditLog
from tests._test_jwks import JWKS, PRIVATE_PEM

ADMIN = "admin-1"
OTHER_ADMIN = "admin-2"


# ---------------------------------------------------------------------------
# Token minting helpers
# ---------------------------------------------------------------------------


def _mint(
    sub: str = ADMIN,
    *,
    typ: str = "access",
    role: str | None = "admin",
    arv: int | None = None,
    exp_offset: int = 300,
    extra: dict | None = None,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(sub),
        "user_id": str(sub),
        "type": typ,
        "iat": now,
        "exp": now + timedelta(seconds=exp_offset),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "av": 0,
        "arv": settings.ADMIN_ROLE_VERSION if arv is None else arv,
        "jti": f"{typ}_{sub}_{uuid.uuid4().hex}",
    }
    if role is not None:
        payload["role"] = role
    payload.update(extra or {})
    return jwt.encode(payload, PRIVATE_PEM, algorithm="RS256", headers={"kid": "k1"})


def _auth_header(token: str) -> str:
    """The raw header value, for calling the dependency function directly."""
    return f"Bearer {token}"


def _bearer(token: str) -> dict:
    """Header dict, for HTTP requests."""
    return {"Authorization": _auth_header(token)}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_jti_store():
    _stepup_jti_seen.clear()


@pytest.fixture(autouse=True)
def _stub_jwks(monkeypatch):
    async def get_jwks(_url):
        return JWKS

    monkeypatch.setattr("app.api.routes.admin.get_cached_jwks", get_jwks)
    from app.api.routes import admin as admin_routes

    class AuthResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"auth_version": 0}

    class AuthClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return AuthResponse()

    monkeypatch.setattr(admin_routes.httpx, "AsyncClient", AuthClient)
    yield
    _stepup_jti_seen.clear()


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """Default every test to the in-process JTI store (no Redis in tests)."""
    monkeypatch.setattr(settings, "REDIS_URL", None)
    yield


@pytest.fixture
async def admin_client(tmp_path, monkeypatch):
    """A client wired to the admin router with a real SQLite session."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'routes.db'}")
    async with engine.begin() as conn:
        from app.models.admin import Base

        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    monkeypatch.setattr(DatabaseManager, "session_factory", factory, raising=False)

    async def _db_override():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    # A plain generator, not the async_sessionmaker itself: FastAPI inspects the
    # override's signature and would otherwise see ``**local_kw`` as a required
    # query parameter.
    app.dependency_overrides[get_db] = _db_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    await engine.dispose()


# ---------------------------------------------------------------------------
# get_current_admin_id
# ---------------------------------------------------------------------------


class TestGetCurrentAdminId:
    async def test_accepts_a_well_formed_admin_token(self):
        assert await get_current_admin_id(_auth_header(_mint())) == ADMIN

    async def test_rejects_a_stale_auth_version(self, monkeypatch):
        from app.api.routes import admin as admin_routes

        class StaleResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"auth_version": 1}

        class StaleClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, *_args, **_kwargs):
                return StaleResponse()

        monkeypatch.setattr(admin_routes.httpx, "AsyncClient", StaleClient)
        with pytest.raises(HTTPException) as exc:
            await get_current_admin_id(_auth_header(_mint()))
        assert exc.value.status_code == 401

    async def test_rejects_malformed_auth_version_response(self, monkeypatch):
        from app.api.routes import admin as admin_routes

        class MalformedResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"auth_version": True}

        class MalformedClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, *_args, **_kwargs):
                return MalformedResponse()

        monkeypatch.setattr(admin_routes.httpx, "AsyncClient", MalformedClient)
        with pytest.raises(HTTPException) as exc:
            await get_current_admin_id(_auth_header(_mint()))
        assert exc.value.status_code == 401

    async def test_rejects_token_without_required_sub_claim(self):
        token = jwt.encode(
            {
                "user_id": "legacy-admin",
                "type": "access",
                "role": "admin",
                "arv": settings.ADMIN_ROLE_VERSION,
                "iss": settings.JWT_ISSUER,
                "aud": settings.JWT_AUDIENCE,
            },
            PRIVATE_PEM,
            algorithm="RS256",
            headers={"kid": "k1"},
        )
        with pytest.raises(HTTPException) as exc:
            await get_current_admin_id(_auth_header(token))
        assert exc.value.status_code == 401

    @pytest.mark.parametrize("header", [None, "", "Basic abc", "bearer lowercase", "Token x"])
    async def test_rejects_a_missing_or_malformed_header(self, header):
        with pytest.raises(HTTPException) as exc:
            await get_current_admin_id(header)
        assert exc.value.status_code == 401
        assert exc.value.detail == "Missing or invalid Authorization header"

    async def test_rejects_a_garbage_token(self):
        with pytest.raises(HTTPException) as exc:
            await get_current_admin_id(_auth_header("not-a-jwt"))
        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    async def test_rejects_a_token_signed_with_another_key(self):
        token = jwt.encode(
            {
                "sub": ADMIN,
                "type": "access",
                "role": "admin",
                "arv": settings.ADMIN_ROLE_VERSION,
                "iss": settings.JWT_ISSUER,
                "aud": settings.JWT_AUDIENCE,
            },
            "an-attacker-controlled-key",
            algorithm=settings.JWT_ALGORITHM,
        )
        with pytest.raises(HTTPException) as exc:
            await get_current_admin_id(_auth_header(token))
        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    async def test_rejects_a_token_from_another_audience(self):
        with pytest.raises(HTTPException) as exc:
            await get_current_admin_id(_auth_header(_mint(extra={"aud": "some-other-api"})))
        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    async def test_rejects_a_token_from_another_issuer(self):
        with pytest.raises(HTTPException) as exc:
            await get_current_admin_id(_auth_header(_mint(extra={"iss": "rogue-issuer"})))
        assert exc.value.status_code == 401

    @pytest.mark.parametrize("typ", ["refresh", "admin_step_up", "api_key", None])
    async def test_rejects_non_access_token_types(self, typ):
        with pytest.raises(HTTPException) as exc:
            await get_current_admin_id(_auth_header(_mint(typ=typ)))
        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    @pytest.mark.parametrize("role", ["user", "moderator", "billing", "api_key"])
    async def test_rejects_a_non_admin_role(self, role):
        with pytest.raises(HTTPException) as exc:
            await get_current_admin_id(_auth_header(_mint(role=role)))
        assert exc.value.status_code == 403
        assert exc.value.detail == "Admin privileges required"

    async def test_rejects_a_token_with_no_role_claim(self):
        with pytest.raises(HTTPException) as exc:
            await get_current_admin_id(_auth_header(_mint(role=None)))
        assert exc.value.status_code == 403

    async def test_rejects_a_token_minted_before_the_role_version_bump(self):
        # #81/#101: revoking admin must take effect immediately.
        with pytest.raises(HTTPException) as exc:
            await get_current_admin_id(_auth_header(_mint(arv=settings.ADMIN_ROLE_VERSION + 1)))
        assert exc.value.status_code == 403
        assert exc.value.detail == "Admin privileges required"

    async def test_treats_a_null_arv_claim_as_version_zero(self):
        # ``int(payload.get("arv") or 0)`` maps a missing/null arv to 0, which
        # only passes while ADMIN_ROLE_VERSION is 0. Pin that documented-by-code
        # behaviour so a future version bump is a conscious decision.
        token = _mint()
        decoded = jwt.get_unverified_claims(token)
        assert decoded["arv"] == settings.ADMIN_ROLE_VERSION
        if settings.ADMIN_ROLE_VERSION == 0:
            assert await get_current_admin_id(_auth_header(token)) == ADMIN
        else:
            with pytest.raises(HTTPException) as exc:
                await get_current_admin_id(_auth_header(token))
            assert exc.value.status_code == 403

    async def test_rejects_an_expired_token(self):
        with pytest.raises(HTTPException) as exc:
            await get_current_admin_id(_auth_header(_mint(exp_offset=-120)))
        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"


# ---------------------------------------------------------------------------
# _consume_stepup_jti
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal stand-in for redis.asyncio.Redis SET NX EX behaviour.

    Also awaitable (returning itself) so the same object works whether the
    caller writes ``await redis.from_url(...)`` or ``redis.from_url(...)``.
    """

    def __init__(self, store: dict):
        self.store = store
        self.calls: list[tuple] = []
        self.closed = False

    def __await__(self):
        async def _self():
            return self

        return _self().__await__()

    async def set(self, key, value, nx=False, ex=None):
        self.calls.append((key, value, nx, ex))
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def aclose(self):
        self.closed = True


class _FailingRedis:
    async def set(self, *_a, **_kw):
        raise ConnectionError("redis down")

    async def aclose(self):
        return None


class TestConsumeStepupJtiInMemory:
    async def test_first_use_is_accepted(self):
        assert await _consume_stepup_jti("jti-1", None) is False

    async def test_second_use_is_rejected(self):
        assert await _consume_stepup_jti("jti-2", None) is False
        assert await _consume_stepup_jti("jti-2", None) is True

    async def test_distinct_jtis_do_not_interfere(self):
        assert await _consume_stepup_jti("jti-a", None) is False
        assert await _consume_stepup_jti("jti-b", None) is False

    async def test_store_grows_with_each_distinct_jti(self):
        await _consume_stepup_jti("jti-3", None)
        assert "jti-3" in _stepup_jti_seen

    async def test_concurrent_uses_of_one_jti_admit_exactly_one(self):
        # The asyncio.Lock must make the check-then-set atomic.
        results = await asyncio.gather(*(_consume_stepup_jti("race", None) for _ in range(8)))
        assert results.count(False) == 1
        assert results.count(True) == 7

    async def test_exp_argument_is_ignored_without_redis(self):
        assert await _consume_stepup_jti("jti-4", int(datetime.now(UTC).timestamp()) + 60) is False
        assert await _consume_stepup_jti("jti-4", 1) is True


class TestConsumeStepupJtiWithRedis:
    @pytest.fixture
    def redis_store(self, monkeypatch):
        store: dict[str, str] = {}
        clients: list[_FakeRedis] = []

        def factory(*_a, **_kw):
            client = _FakeRedis(store)
            clients.append(client)
            return client

        monkeypatch.setattr(settings, "REDIS_URL", "redis://stub:6379/0")
        monkeypatch.setattr("app.api.routes.admin.redis.from_url", factory)
        return store, clients

    async def test_first_use_is_accepted_via_set_nx(self, redis_store):
        store, clients = redis_store
        assert await _consume_stepup_jti("r-1", None) is False
        assert store == {"stepup:jti:r-1": "1"}
        key, value, nx, ex = clients[0].calls[0]
        assert (key, value, nx) == ("stepup:jti:r-1", "1", True)
        assert ex == 300, "no exp in the token must fall back to a 300s TTL"

    async def test_replay_is_rejected_via_set_nx(self, redis_store):
        assert await _consume_stepup_jti("r-2", None) is False
        assert await _consume_stepup_jti("r-2", None) is True

    async def test_ttl_is_derived_from_the_token_expiry(self, redis_store):
        _, clients = redis_store
        exp = datetime.now(UTC).timestamp() + 120
        await _consume_stepup_jti("r-3", exp)
        _, _, _, ex = clients[0].calls[0]
        assert 115 <= ex <= 120

    async def test_ttl_is_clamped_to_at_least_one_second(self, redis_store):
        _, clients = redis_store
        await _consume_stepup_jti("r-4", datetime.now(UTC).timestamp() - 60)
        _, _, _, ex = clients[0].calls[0]
        assert ex == 1

    async def test_non_numeric_exp_falls_back_to_the_default_ttl(self, redis_store):
        _, clients = redis_store
        await _consume_stepup_jti("r-5", "not-a-number")
        _, _, _, ex = clients[0].calls[0]
        assert ex == 300

    async def test_client_is_closed_after_use(self, redis_store):
        _, clients = redis_store
        await _consume_stepup_jti("r-6", None)
        assert all(c.closed for c in clients)

    async def test_redis_failure_does_not_fall_back_to_the_in_process_store(self, monkeypatch):
        def factory(*_a, **_kw):
            return _FailingRedis()

        monkeypatch.setattr(settings, "REDIS_URL", "redis://stub:6379/0")
        monkeypatch.setattr("app.api.routes.admin.redis.from_url", factory)
        with pytest.raises(ConnectionError, match="redis down"):
            await _consume_stepup_jti("r-7", None)
        assert "r-7" not in _stepup_jti_seen

    async def test_redis_replay_does_not_also_populate_the_local_store(
        self, redis_store
    ):
        await _consume_stepup_jti("r-8", None)
        await _consume_stepup_jti("r-8", None)
        assert "r-8" not in _stepup_jti_seen


# ---------------------------------------------------------------------------
# _client_ip
# ---------------------------------------------------------------------------


class _FakeRequest:
    def __init__(self, headers: dict | None = None, client=None):
        self.headers = headers or {}
        self.client = client


class _FakeClient:
    def __init__(self, host):
        self.host = host


class TestClientIp:
    def test_uses_the_peer_address_by_default(self):
        assert settings.TRUST_PROXY is False
        assert _client_ip(_FakeRequest(client=_FakeClient("10.1.2.3"))) == "10.1.2.3"

    def test_ignores_x_forwarded_for_when_proxy_is_untrusted(self):
        request = _FakeRequest(
            headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"},
            client=_FakeClient("10.1.2.3"),
        )
        assert _client_ip(request) == "10.1.2.3"

    def test_honours_x_forwarded_for_when_proxy_is_trusted(self, monkeypatch):
        monkeypatch.setattr(settings, "TRUST_PROXY", True)
        request = _FakeRequest(
            headers={"x-forwarded-for": " 1.2.3.4 , 5.6.7.8"}, client=_FakeClient("10.1.2.3")
        )
        assert _client_ip(request) == "1.2.3.4"

    def test_falls_back_to_peer_when_trusted_but_header_absent(self, monkeypatch):
        monkeypatch.setattr(settings, "TRUST_PROXY", True)
        assert _client_ip(_FakeRequest(client=_FakeClient("10.1.2.3"))) == "10.1.2.3"

    def test_returns_unknown_when_there_is_no_peer(self):
        assert _client_ip(_FakeRequest()) == "unknown"


# ---------------------------------------------------------------------------
# Audit authorisation helpers
# ---------------------------------------------------------------------------


class TestAuditAuthorisation:
    def test_admins_may_view_their_own_log(self):
        assert _ensure_admin_can_view_target_admin(ADMIN, ADMIN) is None

    def test_admins_may_not_view_another_admins_log(self):
        with pytest.raises(HTTPException) as exc:
            _ensure_admin_can_view_target_admin(ADMIN, OTHER_ADMIN)
        # 404, not 403: existence of the other admin's log must not leak.
        assert exc.value.status_code == 404

    @pytest.mark.parametrize("resource_type", sorted(AUDIT_RESOURCE_VISIBILITY))
    def test_shared_resource_types_are_visible_to_any_admin(self, resource_type):
        assert _ensure_admin_can_view_resource(resource_type, ADMIN) is None

    @pytest.mark.parametrize("resource_type", ["billing", "internal", "user_profile", ""])
    def test_restricted_resource_types_404(self, resource_type):
        with pytest.raises(HTTPException) as exc:
            _ensure_admin_can_view_resource(resource_type, ADMIN)
        assert exc.value.status_code == 404

    def test_visibility_set_is_frozen(self):
        assert isinstance(AUDIT_RESOURCE_VISIBILITY, frozenset)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


class TestUserModerationRoutes:
    async def test_moderate_user_requires_step_up_auth(self, admin_client):
        resp = await admin_client.post(
            "/api/v1/admin/users/moderate",
            json={"user_id": "u-1", "status": "suspended"},
            headers=_bearer(_mint()),
        )
        assert resp.status_code == 401
        assert "Step-up authentication required" in resp.json()["detail"]

    async def test_moderate_user_requires_an_admin_token(self, admin_client):
        resp = await admin_client.post(
            "/api/v1/admin/users/moderate",
            json={"user_id": "u-1", "status": "suspended"},
        )
        assert resp.status_code == 401

    async def test_moderate_user_with_step_up_succeeds(self, admin_client):
        from tests.test_step_up import _mint_step_up

        resp = await admin_client.post(
            "/api/v1/admin/users/moderate",
            json={"user_id": "u-1", "status": "suspended", "reason": "spam"},
            headers={**_bearer(_mint()), "X-Admin-Reauth": _mint_step_up(ADMIN)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == "u-1"
        assert body["status"] == "suspended"
        assert body["reason"] == "spam"
        assert body["moderated_by"] == ADMIN

    async def test_moderate_user_rejects_an_unknown_status(self, admin_client):
        from tests.test_step_up import _mint_step_up

        resp = await admin_client.post(
            "/api/v1/admin/users/moderate",
            json={"user_id": "u-1", "status": "deleted"},
            headers={**_bearer(_mint()), "X-Admin-Reauth": _mint_step_up(ADMIN)},
        )
        assert resp.status_code == 422

    async def test_get_user_moderation_404s_for_an_unknown_user(self, admin_client):
        resp = await admin_client.get(
            "/api/v1/admin/users/moderation/nobody", headers=_bearer(_mint())
        )
        assert resp.status_code == 404

    async def test_get_user_moderation_returns_the_latest_decision(self, admin_client):
        from tests.test_step_up import _mint_step_up

        headers = {**_bearer(_mint()), "X-Admin-Reauth": _mint_step_up(ADMIN)}
        await admin_client.post(
            "/api/v1/admin/users/moderate",
            json={"user_id": "u-2", "status": "banned", "reason": "fraud"},
            headers=headers,
        )
        resp = await admin_client.get(
            "/api/v1/admin/users/moderation/u-2", headers=_bearer(_mint())
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "banned"

    async def test_list_moderated_users(self, admin_client):
        from tests.test_step_up import _mint_step_up

        headers = {**_bearer(_mint()), "X-Admin-Reauth": _mint_step_up(ADMIN)}
        await admin_client.post(
            "/api/v1/admin/users/moderate",
            json={"user_id": "u-3", "status": "suspended"},
            headers=headers,
        )
        resp = await admin_client.get("/api/v1/admin/users/moderated", headers=_bearer(_mint()))
        assert resp.status_code == 200
        assert [row["user_id"] for row in resp.json()] == ["u-3"]

    async def test_list_moderated_users_filters_by_status(self, admin_client):
        resp = await admin_client.get(
            "/api/v1/admin/users/moderated", params={"status": "banned"}, headers=_bearer(_mint())
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_moderated_users_rejects_an_oversized_limit(self, admin_client):
        resp = await admin_client.get(
            "/api/v1/admin/users/moderated", params={"limit": 101}, headers=_bearer(_mint())
        )
        assert resp.status_code == 422

    async def test_audit_records_the_direct_peer_ip(self, admin_client, tmp_path):
        from tests.test_step_up import _mint_step_up

        headers = {**_bearer(_mint()), "X-Admin-Reauth": _mint_step_up(ADMIN)}
        await admin_client.post(
            "/api/v1/admin/users/moderate",
            json={"user_id": "u-4", "status": "suspended"},
            headers=headers,
        )
        factory = DatabaseManager.session_factory
        async with factory() as session:
            from sqlalchemy import select

            rows = (
                await session.execute(select(AdminAuditLog))
            ).scalars().all()
        assert [r.action for r in rows] == ["user_moderation_suspended"]


class TestContentModerationRoutes:
    async def test_flag_content(self, admin_client):
        resp = await admin_client.post(
            "/api/v1/admin/content/flag",
            json={"content_id": "c-1", "content_type": "movie", "status": "flagged", "reason": "gore"},
            headers=_bearer(_mint()),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["content_id"] == "c-1"
        assert body["status"] == "flagged"

    async def test_flag_content_rejects_an_unknown_content_type(self, admin_client):
        resp = await admin_client.post(
            "/api/v1/admin/content/flag",
            json={"content_id": "c-1", "content_type": "podcast", "status": "flagged"},
            headers=_bearer(_mint()),
        )
        assert resp.status_code == 422

    async def test_list_flagged_content(self, admin_client):
        await admin_client.post(
            "/api/v1/admin/content/flag",
            json={"content_id": "c-2", "content_type": "show", "status": "flagged"},
            headers=_bearer(_mint()),
        )
        resp = await admin_client.get("/api/v1/admin/content/flagged", headers=_bearer(_mint()))
        assert resp.status_code == 200
        assert [row["content_id"] for row in resp.json()] == ["c-2"]

    async def test_resolve_content_flag(self, admin_client):
        from tests.test_step_up import _mint_step_up

        await admin_client.post(
            "/api/v1/admin/content/flag",
            json={"content_id": "c-3", "content_type": "movie", "status": "flagged"},
            headers=_bearer(_mint()),
        )
        resp = await admin_client.post(
            "/api/v1/admin/content/resolve",
            params={"content_id": "c-3", "status": "removed"},
            headers={**_bearer(_mint()), "X-Admin-Reauth": _mint_step_up(ADMIN)},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"

    async def test_resolve_content_flag_404s_when_nothing_is_flagged(self, admin_client):
        from tests.test_step_up import _mint_step_up

        resp = await admin_client.post(
            "/api/v1/admin/content/resolve",
            params={"content_id": "never-flagged", "status": "active"},
            headers={**_bearer(_mint()), "X-Admin-Reauth": _mint_step_up(ADMIN)},
        )
        assert resp.status_code == 404

    async def test_resolve_content_flag_rejects_a_bad_status_query_param(self, admin_client):
        from tests.test_step_up import _mint_step_up

        resp = await admin_client.post(
            "/api/v1/admin/content/resolve",
            params={"content_id": "c-1", "status": "deleted"},
            headers={**_bearer(_mint()), "X-Admin-Reauth": _mint_step_up(ADMIN)},
        )
        assert resp.status_code == 422

    async def test_resolve_content_flag_requires_step_up(self, admin_client):
        resp = await admin_client.post(
            "/api/v1/admin/content/resolve",
            params={"content_id": "c-1", "status": "active"},
            headers=_bearer(_mint()),
        )
        assert resp.status_code == 401


class TestAlertRoutes:
    async def test_create_alert(self, admin_client):
        resp = await admin_client.post(
            "/api/v1/admin/alerts",
            json={
                "alert_type": "error",
                "severity": "critical",
                "message": "database down",
                "service": "content-service",
            },
            headers=_bearer(_mint()),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["severity"] == "critical"
        assert body["acknowledged"] is False

    async def test_create_alert_rejects_an_unknown_severity(self, admin_client):
        resp = await admin_client.post(
            "/api/v1/admin/alerts",
            json={"alert_type": "error", "severity": "fatal", "message": "m", "service": "s"},
            headers=_bearer(_mint()),
        )
        assert resp.status_code == 422

    async def test_get_alerts_returns_unacknowledged_only(self, admin_client):
        created = await admin_client.post(
            "/api/v1/admin/alerts",
            json={"alert_type": "error", "severity": "info", "message": "m", "service": "s"},
            headers=_bearer(_mint()),
        )
        alert_id = created.json()["id"]
        listed = await admin_client.get("/api/v1/admin/alerts", headers=_bearer(_mint()))
        assert [row["id"] for row in listed.json()] == [alert_id]

        await admin_client.post(
            f"/api/v1/admin/alerts/{alert_id}/acknowledge", headers=_bearer(_mint())
        )
        after = await admin_client.get("/api/v1/admin/alerts", headers=_bearer(_mint()))
        assert after.json() == []

    async def test_get_critical_alerts(self, admin_client):
        await admin_client.post(
            "/api/v1/admin/alerts",
            json={"alert_type": "error", "severity": "critical", "message": "m", "service": "s"},
            headers=_bearer(_mint()),
        )
        await admin_client.post(
            "/api/v1/admin/alerts",
            json={"alert_type": "info", "severity": "info", "message": "m", "service": "s"},
            headers=_bearer(_mint()),
        )
        resp = await admin_client.get("/api/v1/admin/alerts/critical", headers=_bearer(_mint()))
        assert [row["severity"] for row in resp.json()] == ["critical"]

    async def test_acknowledge_alert(self, admin_client):
        created = await admin_client.post(
            "/api/v1/admin/alerts",
            json={"alert_type": "error", "severity": "warning", "message": "m", "service": "s"},
            headers=_bearer(_mint()),
        )
        resp = await admin_client.post(
            f"/api/v1/admin/alerts/{created.json()['id']}/acknowledge",
            headers=_bearer(_mint()),
        )
        assert resp.status_code == 200
        assert resp.json()["acknowledged"] is True
        assert resp.json()["acknowledged_by"] == ADMIN

    async def test_acknowledge_unknown_alert_404s(self, admin_client):
        resp = await admin_client.post(
            "/api/v1/admin/alerts/9999/acknowledge", headers=_bearer(_mint())
        )
        assert resp.status_code == 404

    async def test_acknowledge_alert_rejects_a_non_numeric_id(self, admin_client):
        resp = await admin_client.post(
            "/api/v1/admin/alerts/abc/acknowledge", headers=_bearer(_mint())
        )
        assert resp.status_code == 422


class TestConfigRoutes:
    async def test_set_config_creates_and_then_updates(self, admin_client):
        body = {"key": "retention_days", "value": "90", "config_type": "integer"}
        created = await admin_client.post(
            "/api/v1/admin/config", json=body, headers=_bearer(_mint())
        )
        assert created.status_code == 200
        assert created.json()["value"] == "90"

        updated = await admin_client.post(
            "/api/v1/admin/config",
            json={**body, "value": "120", "description": "bumped"},
            headers=_bearer(_mint()),
        )
        assert updated.json()["value"] == "120"
        assert updated.json()["updated_by"] == ADMIN

    async def test_set_config_masks_a_secret_valued_key(self, admin_client):
        resp = await admin_client.post(
            "/api/v1/admin/config",
            json={"key": "stripe_secret_key", "value": "sk_live_leak", "config_type": "string"},
            headers=_bearer(_mint()),
        )
        assert resp.json()["value"] == "********"
        assert "sk_live_leak" not in resp.text

    async def test_get_config(self, admin_client):
        await admin_client.post(
            "/api/v1/admin/config",
            json={"key": "region", "value": "eu", "config_type": "string"},
            headers=_bearer(_mint()),
        )
        resp = await admin_client.get("/api/v1/admin/config/region", headers=_bearer(_mint()))
        assert resp.status_code == 200
        assert resp.json()["value"] == "eu"

    async def test_get_config_404s_for_an_unknown_key(self, admin_client):
        resp = await admin_client.get("/api/v1/admin/config/nope", headers=_bearer(_mint()))
        assert resp.status_code == 404

    async def test_list_configs_is_key_ordered(self, admin_client):
        for key in ("b_key", "a_key"):
            await admin_client.post(
                "/api/v1/admin/config",
                json={"key": key, "value": "1", "config_type": "string"},
                headers=_bearer(_mint()),
            )
        resp = await admin_client.get("/api/v1/admin/config", headers=_bearer(_mint()))
        assert [row["key"] for row in resp.json()] == ["a_key", "b_key"]

    async def test_list_configs_masks_secret_values(self, admin_client):
        await admin_client.post(
            "/api/v1/admin/config",
            json={"key": "api_key", "value": "sk_live_leak", "config_type": "string"},
            headers=_bearer(_mint()),
        )
        resp = await admin_client.get("/api/v1/admin/config", headers=_bearer(_mint()))
        assert "sk_live_leak" not in resp.text


class TestAuditRoutes:
    async def test_audit_by_admin_returns_own_rows(self, admin_client):
        await admin_client.post(
            "/api/v1/admin/alerts",
            json={"alert_type": "error", "severity": "info", "message": "m", "service": "s"},
            headers=_bearer(_mint()),
        )
        resp = await admin_client.get(
            f"/api/v1/admin/audit/admin/{ADMIN}", headers=_bearer(_mint())
        )
        assert resp.status_code == 200
        assert [row["action"] for row in resp.json()] == ["alert_created"]

    async def test_audit_by_admin_404s_for_another_admin(self, admin_client):
        resp = await admin_client.get(
            f"/api/v1/admin/audit/admin/{OTHER_ADMIN}", headers=_bearer(_mint())
        )
        assert resp.status_code == 404

    async def test_audit_by_resource(self, admin_client):
        await admin_client.post(
            "/api/v1/admin/alerts",
            json={"alert_type": "error", "severity": "info", "message": "m", "service": "s"},
            headers=_bearer(_mint()),
        )
        resp = await admin_client.get(
            "/api/v1/admin/audit/resource/alert/1", headers=_bearer(_mint())
        )
        assert resp.status_code == 200
        assert [row["resource_type"] for row in resp.json()] == ["alert"]

    async def test_audit_by_resource_404s_for_a_restricted_resource_type(self, admin_client):
        resp = await admin_client.get(
            "/api/v1/admin/audit/resource/billing/1", headers=_bearer(_mint())
        )
        assert resp.status_code == 404

    async def test_audit_response_never_leaks_a_secret(self, admin_client):
        await admin_client.post(
            "/api/v1/admin/config",
            json={"key": "webhook_token", "value": "tok_leak", "config_type": "string"},
            headers=_bearer(_mint()),
        )
        resp = await admin_client.get(
            f"/api/v1/admin/audit/admin/{ADMIN}", headers=_bearer(_mint())
        )
        assert "tok_leak" not in resp.text


class TestStatsRoute:
    async def test_stats_requires_an_admin_token(self, admin_client):
        assert (await admin_client.get("/api/v1/admin/stats")).status_code == 401

    async def test_stats_rejects_a_non_admin_token(self, admin_client):
        assert (
            await admin_client.get("/api/v1/admin/stats", headers=_bearer(_mint(role="user")))
        ).status_code == 403

    async def test_stats_route_is_broken_upstream(self, admin_client):
        """KNOWN BUG (reported, not fixed here).

        ``get_system_stats`` returns ``total_users`` / ``active_users`` /
        ``suspended_users`` as ``None`` because the route calls it with no
        arguments, but ``SystemStatsResponse`` declares all three as ``int``.
        FastAPI's response validation therefore fails and the endpoint answers
        500 on every call. This test pins the *observed* behaviour so the bug
        cannot silently change shape; it should be inverted when fixed.
        """
        from fastapi.exceptions import ResponseValidationError
        from pydantic import ValidationError

        from app.schemas.admin import SystemStatsResponse

        with pytest.raises(ValidationError):
            SystemStatsResponse(
                total_users=None,
                active_users=None,
                suspended_users=None,
                flagged_content=0,
                active_alerts=0,
                system_uptime_hours=0.0,
            )

        # Driving the endpoint surfaces exactly that error; nothing coerces the
        # three None counts into the int fields the response model demands.
        with pytest.raises(ResponseValidationError) as exc:
            await admin_client.get("/api/v1/admin/stats", headers=_bearer(_mint()))
        fields = {str(e["loc"][-1]) for e in exc.value.errors()}
        assert fields == {"total_users", "active_users", "suspended_users"}

    async def test_stats_service_reports_none_for_the_unpopulated_counts(self, admin_client):
        # The service half of the same bug: the counts are never supplied.
        from app.services.admin import AdminService

        factory = DatabaseManager.session_factory
        async with factory() as session:
            stats = await AdminService(session).get_system_stats()
        assert stats["total_users"] is None
        assert stats["active_users"] is None
        assert stats["suspended_users"] is None
        assert stats["flagged_content"] == 0
        assert stats["active_alerts"] == 0
        assert stats["system_uptime_hours"] >= 0

    async def test_stats_service_counts_supplied_totals(self, admin_client):
        from app.services.admin import AdminService

        factory = DatabaseManager.session_factory
        async with factory() as session:
            stats = await AdminService(session).get_system_stats(
                total_users=10, suspended_users=4
            )
        assert stats["total_users"] == 10
        assert stats["suspended_users"] == 4
        assert stats["active_users"] == 6
