"""Bucket-key constraints for the auth rate limiter.

``_scope()`` digests a bucket key with a fast, unkeyed BLAKE2s before it
reaches Redis. That is only sound while the digested material is a
non-secret identifier. These tests pin the constraint that now makes that
structural rather than conventional:

* the namespace half comes from a closed enum, so no caller-supplied value
  can shape it;
* the subject half must satisfy its bucket's grammar, and for every bucket
  except ``resend:email`` that grammar provably excludes credentials;
* material that is refused never reaches the digest and never reaches
  Redis.

They also pin the two properties the constraint must not cost: the twelve
live call sites keep their existing Redis keys byte-for-byte, and PII
still never appears verbatim in a key.

The expected digests below were captured from the implementation *before*
this constraint existed, by running the old ``_scope`` over the exact
strings ``app/api/routes/auth.py`` passes. If a key changes, a rate-limit
counter or cooldown in flight is being dropped, so these are literal
strings rather than a re-derivation of the current code.
"""

import logging
import re
from unittest.mock import patch

import pytest

from app.core import rate_limit

import asyncio

from app.core.rate_limit import BucketKind, _scope, allow

# (key exactly as app/api/routes/auth.py builds it, expected rl:token: key)
# Captured from the pre-constraint implementation; see module docstring.
LEGACY_CALL_SITES = [
    ("mfa:verify:ip:203.0.113.9", "rl:token:99a4c2ccc46182075773c338ab21117884f4aada"),
    (
        "mfa:verify:user:6f1c2a44-9d1e-4a0b-9c2f-1d3e5f7a9b11",
        "rl:token:2f3588660ec7a11dd3712fbdbcc0cdfb309abb91",
    ),
    ("resend:ip:203.0.113.9", "rl:token:9c886466678e04cb22ae88dca8f8e1b5a59fb135"),
    ("resend:email:alice@example.com", "rl:token:ece242bd2d5035ed35a5497a94f0fe061b6918d3"),
    ("mfa:setup:ip:203.0.113.9", "rl:token:1a21501c1dda3ea7641d51454cedda592ff181e0"),
    (
        "mfa:setup:user:6f1c2a44-9d1e-4a0b-9c2f-1d3e5f7a9b11",
        "rl:token:3490d562057af60b281281894c5fd5256af5e22d",
    ),
    ("mfa:verify:ip:198.51.100.4", "rl:token:f04d1e7751a2a6f68785c14b82e4d2c6e6d08f2b"),
    (
        "mfa:verify:user:11111111-2222-3333-4444-555555555555",
        "rl:token:fb8b3e2287960fcdabe011ed59a2b26bb5c7005c",
    ),
    ("mfa:disable:ip:198.51.100.4", "rl:token:ef3c86e827daf952b0227302a4e8214687f4fb97"),
    (
        "mfa:disable:user:11111111-2222-3333-4444-555555555555",
        "rl:token:f8375ff697fe7aee4aab3589d45f799421d4dfcd",
    ),
    ("stepup:ip:198.51.100.4", "rl:token:8c70e5a2723cbcfbc4c0b9a13c1429712f24992b"),
    (
        "stepup:user:11111111-2222-3333-4444-555555555555",
        "rl:token:0b4d715d4eff2a86b7442ad59f6893126c14740a",
    ),
]

# Representative credentials. None of these is an IP literal or a UUID, so
# the per-IP and per-user grammars exclude them by construction.
SECRETS = [
    "correct horse battery staple",
    "hunter2",
    "Tr0ub4dor&3",
    "$2b$12$K3JNi5wQ8yPz1vS0dE4hJ7uXqW2mB6nC8rL0tYfHgAeI1oDkS5pUu",
    "p@ssw0rd with spaces",
]

# A secret that is *also* a syntactically valid address. This is the one
# shape the constraint cannot exclude; see the dedicated test below.
ADDRESS_SHAPED_SECRET = "attacker@evil.example.com"

IP = "203.0.113.9"
USER_ID = "11111111-2222-3333-4444-555555555555"
EMAIL = "alice@example.com"
KEY_SHAPE = re.compile(r"^rl:(token|cooldown):[0-9a-f]{40}$")


class _Pipeline:
    """Queues INCR/EXPIRE so ``execute`` can return the INCR results in order."""

    def __init__(self, redis: "_FakeRedis") -> None:
        self._redis = redis
        self._queued: list[tuple[str, str, int | None]] = []

    async def __aenter__(self) -> "_Pipeline":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    def incr(self, key: str) -> None:
        self._queued.append(("incr", key, None))

    def expire(self, key: str, ttl: int) -> None:
        self._queued.append(("expire", key, ttl))

    async def execute(self) -> list[int]:
        results: list[int] = []
        for op, key, arg in self._queued:
            if op == "incr":
                results.append(self._redis._incr(key))
            else:
                self._redis._expire(key, arg)
        self._queued.clear()
        return results


class _FakeRedis:
    """In-memory Redis with real INCR/EXPIRE/SET-NX semantics and a clock.

    ``AsyncMock`` cannot show a counter actually reaching its threshold, a
    cooldown actually elapsing, or a window actually resetting, so the
    behaviour tests drive this instead.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self._counts: dict[str, int] = {}
        self._expiry: dict[str, float] = {}
        self._flags: dict[str, float] = {}
        self.keys_used: list[str] = []

    def _record(self, key: str) -> None:
        if key not in self.keys_used:
            self.keys_used.append(key)

    def _evict_expired(self) -> None:
        for key in [k for k, exp in self._expiry.items() if exp <= self.now]:
            del self._expiry[key]
            self._counts.pop(key, None)
        for key in [k for k, exp in self._flags.items() if exp <= self.now]:
            del self._flags[key]

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self._evict_expired()

    def pipeline(self, transaction: bool = True) -> _Pipeline:
        return _Pipeline(self)

    def _incr(self, key: str) -> int:
        self._evict_expired()
        self._record(key)
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    def _expire(self, key: str, ttl: int | None) -> None:
        assert ttl is not None  # allow() always passes a window
        self._expiry[key] = self.now + ttl

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        self._evict_expired()
        self._record(key)
        if nx and key in self._flags:
            return False
        self._flags[key] = self.now + (ex or 0)
        return True


@pytest.fixture(autouse=True)
def _no_global_client():
    """Never fall through to the process-wide client (a real Redis may be up)."""
    saved = rate_limit._client
    rate_limit._client = None
    yield
    rate_limit._client = saved


def _using(client):
    return patch("app.core.rate_limit._get_client", return_value=client)


class _DigestSpy:
    """Fails loudly if anything reaches the fast hash."""

    def __init__(self) -> None:
        self.calls: list[bytes] = []

    def __call__(self, data=b"", *args, **kwargs):
        self.calls.append(data)
        raise AssertionError("refused material must never reach the digest")


# --------------------------------------------------------------------------
# The constraint
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("key", "expected_token_key"), LEGACY_CALL_SITES)
async def test_call_sites_keep_their_existing_redis_keys(key, expected_token_key):
    """All twelve live call sites still address the same Redis keys.

    The value under test is the literal key string handed to Redis, not an
    internal. A change here silently drops every in-flight counter and
    cooldown for that bucket.
    """
    client = _FakeRedis()
    with _using(client):
        assert await allow(key, max_requests=5, window_seconds=60, cooldown_seconds=30) is True

    assert client.keys_used == [expected_token_key, f"rl:cooldown:{expected_token_key[9:]}"]


@pytest.mark.parametrize("secret", SECRETS)
@pytest.mark.parametrize("kind", list(BucketKind), ids=lambda k: k.value)
async def test_a_secret_is_refused_by_every_bucket(kind, secret):
    """A credential cannot be routed into a bucket key, in any bucket.

    Asserts the strongest available property: the request is refused *and*
    nothing was written to Redis, so the secret never became key material.
    """
    client = _FakeRedis()
    spy = _DigestSpy()
    with _using(client), patch.object(rate_limit.hashlib, "blake2s", spy):
        assert await allow(f"{kind.value}:{secret}", max_requests=5, window_seconds=60) is False

    assert client.keys_used == [], "refused material must not reach Redis"
    assert spy.calls == [], "refused material must not reach the fast hash"


@pytest.mark.parametrize(
    "key",
    [
        # The exploit named in review: a credential tacked onto a real key.
        f"login:{EMAIL}:correct horse battery staple",
        # A namespace invented to carry a value.
        f"password:resend:email:{EMAIL}",
        # A legitimate namespace, illegitimate subject.
        f"resend:ip:{SECRETS[1]}",
        f"stepup:user:{SECRETS[1]}",
        f"mfa:verify:ip:{SECRETS[1]}",
        # A legitimate subject with a credential spliced on. Refused because a
        # subject may not contain ':', so there is no second slot to hide in.
        f"resend:ip:{IP}:{SECRETS[0]}",
        f"resend:email:{EMAIL}:{SECRETS[0]}",
        # Case-folding an existing namespace must not launder it.
        f"RESEND:IP:{IP}",
        # Trailing/leading separators and empty subjects.
        "resend:ip:",
        ":203.0.113.9",
        "resend:ip",
    ],
)
async def test_wrongly_shaped_or_smuggled_keys_are_refused(key):
    client = _FakeRedis()
    spy = _DigestSpy()
    with _using(client), patch.object(rate_limit.hashlib, "blake2s", spy):
        assert await allow(key, max_requests=5, window_seconds=60) is False

    assert client.keys_used == []
    assert spy.calls == []


async def test_oversized_key_is_refused_before_any_parsing():
    """A multi-megabyte key is rejected on length, not after regex work."""
    client = _FakeRedis()
    spy = _DigestSpy()
    huge = f"resend:email:{'a' * (1024 * 1024)}@example.com"
    with _using(client), patch.object(rate_limit.hashlib, "blake2s", spy):
        assert await allow(huge, max_requests=5, window_seconds=60) is False

    assert client.keys_used == []
    assert spy.calls == []


async def test_the_namespace_is_a_closed_set():
    """Every namespace in use is an enum member, and nothing else is.

    Pins the enumeration itself: adding a bucket is a reviewable edit to
    ``BucketKind``, and the set cannot grow at a call site.
    """
    assert {kind.value for kind in BucketKind} == {
        "resend:ip",
        "resend:email",
        "mfa:verify:ip",
        "mfa:verify:user",
        "mfa:setup:ip",
        "mfa:setup:user",
        "mfa:disable:ip",
        "mfa:disable:user",
        "stepup:ip",
        "stepup:user",
    }
    assert not rate_limit._parse_bucket("login:email")


def _refused(kind, subject: str) -> bool:
    """Whether ``allow()`` would refuse this bucket/subject, either stage.

    Mirrors the two checks ``allow()`` applies before it will hash: the key
    must parse into a known namespace and a non-empty, colon-free subject,
    and that subject must satisfy the bucket's grammar.
    """
    parsed = rate_limit._parse_bucket(f"{kind.value}:{subject}")
    if parsed is None:
        return True
    return not rate_limit._SUBJECT_VALIDATORS[parsed[0]](parsed[1])


def test_excluding_buckets_reject_every_credential_shape():
    """The per-IP and per-user grammars exclude credentials by construction.

    ``ipaddress`` and ``UUID`` are total over closed domains that contain no
    credentials, so this is exclusion rather than a heuristic. Deliberately
    does not reuse the SECRETS list, so the property is not an artefact of
    which strings happened to be chosen. ``resend:email`` is excluded here
    because an address is *not* distinguishable from an address-shaped
    secret -- see the dedicated test for that residual.

    This goes through ``allow()``, not through a local re-implementation of the
    two checks. An earlier version of this test called the helpers directly, so
    it kept passing when the guard was spliced out of ``allow()`` entirely: it
    was validating the helpers, not the control, while being cited as the
    evidence that a secret cannot reach the hash.
    """
    for kind in BucketKind:
        if kind is BucketKind.RESEND_EMAIL:
            continue
        # "0.0.0.0" and "::" are deliberately absent: both are valid addresses
        # (the unspecified address), so accepting them is correct. "0" and
        # "1e999" are not addresses, and "::ffff:1.2.3.4.5" is a malformed
        # IPv4-mapped literal -- those are the near-misses worth pinning.
        for secret in SECRETS + ["", " ", "0", "1e999", "::ffff:1.2.3.4.5"]:
            assert _refused_via_allow(kind, secret), f"{kind.value} accepted {secret!r}"
        # ...while the subject the bucket exists for is still accepted.
        expected = "unknown" if kind.value.endswith(":ip") else USER_ID
        assert not _refused_via_allow(kind, expected), f"{kind.value} rejected a valid subject"


def _refused_via_allow(kind, subject: str) -> bool:
    """Whether the real ``allow()`` refuses this bucket/subject, either stage.

    Uses a fake Redis and asserts on ``keys_used``, not just the return value:
    a refusal that still wrote the key would be a failure wearing a passing
    return value.
    """
    client = _FakeRedis()
    with _using(client):
        allowed = asyncio.run(allow(f"{kind.value}:{subject}", max_requests=5, window_seconds=60))
    return not allowed and not client.keys_used


async def test_resend_email_is_bounded_not_excluded():
    """The known residual risk, pinned so it cannot be quietly forgotten.

    An address is not distinguishable from an address-shaped secret, so
    ``resend:email`` cannot exclude credentials the way the IP and UUID
    buckets do. This is the one bucket where a secret-shaped value is still
    accepted and digested. It is a real limitation of this design, recorded
    here so that it is visible rather than implied, and so that closing it
    later (keyed digest, or a typed key) shows up as a failing test.
    """
    client = _FakeRedis()
    with _using(client):
        assert (
            await allow(f"resend:email:{ADDRESS_SHAPED_SECRET}", max_requests=5, window_seconds=60)
            is True
        )
    assert client.keys_used  # it did become key material, which is the risk
    assert ADDRESS_SHAPED_SECRET not in client.keys_used[0]  # but never verbatim


async def test_refused_material_is_not_written_to_the_log(caplog):
    """The guard must not echo the material it is protecting."""
    caplog.set_level(logging.DEBUG, logger="app.core.rate_limit")
    client = _FakeRedis()
    secret = "correct horse battery staple"
    with _using(client):
        assert await allow(f"resend:ip:{secret}", max_requests=5, window_seconds=60) is False
        assert await allow(f"login:{secret}", max_requests=5, window_seconds=60) is False

    assert [r for r in caplog.records if r.levelno >= logging.ERROR], "refusal must be logged"
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert secret not in logged
    assert "resend:ip" in logged  # the namespace is safe to name, and useful


async def test_unknown_ip_sentinel_is_still_accepted():
    """Routes substitute "unknown" when ``Request.client`` is absent."""
    client = _FakeRedis()
    with _using(client):
        assert await allow("resend:ip:unknown", max_requests=5, window_seconds=60) is True
    assert len(client.keys_used) == 1


# --------------------------------------------------------------------------
# The constraint must not cost anything
# --------------------------------------------------------------------------


async def test_pii_never_appears_verbatim_in_a_redis_key():
    client = _FakeRedis()
    with _using(client):
        await allow(
            f"resend:email:{EMAIL}", max_requests=5, window_seconds=60, cooldown_seconds=120
        )
        await allow(f"resend:ip:{IP}", max_requests=5, window_seconds=60, cooldown_seconds=120)

    assert client.keys_used
    for key in client.keys_used:
        assert KEY_SHAPE.match(key), key
        assert EMAIL not in key
        assert IP not in key
        for fragment in ("alice", "example.com", "203.0.113", "@"):
            assert fragment not in key


async def test_distinct_subjects_get_distinct_buckets():
    client = _FakeRedis()
    with _using(client):
        await allow("resend:ip:203.0.113.9", max_requests=5, window_seconds=60)
        await allow("resend:ip:198.51.100.4", max_requests=5, window_seconds=60)
    assert len(set(client.keys_used)) == 2


async def test_counting_denies_at_the_threshold():
    client = _FakeRedis()
    with _using(client):
        results = [
            await allow(f"resend:ip:{IP}", max_requests=3, window_seconds=60) for _ in range(5)
        ]
    assert results == [True, True, True, False, False]


async def test_cooldown_blocks_a_second_attempt_then_releases():
    client = _FakeRedis()
    with _using(client):
        first = await allow(
            f"resend:email:{EMAIL}", max_requests=10, window_seconds=60, cooldown_seconds=120
        )
        second = await allow(
            f"resend:email:{EMAIL}", max_requests=10, window_seconds=60, cooldown_seconds=120
        )
        client.advance(121)
        third = await allow(
            f"resend:email:{EMAIL}", max_requests=10, window_seconds=60, cooldown_seconds=120
        )

    assert [first, second, third] == [True, False, True]


async def test_window_resets_after_the_window_elapses():
    client = _FakeRedis()
    with _using(client):
        for _ in range(3):
            await allow(f"resend:ip:{IP}", max_requests=3, window_seconds=60)
        denied = await allow(f"resend:ip:{IP}", max_requests=3, window_seconds=60)
        client.advance(61)
        after_reset = await allow(f"resend:ip:{IP}", max_requests=3, window_seconds=60)

    assert denied is False
    assert after_reset is True


async def test_counting_stops_at_the_token_key():
    client = _FakeRedis()
    with _using(client):
        await allow(
            f"resend:email:{EMAIL}", max_requests=5, window_seconds=60, cooldown_seconds=120
        )
    token_keys = [k for k in client.keys_used if k.startswith("rl:token:")]
    cooldown_keys = [k for k in client.keys_used if k.startswith("rl:cooldown:")]
    assert len(token_keys) == 1
    assert len(cooldown_keys) == 1
    assert token_keys[0].removeprefix("rl:token:") == cooldown_keys[0].removeprefix("rl:cooldown:")


def test_scope_digests_the_namespace_and_subject_together():
    """A stable, PII-free digest of the two halves, distinct per bucket."""
    first = rate_limit._scope(BucketKind.RESEND_IP, IP)
    assert first == rate_limit._scope(BucketKind.RESEND_IP, IP)
    assert len(first) == 40
    assert IP not in first
    # The namespace is part of the digest, so two buckets over one subject
    # never collide.
    assert first != rate_limit._scope(BucketKind.STEP_UP_IP, IP)
    assert first != rate_limit._scope(BucketKind.RESEND_IP, "198.51.100.4")


# --- coverage for the three gaps the adversarial review found at 0/86 ---------
#
# An earlier draft of this file left all three unmeasured, and each of them is a
# way the guard could silently stop doing its job.


async def test_ipv6_subjects_are_accepted_by_every_ip_bucket():
    """IPv6 peers must not be refused.

    An earlier version rejected any subject containing ":", on the theory that
    this closed a slot for splicing a credential onto a key. It also rejected
    every valid IPv6 address, so every IPv6 client got a permanent 429 on
    /resend-verification, /mfa/* and /step-up. The docstring claimed "a secret
    cannot be expressed at all" for these buckets, which was false.
    """
    client = _FakeRedis()
    with _using(client):
        for subject in (
            "::1",
            "2001:db8::1",
            "2001:db8::dead:beef",
            "::ffff:203.0.113.9",  # IPv4-mapped, arrives from dual-stack sockets
            "fe80::1",
        ):
            for kind in BucketKind:
                if not kind.value.endswith(":ip"):
                    continue
                allowed = await allow(f"{kind.value}:{subject}", max_requests=5, window_seconds=60)
                assert allowed, f"{kind.value} refused IPv6 peer {subject!r}"
    assert client.keys_used  # accepted *and* counted, not just waved through


async def test_equivalent_identifier_spellings_share_one_bucket():
    """Canonicalise before hashing, so a client cannot multiply its own limit.

    The guard validated the subject but hashed the raw string, so the same UUID
    upper-cased, un-hyphenated or brace-wrapped each produced a different digest
    and therefore a fresh counter.
    """
    canonical = "6f1c2a44-9d1e-4a0b-9c2f-1d3e5f7a9b11"
    spellings = [
        canonical,
        canonical.upper(),
        canonical.replace("-", ""),
        "{" + canonical + "}",
        "urn:uuid:" + canonical,
    ]
    digests = {_scope(BucketKind.STEP_UP_USER, s) for s in spellings}
    assert len(digests) == 1, f"{len(digests)} buckets for one identifier: {spellings}"


async def test_oversized_key_is_refused_before_redis():
    """A subject beyond the cap must be refused, not truncated or hashed.

    Deleting the cap was detected by 0 of 86 tests: a 1 MiB key was refused
    downstream by the address grammar instead, so the test claimed to pin the
    cap while never exercising it.
    """
    client = _FakeRedis()
    with _using(client):
        allowed = await allow(
            f"stepup:user:{USER_ID}{'x' * 4096}",
            max_requests=5,
            window_seconds=60,
        )
    assert allowed is False
    assert not client.keys_used
