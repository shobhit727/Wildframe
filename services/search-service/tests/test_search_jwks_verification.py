"""The search-service identity boundary, after it was moved onto the SDK verifier.

``app/core/security.py`` is a *library* module, not a route module, so this
migration had one extra hazard the route-module migrations did not:
``verify_token`` was **synchronous**, and the replacement
``verify_token_with_jwks`` fetches the JWKS over the network. It is now a
coroutine, and this file exists partly to pin that every caller awaits it --
a missed ``await`` would be a coroutine-never-awaited bug that still type-checks
and still returns ``None``, i.e. it would silently turn every authenticated
caller into an anonymous one.

The other hazard is the return contract. ``verify_token`` answers ``None`` for
"no usable identity", and it used to swallow every exception to do it. With a
JWKS in the path, swallowing the outage would be wrong: it would turn an
availability problem into an authorization answer, and for
``get_admin_identity`` it would present a 403 against a caller whose token is
perfectly valid. So:

* the JWKS cannot be had (transport failure, or a body that is not a JWKS)
  -> **503** "Token verification is unavailable"
* the token is simply not valid (bad signature, expired, wrong audience,
  unknown kid) -> ``None``, i.e. anonymous / 401, exactly as before

The bypass itself: ``JWT_SECRET_KEY`` is a committed development value and
``DEV_ENVIRONMENTS`` exempts it from the production validator, so a forged
HS256 token carrying any ``sub`` *and* ``role: "admin"`` was accepted. That is
a privilege escalation here specifically, because ``is_admin`` gates the
index-mutating routes (``/reindex``, bulk operations) via
``get_admin_identity``.

Every token here is a real RS256 signature over the real RSA-2048 keypair
generated at import time in ``tests/_test_jwks.py``; only the outbound JWKS HTTP
call is replaced.
"""

import inspect
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import wildframe_auth
from fastapi import HTTPException
from jose import JWTError, jwt

import app.core.security as security
from app.core.security import (
    Identity,
    get_admin_identity,
    get_optional_identity,
    get_required_identity,
    verify_token,
)
from app.core.settings import settings
from tests._test_jwks import JWKS, PRIVATE_PEM
from wildframe_auth.verifier import clear_jwks_cache

#: The key set auth-service publishes after a rotation (same material, new kid).
ROTATED_JWKS = {"keys": [*JWKS["keys"], {**JWKS["keys"][0], "kid": "k2"}]}

#: The two committed development secrets. Both are exempt from the production
#: validator, so either one was a *valid* verification key here:
#: ``dev-secret-key`` is what ``deployments/docker-compose.dev.yml`` injects into
#: the local stack, and ``dev-secret-key-change-in-production-min-32-bytes`` is the
#: pydantic default in ``app/core/settings.py`` that a service sees when the
#: variable is unset. Both are covered so the regression is pinned regardless of
#: which one the service under test actually loaded.
COMMITTED_DEV_SECRETS = ["dev-secret-key", "dev-secret-key-change-in-production-min-32-bytes"]

#: A path the guarded routes use, so ``require_self``-style assertions are real.
GUARDED_PATH = "/api/v1/search/reindex"


def _request(headers: dict[str, str]):
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": GUARDED_PATH,
            "headers": raw,
            "query_string": b"",
        }
    )


def _mint(
    *,
    kid: str = "k1",
    typ: str = "access",
    role: str = "user",
    exp_offset: int = 300,
    aud: str | None = None,
    iss: str | None = None,
    sub: str | None = None,
    private_pem: str = PRIVATE_PEM,
    algorithm: str = "RS256",
    **extra: object,
) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": sub or str(uuid.uuid4()),
        "type": typ,
        "aud": aud or settings.JWT_AUDIENCE,
        "iss": iss or settings.JWT_ISSUER,
        "role": role,
        "arv": 0,
        "av": 0,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()) + exp_offset,
    }
    claims.update(extra)
    return jwt.encode(claims, private_pem, algorithm=algorithm, headers={"kid": kid})


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class _Endpoint:
    """The auth service's JWKS endpoint, as the routes see it."""

    def __init__(self) -> None:
        self.jwks = JWKS
        self.calls: list[str] = []
        self.raises: Exception | None = None
        self.body = None

    async def fetch(self, url: str):
        self.calls.append(url)
        if self.raises is not None:
            raise self.raises
        return self.body if self.body is not None else self.jwks

    @property
    def fetches(self) -> int:
        return len(self.calls)


@pytest.fixture
def endpoint(monkeypatch) -> _Endpoint:
    """Replace the outbound JWKS fetch only; the SDK verifier still runs for real."""
    ep = _Endpoint()
    monkeypatch.setattr(wildframe_auth.verifier, "fetch_jwks", ep.fetch)
    clear_jwks_cache()
    yield ep
    clear_jwks_cache()


# ---------------------------------------------------------------------------
# The async conversion itself -- the failure mode that silently loses auth
# ---------------------------------------------------------------------------


class TestVerifyTokenIsAwaited:
    """``verify_token`` became a coroutine; a missed ``await`` is a silent bug.

    If any caller forgets to await, it receives a never-awaited coroutine
    instead of an ``Identity``. ``if not identity`` is still true for a
    coroutine object, so the call would resolve to "anonymous" and the request
    would 401 -- or, on an optional-auth route, would quietly serve
    unauthenticated results. Nothing would raise, so these are structural
    assertions rather than behavioural ones.
    """

    def test_verify_token_is_a_coroutine_function(self):
        assert inspect.iscoroutinefunction(verify_token)

    @pytest.mark.parametrize(
        "dep",
        [get_optional_identity, get_required_identity, get_admin_identity],
        ids=["optional", "required", "admin"],
    )
    def test_every_dependency_is_a_coroutine_function(self, dep):
        assert inspect.iscoroutinefunction(dep)

    async def test_awaiting_verify_token_returns_an_identity_not_a_coroutine(self, endpoint):
        identity = await verify_token(_request(_bearer(_mint())))

        assert isinstance(identity, Identity)
        assert not inspect.isawaitable(identity)

    async def test_a_forgotten_await_would_be_anonymous_not_an_identity(self, endpoint):
        """Document *why* the assertions above matter.

        ``inspect.isawaitable`` on the un-awaited result is the exact signal a
        reviewer should look for; ``bool(coroutine)`` is always True, so a
        naive ``if not identity`` guard does not catch it.
        """
        un_awaited = verify_token(_request(_bearer(_mint())))

        assert inspect.isawaitable(un_awaited)
        assert not isinstance(un_awaited, Identity)
        # The contrast: a correctly-awaited call is a plain frozen dataclass.
        assert not inspect.isawaitable(await verify_token(_request(_bearer(_mint()))))


# ---------------------------------------------------------------------------
# Finding 1 -- the bypass itself (#941)
# ---------------------------------------------------------------------------


class TestForgedSharedSecretTokenIsRejected:
    """The regression these tests exist for.

    Against the old shared-secret decode every one of these was *accepted* as
    an admin identity -- the forgery is signed with the very secret the service
    used to verify with, and it asserts ``role: "admin"``.
    """

    @pytest.mark.parametrize("secret", COMMITTED_DEV_SECRETS)
    async def test_a_forged_admin_token_is_anonymous(self, endpoint, secret):
        forged = _mint(role="admin", private_pem=secret, algorithm="HS256")

        assert await verify_token(_request(_bearer(forged))) is None

    @pytest.mark.parametrize("secret", COMMITTED_DEV_SECRETS)
    async def test_a_forged_admin_token_cannot_reach_the_admin_guard(self, endpoint, secret):
        forged = _mint(role="admin", private_pem=secret, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await get_admin_identity(_request(_bearer(forged)))

        assert exc.value.status_code == 401
        assert exc.value.detail["message"] == "Authentication required"

    @pytest.mark.parametrize("secret", COMMITTED_DEV_SECRETS)
    async def test_a_forged_admin_token_cannot_reach_the_required_guard(self, endpoint, secret):
        forged = _mint(role="admin", private_pem=secret, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await get_required_identity(_request(_bearer(forged)))

        assert exc.value.status_code == 401

    @pytest.mark.parametrize("secret", COMMITTED_DEV_SECRETS)
    async def test_a_forged_admin_token_is_anonymous_on_the_optional_guard(self, endpoint, secret):
        forged = _mint(role="admin", private_pem=secret, algorithm="HS256")

        assert await get_optional_identity(_request(_bearer(forged))) is None

    def test_the_module_no_longer_reaches_for_the_shared_secret(self):
        """The bypass is removed structurally, not merely out-tested."""
        assert security.verify_token_with_jwks is wildframe_auth.verify_token_with_jwks
        assert not hasattr(security, "get_cached_jwks")
        assert security.settings.JWT_JWKS_URL
        # The cursor HMAC still keys off the shared secret -- that is a separate
        # integrity concern, not token verification -- so the check is on the
        # decode *call*, which is what the contract gate greps for. Asserting on
        # the bare setting name instead would also trip over this module's own
        # docstrings, and would flag the cursor code for the wrong reason.
        assert "jwt.decode(" not in inspect.getsource(security)


class TestGenuineRs256IsAccepted:
    async def test_a_genuine_access_token_yields_an_identity(self, endpoint):
        sub = str(uuid.uuid4())

        identity = await verify_token(_request(_bearer(_mint(sub=sub))))

        assert identity is not None
        assert identity.user_id == UUID(sub)
        assert identity.role == "user"

    async def test_the_sdk_rotates_a_key_without_waiting_out_the_cache_ttl(self, endpoint):
        """A rotation lands immediately instead of 401ing for up to 300s.

        The old hand-wiring had no refresh path at all, so every token signed
        with a newly published key was anonymous until the cache expired.
        """
        assert await verify_token(_request(_bearer(_mint())))
        assert endpoint.fetches == 1

        endpoint.jwks = ROTATED_JWKS
        assert await verify_token(_request(_bearer(_mint(kid="k2"))))
        assert endpoint.fetches == 2, "the warm cache must be refreshed once"

    async def test_a_rotated_token_still_yields_an_identity(self, endpoint):
        assert await verify_token(_request(_bearer(_mint())))
        endpoint.jwks = ROTATED_JWKS
        assert await verify_token(_request(_bearer(_mint(kid="k2"))))


# ---------------------------------------------------------------------------
# The 503 / anonymous split
# ---------------------------------------------------------------------------


class TestJwksOutageIs503:
    async def test_a_transport_failure_is_503_not_anonymous(self, endpoint):
        endpoint.raises = ConnectionError("auth-service unreachable")

        with pytest.raises(HTTPException) as exc:
            await verify_token(_request(_bearer(_mint())))

        assert exc.value.status_code == 503
        assert exc.value.detail["message"] == "Token verification is unavailable"

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(["not", "a", "jwks"], id="json-array"),
            pytest.param("<html>502 Bad Gateway</html>", id="html-string"),
            pytest.param({"keys": "k1"}, id="keys-not-a-list"),
            pytest.param({"keys": [{"kid": "k1"}]}, id="key-without-kty"),
        ],
    )
    async def test_a_malformed_jwks_body_is_503(self, endpoint, body):
        """A poisoned JWKS must not become a 500, nor stay cached."""
        endpoint.body = body

        with pytest.raises(HTTPException) as exc:
            await verify_token(_request(_bearer(_mint())))

        assert exc.value.status_code == 503
        assert exc.value.detail["message"] == "Token verification is unavailable"

    async def test_a_malformed_body_does_not_poison_the_cache(self, endpoint):
        endpoint.body = {"keys": "garbage"}
        for _ in range(3):
            with pytest.raises(HTTPException) as exc:
                await verify_token(_request(_bearer(_mint())))
            assert exc.value.status_code == 503
        assert endpoint.fetches == 3  # never cached, so always retried

    async def test_a_rotation_time_outage_is_503_not_a_500(self, endpoint):
        """A failed *forced* refetch must not escape as a bare transport error."""
        assert await verify_token(_request(_bearer(_mint())))
        endpoint.raises = ConnectionError("auth-service dropped the refresh")

        with pytest.raises(HTTPException) as exc:
            await verify_token(_request(_bearer(_mint(kid="k-rotated"))))

        assert exc.value.status_code == 503

    async def test_the_outage_is_503_on_the_optional_guard_too(self, endpoint):
        """An outage must not be laundered into "anonymous" on a public route.

        This is the guard that would otherwise hide the outage entirely: search
        results would simply come back unscoped, with no signal to the operator.
        """
        endpoint.raises = ConnectionError("auth-service unreachable")

        with pytest.raises(HTTPException) as exc:
            await get_optional_identity(_request(_bearer(_mint())))

        assert exc.value.status_code == 503

    async def test_the_outage_is_503_on_the_admin_guard_not_403(self, endpoint):
        """A 503 here, not a 403 -- the caller's token is perfectly valid.

        Degrading to a role failure would blame the caller for an outage they
        did not cause, and would push an operator toward rotating credentials
        instead of restarting auth-service.
        """
        endpoint.raises = ConnectionError("auth-service unreachable")

        with pytest.raises(HTTPException) as exc:
            await get_admin_identity(_request(_bearer(_mint(role="admin"))))

        assert exc.value.status_code == 503

    async def test_an_outage_still_401s_a_caller_with_no_credentials(self, endpoint):
        """No token means no JWKS fetch, so a missing header is still a plain 401.

        The 503 is for "we have a token and cannot check it", not for "there was
        nothing to check".
        """
        endpoint.raises = ConnectionError("auth-service unreachable")

        with pytest.raises(HTTPException) as exc:
            await get_required_identity(_request({}))

        assert exc.value.status_code == 401
        assert exc.value.detail["message"] == "Authentication required"

    async def test_the_outage_detail_carries_a_correlation_id(self, endpoint):
        """Consistent with the 401/403 detail shape, for log correlation."""
        from wildframe_observability.logging import correlation_id_var

        token = correlation_id_var.set("cid-outage")
        try:
            endpoint.raises = ConnectionError("auth-service unreachable")
            with pytest.raises(HTTPException) as exc:
                await verify_token(_request(_bearer(_mint())))
        finally:
            correlation_id_var.reset(token)

        assert exc.value.detail["correlation_id"] == "cid-outage"


class TestBadTokenIsStillAnonymous:
    """The pre-existing contract: a bad token is anonymous, never an exception."""

    async def test_a_garbage_token_is_anonymous(self, endpoint):
        assert await verify_token(_request(_bearer("not-a-jwt"))) is None

    async def test_an_expired_token_is_anonymous(self, endpoint):
        # -1h, not -60s: the shared verifier allows ~60s of clock skew, so a
        # token that expired "just now" is *meant* to pass.
        assert await verify_token(_request(_bearer(_mint(exp_offset=-3600)))) is None

    async def test_a_token_signed_by_another_key_is_anonymous(self, endpoint):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        intruder = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = intruder.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()

        assert await verify_token(_request(_bearer(_mint(private_pem=pem)))) is None

    async def test_a_wrong_audience_is_anonymous(self, endpoint):
        assert await verify_token(_request(_bearer(_mint(aud="somebody-else")))) is None

    async def test_a_wrong_issuer_is_anonymous(self, endpoint):
        assert await verify_token(_request(_bearer(_mint(iss="https://evil.test")))) is None

    async def test_an_unknown_kid_is_anonymous(self, endpoint):
        """The rotation refresh must not turn "unknown" into "accepted"."""
        assert await verify_token(_request(_bearer(_mint(kid="k-never-published")))) is None

    async def test_a_refresh_token_is_anonymous(self, endpoint):
        """Token-type separation (#221) survives the move to the shared verifier."""
        assert await verify_token(_request(_bearer(_mint(typ="refresh")))) is None

    async def test_a_token_without_a_sub_is_anonymous(self, endpoint):
        """``sub`` is a required claim, so the ``user_id`` fallback is unreachable."""
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "user_id": str(uuid.uuid4()),
                "type": "access",
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
                "arv": 0,
                "av": 0,
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=15)).timestamp()),
            },
            PRIVATE_PEM,
            algorithm="RS256",
            headers={"kid": "k1"},
        )

        assert await verify_token(_request(_bearer(token))) is None

    async def test_a_non_uuid_subject_is_anonymous(self, endpoint):
        """A present-but-unusable ``sub`` stays anonymous, as before."""
        assert await verify_token(_request(_bearer(_mint(sub="not-a-uuid")))) is None

    async def test_a_non_integer_arv_is_anonymous(self, endpoint):
        """The ``arv`` cast must not raise out of the identity builder."""
        assert await verify_token(_request(_bearer(_mint(arv="not-a-number")))) is None

    @pytest.mark.parametrize("header", ["Basic abc", "Bearer", "token abc", "Bearer "])
    async def test_non_bearer_schemes_are_anonymous_without_touching_the_network(
        self, endpoint, header
    ):
        assert await verify_token(_request({"Authorization": header})) is None
        assert endpoint.fetches == 0, "a non-bearer scheme must not cost a JWKS fetch"

    def test_a_bad_token_is_a_jwt_error_not_an_outage(self):
        """The 503 branch is keyed on ``JWKSUnavailableError``, not on ``JWTError``."""
        from wildframe_auth import JWKSUnavailableError, UnknownKidError

        assert issubclass(UnknownKidError, JWTError)
        assert not issubclass(UnknownKidError, JWKSUnavailableError)
        assert issubclass(JWKSUnavailableError, JWTError)


# ---------------------------------------------------------------------------
# Authorization is unchanged: who is allowed did not change, only how the
# signature is checked.
# ---------------------------------------------------------------------------


class TestRoleChecksStillApply:
    async def test_admin_is_still_admin(self, endpoint):
        sub = str(uuid.uuid4())

        identity = await get_admin_identity(_request(_bearer(_mint(sub=sub, role="admin"))))

        assert identity.user_id == UUID(sub)
        assert identity.is_admin is True

    async def test_user_is_still_403_on_the_admin_guard(self, endpoint):
        """A *valid* signature is not a pass: a non-admin is still forbidden."""
        with pytest.raises(HTTPException) as exc:
            await get_admin_identity(_request(_bearer(_mint(role="user"))))

        assert exc.value.status_code == 403
        assert exc.value.detail["message"] == "Administrator privileges required"

    async def test_a_stale_admin_role_version_is_still_403(self, endpoint, monkeypatch):
        """#81/#101 revocation stays immediate: an old ``arv`` grants nothing."""
        monkeypatch.setattr(settings, "ADMIN_ROLE_VERSION", 7)

        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "type": "access",
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
                "role": "admin",
                "arv": 0,
                "av": 0,
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=15)).timestamp()),
            },
            PRIVATE_PEM,
            algorithm="RS256",
            headers={"kid": "k1"},
        )

        with pytest.raises(HTTPException) as exc:
            await get_admin_identity(_request(_bearer(token)))

        assert exc.value.status_code == 403
        assert "role version changed" in exc.value.detail["message"]

    async def test_a_current_admin_role_version_passes(self, endpoint, monkeypatch):
        monkeypatch.setattr(settings, "ADMIN_ROLE_VERSION", 0)

        identity = await get_admin_identity(_request(_bearer(_mint(role="admin", arv=0))))

        assert identity.is_admin is True

    async def test_user_is_still_a_user_on_the_optional_guard(self, endpoint):
        sub = str(uuid.uuid4())

        identity = await get_optional_identity(_request(_bearer(_mint(sub=sub))))

        assert identity.user_id == UUID(sub)
        assert identity.is_admin is False
