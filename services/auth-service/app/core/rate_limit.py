"""Redis-backed rate limiting for abuse-prone, security-sensitive endpoints.

Used by the email-verification resent flow (#54): per-IP quotas plus a
per-email cooldown so an address cannot be flooded with verification
emails, and repeated probes are throttled. Redis is the single source of
truth so limits survive service restarts.

Fail-open by design: if Redis is unreachable the endpoint still responds
(round-robin behaviour), because the enumeration-safe response contract is
what matters most — throttling is defense-in-depth on top of it.
"""

import hashlib

from redis.asyncio import Redis

from app.core.settings import settings

_client: Redis | None = None


def _get_client() -> Redis | None:
    """Lazily create the shared redis.asyncio client."""
    global _client
    if _client is None:
        try:
            _client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        except Exception:  # noqa: BLE001 - malformed URL etc. degrades to fail-open
            _client = None
    return _client


async def close_client() -> None:
    """Close the shared client (called on app shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _scope(key: str) -> str:
    """Hash the raw key so no PII (emails/IPs) is written into Redis keys."""
    return hashlib.sha256(key.encode()).hexdigest()[:40]


async def allow(
    key: str,
    *,
    max_requests: int,
    window_seconds: int,
    cooldown_seconds: int = 0,
) -> bool:
    """Return True when the caller may proceed, False when throttled.

    ``key`` is any stable caller dimension (IP, hashed email). Requests are
    counted in a sliding-ish window via INCR + EXPIRE; when ``cooldown_seconds``
    is set, the key is allowed only if the cooldown flag was just created —
    i.e. send attempts are spaced at least cooldown_seconds apart.
    """
    client = _get_client()
    if client is None:
        return True

    token_key = f"rl:token:{_scope(key)}"
    try:
        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(token_key)
            pipe.expire(token_key, window_seconds)
            count = (await pipe.execute())[0]
        if int(count) > max_requests:
            return False

        if cooldown_seconds:
            cooldown_key = f"rl:cooldown:{_scope(key)}"
            if not await client.set(cooldown_key, "1", nx=True, ex=cooldown_seconds):
                return False

        return True
    except Exception:  # noqa: BLE001 - fail open on Redis errors
        return True
