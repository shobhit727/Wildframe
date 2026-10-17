"""Security-hardening tests for the ``wildframe_auth`` JWKS verifier.

Covers the four defects an independent review found in the shared verifier:

1. ``verify_token_with_jwks`` existed but no production caller used it, so the
   key-rotation fix never reached admin-service or streaming-service.
2. An unauthenticated random-``kid`` flood could force one outbound JWKS fetch
   per request. ``UnknownKidError`` is raised *before* any signature check and
   is gated only on attacker-chosen ``alg``/``kid`` header values, so no valid
   signature is needed to reach the forced-refetch path. Fixed with
   single-flighted fetches plus a per-URL negative cache.
3. A failed forced refetch escaped as a bare transport error, which a caller's
   ``except JWTError`` does not catch, so a JWKS outage surfaced as a 500
   rather than a 503.
4. A malformed JWKS body was cached unvalidated and poisoned the cache for the
   whole TTL: a self-sustaining 500 instead of a clean 503.

Everything here runs real crypto -- RSA-2048 keypairs, real
``jwt.encode(..., algorithm="RS256")`` signatures, real ``asyncio``
concurrency against the real single-flight code. Only the outbound HTTP call is
substituted, and it is substituted with a *real JWKS document*, so the verifier
keeps doing genuine signature verification throughout.
"""

from __future__ import annotations

import asyncio
import base64
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import JWTError, jwt

# ---------------------------------------------------------------------------
# Make ``wildframe_auth.verifier`` importable (no .pth entry for it).
# ---------------------------------------------------------------------------
_SDK = Path(__file__).resolve().parents[1]
_AUTH_ROOT = _SDK / "wildframe_auth"
if str(_AUTH_ROOT) not in sys.path:
    sys.path.insert(0, str(_AUTH_ROOT))

import wildframe_auth.verifier as mod  # noqa: E402
from wildframe_auth import (  # noqa: E402
    InvalidJWKSError,
    JWKSUnavailableError,
    UnknownKidError,
    verify_token_with_jwks,
)
from wildframe_auth.verifier import (  # noqa: E402
    MAX_TRACKED_UNKNOWN_KIDS,
    UNKNOWN_KID_BACKOFF_SECONDS,
    clear_jwks_cache,
    get_cached_jwks,
    verify_token,
)

AUDIENCE = "wildframe-api"
ISSUER = "https://auth.wildframe.test"
URL = "https://auth.wildframe.test/.well-known/jwks.json"
OTHER_URL = "https://auth2.wildframe.test/.well-known/jwks.json"


def _b64u(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def gen_keypair(kid: str) -> dict[str, Any]:
    """A real RSA-2048 keypair published as a real JWK."""
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
    token_type: str = "access",
    sub: str = "user-123",
) -> str:
    # Real wall-clock, not the fake clock: several tests freeze
    # ``verifier.time.time`` to open a backoff window, and the expiry check
    # inside python-jose runs against real time, so a token minted from the fake
    # clock would look expired.
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": sub,
        "av": 1,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    return jwt.encode(
        claims, kp["private_pem"], algorithm="RS256", headers={"kid": kid or kp["jwk"]["kid"]}
    )


class _Clock:
    """A fake ``time.time`` so TTL and backoff windows are exact, not slept for."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _JwksEndpoint:
    """A stand-in for the auth service's JWKS endpoint.

    The published key set can be rotated mid-test, the fetch can be made to
    fail or to be slow, and every outbound call is recorded so a test can
    assert the egress bound.
    """

    def __init__(self) -> None:
        self.jwks: dict = {"keys": []}
        self.calls: list[str] = []
        self.delay: float = 0.0
        self.body: Any = None  # when set, served raw instead of ``self.jwks``
        self.fail: bool = False

    def publish(self, *jwk: dict) -> None:
        """Rotate the published key set (what auth-service does on rotation)."""
        self.jwks = {"keys": list(jwk)}

    @property
    def fetches(self) -> int:
        return len(self.calls)

    async def fetch(self, url: str) -> Any:
        self.calls.append(url)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise ConnectionError("jwks endpoint unreachable")
        if self.body is not None:
            return self.body
        return self.jwks


@pytest.fixture
def endpoint(monkeypatch) -> _JwksEndpoint:
    """Install a fake JWKS endpoint behind the real ``fetch_jwks`` seam."""
    ep = _JwksEndpoint()
    monkeypatch.setattr(mod, "fetch_jwks", ep.fetch)
    return ep


@pytest.fixture(autouse=True)
def _reset_state():
    """The cache, the per-URL locks and the backoff bookkeeping are global."""
    clear_jwks_cache()
    yield
    clear_jwks_cache()


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(mod.time, "time", fake)
    return fake


@pytest.fixture(scope="module")
def issuer() -> dict[str, Any]:
    return gen_keypair("kid-issuer")


@pytest.fixture(scope="module")
def rotated() -> dict[str, Any]:
    return gen_keypair("kid-rotated")


@pytest.fixture(scope="module")
def attacker() -> dict[str, Any]:
    """An attacker-controlled keypair: real RSA-2048 material they own."""
    return gen_keypair("kid-attacker")


@pytest.fixture(scope="module")
def any_token() -> str:
    """A correctly shaped token from a throwaway key, for pre-parse failures."""
    return make_token(gen_keypair("kid-throwaway"))


async def _verify(token: str, url: str = URL, **kwargs) -> dict[str, Any]:
    return await verify_token_with_jwks(token, audience=AUDIENCE, issuer=ISSUER, url=url, **kwargs)


# ---------------------------------------------------------------------------
# Finding 1 -- the public entry point the services call
# ---------------------------------------------------------------------------


class TestVerifyTokenWithJwks:
    async def test_verifies_a_token_against_the_fetched_jwks(self, endpoint, issuer):
        endpoint.publish(issuer["jwk"])
        payload = await _verify(make_token(issuer))
        assert payload["sub"] == "user-123"
        assert payload["type"] == "access"
        assert endpoint.calls == [URL]

    async def test_repeat_verifications_hit_the_cache(self, endpoint, issuer):
        endpoint.publish(issuer["jwk"])
        for _ in range(5):
            assert (await _verify(make_token(issuer)))["sub"] == "user-123"
        assert endpoint.fetches == 1

    async def test_a_bad_signature_is_rejected(self, endpoint, issuer, attacker):
        """Right kid, wrong key material: rejected on the signature, not the kid."""
        endpoint.publish(issuer["jwk"])
        with pytest.raises(JWTError):
            await _verify(make_token(attacker, kid="kid-issuer"))

    async def test_a_refresh_token_is_not_accepted_as_an_access_token(self, endpoint, issuer):
        endpoint.publish(issuer["jwk"])
        with pytest.raises(JWTError):
            await _verify(make_token(issuer, token_type="refresh"))

    async def test_the_step_up_type_is_accepted_when_asked_for(self, endpoint, issuer):
        endpoint.publish(issuer["jwk"])
        step_up = make_token(issuer, token_type="admin_step_up")
        assert (await _verify(step_up, expected_type="admin_step_up"))["type"] == "admin_step_up"

    async def test_a_wrong_audience_is_rejected(self, endpoint, issuer):
        endpoint.publish(issuer["jwk"])
        with pytest.raises(JWTError):
            await verify_token_with_jwks(
                make_token(issuer), audience="somebody-else", issuer=ISSUER, url=URL
            )


# ---------------------------------------------------------------------------
# Finding 2 -- unauthenticated forced-refetch amplification
# ---------------------------------------------------------------------------


class TestAmplificationBound:
    async def test_a_hundred_forged_kids_cost_one_fetch(self, endpoint, issuer, attacker):
        """100 unauthenticated requests, 100 distinct unknown kids.

        The verifier rejects on the *header* -- ``alg=RS256`` and a non-empty
        ``kid`` are attacker-chosen -- so none of these needs a valid
        signature. Before the fix every one of them drove its own outbound
        fetch (101 fetches including the initial population). The bound now is
        one population fetch plus at most one forced refetch for the whole
        flood: egress that does not scale with the attacker's request count.
        """
        endpoint.publish(issuer["jwk"])
        await _verify(make_token(issuer))
        assert endpoint.fetches == 1

        for attempt in range(100):
            with pytest.raises(UnknownKidError):
                await _verify(make_token(attacker, kid=f"forged-{attempt}"))

        assert endpoint.fetches == 2, "egress must not scale with forged requests"

    async def test_repeating_one_forged_kid_costs_one_fetch(self, endpoint, issuer, attacker):
        endpoint.publish(issuer["jwk"])
        await _verify(make_token(issuer))
        forged = make_token(attacker, kid="forged-repeated")
        for _ in range(50):
            with pytest.raises(UnknownKidError):
                await _verify(forged)
        assert endpoint.fetches == 2

    async def test_concurrent_forged_kids_collapse_to_one_fetch(self, endpoint, issuer, attacker):
        """50 simultaneous unknown kids must not become 50 simultaneous requests."""
        endpoint.publish(issuer["jwk"])
        await _verify(make_token(issuer))  # warm
        endpoint.delay = 0.02

        tokens = [make_token(attacker, kid=f"concurrent-{i}") for i in range(50)]
        results = await asyncio.gather(*(_verify(t) for t in tokens), return_exceptions=True)

        assert all(isinstance(r, UnknownKidError) for r in results)
        assert endpoint.fetches == 2

    async def test_concurrent_cold_cache_misses_collapse_to_one_fetch(self, endpoint, issuer):
        """The plain cache path is single-flighted too: 50 cold readers, 1 fetch."""
        endpoint.publish(issuer["jwk"])
        endpoint.delay = 0.02

        documents = await asyncio.gather(*(get_cached_jwks(URL) for _ in range(50)))

        assert all(doc == issuer["jwks"] for doc in documents)
        assert endpoint.fetches == 1

    async def test_concurrent_forced_refetches_collapse_to_one_fetch(
        self, endpoint, issuer, attacker
    ):
        """With the rate cap switched off, single-flight is the only thing left
        bounding egress -- 50 simultaneous forced refetches, one request."""
        endpoint.publish(issuer["jwk"])
        await _verify(make_token(issuer))  # warm
        endpoint.delay = 0.02

        tokens = [make_token(attacker, kid=f"forced-{i}") for i in range(50)]
        results = await asyncio.gather(
            *(_verify(t, unknown_kid_backoff=0) for t in tokens), return_exceptions=True
        )

        assert all(isinstance(r, UnknownKidError) for r in results)
        assert endpoint.fetches == 2, "concurrent forced refetches must share one request"

    async def test_unknown_kid_bookkeeping_is_bounded(self, clock):
        """Endless distinct kids must not grow the bookkeeping without bound.

        Driven through the real ``_remember_unknown_kid`` (the production
        bookkeeping call) with a frozen clock, so the cap is exercised
        deterministically rather than depending on how fast 500+ RSA
        verifications happen to run.
        """
        for attempt in range(MAX_TRACKED_UNKNOWN_KIDS + 50):
            mod._remember_unknown_kid(URL, f"kid-{attempt}", UNKNOWN_KID_BACKOFF_SECONDS)

        recorded = mod._unknown_kids[URL]
        assert len(recorded) == MAX_TRACKED_UNKNOWN_KIDS
        # Oldest-first eviction: the first 50 recorded kids are the ones dropped.
        assert "kid-0" not in recorded
        assert f"kid-{MAX_TRACKED_UNKNOWN_KIDS + 49}" in recorded

    async def test_a_genuine_rotation_succeeds_immediately(self, endpoint, issuer, rotated):
        """A brand-new ``kid`` with a valid signature must not wait anything out.

        Nothing about this request looks like an attack: the kid has never been
        seen, so its first sighting is allowed to force its own refetch and the
        token is accepted on the spot.
        """
        endpoint.publish(issuer["jwk"])
        assert (await _verify(make_token(issuer)))["sub"] == "user-123"

        endpoint.publish(issuer["jwk"], rotated["jwk"])  # auth-service rotates
        payload = await _verify(make_token(rotated))

        assert payload["sub"] == "user-123"
        assert endpoint.fetches == 2

    async def test_a_rotation_still_lands_after_the_backoff_window(
        self, endpoint, issuer, rotated, attacker, clock
    ):
        """The cap is a short window, not a permanent lock-out.

        A forged kid consumes the window, a later forged kid inside it buys
        nothing, and once the window elapses a real rotation is picked up.
        """
        endpoint.publish(issuer["jwk"])
        await _verify(make_token(issuer))

        with pytest.raises(UnknownKidError):
            await _verify(make_token(attacker, kid="forged-early"))
        assert endpoint.fetches == 2

        clock.advance(UNKNOWN_KID_BACKOFF_SECONDS - 1)
        with pytest.raises(UnknownKidError):
            await _verify(make_token(attacker, kid="forged-late"))
        assert endpoint.fetches == 2  # still inside the window: no egress

        endpoint.publish(issuer["jwk"], rotated["jwk"])
        clock.advance(2)
        assert (await _verify(make_token(rotated)))["sub"] == "user-123"
        assert endpoint.fetches == 3

    async def test_a_rotation_drops_kids_recorded_against_the_old_keys(
        self, endpoint, issuer, rotated, clock
    ):
        """A kid seen *before* it was published must not stay blocked.

        Its first sighting forced a refetch that still lacked it, so it was
        recorded. Once a later refetch does return it, the world has rotated
        and that record is stale evidence, so it is dropped.
        """
        endpoint.publish(issuer["jwk"])
        with pytest.raises(UnknownKidError):
            await _verify(make_token(rotated))
        assert mod._unknown_kids[URL].get("kid-rotated") is not None

        endpoint.publish(issuer["jwk"], rotated["jwk"])
        clock.advance(UNKNOWN_KID_BACKOFF_SECONDS + 1)
        assert (await _verify(make_token(rotated)))["sub"] == "user-123"
        assert mod._unknown_kids[URL] == {}

    async def test_an_expired_record_is_pruned_and_the_window_reopens(
        self, endpoint, issuer, attacker, clock
    ):
        endpoint.publish(issuer["jwk"])
        await _verify(make_token(issuer))
        with pytest.raises(UnknownKidError):
            await _verify(make_token(attacker, kid="forged-repeated"))
        first_deadline = mod._unknown_kids[URL]["forged-repeated"]
        assert first_deadline == clock() + UNKNOWN_KID_BACKOFF_SECONDS

        clock.advance(UNKNOWN_KID_BACKOFF_SECONDS + 1)
        with pytest.raises(UnknownKidError):
            await _verify(make_token(attacker, kid="forged-repeated"))

        # A fresh deadline, not the stale one: the expired entry was pruned.
        assert mod._unknown_kids[URL]["forged-repeated"] > first_deadline
        assert endpoint.fetches == 3  # the window really did reopen

    async def test_the_backoff_is_scoped_per_url(self, endpoint, issuer, attacker):
        """One endpoint being hammered must not suppress a refresh for another."""
        endpoint.publish(issuer["jwk"])
        await _verify(make_token(issuer), url=URL)
        with pytest.raises(UnknownKidError):
            await _verify(make_token(attacker, kid="forged"), url=URL)
        # A different URL carries no forced-refetch stamp, so it still refreshes.
        with pytest.raises(UnknownKidError):
            await _verify(make_token(attacker, kid="forged"), url=OTHER_URL)
        assert endpoint.calls.count(URL) == 2
        assert endpoint.calls.count(OTHER_URL) == 2

    async def test_a_zero_backoff_restores_one_refetch_per_request(
        self, endpoint, issuer, attacker
    ):
        """The cap is a parameter, and turning it off really does turn it off."""
        endpoint.publish(issuer["jwk"])
        await _verify(make_token(issuer))
        for attempt in range(3):
            with pytest.raises(UnknownKidError):
                await _verify(make_token(attacker, kid=f"forged-{attempt}"), unknown_kid_backoff=0)
        assert endpoint.fetches == 4

    def test_a_non_positive_backoff_records_and_gates_nothing(self):
        assert mod._unknown_kid_backoff_active(URL, "kid", 0) is False
        mod._jwks_forced_at[URL] = time.time()
        assert mod._unknown_kid_backoff_active(URL, "kid", -1) is False
        mod._remember_unknown_kid(URL, "kid", 0)
        assert mod._unknown_kids == {}
        clear_jwks_cache()


# ---------------------------------------------------------------------------
# Finding 3 -- a failed refetch must be catchable as a JWTError
# ---------------------------------------------------------------------------


class TestUnavailableSurfacesAsJwtError:
    async def test_the_initial_fetch_failure_is_jwks_unavailable(self, endpoint, any_token):
        endpoint.fail = True
        with pytest.raises(JWKSUnavailableError) as exc:
            await _verify(any_token)
        # The whole point: a caller's single ``except JWTError`` catches it.
        assert isinstance(exc.value, JWTError)
        assert isinstance(exc.value.__cause__, ConnectionError)

    async def test_a_refetch_failure_is_jwks_unavailable_not_a_bare_transport_error(
        self, endpoint, issuer, attacker
    ):
        """The rotation path must not leak an un-catchable transport error.

        Before the fix the forced refetch let the transport error propagate
        verbatim, so a service's ``except JWTError`` missed it and a JWKS
        outage during a rotation surfaced as a 500.
        """
        endpoint.publish(issuer["jwk"])
        await _verify(make_token(issuer))  # populates the cache

        endpoint.fail = True
        with pytest.raises(JWKSUnavailableError) as exc:
            await _verify(make_token(attacker, kid="forged"))

        assert isinstance(exc.value, JWTError)
        assert not isinstance(exc.value, UnknownKidError)
        assert isinstance(exc.value.__cause__, ConnectionError)

    async def test_a_bad_token_is_never_an_availability_error(self, endpoint, issuer, attacker):
        """The 401 path must not be polluted by the availability types."""
        endpoint.publish(issuer["jwk"])
        with pytest.raises(UnknownKidError) as exc:
            await _verify(make_token(attacker, kid="nope"))
        assert not isinstance(exc.value, JWKSUnavailableError)
        assert not isinstance(exc.value, InvalidJWKSError)


# ---------------------------------------------------------------------------
# Finding 4 -- a malformed JWKS must never be cached
# ---------------------------------------------------------------------------

MALFORMED_BODIES = [
    pytest.param(["not", "a", "jwks"], id="json-array"),
    pytest.param("<html>502 Bad Gateway</html>", id="html-string"),
    pytest.param({"no_keys_here": True}, id="missing-keys"),
    pytest.param({"keys": {"kid": "k1"}}, id="keys-not-a-list"),
    pytest.param({"keys": "k1"}, id="keys-string"),
    pytest.param({"keys": ["kid-1"]}, id="key-not-an-object"),
    pytest.param({"keys": [{"kid": "k1"}]}, id="key-without-kty"),
    pytest.param({"keys": [{"kty": "RSA"}, "kid-1"]}, id="mixed-valid-and-invalid"),
]


class TestJwksValidation:
    @pytest.mark.parametrize("body", MALFORMED_BODIES)
    async def test_a_malformed_body_is_rejected_before_it_is_cached(self, endpoint, body):
        endpoint.body = body
        with pytest.raises(InvalidJWKSError):
            await get_cached_jwks(URL)
        assert URL not in mod._jwks_cache
        assert URL not in mod._jwks_cache_expiry

    @pytest.mark.parametrize("body", MALFORMED_BODIES)
    async def test_a_malformed_body_does_not_become_self_sustaining(self, endpoint, body):
        """The old behaviour cached the poison for the whole TTL and re-cached
        the same poison on every refetch, so one broken endpoint stayed broken
        for 300s. Every attempt is now rejected and the cache stays empty."""
        endpoint.body = body
        for _ in range(3):
            with pytest.raises(InvalidJWKSError):
                await get_cached_jwks(URL)
        assert endpoint.fetches == 3
        assert mod._jwks_cache == {}

    @pytest.mark.parametrize("body", MALFORMED_BODIES)
    async def test_a_malformed_body_surfaces_as_jwks_unavailable(self, endpoint, body, any_token):
        """So a service's 503 branch catches it, instead of a 500."""
        endpoint.body = body
        with pytest.raises(JWKSUnavailableError):
            await _verify(any_token)

    async def test_a_malformed_refetch_leaves_the_warm_entry_usable(
        self, endpoint, issuer, rotated
    ):
        """A bad refresh must not destroy the last good document."""
        endpoint.publish(issuer["jwk"])
        await _verify(make_token(issuer))

        endpoint.body = {"keys": "not-a-list"}
        with pytest.raises(InvalidJWKSError):
            await _verify(make_token(rotated))  # unknown kid -> forced refetch

        cached = mod._jwks_cache[URL]
        assert cached == issuer["jwks"]
        assert verify_token(make_token(issuer), cached, AUDIENCE, ISSUER)["sub"] == "user-123"

    async def test_an_empty_key_set_is_accepted(self, endpoint, issuer):
        """Every key retired mid-rotation is legitimate, not a broken endpoint."""
        endpoint.publish()  # zero keys
        assert await get_cached_jwks(URL) == {"keys": []}
        with pytest.raises(UnknownKidError):
            await _verify(make_token(issuer))

    async def test_extra_top_level_fields_are_tolerated(self, endpoint, issuer):
        endpoint.body = {"keys": [issuer["jwk"]], "issuer": "https://auth", "ttl": 300}
        assert await get_cached_jwks(URL) == endpoint.body


# ---------------------------------------------------------------------------
# Finding 5 -- the cache is per-URL, and the TTL is stamped after the fetch
# ---------------------------------------------------------------------------


class TestPerUrlCache:
    async def test_each_url_keeps_its_own_entry(self, endpoint, issuer):
        endpoint.publish(issuer["jwk"])
        await get_cached_jwks(URL)
        await get_cached_jwks(OTHER_URL)
        assert endpoint.calls == [URL, OTHER_URL]

        assert await get_cached_jwks(URL) == issuer["jwks"]
        assert await get_cached_jwks(OTHER_URL) == issuer["jwks"]
        assert endpoint.fetches == 2  # both still warm

    async def test_a_forced_refetch_for_one_url_keeps_the_other_warm(self, endpoint, issuer):
        """Before the fix ``force`` skipped the URL guard entirely, so a refresh
        of one endpoint evicted the other endpoint's warm entry."""
        endpoint.publish(issuer["jwk"])
        await get_cached_jwks(URL)
        await get_cached_jwks(OTHER_URL, force=True)
        assert endpoint.calls == [URL, OTHER_URL]

        assert await get_cached_jwks(URL) == issuer["jwks"]
        assert endpoint.fetches == 2, "the other URL's entry was evicted"

    async def test_a_forced_refetch_keeps_its_own_url_warm(self, endpoint, issuer):
        endpoint.publish(issuer["jwk"])
        await get_cached_jwks(URL)
        await get_cached_jwks(URL, force=True)
        await get_cached_jwks(URL)
        assert endpoint.fetches == 2

    async def test_concurrent_requests_for_two_urls_never_cross_over(
        self, endpoint, issuer, rotated
    ):
        """Each caller must receive the keys of *its own* URL, even while the
        other URL's fetch is still in flight."""
        endpoint.publish(issuer["jwk"], rotated["jwk"])
        endpoint.delay = 0.02

        from_a, from_b = await asyncio.gather(
            _verify(make_token(issuer, sub="issuer-sub"), url=URL),
            _verify(make_token(rotated, sub="rotated-sub"), url=OTHER_URL),
        )

        assert from_a["sub"] == "issuer-sub"
        assert from_b["sub"] == "rotated-sub"
        assert sorted(endpoint.calls) == sorted([URL, OTHER_URL])

    async def test_the_expiry_is_stamped_after_the_fetch_not_before(self, monkeypatch, issuer):
        """The pre-fix code captured ``now`` *before* the await, so the entry was
        born already aged by the whole fetch duration."""
        clock = _Clock()
        calls: list[str] = []

        async def _slow(url: str) -> dict:
            calls.append(url)
            clock.advance(30)  # a 30 "second" fetch
            return issuer["jwks"]

        monkeypatch.setattr(mod.time, "time", clock)
        monkeypatch.setattr(mod, "fetch_jwks", _slow)

        await get_cached_jwks(URL, ttl=300)
        assert len(calls) == 1

        clock.advance(299)  # 299s after the document actually landed
        await get_cached_jwks(URL, ttl=300)
        assert len(calls) == 1, "the window must start when the fetch completed"

        clock.advance(2)  # now genuinely past 300s
        await get_cached_jwks(URL, ttl=300)
        assert len(calls) == 2
