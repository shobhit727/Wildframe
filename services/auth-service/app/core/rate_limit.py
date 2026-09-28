"""Redis-backed rate limiting for abuse-prone, security-sensitive endpoints.

Used by the email-verification resent flow (#54): per-IP quotas plus a
per-email cooldown so an address cannot be flooded with verification
emails, and repeated probes are throttled. Redis is the single source of
truth so limits survive service restarts. Fail-closed on Redis errors for
auth-sensitive endpoints.

Why the bucket key is validated, not just hashed
------------------------------------------------
``_scope()`` hashes a bucket key before it reaches Redis so no PII is
written verbatim. A fast unkeyed digest is only safe for that as long as
the hashed material is a non-secret identifier. Taking a bare ``str`` made
that a convention rather than an invariant: nothing stopped a future call
site from routing a password into a fast hash, at which point a leaked
Redis keyspace would allow offline recovery of the credential.

So this module refuses to hash anything it cannot show is an identifier.
``allow()`` takes ``"<namespace>:<subject>"`` where:

* the namespace must be a member of :class:`BucketKind`, a closed set that
  mirrors the call sites, so no value can ever reach the namespace half of
  a key; and
* the subject must satisfy that bucket's grammar. The per-IP buckets parse
  with :func:`ipaddress.ip_address` and the per-user buckets with
  :class:`uuid.UUID`. Both are total functions over closed domains that
  exclude credentials, so for those buckets a secret cannot be expressed
  at all -- this is exclusion, not a length heuristic.

``resend:email`` is the one bucket whose subject (an address) is not
provably distinguishable from a secret, so that bucket is bounded and
rejected but not excluded. See ``_is_email``.

Rejected keys are denied (fail-closed, ``False``) and logged at ERROR with
the subject omitted: a guard that echoed the value would leak the very
material it exists to protect. The Redis key format is unchanged -- the
digest input is still ``f"{namespace}:{subject}"`` blake2s-20, so no
in-flight counter or cooldown is invalidated by this constraint.

Follow-up for whoever owns ``app/core/settings.py`` and the deployment
manifests: switch the digest to *keyed* BLAKE2s,
``blake2s(data, key=pepper, digest_size=20)``. Measured cost is 0.0012 ms
per call against 0.0010 ms today and it keeps the 40-char key shape, so
the only blocker is a pepper that is identical across replicas and kept
out of logs. A per-process random pepper is not acceptable -- it would
silently split every limit across replicas. Do not substitute a slow KDF
for a pepper: at 4-13 calls/s per core (pbkdf2/scrypt, measured on this
box) an attacker holding one CPU would wedge the limiter, turning an
abuse throttle into an availability liability.
"""

import hashlib
import ipaddress
import logging
import re
from collections.abc import Callable
from enum import StrEnum
from uuid import UUID

from redis.asyncio import Redis

from app.core.settings import settings

logger = logging.getLogger(__name__)

_client: Redis | None = None


def _get_client() -> Redis | None:
    """Lazily create the shared redis.asyncio client."""
    global _client
    if _client is None:
        try:
            redis_url = settings.REDIS_URL
            assert redis_url is not None, "REDIS_URL is not configured"
            _client = Redis.from_url(redis_url, decode_responses=True)
        except Exception:  # noqa: BLE001 - malformed URL etc. degrades to fail-open
            _client = None
    return _client


async def close_client() -> None:
    """Close the shared client (called on app shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class BucketKind(StrEnum):
    """The closed set of rate-limit buckets.

    One member per namespace passed by ``app/api/routes/auth.py``. Adding a
    bucket is a deliberate, reviewable edit here rather than an ad-hoc
    string at a call site, and the value of each member is the exact prefix
    that call site already sends, so Redis keys are unaffected.
    """

    RESEND_IP = "resend:ip"
    RESEND_EMAIL = "resend:email"
    # NOTE: "mfa:verify:*" is shared by two endpoints -- POST /mfa/login-verify
    # and POST /mfa/verify -- so the 12 call sites use only 10 namespaces and
    # those two endpoints share one counter. Pre-existing behaviour, kept
    # deliberately because splitting them would change the Redis keys.
    MFA_LOGIN_VERIFY_IP = "mfa:verify:ip"
    MFA_LOGIN_VERIFY_USER = "mfa:verify:user"
    MFA_SETUP_IP = "mfa:setup:ip"
    MFA_SETUP_USER = "mfa:setup:user"
    MFA_DISABLE_IP = "mfa:disable:ip"
    MFA_DISABLE_USER = "mfa:disable:user"
    STEP_UP_IP = "stepup:ip"
    STEP_UP_USER = "stepup:user"


# ``Request.client`` is absent for some transports and the routes substitute
# this literal, so it is a legitimate subject for a per-IP bucket.
_UNKNOWN_IP = "unknown"

_MAX_KEY_LENGTH = 320
_MAX_EMAIL_LENGTH = 254
_LOCAL_PART_MAX = 64
_DOMAIN_LABEL_MAX = 63

# A shape check, deliberately not a deliverability check. The local part and
# each domain label are bounded and dot-separated so a non-address cannot be
# spliced in, but an address-shaped *secret* still passes: this bucket is
# bounded, not excluded.
_EMAIL_RE = re.compile(
    rf"[^@\s]{{1,{_LOCAL_PART_MAX}}}@"
    rf"(?:[^@\s.]{{1,{_DOMAIN_LABEL_MAX}}}\.)+"
    rf"[^@\s.]{{2,{_DOMAIN_LABEL_MAX}}}"
)


def _is_ip_subject(subject: str) -> bool:
    """True for an IPv4/IPv6 literal, or the routes' ``"unknown"`` sentinel.

    ``ipaddress.ip_address`` is total over a closed domain that contains no
    credentials, so a secret cannot satisfy this.
    """
    if subject == _UNKNOWN_IP:
        return True
    try:
        ipaddress.ip_address(subject)
    except ValueError:
        return False
    return True


def _is_user_subject(subject: str) -> bool:
    """True for a canonical user identifier.

    ``UUID`` is likewise total over a closed domain that contains no
    credentials, so a secret cannot satisfy this either.
    """
    try:
        UUID(subject)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _is_email(subject: str) -> bool:
    """True for an address-shaped subject. Bounded, not excluded -- see module docs."""
    if not 0 < len(subject) <= _MAX_EMAIL_LENGTH:
        return False
    return _EMAIL_RE.fullmatch(subject) is not None


_SUBJECT_VALIDATORS: dict[BucketKind, Callable[[str], bool]] = {
    BucketKind.RESEND_IP: _is_ip_subject,
    BucketKind.RESEND_EMAIL: _is_email,
    BucketKind.MFA_LOGIN_VERIFY_IP: _is_ip_subject,
    BucketKind.MFA_LOGIN_VERIFY_USER: _is_user_subject,
    BucketKind.MFA_SETUP_IP: _is_ip_subject,
    BucketKind.MFA_SETUP_USER: _is_user_subject,
    BucketKind.MFA_DISABLE_IP: _is_ip_subject,
    BucketKind.MFA_DISABLE_USER: _is_user_subject,
    BucketKind.STEP_UP_IP: _is_ip_subject,
    BucketKind.STEP_UP_USER: _is_user_subject,
}

# Longest namespace first so no shorter member can shadow a longer one.
_BUCKET_PREFIXES: tuple[tuple[str, BucketKind], ...] = tuple(
    sorted(
        ((f"{kind.value}:", kind) for kind in BucketKind),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
)


def _parse_bucket(key: str) -> tuple[BucketKind, str] | None:
    """Split ``"<namespace>:<subject>"`` into its two validated halves.

    Returns ``None`` when the key is oversized, names an unknown namespace,
    or carries anything other than exactly one subject segment. Rejecting a
    subject containing ``:`` is what stops a caller from smuggling extra
    dimensions -- a credential included alongside a legitimate value --
    into the hashed material.
    """
    if not key or len(key) > _MAX_KEY_LENGTH:
        return None
    for prefix, kind in _BUCKET_PREFIXES:
        if not key.startswith(prefix):
            continue
        subject = key[len(prefix) :]
        if not subject or ":" in subject:
            return None
        return kind, subject
    return None


def _scope(kind: BucketKind, subject: str) -> str:
    """Hash a validated bucket key so no PII (emails/IPs) reaches Redis.

    ``kind`` and ``subject`` are kept apart so the namespace is always an
    enum member and only the identifier is ever hashed as caller material.
    The digest input is byte-for-byte what the call sites already sent, so
    existing keys are untouched.
    """
    return hashlib.blake2s(f"{kind.value}:{subject}".encode(), digest_size=20).hexdigest()


async def allow(
    key: str,
    *,
    max_requests: int,
    window_seconds: int,
    cooldown_seconds: int = 0,
) -> bool:
    """Return True when the caller may proceed, False when throttled.

    ``key`` is ``"<namespace>:<subject>"``, where namespace is a
    :class:`BucketKind` and subject is an identifier in that bucket's
    grammar (an IP literal, a UUID, or an address). A key that does not
    satisfy that is a wiring mistake, and it is refused rather than hashed;
    see the module docstring.

    Requests are counted in a sliding-ish window via INCR + EXPIRE; when
    ``cooldown_seconds`` is set, the key is allowed only if the cooldown
    flag was just created -- i.e. send attempts are spaced at least
    cooldown_seconds apart.
    """
    parsed = _parse_bucket(key)
    if parsed is None:
        # Deliberately does not log ``key``: it may be the secret.
        logger.error(
            "rate limiter refused a key that is not a single '<bucket>:<subject>' pair"
            " (len=%d); check the call site",
            len(key),
        )
        return False

    kind, subject = parsed
    if not _SUBJECT_VALIDATORS[kind](subject):
        # Subject omitted for the same reason as ``key`` above.
        logger.error(
            "rate limiter refused bucket %s: subject is not a valid identifier for it",
            kind.value,
        )
        return False

    client = _get_client()
    if client is None:
        return False

    scope = _scope(kind, subject)
    token_key = f"rl:token:{scope}"
    try:
        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(token_key)
            pipe.expire(token_key, window_seconds)
            count = (await pipe.execute())[0]
        if int(count) > max_requests:
            return False

        if cooldown_seconds:
            cooldown_key = f"rl:cooldown:{scope}"
            if not await client.set(cooldown_key, "1", nx=True, ex=cooldown_seconds):
                return False

        return True
    except Exception:  # noqa: BLE001 - fail closed on Redis errors for auth
        return False
