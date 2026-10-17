import asyncio
import time
import logging
from typing import Any

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

logger = logging.getLogger(__name__)

ALLOWED_ALGORITHMS = {"RS256"}
REQUIRED_CLAIMS = {"exp", "iat", "iss", "aud", "sub", "type"}
AUTH_VERSIONED_TYPES = {"access", "admin_step_up"}

#: How long an unknown ``kid`` — and the forced JWKS refetch it triggered — is
#: remembered per URL. Inside this window a repeat sighting is rejected without
#: touching the network, so an unauthenticated random-``kid`` flood costs at
#: most one outbound fetch per window instead of one per request. A genuine key
#: rotation is unaffected: its ``kid`` was never seen before, so it refetches.
UNKNOWN_KID_BACKOFF_SECONDS = 30.0

#: Hard cap on remembered unknown kids per URL. Without it an attacker could
#: supply endless distinct kids and grow the bookkeeping without bound.
MAX_TRACKED_UNKNOWN_KIDS = 512

# Per-URL cache state. Keying by URL (rather than holding a single slot) means a
# forced refetch for one JWKS endpoint can no longer evict another endpoint's
# warm entry, and two URLs in flight at once can never read each other's data.
_jwks_cache: dict[str, dict] = {}
_jwks_cache_expiry: dict[str, float] = {}
_jwks_cache_generation: dict[str, int] = {}
_jwks_cache_locks: dict[str, asyncio.Lock] = {}
#: URL -> epoch of the last *forced* refetch, for the unknown-kid backoff.
_jwks_forced_at: dict[str, float] = {}
#: URL -> {kid: epoch at which the kid stops being back-offed}.
_unknown_kids: dict[str, dict[str, float]] = {}


class JWKSUnavailableError(JWTError):
    """The JWKS could not be obtained, so no token can be verified right now.

    A ``JWTError`` subclass so a caller's single ``except JWTError`` still
    catches it, but it is checked *first* by callers that distinguish a
    verification outage (503) from a bad token (401) — see
    :func:`verify_token_with_jwks`. A transport failure during the forced
    unknown-``kid`` refetch must not escape as a bare ``httpx``/``OSError``,
    because ``except JWTError`` would miss it and the outage would surface as a
    500 instead of a 503.
    """


class InvalidJWKSError(JWKSUnavailableError):
    """The JWKS endpoint answered with something that is not a JWKS document.

    Deliberately a :class:`JWKSUnavailableError`: a malformed body is an
    availability problem (503), never an invalid token (401).
    """


class UnknownKidError(JWTError):
    """The token's ``kid`` is absent from the JWKS that was supplied.

    A subclass of ``JWTError`` so every existing ``except JWTError`` handler
    keeps working unchanged. Callers that can refresh the JWKS (see
    :func:`verify_token_with_jwks`) catch this specifically to tell "the signing
    key rotated under us" apart from "this token is simply not valid", and it
    carries the offending ``kid`` so the backoff bookkeeping can be keyed on it.
    """

    def __init__(self, message: str, kid: str) -> None:
        super().__init__(message)
        self.kid = kid


def get_jwk_for_kid(jwks: dict, kid: str) -> dict | None:
    keys = jwks.get("keys") or []
    for k in keys:
        if k.get("kid") == kid:
            return k
    return None


def verify_token(
    token: str,
    jwks: dict,
    audience: str,
    issuer: str,
    leeway: int = 60,
    expected_type: str = "access",
) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise JWTError(f"invalid header: {e}") from e
    alg = header.get("alg")
    kid = header.get("kid")
    if alg not in ALLOWED_ALGORITHMS:
        raise JWTError(f"unsupported alg {alg}")
    if not kid:
        raise JWTError("missing kid")
    jwk = get_jwk_for_kid(jwks, kid)
    if jwk is None:
        raise UnknownKidError(f"unknown kid {kid}", kid)
    if jwk.get("alg") and jwk["alg"] not in ALLOWED_ALGORITHMS:
        raise JWTError(f"jwk alg not allowed {jwk['alg']}")
    try:
        payload = jwt.decode(
            token,
            jwk,
            algorithms=list(ALLOWED_ALGORITHMS),
            audience=audience,
            issuer=issuer,
            options={"leeway": leeway},
        )
    except ExpiredSignatureError:
        raise
    except JWTError:
        raise
    missing = REQUIRED_CLAIMS.difference(payload)
    if missing:
        raise JWTError(f"missing required claims: {', '.join(sorted(missing))}")
    if payload.get("type") != expected_type:
        raise JWTError(f"invalid type expected {expected_type}")
    if expected_type in AUTH_VERSIONED_TYPES:
        auth_version = payload.get("av")
        if isinstance(auth_version, bool) or not isinstance(auth_version, int):
            raise JWTError("invalid auth version claim")
    return payload


def load_jwks_from_dict(data: dict) -> dict:
    return data


async def fetch_jwks(url: str, timeout: float = 5.0) -> dict:
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


def fetch_jwks_sync(url: str, timeout: float = 5.0) -> dict:
    import httpx

    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()


def validate_jwks(data: Any) -> dict:
    """Return ``data`` when it is a usable JWKS document, else raise.

    Called at the cache-store boundary, before anything is cached, so a
    malformed answer can never become the cached value. Without this an HTML
    error page or a JSON array is cached for the full TTL: ``get_jwk_for_kid``
    would then raise ``AttributeError`` on a non-mapping, turning a bad JWKS
    endpoint into a 500 that also re-caches the same poison on every refetch.
    """
    if not isinstance(data, dict):
        raise InvalidJWKSError(
            f"jwks response is {type(data).__name__}, expected an object with a 'keys' list"
        )
    keys = data.get("keys")
    if not isinstance(keys, list):
        raise InvalidJWKSError(f"jwks response 'keys' is {type(keys).__name__}, expected a list")
    for index, key in enumerate(keys):
        if not isinstance(key, dict) or not key.get("kty"):
            raise InvalidJWKSError(f"jwks key at index {index} is not an object carrying 'kty'")
    return data


def _lock_for(url: str) -> asyncio.Lock:
    """One lock per URL, so misses on *different* URLs never serialise."""
    lock = _jwks_cache_locks.get(url)
    if lock is None:
        lock = _jwks_cache_locks[url] = asyncio.Lock()
    return lock


def _live_jwks(url: str) -> dict | None:
    """The cached document for ``url`` while it is still inside its window.

    Liveness is decided by the stored expiry alone, so a later call with a
    different ``ttl`` does not shorten or extend an entry that is already
    cached — ``ttl`` only ever applies at store time.
    """
    data = _jwks_cache.get(url)
    if data is None or time.time() >= _jwks_cache_expiry.get(url, 0.0):
        return None
    return data


async def get_cached_jwks(url: str, ttl: int = 300, *, force: bool = False) -> dict:
    """Return the JWKS for ``url``, cached for ``ttl`` seconds.

    Misses are single-flighted per URL: concurrent callers that all miss queue
    on one lock, and the first one to get it stores the document, so the rest
    return it instead of issuing their own request. That is what stops a burst
    of unknown-``kid`` requests from turning into a burst of outbound fetches.

    ``force=True`` bypasses a still-valid entry and refetches — used after an
    unknown ``kid`` so a legitimate rotation need not wait out the TTL. Two
    callers that force at the same time still produce a single fetch: the
    generation counter is snapshotted before the lock is taken, so whoever
    arrives second sees that a refresh already landed and uses that result.
    A failed refetch leaves any existing cache entry intact.
    """
    if not force:
        cached = _live_jwks(url)
        if cached is not None:
            return cached
    else:
        # Snapshot *before* blocking: a refresh that lands while we wait is
        # exactly the refresh we were about to ask for.
        generation = _jwks_cache_generation.get(url, 0)

    async with _lock_for(url):
        if not force:
            cached = _live_jwks(url)
            if cached is not None:
                return cached
        elif _jwks_cache_generation.get(url, 0) > generation:
            # Another caller refreshed this URL while we waited on the lock, and
            # that is exactly the refresh we were about to ask for. The
            # document and the generation are written together and cleared
            # together with no await in between, so the document is here.
            return _jwks_cache[url]

        data = validate_jwks(await fetch_jwks(url))
        # The expiry is stamped *after* the await. Reusing the timestamp taken
        # before the fetch would shorten the TTL by the whole fetch duration.
        _jwks_cache[url] = data
        _jwks_cache_expiry[url] = time.time() + ttl
        _jwks_cache_generation[url] = _jwks_cache_generation.get(url, 0) + 1
        return data


def _cap_unknown_kids(live: dict[str, float]) -> dict[str, float]:
    """Keep at most :data:`MAX_TRACKED_UNKNOWN_KIDS` entries, newest kept.

    Oldest-first eviction, so an attacker supplying endless distinct kids cannot
    grow this bookkeeping without bound.
    """
    if len(live) <= MAX_TRACKED_UNKNOWN_KIDS:
        return live
    ordered = sorted(live.items(), key=lambda item: item[1])
    return dict(ordered[-MAX_TRACKED_UNKNOWN_KIDS:])


def _prune_unknown_kids(url: str) -> dict[str, float]:
    """Live unknown-kid entries for ``url``, expired ones and the overflow gone."""
    seen = _unknown_kids.get(url) or {}
    now = time.time()
    live = _cap_unknown_kids({kid: deadline for kid, deadline in seen.items() if now < deadline})
    _unknown_kids[url] = live
    return live


def _unknown_kid_backoff_active(url: str, kid: str, backoff: float) -> bool:
    """Is a forced refetch suppressed for this ``kid``/``url`` right now?

    Two independent guards, both attacker-visible:
    * the kid itself was already reported missing inside the window, and
    * *any* forced refetch for this URL happened inside the window — this is
      the hard egress bound, and it is what makes a random-kid flood cost one
      fetch per window rather than one per request.
    """
    if backoff <= 0:
        return False
    now = time.time()
    if now < _prune_unknown_kids(url).get(kid, 0.0):
        return True
    forced_at = _jwks_forced_at.get(url)
    return forced_at is not None and now < forced_at + backoff


def _remember_unknown_kid(url: str, kid: str, backoff: float) -> None:
    if backoff <= 0:
        return
    seen = _prune_unknown_kids(url)
    seen[kid] = time.time() + backoff
    _unknown_kids[url] = _cap_unknown_kids(seen)


async def _jwks_or_unavailable(url: str, ttl: int, *, force: bool) -> dict:
    """``get_cached_jwks`` with every failure mode folded into one type.

    A transport error is not a ``JWTError``, so letting it escape would skip a
    caller's ``except JWTError`` and surface an outage as a 500. Callers that
    map ``JWKSUnavailableError`` to 503 therefore get a 503 for a JWKS outage,
    for a refetch that failed mid-rotation, and for a malformed body alike.
    """
    try:
        return await get_cached_jwks(url, ttl, force=force)
    except JWKSUnavailableError:
        raise
    except Exception as exc:
        raise JWKSUnavailableError(f"jwks unavailable for {url}") from exc


async def verify_token_with_jwks(
    token: str,
    *,
    audience: str,
    issuer: str,
    url: str,
    ttl: int = 300,
    leeway: int = 60,
    expected_type: str = "access",
    unknown_kid_backoff: float = UNKNOWN_KID_BACKOFF_SECONDS,
) -> dict[str, Any]:
    """Verify ``token`` against the cached JWKS, refreshing once on a new ``kid``.

    The cache in :func:`get_cached_jwks` is TTL-only, so between a signing-key
    rotation and the next expiry every token signed with the new key is
    rejected as an unknown ``kid`` -- up to ``ttl`` seconds (300 by default) of
    401s after a perfectly legitimate rotation.

    On an unknown ``kid`` this refreshes and retries **once**. The retry is
    straight-line rather than a loop, so an infinite retry is structurally
    impossible: if the freshly fetched JWKS still does not carry the ``kid``,
    the error from that second :func:`verify_token` propagates unchanged.

    The refresh is rate-limited, because ``kid`` is attacker-controlled and
    ``UnknownKidError`` is raised *before* any signature check. A caller that
    saw no cap would let an unauthenticated flood of random kids drive one
    outbound fetch per request; here a random-kid flood costs at most one fetch
    per :data:`UNKNOWN_KID_BACKOFF_SECONDS` window, and a rotation still lands
    immediately because its ``kid`` is new. See
    :func:`_unknown_kid_backoff_active`.

    Raises :class:`JWKSUnavailableError` when the JWKS itself cannot be had
    (transport failure, or a body that is not a JWKS) and :class:`JWTError` —
    including :class:`UnknownKidError` — when the token is simply not valid.
    """
    jwks = await _jwks_or_unavailable(url, ttl, force=False)
    try:
        return verify_token(token, jwks, audience, issuer, leeway, expected_type)
    except UnknownKidError as exc:
        kid = exc.kid
        if _unknown_kid_backoff_active(url, kid, unknown_kid_backoff):
            # Still record the kid: it is evidence in its own right, and keeping
            # it means a flood of distinct kids stays bounded while the
            # per-URL window is what gates the egress.
            _remember_unknown_kid(url, kid, unknown_kid_backoff)
            logger.info(
                "unknown kid %s is inside the JWKS refresh backoff for %s; not refetching",
                kid,
                url,
            )
            raise

    # Stamp the window *before* asking, so the cap holds even when this call's
    # refresh collapses into one another caller already has in flight.
    _jwks_forced_at[url] = time.time()
    jwks = await _jwks_or_unavailable(url, ttl, force=True)
    if get_jwk_for_kid(jwks, kid) is None:
        _remember_unknown_kid(url, kid, unknown_kid_backoff)
    else:
        # The refresh actually rotated the keys, so every kid recorded against
        # the previous document is stale evidence. Dropping it keeps a kid that
        # was seen once *before* the rotation from waiting out the window.
        _unknown_kids[url] = {}
    return verify_token(token, jwks, audience, issuer, leeway, expected_type)


def clear_jwks_cache() -> None:
    # The per-URL locks are dropped too: an ``asyncio.Lock`` is bound to the
    # loop that first awaited it, so a leftover one would be unusable in the
    # next loop (and is only ever a correctness guard, never state).
    _jwks_cache.clear()
    _jwks_cache_expiry.clear()
    _jwks_cache_generation.clear()
    _jwks_cache_locks.clear()
    _jwks_forced_at.clear()
    _unknown_kids.clear()
