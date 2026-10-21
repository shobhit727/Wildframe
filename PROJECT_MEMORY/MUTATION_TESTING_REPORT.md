# Mutation-testing report — finding tests that cannot fail

Branch: `audit/fix-open-github-issues` · HEAD at time of run: `1887980e`

Produced by four subagents mutating source and watching the suite. Three
completed; the auth-service agent was cut off mid-run and its area is **not**
covered here. All baselines were restored exactly and `git status` was clean at
the end — no mutant reached a commit.

## Why this exists alongside `find_vacuous_tests.py`

`scripts/find_vacuous_tests.py` finds tests with **no** assertion. That is the
weakest form of the problem and it cannot see a test that asserts the wrong
thing. Mutation testing asks the only question that matters: if the behaviour
were broken, would this test go red?

## Scores

| Area | Mutants | Killed | Survived | Score |
|---|---|---|---|---|
| `wildframe_compliance` | 13 | 12 | 1 | 92% |
| `wildframe_auth` | 12 | 11 | 1 | 92% |
| `wildframe_events` | 11 | 11 | 0 | 100% |
| billing-service | 33 | 29 | 4 | 88% |
| admin-service | 16 | 15 | 1 | 94% |
| api-gateway | 22 | 19 | 3 | 86% |
| media-pipeline | 21 | 16 | 5 | **76%** |
| content-service | 15 | 12 | 3 | 80% |
| user-service | 21 | 18 | 3 | 86% |
| **Total** | **164** | **143** | **21** | **87%** |

Every area ran a **control** mutation it expected to be caught, so "nothing
survived" is never indistinguishable from a broken harness. Examples:

- billing `from_minor_units(...)` → `Decimal(...) / 100`: caught, with
  `assert Decimal('10') == Decimal('1000')` — the exact money error.
- media-pipeline `_storage_is_durable()` → `if False:`: caught by
  `test_storage_durability.py` (restores the #1000 defect).
- SDK `CA_QC`'s parent jurisdiction → US: caught.
- gateway `_unknown_kid` rotation logic: caught.

## CRITICAL — the gateway age gate is dead code

**This is a real defect, not a test gap.** Verified independently:

- `check_age_gate` is referenced **only in test files**. No production module
  calls it.
- `age_middleware` (`app/middleware.py:1084`) is a pure pass-through that sets
  `Vary` and never calls `check_age_gate` — and it is **not registered in
  `app/main.py`** either.
- 16 unit tests call `check_age_gate` directly and all pass.

So `/maturity/*`, `/purchase` and `/content/restricted` are **not age-gated at
runtime**. A child-safety control is absent from the request path while its unit
suite is green.

`age_gate.py:38` documents itself as "called from proxy middleware". It is not.

**Not fixed here.** Wiring a new middleware into the gateway's request path is a
behaviour change to a compliance control, and needs an owner decision: which
routes, which upstream services, and what happens when the caller's age claim is
absent versus forged.

## Highest-risk test gaps

**1. The ads write endpoint can be exposed and nothing fails** — content-service

`app/main.py` deliberately does **not** mount the ads router, because
`create_ad` has no auth dependency and mounting it would open an unauthenticated
write surface (decision recorded in `73ff0485`'s commit message and a test
docstring). Mutating `main.py` to mount it survives: all 446 tests pass, and the
production OpenAPI then contains `/ads` and `/ads/check` with `get_db` as the
only dependency.

Nothing **executable** enforces a decision that only exists in prose. One
`include_router` line silently opens an unauthenticated write endpoint.

**2. The rate limiter's Lua is never executed** — api-gateway

`app/middleware.py:263` runs a Lua script under Redis. Both rate-limit test
files use a Python `FakeRedis` that **re-implements the Lua**, including its own
`if len(leases) >= int(limit)`. Mutating the production script's `>=` to `>`
survives, because the test asserts against a *copy* of the logic.

This is AGENTS.md §19.1 exactly: the test mocks the layer where the defect
lives. Impact: the real limiter admits `limit + 1` concurrent requests per key.

**3. Two tests named `..._rolls_back` assert nothing about rollback** — user-service

`test_subscription_create_default_rolls_back_on_a_duplicate_user`
(`test_core_repositories.py:352`) and `test_preference_create_default_rolls_back_...`
(`:282`) both `pytest.raises` and never assert `rollback()` was called. Deleting
the `rollback()` from both repositories survives.

**4. A test that asserts, and proves nothing** — media-pipeline

`test_media_requires_both_playlists_before_packaged_event` builds
hls-succeeds + dash-fails. `dash_package` is critical, so the job fails and
`advance()` returns via `except PipelineError` — the `and` guard at
`services.py:710` is **never evaluated**. The assertion passes because execution
never reached the code it appears to cover. Verified: the guard *is* reachable
with an hls-only registry, and nothing tests that.

**5. JWT key rotation does not clear stale kid records** — `wildframe_auth`

`_unknown_kids[url] = {}` → `= _unknown_kids.get(url, {})` survives, even though
a test asserts `_unknown_kids[URL] == {}`. It survives through an interaction:
the test advances the clock past the backoff, so pruning has already dropped the
entry and both lines assign `{}`. Proven distinguishable with **two** live kids
where only one rotates. No test in the suite ever has two kids in flight.

## The vacuous-test detector has at least six undeclared blind spots

13 probe classes were written; **all 13 were missed**. The detector reported 0
findings on a file where no test can fail. The classes:

| Class | Missed? |
|---|---|
| `assert broken.compute(5) == broken.compute(5)` (tautology) | yes |
| `expected = got; assert got == expected` (self-computed) | yes |
| `if got is None: assert got == "must not be None"` (dead branch) | yes |
| assert on mock *configuration* rather than behaviour | yes |
| `try: assert ...; except AssertionError: pass` | yes |
| assertion that is green only when production is broken | yes |
| assertion reachable only through another module | yes |

The sharpest: the detector ships a `swallowed` tier, but its handler matcher
checks handler *names* against `("Exception", "BaseException", "suppress",
"pytest.skip")`. Measured:

```
FLAGGED  bare `except:`            FLAGGED  `except Exception:`
MISSED   `except AssertionError:`  MISSED   `except (AssertionError, ValueError):`
MISSED   contextlib.suppress
```

`except AssertionError: pass` is the most direct possible swallow and is
invisible. `contextlib.suppress` is claimed in the tool's own docstring but is
structurally undetectable — it parses as `ast.With` and the check only walks
`ast.Try`.

**So "61 vacuous tests" is a sound floor for the question the tool actually
answers, and not a measure of whether a test can fail.** It should not be cited
as the latter.

## Backlog corrections

`tests/contract/VACUOUS_TESTS_BACKLOG.md` — the 61 are right, the narrative is
not:

- **4 undocumented findings**: `user-service/tests/test_app_lifecycle.py:133`,
  `uploads-service/tests/test_core_events.py:252`,
  `auth-service/tests/test_core_events.py:383` and `:493`.
- **3 section totals wrong**: auth 3→5, recommendation 9→11,
  content `test_routes.py` 7→8.
- **1 mis-cited line**: `test_set_swallows_a_redis_failure` is at
  `test_app_lifecycle.py:1127`, not `:1107`.
- The `packages/sdk` section cites four `wildframe_events/tests/*.py` paths that
  do not exist; the real files are under `packages/sdk/tests/`.
- Any entry whose fix is "assert the return is `None`" should be read with
  care — pinning a no-op return is the weakest fix available and can be
  satisfied by code that does nothing.

## Not covered

`auth-service` — the agent running it was cut off. Its area still needs this
treatment, and it is the most security-critical service in the repo.