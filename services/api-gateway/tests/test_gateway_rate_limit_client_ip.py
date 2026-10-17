"""Regression tests for the gateway rate-limit client key (#130).

The defect: ``proxy_request`` built its rate-limit key from a
client-supplied ``X-Forwarded-For`` (or ``X-Real-IP``) with no gating and no
chain validation, so a caller could rotate the header and receive a fresh
bucket on every request. The per-client limiter was fully bypassable by an
unauthenticated caller with no forged credential of any kind.

These tests assert the *key* the limiter was handed, not merely that a 429
came back. A "the Nth request is 429" assertion can pass for unrelated
reasons (an upstream 502, a dead Redis, a broken limiter); asserting the
Redis key set proves the caller's header did or did not select the bucket.

The rate limiter here is the real ``RateLimiter`` with only Redis faked, so
the exercised key format, burst and concurrency logic are the shipped ones.
"""

import pytest
from starlette.testclient import TestClient


class FakeRedis:
    """Minimal ``redis.asyncio.Redis`` stand-in that records every key touched."""

    def __init__(self):
        self.counts: dict[str, int] = {}
        self.expires: dict[str, int] = {}
        self.leases: dict[str, dict[str, float]] = {}

    def __await__(self):
        async def _identity():
            return self

        return _identity().__await__()

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, window):
        self.expires[key] = window
        return True

    async def eval(self, script, numkeys, key, *args):
        assert numkeys == 1
        leases = self.leases.setdefault(key, {})
        if "ZREMRANGEBYSCORE" in script:
            lease_id, now, expiry, limit = args
            leases = {m: exp for m, exp in leases.items() if exp > float(now)}
            self.leases[key] = leases
            if len(leases) >= int(limit):
                return 0
            leases[lease_id] = float(expiry)
            return 1
        lease_id = args[0]
        return int(leases.pop(lease_id, None) is not None)


def _limiter(redis_client, *, limit=3):
    """A real RateLimiter with a small, deterministic auth ceiling."""
    from app.middleware import RateLimiter

    limiter = RateLimiter(redis_client)
    limiter.limits["auth"] = limit
    limiter.burst_limits["auth"] = 100
    limiter.concurrency_limits["auth"] = 100
    return limiter


def _ip_keys(redis_client: FakeRedis) -> set[str]:
    """The set of distinct per-IP keys the limiter actually consumed."""
    return {k for k in redis_client.counts if k.startswith("rate_limit:ip:")}


class _Unauthenticated:
    """Stand-in for the lifespan-built AuthenticationMiddleware.

    The lifespan is deliberately not run: it dials Redis and the JWKS endpoint,
    and it rebinds ``app.main.rate_limiter`` to a Redis-backed limiter, which
    would replace the instrumented one. Everything under test here (the real
    router, the real middleware stack and the real RateLimiter) is untouched by
    that; only the network edges are faked.
    """

    async def verify_token(self, request):
        return None


def _client(monkeypatch, app, client_host="testclient"):
    """Wire the app for an unauthenticated caller and return a TestClient.

    ``shutting_down`` is reset because earlier test modules in this suite drive
    the real lifespan on the shared ``app.main.app`` singleton; its shutdown
    branch sets that flag and nothing clears it again, so without this every
    request here would be answered 503 "Service shutting down" before reaching
    the route under test. That is unrelated pre-existing state, not the
    behaviour these tests assert.
    """
    import app.main as main

    monkeypatch.setattr(main, "auth_middleware", _Unauthenticated(), raising=False)
    app.state.shutting_down = False
    return TestClient(app, client=(client_host, 51000))


@pytest.fixture(autouse=True)
def _proxy_trust_off(monkeypatch):
    """Default every test to the shipped dev-stack posture: no proxy trusted."""
    from app.core.settings import settings

    monkeypatch.setattr(settings, "TRUST_PROXY", False)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "")


def _patch_limiter(monkeypatch, limiter):
    import app.main as main

    monkeypatch.setattr(main, "rate_limiter", limiter)


def test_rotating_xff_cannot_forge_a_fresh_rate_limit_bucket(monkeypatch):
    """The core #130 bypass: N distinct XFF values must not mean N buckets.

    Against the pre-fix code every request carried a different
    ``X-Forwarded-For``, so ``rate_limit:ip:<forged>:<service>`` was a new key
    each time and no request was ever limited.
    """
    from app.main import app

    redis_client = FakeRedis()
    _patch_limiter(monkeypatch, _limiter(redis_client, limit=3))

    forged = [f"203.0.113.{n}" for n in range(1, 11)]
    client = _client(monkeypatch, app)
    statuses = [
        client.get("/auth/login", headers={"X-Forwarded-For": ip}).status_code for ip in forged
    ]

    # The caller must not have been able to select the bucket.
    assert len(_ip_keys(redis_client)) == 1, (
        f"rotating X-Forwarded-For produced {len(_ip_keys(redis_client))} buckets: "
        f"{sorted(_ip_keys(redis_client))}"
    )
    # ...and the limiter must actually have refused the excess traffic.
    assert 429 in statuses, f"rotating X-Forwarded-For was never rate-limited: {statuses}"
    assert statuses.count(429) == 7, f"expected 7 refusals at a limit of 3, got {statuses}"


def test_rotating_x_real_ip_cannot_forge_a_fresh_rate_limit_bucket(monkeypatch):
    """``X-Real-IP`` must never select the bucket, not even behind a proxy.

    This is deliberately exercised with ``TRUST_PROXY`` **on** and a trusted
    peer. With the flag off ``_derive_real_ip`` short-circuits to the socket
    address, so a test written that way would pass whether or not the header
    were consulted — it would be asserting the flag, not the decision about
    ``X-Real-IP``. Only in the trusted-proxy posture is re-adding the header as
    a fallback actually exploitable, so that is where it is pinned.

    ``X-Real-IP`` is a single value with no chain, so there is no way to tell a
    value set by the trusted proxy from one the caller sent and the proxy
    passed through. Nothing in the deployed stack emits it (Caddy does not), so
    honouring it can only ever be a bypass.
    """
    from app.core.settings import settings
    from app.main import app

    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.0/8")
    redis_client = FakeRedis()
    _patch_limiter(monkeypatch, _limiter(redis_client, limit=3))

    forged = [f"198.51.100.{n}" for n in range(1, 11)]
    client = _client(monkeypatch, app, client_host="10.0.0.5")  # trusted Caddy hop
    statuses = [client.get("/auth/login", headers={"X-Real-IP": ip}).status_code for ip in forged]

    assert _ip_keys(redis_client) == {"rate_limit:ip:10.0.0.5:auth"}, (
        f"X-Real-IP selected {len(_ip_keys(redis_client))} buckets: "
        f"{sorted(_ip_keys(redis_client))}"
    )
    assert 429 in statuses, f"rotating X-Real-IP was never rate-limited: {statuses}"


def test_key_is_the_socket_ip_when_no_proxy_is_trusted(monkeypatch):
    """With TRUST_PROXY off the key is the peer address, whatever is claimed."""
    from app.core.settings import settings
    from app.main import app

    monkeypatch.setattr(settings, "TRUST_PROXY", False)
    redis_client = FakeRedis()
    _patch_limiter(monkeypatch, _limiter(redis_client, limit=50))

    client = _client(monkeypatch, app, client_host="198.18.7.7")
    for n in range(5):
        client.get("/auth/login", headers={"X-Forwarded-For": f"203.0.113.{n}"})

    assert _ip_keys(redis_client) == {"rate_limit:ip:198.18.7.7:auth"}


def test_legitimate_proxy_traffic_still_keys_on_the_real_client(monkeypatch):
    """Anti-regression: a genuinely trusted proxy must still yield per-client keys.

    Fixing #130 by keying on the socket address unconditionally would collapse
    every dev request onto Caddy's address into one shared bucket. This is the
    guard that the correct, non-collapsing behaviour is preserved once an
    operator actually configures TRUST_PROXY.
    """
    from app.core.settings import settings
    from app.main import app

    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.0/8")
    redis_client = FakeRedis()
    _patch_limiter(monkeypatch, _limiter(redis_client, limit=50))

    client = _client(monkeypatch, app, client_host="10.0.0.5")  # the Caddy hop
    for ip in ("203.0.113.7", "203.0.113.8"):
        # Caddy appends the address it observed to any inbound XFF chain.
        client.get("/auth/login", headers={"X-Forwarded-For": f"{ip}, 10.0.0.5"})

    assert _ip_keys(redis_client) == {
        "rate_limit:ip:203.0.113.7:auth",
        "rate_limit:ip:203.0.113.8:auth",
    }


def test_forged_hops_left_of_a_trusted_proxy_are_ignored(monkeypatch):
    """A caller prepending a hop to a trusted chain must not select the bucket.

    ``_derive_real_ip`` walks right-to-left and stops at the first untrusted
    hop, so ``<forged>, <real client>`` resolves to the real client and not to
    the attacker's chosen value.
    """
    from app.core.settings import settings
    from app.main import app

    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.0/8")
    redis_client = FakeRedis()
    _patch_limiter(monkeypatch, _limiter(redis_client, limit=50))

    client = _client(monkeypatch, app, client_host="10.0.0.5")
    for forged in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        client.get(
            "/auth/login",
            headers={"X-Forwarded-For": f"{forged}, 203.0.113.7, 10.0.0.5"},
        )

    assert _ip_keys(redis_client) == {"rate_limit:ip:203.0.113.7:auth"}


def test_untrusted_peer_cannot_smuggle_an_xff(monkeypatch):
    """A peer that is not itself trusted gets the socket key, header ignored."""
    from app.core.settings import settings
    from app.main import app

    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.0/8")
    redis_client = FakeRedis()
    _patch_limiter(monkeypatch, _limiter(redis_client, limit=50))

    # Direct hit on the gateway: peer 203.0.113.9 is not in the trusted list.
    client = _client(monkeypatch, app, client_host="203.0.113.9")
    client.get("/auth/login", headers={"X-Forwarded-For": "1.1.1.1"})

    assert _ip_keys(redis_client) == {"rate_limit:ip:203.0.113.9:auth"}
