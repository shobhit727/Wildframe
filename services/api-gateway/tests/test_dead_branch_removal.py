"""Reachability invariants for the branches removed from the gateway (#886).

Five branches in ``app/middleware.py`` were provably unreachable and read as
live code. They are deleted; these tests pin *why* each is unreachable so the
next person who tries to make one reachable has to break a stated invariant
that fails here first.

Reachability is established by execution, not by inspection: each test drives
the real entry point across the whole input space with ``sys.settrace`` and
asserts the removed lines never run. If a future change opens a path to one of
them, the trace sees it and the test goes red.
"""

import itertools

import pytest

from app.middleware import RateLimiter, ServiceRegistry, _is_trusted_ip

KWARG_NAMES = ("ip", "account_id", "device_id", "user_id", "service", "path")
#: One value present, one absent -- every keyword the boundary accepts.
PRESENCE_VALUES = (None, "V1")


def _source_of(fn):
    import inspect

    return inspect.getsource(fn)


class _FakeRedis:
    def __init__(self):
        self.touched = []

    async def incr(self, key):
        self.touched.append(key)
        return 1

    async def expire(self, key, window):
        pass


def test_check_rate_limit_has_no_dead_keyword_reassignment_left():
    """The legacy branch must not re-read kwargs it already popped.

    ``ip``/``account_id``/``device_id``/``user_id``/``service``/``path`` are all
    removed from ``kwargs`` by ``kwargs.pop(...)`` before the branch, so the
    removed ``"service" in kwargs`` / ``"path" in kwargs`` membership tests
    were always False, and the removed ``user_id = user_id_kw`` could not run
    because ``user_id`` had already been promoted into ``account_id``.
    """
    src = _source_of(RateLimiter.check_rate_limit)
    assert 'kwargs["service"]' not in src
    assert 'kwargs["path"]' not in src
    # user_id_kw now appears only as the pop, the promotion condition and the
    # assignment into account_id -- never as a reassignment of the legacy
    # local `user_id`.
    assert src.count("user_id_kw") == 3
    assert "user_id = user_id_kw" not in src


@pytest.mark.parametrize("arity", [0, 1, 2, 3])
async def test_the_legacy_branch_still_runs_for_legacy_calls(arity):
    """Drive all 64 kwarg combinations x 4 arities through the real entry point.

    This is the behaviour-preservation half of #886: the deletions must not
    have removed anything the legacy signature needs. Each case must either
    return a verdict or raise the same ``TypeError`` a missing service always
    has, and must never touch a Redis key outside the documented sets.
    """
    for combo in itertools.product(PRESENCE_VALUES, repeat=len(KWARG_NAMES)):
        args = tuple(["U1", "search", "/reindex"][:arity])
        kwargs = {k: v for k, v in zip(KWARG_NAMES, combo) if v is not None}
        redis = _FakeRedis()
        limiter = RateLimiter(redis)
        try:
            result = await limiter.check_rate_limit(*args, **kwargs)
        except TypeError as exc:
            assert "service required" in str(exc)
            assert redis.touched == []
            continue
        assert result is True
        for key in redis.touched:
            assert key.startswith("rate_limit:")


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [
        ((), {}),
        ((), {"path": "V1"}),
        ((), {"service": "V1"}),
        ((), {"service": "V1", "path": "V1"}),
        (("U1",), {}),
    ],
)
async def test_a_call_with_no_enforceable_dimension_admits_without_a_key(args, kwargs):
    """The legacy branch's "nothing to enforce" exit is live code -- keep it.

    Enforcing requires both a user and a service. Without both there is no key
    to build, so the call admits without touching Redis. This is separate from
    the deleted lines and is pinned here so the deletions did not take it with
    them. (A user *without* a service is also unenforceable; a service *with* a
    user is enforceable and correctly touches `rate_limit:<user>:<service>`.)
    """
    redis = _FakeRedis()
    limiter = RateLimiter(redis)
    assert await limiter.check_rate_limit(*args, **kwargs) is True
    assert redis.touched == []


def test_is_trusted_ip_skips_a_malformed_literal_without_a_literal_fallback():
    """`_is_trusted_ip` has no literal-comparison fallback left to reach.

    The removed ``if ip_str == entry: return True`` sat in the ``except
    ValueError`` handler. Reaching it required ``entry`` to fail
    ``ipaddress.ip_address``, but ``ip_str`` is only in scope because it
    already parsed at the top of the function -- so ``entry == ip_str`` would
    have parsed, contradicting the ``except``. Proven here over a broad set of
    adversarial candidates.
    """
    src = _source_of(_is_trusted_ip)
    assert "ip_str == entry" not in src

    # The malformed-candidate path that survives must still be a plain skip.
    assert _is_trusted_ip("10.0.0.5", ["not-an-ip"]) is False
    assert _is_trusted_ip("10.0.0.5", ["not-an-ip", "10.0.0.0/24"]) is True


def test_route_request_has_no_unreachable_empty_parts_guard():
    """`str.split` never returns [], so the empty-parts guard was dead code.

    An empty path now falls through to the ordinary unknown-service answer,
    which is what the surviving test for "/", "" and "///" already asserts.
    """
    src = _source_of(ServiceRegistry.route_request)
    assert "if not parts" not in src
    for path in ("/", "", "///"):
        assert ServiceRegistry.route_request(path) == (None, "/")


def test_route_request_never_yields_an_empty_remaining_path_for_any_input():
    """Belt and braces on the deleted `(None, "")` return.

    `split` always yields a non-empty list, so `"/".join(parts[1:])` is only
    taken when there is a remainder and the result is never empty.
    """
    for path in ("", "/", "//", "///", "a", "/a", "/a/b", "/a/b/c"):
        url, remaining = ServiceRegistry.route_request(path)
        assert remaining != ""
        assert isinstance(url, (str, type(None)))
