# Verification scripts

Tools for checking the running stack against reality. They exist because a green CI
run is not evidence the application works — during the audit that produced issues
#978, #980 and #981, CI was fully green while the site rendered a blank page,
registration silently failed, and five services could not start.

Run these from the **repo root** (they resolve `playwright-core` from there or from
`apps/web`).

| Script | What it answers |
|---|---|
| `browser-check.mjs` | Does each page actually render in a browser? |
| `causation-check.mjs` | *Why* does it break — which header or setting is the cause? |
| `auth-flow-check.mjs` | Can a real user register and stay signed in? |
| `verify-csp-nonce.mjs` | Do the CSP nonces actually reach the rendered scripts? |
| `compose-smoke.sh` | Does the whole stack come up, and do real routes answer? |
| `kafka_init.py --print-plan` | Which topics and per-service ACLs should the broker have? |
| `find_vacuous_tests.py` | Which tests assert nothing, and therefore cannot fail? |

All are read-only against the API except `auth-flow-check.mjs`, which creates two
throwaway accounts per run (`agent-api-*` and `agent-ui-*` at `example.com`).

---

## `compose-smoke.sh`

Brings the full dev stack up and probes it. Three things about it are worth knowing
before changing it.

**It bootstraps schemas inside the containers, not on the host.** A fresh volume has
databases and no tables — `infrastructure/database/init-databases.sql` stops at
databases/users/extensions and no service calls `create_all` at startup — so every
table-backed route 500s while `/health` stays green, because health is a bare
`SELECT 1` and passes against an empty database. `scripts/schema_bootstrap.py` is the
per-service half of `init_schemas.py`, piped into each service's own container with
`compose exec -T -w /app <svc> python -`, for the services that declare a
`DATABASE_URL`. That indirection exists because the CI job installs no Python
packages on purpose, and installing SQLAlchemy in the job would resolve independently
of the service locks. Bootstrap failures are reported and logged, not fatal: the probes
are the verdict, and two services refuse to bootstrap for an unrelated model bug.

**The wait loop reads healthchecks from the Compose config, not from `docker compose
ps`.** 15 of the 29 services have no healthcheck and report `Health: ""` forever, so a
loop that demanded `healthy` from every container could never succeed. A service with no
`healthcheck` only has to be `running`; one with a disabled healthcheck counts as having
none; `exited`/`dead` is a terminal failure whatever its health.

**It times out far later than it looks like it should.** `compose up -d --build` for 29
containers is most of the wall clock; the observed time from "stack started" to "all
services ready" was 11s of a 420s budget. Raising `COMPOSE_SMOKE_WAIT_SECONDS` is
therefore very unlikely to fix anything — read the `PENDING:`/`READY:` line in the log
first, and note that the table printed *before* it is the previous poll, not the last.

```bash
bash scripts/compose-smoke.sh                       # against deployments/docker-compose.dev.yml
COMPOSE_SMOKE_WAIT_SECONDS=900 bash scripts/compose-smoke.sh
COMPOSE_SMOKE_LOG=/tmp/smoke.log bash scripts/compose-smoke.sh
```

`COMPOSE_SMOKE_LOG` holds the full `compose logs` dump, which is the artifact to read
first — it names the exception behind any probe failure, where the script's own output
only says which route failed.

---

## `browser-check.mjs`

`curl` cannot tell you whether a page hydrated. During the blank-page outage every
route returned HTTP 200 with an empty body, and a curl-and-grep for `<input`
confidently reported "0 inputs" on a page a browser rendered with five.

Reports, per route: document status, input count after hydration, CSP violations the
browser actually logged, visible text, and **where the browser ended up** after
redirects — an auth bounce is usually the real story (`/browse` → `/login`).

```bash
node scripts/browser-check.mjs                      # /, /login, /signup, /browse
node scripts/browser-check.mjs / /signup            # specific routes
BASE_URL=http://localhost:3000 node scripts/browser-check.mjs
```

Exit 0 if every route rendered content and logged no CSP violation. It uses a fresh
browser context per route, so a session cookie cannot make the next route look
authenticated — a classic false pass.

---

## `causation-check.mjs`

Strips exactly one response header, reloads, and reports whether the symptom
disappears. If it does, that header is the cause. If it does not, your hypothesis is
wrong and you have ruled a layer out — which is worth more than the fix.

This is how the blank page was diagnosed: remove only `content-security-policy`, and
the page renders with five inputs. One run, and the CSP went from "probably involved"
to a demonstrated cause.

```bash
node scripts/causation-check.mjs                              # CSP on /signup
node scripts/causation-check.mjs /browse x-frame-options      # any header
KEEP_HEADER=1 node scripts/causation-check.mjs                # reverse the polarity
```

Exit codes: `0` cause supported · `1` hypothesis not supported · `3` nothing to
explain (the page already renders, or a navigation failed).

---

## `auth-flow-check.mjs`

Registration returning **201 is not sufficient**. Three defects once stacked behind a
single "Could not create the account" message: the page was blank, the session cookie
was rejected because the token was longer than the route's cap, and a hard reload
502'd. A check that stops at the 201 catches none of them.

It walks the whole chain — register, set session, read session server-side, then
submit the **real signup form in a browser** and hard-reload a protected route.

```bash
NODE_EXTRA_CA_CERTS=apps/web/certificates/localhost.pem node scripts/auth-flow-check.mjs
ALLOW_INSECURE_TLS=1 node scripts/auth-flow-check.mjs        # alternative to the above
```

Exit 0 only if a user can register, be recognised, and still be signed in after a
reload. Exits `3` and aborts if registration itself fails, so a rate-limited setup
cannot be misreported as a session regression.

Notes on two things that make it correct rather than merely plausible:

- It submits the form instead of injecting a cookie. A `__Host-` cookie must carry no
  `Domain` attribute, so `addCookies({ domain })` produces a cookie the browser
  silently drops — the reload then "fails" for a reason unrelated to the product.
- It uses two different addresses. The API probe registers one; the form step
  registers another. Reusing one address returns `409`, which is the service behaving
  correctly but looks like a script failure.

---

## `kafka_init.py`

Creates the dev stack's Kafka topics and per-service ACLs. Normally you do not run
it by hand — it runs as the one-shot `kafka-init` compose service, and every
Kafka-using service waits on `service_completed_successfully`. Run it directly
when you want to inspect the plan, or to re-apply it after changing
`_SERVICE_ACL` in `packages/sdk/wildframe_events/topics.py`.

```bash
# what it would do, with no broker and no credentials needed
python scripts/kafka_init.py --topics-registry packages/sdk/wildframe_events/topics.py --print-plan

# apply it (must run inside the cp-kafka image, which has the CLIs)
docker compose -f deployments/docker-compose.dev.yml up kafka-init

# what the broker actually has now
docker compose -f deployments/docker-compose.dev.yml exec kafka \
  kafka-acls --bootstrap-server localhost:29092 \
             --command-config /etc/kafka/kafka-client.properties --list
```

It exists because the broker runs `AclAuthorizer` with
`allow.everyone.if.no.acl.found=false` and `auto.create.topics.enable=false`, and
nothing created a topic or an ACL until now — so every publish failed with
`TOPIC_AUTHORIZATION_FAILED` (issue #893). The ACLs are derived from
`_SERVICE_ACL`, which is the source of truth for the matrix, and are per
`(principal, topic, operation)`: no wildcard principals, no wildcard topics.

Two errors that look identical and are not: with no ACL, `kafka-topics
--describe` reports a topic that really exists as `does not exist as expected`,
and a producer gets `TOPIC_AUTHORIZATION_FAILED` whether or not the topic
exists. The authorization check runs before the topic lookup, so "denied" masks
"missing". `tests/test_kafka_init.py` guards both halves.

---

## `find_vacuous_tests.py`

Finds test functions that assert nothing, so a refactor can delete the behaviour
under test and CI stays green. Issue #908.

```bash
python scripts/find_vacuous_tests.py                  # whole repo
python scripts/find_vacuous_tests.py --root tests      # one subtree
python scripts/find_vacuous_tests.py --list            # file:line only
python scripts/find_vacuous_tests.py --report json     # machine-readable
python scripts/find_vacuous_tests.py --severity decorative
python scripts/find_vacuous_tests.py --self-test       # verify the detector
```

Exit codes: `0` clean · `1` findings above threshold · `2` usage error · `3`
`--self-test` failed, so the verdict is not evidence. `--max-vacuous N` exits 0
while the count is `<= N`, for gating a tree that is not clean yet.

**Run `--self-test` after editing it.** A vacuous-test detector that stops
detecting reports "0 findings", which reads as good news — the same failure mode
the script exists to catch, one level up.

### `assert` is not the only assertion syntax, and getting that wrong is the whole trap

A token scan — "does the body contain `assert`?" — reports **817 of 6,976
tests (11.7%)** on this branch. Issue #908 quoted 184/1,388 (13.3%), which is
the same ratio, so its number almost certainly came from that scan. It is not a
count of tests that cannot fail, because of what the other 817 assert through:

| mechanism | count | example |
|---|---|---|
| `assert` statement | 6,150 | `assert user.id == actor` |
| `pytest.raises` / `pytest.fail` | 602 | `with pytest.raises(BillingError):` |
| `unittest.mock` / `TestCase` `.assert*` | 145 | `svc.subscribe.assert_not_awaited()` |
| same-module helper | 10 | `_assert_valid(x)` |
| **asserts nothing** | **61** | `await _enforce_auth_version(...)` |

`svc.subscribe.assert_not_awaited()` raises `AssertionError` like any other
assert. Count those as vacuous and you get a list that is large, alarming and
wrong — which is the likely reason a real 0.9% defect rate was reported as
13.3% and then ignored. The real rate is **61 / 6,976 = 0.9%**.

Two consequences for anyone changing this script:

- Removing mock/`pytest.raises` recognition from `_is_pytest_assert` or
  `_is_mock_or_unittest_assert` inflates the number by ~750 overnight.
  `tests/contract/test_no_vacuous_tests.py` pins both.
- `def test_app` decorated `@pytest.fixture` is **not a test**. Several suites
  name the app-building fixture that way. Counting them is a false positive
  against code pytest never runs, and it inflates the denominator.

### What it cannot see

Helper resolution is same-module by default (`--follow-imports` widens it), and
assertions inside a **fixture** are not credited to the test that uses it. A
test that delegates all of its checking elsewhere is reported vacuous. That
direction is deliberate: the tool under-reports rather than accuses.

### Reading the output

The severity tiers are ranked by how easily a reviewer dismisses a finding, not
by how often it occurs:

- `decorative` — the body is one statement or a docstring. An obvious
  placeholder; cheap to fix.
- `unasserted` — a substantial body that runs real code and asserts nothing.
  **This is the dangerous one.** `test_matching_auth_version_is_accepted` builds
  a mock client, patches the transport, calls the function — and proves nothing,
  because "does not raise" is not an assertion. Delete the body and it still
  passes.
- `swallowed` — an assertion *is* present but a bare `except:` or
  `except Exception:` can discard it, so the test passes either way.

---

## Prerequisites

`playwright-core` and a Chromium build. No browser download is needed if you point at
one:

```bash
npm i -D playwright-core                       # from repo root
CHROMIUM_PATH=/path/to/chrome node scripts/browser-check.mjs
```

The scripts auto-discover Playwright's cache under `~/.cache/ms-playwright`.

For the self-signed dev certificate, prefer passing the cert as a CA
(`NODE_EXTRA_CA_CERTS`) over `ALLOW_INSECURE_TLS=1`. TLS verification is never
disabled silently.
