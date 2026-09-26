"""Behavioural tests for the shared ``wildframe_auth`` JWKS/JWT verifier.

``wildframe_auth`` is a real dependency of auth-service and billing-service
(it is the token boundary for both), so the happy path, every rejection
reason, and the JWKS cache contract are all exercised here with real
RSA-2048 signatures — no mocked crypto.

The package lives at ``packages/sdk/wildframe_auth/wildframe_auth`` and is
*not* covered by a ``.pth`` file (unlike wildframe_events /
wildframe_observability), so the distribution root is put on ``sys.path``
here to make ``wildframe_auth.verifier`` importable.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from jose.exceptions import ExpiredSignatureError

# ---------------------------------------------------------------------------
# Make ``wildframe_auth.verifier`` importable (no .pth entry for it).
# ---------------------------------------------------------------------------
_SDK = Path(__file__).resolve().parents[1]
_AUTH_ROOT = _SDK / "wildframe_auth"
if str(_AUTH_ROOT) not in sys.path:
    sys.path.insert(0, str(_AUTH_ROOT))

from wildframe_auth.verifier import (  # noqa: E402
    ALLOWED_ALGORITHMS,
    REQUIRED_CLAIMS,
    clear_jwks_cache,
    fetch_jwks,
    fetch_jwks_sync,
    get_cached_jwks,
    get_jwk_for_kid,
    load_jwks_from_dict,
    verify_token,
)

AUDIENCE = "wildframe-api"
ISSUER = "https://auth.wildframe.test"


# ---------------------------------------------------------------------------
# Real RSA-2048 keypair factory (no signing shortcuts — real signatures).
# ---------------------------------------------------------------------------


def _b64u(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def gen_keypair(kid: str) -> dict[str, Any]:
    """Return ``{"jwk", "jwks", "private_pem"}`` for a fresh RSA-2048 key."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    numbers = private.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _b64u(numbers.n),
        "e": _b64u(numbers.e),
    }
    return {"jwk": jwk, "jwks": {"keys": [jwk]}, "private_pem": private_pem}


def make_token(
    kp: dict[str, Any],
    *,
    kid: str | None = None,
    alg: str = "RS256",
    typ: str = "access",
    lifetime: int = 300,
    issuer: str = ISSUER,
    audience: Any = AUDIENCE,
    drop: tuple = (),
    overrides: dict[str, Any] | None = None,
) -> str:
    """Mint a signed JWT. ``drop`` removes required claims to exercise gaps."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": "user-123",
        "av": 1,
        "type": typ,
        "iat": now,
        "exp": now + lifetime,
    }
    if overrides:
        claims.update(overrides)
    for key in drop:
        claims.pop(key, None)
    headers = {"kid": kid if kid is not None else kp["jwk"]["kid"]}
    return jwt.encode(claims, kp["private_pem"], algorithm=alg, headers=headers)


def _unsigned_token(claims: dict[str, Any], alg: str = "none") -> str:
    """Hand-build a JWS compact token with an unsigned body (alg=none / HS*).

    python-jose refuses to *produce* these, which is exactly why the verifier
    must reject them itself.
    """

    def seg(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{seg({'alg': alg, 'typ': 'JWT'})}.{seg(claims)}."


@pytest.fixture(autouse=True)
def _reset_cache():
    """The JWKS cache is module-global; keep tests independent."""
    clear_jwks_cache()
    yield
    clear_jwks_cache()


@pytest.fixture(scope="module")
def kp() -> dict[str, Any]:
    return gen_keypair("kid-primary")


# ---------------------------------------------------------------------------
# Module-level policy constants
# ---------------------------------------------------------------------------


class TestPolicyConstants:
    def test_only_rs256_allowed(self):
        # Pin the security posture: no symmetric (HS*) or None-alg fallback.
        assert ALLOWED_ALGORITHMS == {"RS256"}

    def test_required_claims_include_security_claims(self):
        assert {"iss", "aud", "sub", "exp", "iat", "type"} <= REQUIRED_CLAIMS


# ---------------------------------------------------------------------------
# get_jwk_for_kid
# ---------------------------------------------------------------------------


class TestGetJwkForKid:
    def test_finds_matching_kid(self, kp):
        assert get_jwk_for_kid(kp["jwks"], "kid-primary") == kp["jwk"]

    def test_returns_none_for_unknown_kid(self, kp):
        assert get_jwk_for_kid(kp["jwks"], "kid-nope") is None

    def test_returns_none_for_empty_keys_list(self):
        assert get_jwk_for_kid({"keys": []}, "kid-primary") is None

    def test_returns_none_when_keys_absent(self):
        assert get_jwk_for_kid({}, "kid-primary") is None

    def test_returns_none_when_keys_null(self):
        # `jwks.get("keys") or []` — a null keys field must not blow up.
        assert get_jwk_for_kid({"keys": None}, "kid-primary") is None

    def test_selects_the_right_key_from_a_rotation_set(self):
        old = gen_keypair("kid-old")
        new = gen_keypair("kid-new")
        jwks = {"keys": [old["jwk"], new["jwk"]]}
        assert get_jwk_for_kid(jwks, "kid-new")["kid"] == "kid-new"

    def test_key_without_kid_field_never_matches(self):
        anon = {"kty": "RSA", "alg": "RS256"}
        assert get_jwk_for_kid({"keys": [anon]}, "kid-primary") is None


# ---------------------------------------------------------------------------
# verify_token — happy path
# ---------------------------------------------------------------------------


class TestVerifyTokenHappyPath:
    def test_returns_claims_for_valid_token(self, kp):
        token = make_token(kp)
        payload = verify_token(token, kp["jwks"], AUDIENCE, ISSUER)
        assert payload["sub"] == "user-123"
        assert payload["iss"] == ISSUER
        assert payload["aud"] == AUDIENCE
        assert payload["type"] == "access"

    def test_claims_survive_extra_custom_fields(self, kp):
        token = make_token(kp, overrides={"roles": ["admin"], "content_id": "c-1"})
        payload = verify_token(token, kp["jwks"], AUDIENCE, ISSUER)
        assert payload["roles"] == ["admin"]
        assert payload["content_id"] == "c-1"

    def test_accepts_a_token_from_the_previous_key_during_rotation(self):
        old = gen_keypair("kid-old")
        new = gen_keypair("kid-new")
        jwks = {"keys": [old["jwk"], new["jwk"]]}
        token = make_token(old, kid="kid-old")
        assert verify_token(token, jwks, AUDIENCE, ISSUER)["sub"] == "user-123"

    def test_expected_type_can_be_overridden(self, kp):
        token = make_token(kp, typ="refresh")
        payload = verify_token(token, kp["jwks"], AUDIENCE, ISSUER, expected_type="refresh")
        assert payload["type"] == "refresh"

    def test_leeway_tolerates_a_just_expired_token(self, kp):
        token = make_token(kp, lifetime=-30)  # expired 30s ago
        # Default leeway is 60s, so a 30s-stale token is still inside the window.
        payload = verify_token(token, kp["jwks"], AUDIENCE, ISSUER)
        assert payload["sub"] == "user-123"

    def test_uses_the_leeway_argument(self, kp):
        token = make_token(kp, lifetime=-30)
        with pytest.raises(ExpiredSignatureError):
            verify_token(token, kp["jwks"], AUDIENCE, ISSUER, leeway=0)

    def test_jwk_without_alg_field_is_accepted(self, kp):
        # Only `jwk["alg"]` *if present* is policed; absent means "trust the header alg".
        jwks = {"keys": [{k: v for k, v in kp["jwk"].items() if k != "alg"}]}
        assert verify_token(make_token(kp), jwks, AUDIENCE, ISSUER)["sub"] == "user-123"


# ---------------------------------------------------------------------------
# verify_token — rejections
# ---------------------------------------------------------------------------


class TestVerifyTokenRejections:
    def test_garbage_token_reports_invalid_header(self):
        with pytest.raises(Exception, match="invalid header"):
            verify_token("not-a-jwt", {"keys": []}, AUDIENCE, ISSUER)

    def test_empty_string_token_reports_invalid_header(self):
        with pytest.raises(Exception, match="invalid header"):
            verify_token("", {"keys": []}, AUDIENCE, ISSUER)

    def test_wrong_issuer_rejected(self, kp):
        token = make_token(kp, issuer="https://evil.test")
        with pytest.raises(Exception, match="issuer"):
            verify_token(token, kp["jwks"], AUDIENCE, ISSUER)

    def test_wrong_audience_rejected(self, kp):
        token = make_token(kp, audience="someone-else")
        with pytest.raises(Exception, match="audience"):
            verify_token(token, kp["jwks"], AUDIENCE, ISSUER)

    def test_wrong_audience_is_not_accepted_when_token_aud_is_a_list(self, kp):
        token = make_token(kp, audience=["wildframe-api", "other"])
        # audience= must still match one of the listed audiences.
        assert verify_token(token, kp["jwks"], AUDIENCE, ISSUER)["sub"] == "user-123"
        with pytest.raises(Exception, match="audience"):
            verify_token(token, kp["jwks"], "third-party", ISSUER)

    def test_token_signed_by_a_different_key_is_rejected(self):
        trusted = gen_keypair("kid-primary")
        attacker = gen_keypair("kid-primary")  # same kid, different material
        token = make_token(attacker)
        with pytest.raises(Exception) as exc:
            verify_token(token, trusted["jwks"], AUDIENCE, ISSUER)
        assert "signature" in str(exc.value).lower()

    def test_expired_token_raises_expired_signature_error(self, kp):
        token = make_token(kp, lifetime=-10_000)
        with pytest.raises(ExpiredSignatureError):
            verify_token(token, kp["jwks"], AUDIENCE, ISSUER, leeway=0)

    def test_expired_error_is_preserved_not_wrapped(self, kp):
        """ExpiredSignatureError must propagate unchanged (callers match on it)."""
        token = make_token(kp, lifetime=-10_000)
        with pytest.raises(ExpiredSignatureError) as exc:
            verify_token(token, kp["jwks"], AUDIENCE, ISSUER, leeway=0)
        assert "invalid header" not in str(exc.value)

    def test_wrong_algorithm_rejected_before_key_lookup(self, kp):
        # HS256 offered while only RS256 is allowed: rejected on the *header*,
        # so a matching kid must not let it through (algorithm-confusion guard).
        secret = "a-shared-secret-that-should-never-be-accepted"
        token = jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "u", "type": "access",
             "exp": int(time.time()) + 300},
            secret,
            algorithm="HS256",
            headers={"kid": kp["jwk"]["kid"]},
        )
        with pytest.raises(Exception, match="unsupported alg HS256"):
            verify_token(token, kp["jwks"], AUDIENCE, ISSUER)

    def test_alg_none_rejected(self):
        # python-jose refuses to *mint* alg=none, so the token is hand-built
        # the way an attacker would: header says "none", body unsigned.
        token = _unsigned_token({"iss": ISSUER, "aud": AUDIENCE, "sub": "u", "type": "access"})
        with pytest.raises(Exception, match="unsupported alg none"):
            verify_token(token, {"keys": []}, AUDIENCE, ISSUER)

    def test_missing_kid_rejected(self, kp):
        token = make_token(kp, kid="")
        with pytest.raises(Exception, match="missing kid"):
            verify_token(token, kp["jwks"], AUDIENCE, ISSUER)

    def test_unknown_kid_rejected(self, kp):
        token = make_token(kp, kid="kid-not-published")
        with pytest.raises(Exception, match="unknown kid kid-not-published"):
            verify_token(token, kp["jwks"], AUDIENCE, ISSUER)

    def test_jwk_with_disallowed_alg_rejected(self, kp):
        poisoned = dict(kp["jwk"], alg="HS256")
        token = make_token(kp)
        with pytest.raises(Exception, match="jwk alg not allowed HS256"):
            verify_token(token, {"keys": [poisoned]}, AUDIENCE, ISSUER)

    def test_wrong_type_claim_rejected(self, kp):
        token = make_token(kp, typ="refresh")
        with pytest.raises(Exception, match="invalid type expected access"):
            verify_token(token, kp["jwks"], AUDIENCE, ISSUER)

    def test_missing_type_claim_rejected(self, kp):
        token = make_token(kp, drop=("type",))
        with pytest.raises(Exception, match="missing required claims"):
            verify_token(token, kp["jwks"], AUDIENCE, ISSUER)

    def test_missing_required_claims_rejected(self, kp):
        for claim in ("aud", "sub", "iat", "exp", "iss"):
            token = make_token(kp, drop=(claim,))
            with pytest.raises(Exception):
                verify_token(token, kp["jwks"], AUDIENCE, ISSUER)

    def test_access_requires_integer_auth_version(self, kp):
        for value in (None, "1", 1.5, True):
            overrides = {} if value is None else {"av": value}
            token = make_token(kp, overrides=overrides, drop=("av",) if value is None else ())
            with pytest.raises(Exception, match="auth version"):
                verify_token(token, kp["jwks"], AUDIENCE, ISSUER)

    def test_missing_issuer_rejected(self, kp):
        token = make_token(kp, drop=("iss",))
        with pytest.raises(Exception, match="[Ii]ssuer"):
            verify_token(token, kp["jwks"], AUDIENCE, ISSUER)

    def test_missing_audience_and_supplied_audience_is_accepted(self, kp):
        token = make_token(kp, drop=("aud",))
        with pytest.raises(Exception):
            verify_token(token, kp["jwks"], AUDIENCE, ISSUER)

    def test_audience_mismatch_is_rejected_when_claim_present(self, kp):
        """The audience restriction IS enforced for tokens that carry aud."""
        token = make_token(kp, audience="someone-else")
        with pytest.raises(Exception, match="[Aa]udience"):
            verify_token(token, kp["jwks"], AUDIENCE, ISSUER)

    def test_audience_claim_wrong_type_rejected(self, kp):
        token = make_token(kp, audience=123)
        with pytest.raises(Exception, match="Invalid claim format"):
            verify_token(token, kp["jwks"], AUDIENCE, ISSUER)

    def test_audience_claim_list_with_non_string_rejected(self, kp):
        token = make_token(kp, audience=["wildframe-api", 7])
        with pytest.raises(Exception, match="Invalid claim format"):
            verify_token(token, kp["jwks"], AUDIENCE, ISSUER)


# ---------------------------------------------------------------------------
# load_jwks_from_dict
# ---------------------------------------------------------------------------


class TestLoadJwksFromDict:
    def test_returns_the_same_mapping(self, kp):
        assert load_jwks_from_dict(kp["jwks"]) is kp["jwks"]

    def test_does_not_strip_or_rewrite_keys(self):
        jwks = {"keys": [{"kid": "a", "alg": "RS256", "extra": 1}], "other": True}
        assert load_jwks_from_dict(jwks) == jwks

    def test_empty_jwks_passes_through(self):
        assert load_jwks_from_dict({}) == {}


# ---------------------------------------------------------------------------
# fetch_jwks / fetch_jwks_sync — HTTP is mocked, shapes are asserted
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.raised = False

    def raise_for_status(self) -> None:
        self.raised = True
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse, recorder: dict, timeout: float) -> None:
        self._response = response
        self._recorder = recorder
        self._timeout = timeout

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def get(self, url: str) -> _FakeResponse:
        self._recorder["timeout"] = self._timeout
        self._recorder["url"] = url
        return self._response


class _FakeSyncClient:
    def __init__(self, response: _FakeResponse, recorder: dict, timeout: float) -> None:
        self._response = response
        self._recorder = recorder
        self._timeout = timeout

    def __enter__(self) -> "_FakeSyncClient":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def get(self, url: str) -> _FakeResponse:
        self._recorder["timeout"] = self._timeout
        self._recorder["url"] = url
        return self._response


class TestFetchJwks:
    @pytest.mark.asyncio
    async def test_async_fetch_returns_parsed_body(self, monkeypatch, kp):
        import httpx

        recorder: dict = {}
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda timeout: _FakeAsyncClient(_FakeResponse(kp["jwks"]), recorder, timeout),
        )
        out = await fetch_jwks("https://auth.test/.well-known/jwks.json")
        assert out == kp["jwks"]
        assert recorder["url"] == "https://auth.test/.well-known/jwks.json"

    @pytest.mark.asyncio
    async def test_async_fetch_passes_the_timeout_through(self, monkeypatch, kp):
        import httpx

        recorder: dict = {}
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda timeout: _FakeAsyncClient(_FakeResponse(kp["jwks"]), recorder, timeout),
        )
        await fetch_jwks("https://auth.test/jwks.json", timeout=1.25)
        assert recorder["timeout"] == 1.25

    @pytest.mark.asyncio
    async def test_async_fetch_default_timeout_is_five_seconds(self, monkeypatch, kp):
        import httpx

        recorder: dict = {}
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda timeout: _FakeAsyncClient(_FakeResponse(kp["jwks"]), recorder, timeout),
        )
        await fetch_jwks("https://auth.test/jwks.json")
        assert recorder["timeout"] == 5.0

    @pytest.mark.asyncio
    async def test_async_fetch_propagates_http_error(self, monkeypatch):
        import httpx

        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda timeout: _FakeAsyncClient(_FakeResponse({}, status=503), {}, timeout),
        )
        with pytest.raises(RuntimeError, match="HTTP 503"):
            await fetch_jwks("https://auth.test/jwks.json")

    @pytest.mark.asyncio
    async def test_async_fetch_propagates_connection_error(self, monkeypatch):
        import httpx

        def _boom(timeout):
            raise OSError("connection refused")

        monkeypatch.setattr(httpx, "AsyncClient", _boom)
        with pytest.raises(OSError, match="connection refused"):
            await fetch_jwks("https://auth.test/jwks.json")

    def test_sync_fetch_returns_parsed_body(self, monkeypatch, kp):
        import httpx

        recorder: dict = {}
        monkeypatch.setattr(
            httpx, "Client",
            lambda timeout: _FakeSyncClient(_FakeResponse(kp["jwks"]), recorder, timeout),
        )
        out = fetch_jwks_sync("https://auth.test/.well-known/jwks.json", timeout=2.0)
        assert out == kp["jwks"]
        assert recorder == {"url": "https://auth.test/.well-known/jwks.json", "timeout": 2.0}

    def test_sync_fetch_propagates_http_error(self, monkeypatch):
        import httpx

        monkeypatch.setattr(
            httpx, "Client", lambda timeout: _FakeSyncClient(_FakeResponse({}, 404), {}, timeout)
        )
        with pytest.raises(RuntimeError, match="HTTP 404"):
            fetch_jwks_sync("https://auth.test/jwks.json")

    def test_sync_fetch_propagates_connection_error(self, monkeypatch):
        import httpx

        def _boom(timeout):
            raise OSError("name resolution failed")

        monkeypatch.setattr(httpx, "Client", _boom)
        with pytest.raises(OSError, match="name resolution failed"):
            fetch_jwks_sync("https://auth.test/jwks.json")


# ---------------------------------------------------------------------------
# get_cached_jwks — cache hit / miss / TTL / clear
# ---------------------------------------------------------------------------


class TestGetCachedJwks:
    @pytest.fixture
    def counted(self, monkeypatch):
        """Patch fetch_jwks with a call counter; returns the counter dict."""
        import wildframe_auth.verifier as mod

        calls: list[str] = []

        async def _fake(url: str) -> dict:
            calls.append(url)
            return {"keys": [{"kid": f"k{len(calls)}"}]}

        monkeypatch.setattr(mod, "fetch_jwks", _fake)
        return calls

    @pytest.mark.asyncio
    async def test_first_call_misses_and_fetches(self, counted):
        out = await get_cached_jwks("https://auth.test/jwks.json")
        assert out == {"keys": [{"kid": "k1"}]}
        assert counted == ["https://auth.test/jwks.json"]

    @pytest.mark.asyncio
    async def test_second_call_is_a_cache_hit(self, counted):
        first = await get_cached_jwks("https://auth.test/jwks.json")
        second = await get_cached_jwks("https://auth.test/jwks.json")
        assert first is second  # identical object, not a refetch
        assert len(counted) == 1

    @pytest.mark.asyncio
    async def test_many_hits_still_one_fetch(self, counted):
        for _ in range(5):
            await get_cached_jwks("https://auth.test/jwks.json")
        assert len(counted) == 1

    @pytest.mark.asyncio
    async def test_different_url_is_a_miss(self, counted):
        await get_cached_jwks("https://auth.test/a.json")
        await get_cached_jwks("https://auth.test/b.json")
        assert counted == ["https://auth.test/a.json", "https://auth.test/b.json"]

    @pytest.mark.asyncio
    async def test_shorter_ttl_does_not_shorten_an_existing_entry(self, counted):
        """FINDING (verifier.py:89-98): ``ttl`` is only read when *storing*
        the expiry, never when checking it, so passing a shorter/zero ttl for
        an already-cached URL is ignored. Pinned as-is.
        """
        await get_cached_jwks("https://auth.test/jwks.json", ttl=300)
        await get_cached_jwks("https://auth.test/jwks.json", ttl=0)
        assert len(counted) == 1  # ttl=0 did NOT evict

    @pytest.mark.asyncio
    async def test_ttl_bounds_the_cache_window(self, counted, monkeypatch):
        import wildframe_auth.verifier as mod

        now = [1_000.0]
        monkeypatch.setattr(mod.time, "time", lambda: now[0])
        await get_cached_jwks("https://auth.test/jwks.json", ttl=300)
        assert len(counted) == 1

        now[0] = 1_000.0 + 299  # still inside the window
        await get_cached_jwks("https://auth.test/jwks.json", ttl=300)
        assert len(counted) == 1

        now[0] = 1_000.0 + 301  # past the window
        await get_cached_jwks("https://auth.test/jwks.json", ttl=300)
        assert len(counted) == 2

    @pytest.mark.asyncio
    async def test_failed_fetch_does_not_poison_the_cache(self, monkeypatch):
        import wildframe_auth.verifier as mod

        attempts = {"n": 0}

        async def _flaky(url: str) -> dict:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise OSError("jwks endpoint unreachable")
            return {"keys": [{"kid": "recovered"}]}

        monkeypatch.setattr(mod, "fetch_jwks", _flaky)

        with pytest.raises(OSError, match="unreachable"):
            await get_cached_jwks("https://auth.test/jwks.json")

        # Nothing was cached, so the next attempt really hits the network again.
        assert await get_cached_jwks("https://auth.test/jwks.json") == {
            "keys": [{"kid": "recovered"}]
        }
        assert attempts["n"] == 2

    @pytest.mark.asyncio
    async def test_unreachable_endpoint_raises_and_leaves_cache_empty(self, monkeypatch):
        import wildframe_auth.verifier as mod

        async def _down(url: str) -> dict:
            raise OSError("jwks endpoint unreachable")

        monkeypatch.setattr(mod, "fetch_jwks", _down)
        with pytest.raises(OSError, match="unreachable"):
            await get_cached_jwks("https://auth.test/jwks.json")
        assert mod._jwks_cache is None

    @pytest.mark.asyncio
    async def test_end_to_end_cached_jwks_verifies_a_token(self, monkeypatch, kp):
        """The real integration shape: cache the JWKS, then verify against it."""
        import wildframe_auth.verifier as mod

        calls = {"n": 0}

        async def _fetch(url: str) -> dict:
            calls["n"] += 1
            return kp["jwks"]

        monkeypatch.setattr(mod, "fetch_jwks", _fetch)
        token = make_token(kp)

        for _ in range(3):
            jwks = await get_cached_jwks("https://auth.test/jwks.json")
            assert verify_token(token, jwks, AUDIENCE, ISSUER)["sub"] == "user-123"

        assert calls["n"] == 1  # one fetch, three verifications

        clear_jwks_cache()
        await get_cached_jwks("https://auth.test/jwks.json")
        assert calls["n"] == 2  # cleared -> refetched


# ---------------------------------------------------------------------------
# clear_jwks_cache
# ---------------------------------------------------------------------------


class TestClearJwksCache:
    @pytest.mark.asyncio
    async def test_clear_forces_a_refetch(self, monkeypatch):
        import wildframe_auth.verifier as mod

        calls = {"n": 0}

        async def _fetch(url: str) -> dict:
            calls["n"] += 1
            return {"keys": [{"kid": f"k{calls['n']}"}]}

        monkeypatch.setattr(mod, "fetch_jwks", _fetch)

        first = await get_cached_jwks("https://auth.test/jwks.json")
        assert (await get_cached_jwks("https://auth.test/jwks.json")) is first
        assert calls["n"] == 1

        clear_jwks_cache()

        second = await get_cached_jwks("https://auth.test/jwks.json")
        assert second is not first
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_clear_resets_all_three_cache_slots(self, monkeypatch):
        import wildframe_auth.verifier as mod

        async def _fetch(url: str) -> dict:
            return {"keys": []}

        monkeypatch.setattr(mod, "fetch_jwks", _fetch)
        await get_cached_jwks("https://auth.test/jwks.json", ttl=10_000)
        assert mod._jwks_cache is not None
        assert mod._jwks_cache_expiry > 0
        assert mod._jwks_cache_url == "https://auth.test/jwks.json"

        clear_jwks_cache()

        assert mod._jwks_cache is None
        assert mod._jwks_cache_expiry == 0
        assert mod._jwks_cache_url is None

    def test_clear_is_idempotent(self):
        clear_jwks_cache()
        clear_jwks_cache()
        import wildframe_auth.verifier as mod

        assert mod._jwks_cache is None
