# Test-quality audit — second pass: the 9 uncovered services

Follow-up to the first mutation-testing pass, which covered only 7 services plus
the SDK. Nine services had **no mutation coverage at all**, and nobody had
independently checked whether the tests the first pass produced actually work.

Four subagents: two smell-hunting across six uncovered services, one
adversarially attacking the previous round's new tests, one mutating the modules
auth/streaming/recommendation had not yet reached.

**Nothing here is fixed.** These are findings. Baselines re-verified on the
restored tree, and `git status --porcelain` is empty repo-wide with no stray
`.MUTBAK`/`.orig` artifacts.

## The pattern that produced every finding below

A mutant that **survives the full suite** means one of two things: production is
correct but unprotected, or **production is wrong and the suite is green**.
The second case turned out to be common enough to be the headline.

---

## PRODUCTION BUGS (shipped green)

### 1. streaming — entitlement bypass on `GET /api/v1/manifests/{manifest_id}`

`app/api/routes/__init__.py:294`. Deleting
`await service.require_manifest_session(...)` leaves all **376 tests green**.

Any authenticated user can fetch **any** manifest by UUID with no active
playback session.

The sibling route `GET /episodes/{id}/manifest` has the identical guard
(`:340`) **and is tested** (`test_routes_gaps.py:552`, `:694`) — which is exactly
why the gap is invisible. The protection exists and is verified on one route and
silently absent on the other.

The only test here, `tests/test_routes.py:299`, asserts `status_code == 200`
against an `AsyncMock` service, where `require_manifest_session` is a no-op
either way.

Compounds with S-R23: `Cache-Control: private, no-store` → `public, max-age=3600`
also survives, and that header *is* asserted for the episode route but not here.
So an entitlement-less manifest that a shared CDN may store.

### 2. search — a token with no `role` claim becomes an admin

`app/core/security.py:114` — `role=str(payload.get("role") or "user")`.
Changing the default to `"admin"` leaves **398 tests green**. Live proof:

```
get_admin_identity GRANTED -> Identity(user_id=…, role='admin', arv=0)
```

`arv` defaults to `0` and `ADMIN_ROLE_VERSION = 0`, so a token carrying only
`sub` reaches the index-mutating routes.

### 3. recommendation — a stated dislike is silently discarded

`app/services.py:357-358`. `PUT /preferences/{user_id}` validates and passes
`disliked_genres`; the service drops it. No test fails. **A user can never
express a dislike.**

### 4. recommendation — a regression the code comment says was already fixed

`app/services.py:266`. `app/services.py:258-263` documents that name-only genre
matching was a past defect that "leaked content from name-only dislikes".
Reverting `resolve()` to slug-only re-introduces it and the suite stays green.
The tests named in that comment cover the **disliked** path, not the liked path.

### 5. recommendation — a test that cannot fail

`test_cache_set_uses_jittered_ttl` asserts `240 <= ttl <= 360`. The fixed TTL is
**300 — inside that range**. Removing the ±60 s jitter added for #456 is
satisfied by the very assertion meant to detect it. It verifies a bound, not a
jitter. The two vacuous tests at `tests/test_cache.py:51,62` carry the literal
comment `# No assertion needed`.

### 6. auth — a case-mismatched email can never be verified

`app/api/routes/auth.py:588` — `email = request.email.strip().lower()`.
`UserRepository.get_by_email` is case-sensitive. A user registered as
`User@Example.com` who requests resend as `user@example.com` silently receives
"no email was sent" and **can never obtain a verification token**. Untested in
either direction.

### 7. auth — MFA brute-force caps are unpinned

`auth.py:275` per-IP `30/900s` and `:281-283` per-user `10/900s`. Raising
either 100× ships green. `test_rate_limit_key_guard.py` (90 tests) pins only the
**key format**, never the **limits** — a test that passes while the control is
deleted.

### 8. auth — JWKS accepts a weakened signing key

`app/security/jwks.py:37` — RSA `key_size=2048` → `1024` survives.
`test_rsa_public_to_jwk_shape` asserts shape, never key size.
Also `jwks.py:17` `urlsafe_b64encode` → `b64encode` survives; the configured test
key's modulus happens to contain no `+/` bytes.

### 9. streaming — a missing `arv` is treated as the admin version

`:129` — `int(payload.get("arv") or 0)` → `or settings.ADMIN_ROLE_VERSION`.
`test_require_admin_rejects_a_stale_role_version` covers a *stale* `arv`, not a
*missing* one. A token with `role: "admin"` and no `arv` is accepted for
`POST /manifests`, `/transcoding-jobs`, `/quality-profiles`, `/cdn-regions`.

### 10. streaming — signed-URL signing and verification have drifted

`generate_signed_url` (`app/services/__init__.py:157`) and
`verify_signed_url` (`:188`) each **duplicate** the HMAC message construction
instead of sharing it. Dropping `episode_id` from the signing message leaves the
whole suite green — the two functions never meet. Signature *verification* is
well tested (7 failures on mutation), so this is precisely the untested seam.

### 11. moderation — the DMCA surface is dead code

`app/api/routes/dmca.py`. `app/main.py:104` includes only `moderation_router`.
Both DMCA endpoints (takedown, counter-notice) are **unreachable**.

Two tests pin this as intended (`test_unmounted_routers.py:58`,
`test_app_lifecycle.py:347`), so it looks deliberate — but an entire
copyright-compliance surface is dark and nobody flags it.

### 12. uploads — a cleanup failure is swallowed

`S3StoragePort.cleanup_upload`. Its docstring says a non-404 failure must
propagate "so the caller keeps `storage_cleaned_at` unset and the reaper retries;
swallowing it would mark the session cleaned while the bytes stayed in the bucket
forever." Replacing the raise with `pass` survives the whole suite. Only the 404
path is tested; `test_upload_failure_paths.py:414` patches `cleanup_upload`
itself, so the adapter's error *classification* is never exercised.

### 13. search — the Kafka offset commit's partition is never tested

`_envelope(topic, payload, partition=0, ...)` defaults to 0 and all three call
sites pass only `offset=`. Hardcoding `TopicPartition(msg.topic, 0)` leaves 398
tests green. Off-by-one on partition means skipping or replaying events in a
multi-partition topic.

### 14. notification — truncation limits are duplicated and untied

`models.py:27-28` declares `String(255)` / `String(1000)` while
`TITLE_MAX_LENGTH` / `MESSAGE_MAX_LENGTH` live in a different file. Raising the
sanitisation limits 10× leaves 302 tests green; a 900-char title survives. One
constant edit from `StringDataRightTruncation` on every send — which is exactly
what `test_sanitization.py:7` says the truncation exists to prevent.

---

## CI gap: the Redis tests do not run

`.github/workflows/ci-cd.yml` `backend-test` (line 283) declares `services:`
containing **only** `postgres:15`. The single `redis` occurrence in the workflow
is a `pip install redis` in the unrelated SDK job. `WILDFRAME_TEST_REDIS_URL` is
set nowhere.

Empirically, with Redis stopped: **1 passed, 6 skipped**. So in CI the 6
behavioural Lua tests never execute, and the mutant is killed only by a literal
substring assertion on the script text. The file documents this honestly
("WEAKER PROXY… executes nothing"), so it is not hidden — but the behavioural
claim does not hold in CI.

**And one of those tests is not hermetic.** `test_a_partially_taken_lease_is_rolled_back`
hardcodes `account_id="acct"` and never releases its lease (60s TTL):

```
GREEN code, fresh db15  -> 1 passed
GREEN code, immediate re-run -> 1 failed   assert False is True
```

That failure is on the *first* acquire — pure leftover state. CI never sees it
because it skips.

---

## The previous round's tests: verified

Attacked independently, mutation applied by the reviewer rather than trusted from
the author. **11 of 12 VERIFIED** — each fails under its claimed mutant, for the
right reason, with a failure message naming the actual defect.

- **Age-gate 403 provenance is airtight.** The fake upstream hardcodes 200, the
  only 403 emitter is `age_gate.py:115`, the rate limiter emits 429. Under the
  mutation the same requests flip to 200 and the test asserts upstream was not
  called.
- **The ads auth-surface test is stronger than claimed.** The reviewer ran the
  *inverse* mutation — mounting the router **with** `dependencies=[Depends(get_current_user)]`
  — and 5 tests passed, so a legitimately authenticated router stays green.
- **Weakest found:** `test_the_table_has_exactly_one_row_per_payout_status` is
  `assert len(PAYOUT_TRANSITIONS) == len(PayoutStatus)`. Near-tautological: the
  whole class asserts properties of a literal constant, so it only fails if
  someone edits the literal.
- **Undocumented structural blindness:** `test_rate_limiter_dual.py`'s
  `FakeRedis.eval` re-implements the Lua in Python. Under the Lua `>=`→`>` mutation
  it is **48 passed, 0 failed** — and it carries no note saying so, in the same
  directory as the file that exists to fix exactly this.

---

## Mutation scores, uncovered services

| Service | Mutants | Score | Notable |
|---|---|---|---|
| creators | 30 | **100%** | strong |
| uploads | 25 | 96% | strong |
| search | 24+4 | 96% | 2 real gaps, one an admin bypass |
| notification | 26 | 92% | 3 gaps |
| analytics | 17 | 94% | 16/16 reachable |
| **auth** | 68 | **86.8%** | 9 survivors, incl. 2 user-facing bugs |
| **streaming** | 41 | **80.5%** | 8 survivors, incl. an entitlement bypass |
| **recommendation** | 31 | **75.9%** | 7 real survivors, incl. 2 bugs |
| moderation | 25 | **72%** | 6 gaps, worst service |

**One flaky kill, worth knowing:** in moderation, mutating
`byteorder="big", signed=True` → `signed=False` was caught by `TestLockCreator` —
but only in **20 of 25 runs**. The kill depends on the top bit of a random
`uuid4()`. Roughly 12.5% of runs would let it through. A test that catches a
mutation one run in five is not catching it.

## Defective-test classes, counted

Mechanical classes (tautology, self-computed expectation, dead-branch, mock-config)
are largely **absent** repo-wide — most suites use hand-written fakes rather than
Mocks. The two dominant real-world classes here:

- **"Named for something it does not test."** Found: `..._rolls_back` (auth ×2,
  user ×2, notification ×1) asserting only `pytest.raises`; `test_create_ad_commits`
  reading back through the writing session; `test_get_manifest` against a no-op mock;
  `test_get_for_update`-adjacent tests that compile their own `select()` and never
  call the repository.
- **"Mocks the layer the defect lives in."** Found: 46 gateway tests re-implementing
  the Redis Lua; moderation's `FakeStrikeRepo.lock_creator` (a no-op append) making
  the real advisory lock invisible; notification's `MagicMock` session being unable
  to model one poisoned by an `IntegrityError`.

**One large cluster of pure noise:** `moderation-service/tests/test_models.py`
holds ~50 assertions that assign a literal to a plain Python attribute and assert
it reads back. Zero production code runs. Proven: defaulting
`DMCATakedown.reason` and `.content_id` to attacker-controlled values and re-running
those six files — **14 passed**.

Also: `test_creators_final2.py` and `test_creators_final3.py` are byte-for-byte
duplicates of each other.

## Equivalence, and what is deliberately not a gap

Reported as gaps and then **disproved**, rather than inflating the count:

- Removing `save()`'s `updated_at` assignment survives — but the column carries
  `onupdate=`. Equivalent mutant.
- Removing `if redis_url is None` in recommendation — `redis.from_url(None)` raises
  `ValueError`, already caught by the surrounding `except Exception`. Equivalent.
- `RSAPublicNumbers` has no `.d` attribute, so that mutation is a true no-op.
- `test_es_lifecycle.py:983` `assert _cursor_secret() == _cursor_secret()` looks like
  a tautology but is load-bearing: a broken process-cache would return a fresh
  `token_bytes(32)`. Mutation killed it.
- `search-service/test_app_lifecycle.py:362` looked like it tested a Starlette
  default; deleting the handler block made it fail. Sound.

## Method note

Three of four agents reported **false survivors** at least once — every time
because the file holding the real assertions was outside their scoped selection.
One agent's *first control mutation survived*, for the same reason.

One agent ran two phase-2 batches concurrently, each applying a mutation while
the other's full suite ran. That produced two false **CAUGHT**s whose failure text
came from the other batch's mutation. Its rule is worth keeping:

> Contamination can only add failures, so SURVIVED verdicts are always
> trustworthy; CAUGHT verdicts from a concurrent run are not.

## Not covered

auth-service `app/api/routes/privacy.py` (464 lines), `app/api/routes/age.py`
(106), and `SecretCipher` remain unmutated — the obvious next pass. Streaming's
`app/services/__init__.py` HMAC seam has 1 mutant covered of many.

No running-stack verification; these are suite-level findings, and nothing
shipped.