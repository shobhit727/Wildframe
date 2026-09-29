# One-R Tasks — everything a human has to do

**Branch:** `audit/fix-open-github-issues` · **PR:** [#938](https://github.com/shobhit727/Wildframe/pull/938) (OPEN, MERGEABLE, no review yet) · **Base:** `main` (protected — do not push, merge, or force-push it)

Last updated: 2026-09-28. New agents should start with `ONBOARDING.md`. Produced while fixing the Docker stack on the audit branch.
Every item below is a decision, a review, or a piece of work an agent deliberately
did not do. Nothing here is a surprise left at the end — the reasoning is in the
commit messages and on `Message-board.md`.

---

## BLOCKER — CI and the image build are broken

### 1. `npm run build` fails with 11 pre-existing TypeScript errors

**Owner: unassigned. This is the highest-value thing left.**

`apps/web` does not typecheck, which fails the CI `Build` step and
`docker compose build web`. Introduced by commit `e8b6666b` (content-normalization
types), not by any of the outage fixes.

The dominant shape of the error:

```
Types of property 'is_premium' are incompatible.
  Type 'boolean | undefined' is not assignable to type 'boolean'.
```

Files involved: `apps/web/e2e/fixtures.ts`, `apps/web/src/api/client.ts`,
`apps/web/src/__tests__/client.test.ts`.

**How to confirm:** `cd apps/web && npx tsc --noEmit`

**Do not** paper over it with `typescript.ignoreBuildErrors` in `next.config.ts`.
Three agents used that as a local-only workaround to get an image built during
diagnosis and reverted it each time. It hides the real work.

### 2. Three pre-existing vitest failures

`apps/web/src/__tests__/client.test.ts` and `client.data.test.ts`. Same origin as
(1). Verify with `cd apps/web && npm run test` before assuming a change caused them.

### 3. `services/content-service/tests/conftest.py` fails collection

Line 13: `from tests._test_jwks import JWKS` — a broken import left mid-edit by
another agent. Until it is fixed, **content-service's test suite cannot be collected
at all**, which hides any regression in that service.

---

## SECURITY — needs a human decision, not an agent

### 4. A committed credential, and a dispute about whether it is authorized

The git log contains a redaction and then two reverts of that redaction:

```
37b811e3 Revert redaction of owner-authorized dev credential
90db934d Revert "security: redact a committed credential, and guard the board against a repeat"
1c25a44e Revert "docs(board): remove a credential fragment I quoted into my own entry"
```

There is a disagreement on the board about whether the owner authorized this
credential. **I deliberately did not rule on it.** An agent should not decide
whether a secret in the repository is acceptable, and the evidence is contradictory
by construction: one commit says "authorized", another says it should never have
been quoted.

**Decide, explicitly and in the issue/PR:**
- Is this credential authorized to be in the repository at all?
- If yes: record where the authorization lives, so the next agent does not re-litigate it.
- If no: rotate it. Redacting history is not enough — a committed credential must be treated as compromised.
- Then settle the board disagreement in writing so it stops recurring.

### 5. Do not forget the standing rules

Per `AGENTS.md`: never merge to `main`, never commit real credentials, and
`SECURITY.md` is the path for genuine vulnerability reports. Do not disclose
exploitable secrets in a public issue or PR.

---

## REVIEW REQUIRED — nothing is merged

### 6. PR #938 has no review decision yet

All the outage fixes are on the branch and unmerged. Reviewers should look hardest at:

- `apps/web/src/app/layout.tsx` — `force-dynamic` makes **every** route dynamic.
  This fixes the blank page, but it also means no static optimization anywhere and
  every request renders server-side. Confirm that trade is wanted, or scope it more narrowly.
- `scripts/init_schemas.py` — the additive reconcile pass now runs DDL during
  bootstrap. It only `ADD COLUMN`s and never drops or retypes, but it is schema-changing
  code on a path that runs in every service.
- `apps/web/src/app/auth-session/route.ts` — `MAX_REFRESH_TOKEN_LENGTH = 2048`.

---

## PRODUCT DECISIONS — deliberately not actioned

### 7. After registering, the user lands on `/login` with a session already set

`apps/web/src/app/signup/page.tsx` calls `register()`, toasts
**"Account created! Please sign in."**, then `router.push('/login')` — even though a
valid `__Host-wf_refresh` cookie has just been stored. The user meets a sign-in form
immediately after signing up, under a message that contradicts what happened.

This is onboarding behaviour, not a bug, so it was left alone. Someone who owns the
product should decide: redirect to `/browse` instead, or keep `/login` and fix the
copy. Worth knowing: this was masked for a long time because the page was blank.

### 8. Kafka ACLs are still not reproducible from the repo

Tracked in [#893](https://github.com/shobhit727/Wildframe/issues/893) and
[#795](https://github.com/shobhit727/Wildframe/issues/795).

The services are healthy right now, but only because topics happen to exist in the
running volume. `grep -rln "kafka-acl" scripts/ infrastructure/ deployments/` still
returns **nothing**: the compose file sets `AclAuthorizer` with
`ALLOW_EVERYONE_IF_NO_ACL_FOUND=false` and declares superusers, and then never grants
anything. **A fresh volume will fail the same way it did before.** This needs a
decision: ship an ACL bootstrap, or relax the authorizer for development.

Do not confuse it with the credentials warning in `deployments/.env.example` — that is
about unset passwords, this is about grants that exist in no configuration at all.

### 9. The `output: 'standalone'` / `next start` mismatch

`apps/web/next.config.ts` sets `output: 'standalone'`; `apps/web/Dockerfile` runs
`next start`. The container logs this on every boot:

```
⚠ "next start" does not work with "output: standalone" configuration.
  Use "node .next/standalone/server.js" instead.
```

**This was the main suspect for the blank page and it was wrong** — the real cause was
prerendering. The mismatch is still real and unsupported, and worth fixing on its own
merits. If you switch to the standalone server, note that standalone expects
`.next/standalone/apps/web/.next/static` and `public/` to exist in the standalone tree,
which the current Dockerfile (which copies all of `/workspace`) does not assemble.

### 10. `apps/web/AGENTS.md` points at a directory that does not exist

It tells agents to read `node_modules/next/dist/docs/` before writing Next.js code,
per the "this is not the Next.js you know" warning. That directory is **not present**
in this install. Every agent that follows the instruction hits a dead end; one spent
significant time on it. Either vendor the docs or correct the instruction.

---

## LOST WORK — recoverable, but not committed

### 11. Conflict markers left in the shared checkout

Seven Python files were found with unresolved conflict markers, syntactically invalid
so the affected services could not import:

`services/analytics-service/app/{app/core/settings.py, app/api/analytics_routes.py}`,
`services/content-service/app/{app/core/settings.py, app/api/routes/__init__.py}`,
`services/creators-service/app/core/settings.py`,
`services/media-pipeline/app/core/settings.py`,
`services/moderation-service/app/core/settings.py`

No merge or rebase was in progress and committed `HEAD` was clean, so these were
abandoned artifacts. They were backed up to `/tmp/opencode/conflict-backup/` and
restored from `HEAD`.

**If that was someone's work in flight, it is in that backup and in git history.**
`/tmp` does not survive a reboot — move it somewhere durable if it matters.

### 12. Untracked files sitting in the tree

`audit-1.md` and `image.png` are untracked leftovers. `image.png` is a screenshot of
the signup failure and may be worth keeping as evidence for the PR. Decide, then
delete or commit. Do not leave them untracked indefinitely.

---

## KNOWN LIMITATIONS — documented, not fixed

### 13. The schema reconcile pass only adds columns

`scripts/init_schemas.py` reconciles by `ADD COLUMN` for whatever the model declares
and the table lacks. It will **not** handle a model that later:

- removes a column,
- changes a column's type, or
- adds a `NOT NULL` column with no default to a populated table.

Those still need a human `ALTER TABLE`. This is now stated in `docs/TEST_GUIDE.md`
rather than left implicit. It is a deliberate limit: an automatic pass that drops or
retypes would be far more dangerous than one that only adds.

### 14. `media-pipeline/sql/0001_pipeline_stage_logs_status.sql` is now superseded

That file describes itself as *"Artifact only: NOT applied by any tooling… has to be
run by hand once per existing environment."* The reconcile pass now covers the same
drift, so the hand-run file is misleading — it implies a manual step that is no longer
needed. Removal is a separate cleanup and was not done.

### 15. The E2E mock-versus-real contract is still the recurring root cause

Not fixed here, and it will keep producing bugs. The Playwright suite mocks the API
from `apps/web/e2e/fixtures.ts` against a hand-maintained DTO, so the mock can
disagree with the real service and nothing detects it. Two separate defects came from
this alone:

- `audience_score` used a 0–10 scale in fixtures against a 0–100 backend field
- `content.price_usd` existed in the model but not the schema, invisible to every test

A test that renders against the real service, or a contract test that compares the
fixture DTO against the backend schema, would close this class permanently.

---

## PROCESS NOTES worth keeping

### 16. Playwright structurally cannot catch the CSP class of bug

Playwright runs `NODE_ENV=development`, where the strict CSP is never applied. That is
part of why the blank-page outage shipped through a green CI. That is why
`scripts/verify-csp-nonce.mjs` exists **outside** the Playwright suite: it boots a
real production build, fetches real routes, and evaluates the real CSP against real
HTML. Run it whenever the proxy, the root layout, or CSP changes.

### 17. A green build is not proof the image is current

Observed directly during this work: `docker compose build` reported `COPY ... CACHED`,
exited 0, and the resulting image still contained pre-fix code. After any web change,
use `build --no-cache` and then grep the built artifact for what you expect to find.
Likewise `docker compose up -d` did not pick up a newly built image; `--force-recreate`
was required.

### 18. `NEXT_PUBLIC_API_URL` was removed from compose on purpose

It was hardcoded to `https://192.168.1.14:8080`. Port 8080 is Caddy's **cleartext**
listener, so `https://` on it failed the TLS handshake and the browser could not
reach the API at all — and it was a machine-specific LAN IP committed to source that
overrode the correct value in `apps/web/.env`.

`client.ts` now derives the browser-side base at runtime as `https://<hostname>:8000`.
Server-side calls use a separate `AUTH_SERVICE_URL` (default
`http://auth-service:8000`) because inside the container `localhost:8000` is the web
container itself. **Do not merge those two back into one variable** — that is exactly
the bug that produced the 502 on hard reload.

`NEXT_PUBLIC_APP_URL` was deleted in the same change because nothing in `src`
references it. Confirm nothing external depends on it.

---

## Suggested order of work

1. **#4** credential decision — security, and it blocks a clean security posture
2. **#1 / #2** unbreak the build, or CI stays red and every future PR is blocked on it
3. **#3** fix the conftest so content-service tests can be collected again
4. **#6** review PR #938
5. **#8** make Kafka reproducible, or document that it is not
6. **#7** and **#15** product/quality improvements
7. Everything else is cleanup

## Two registries per service — found by the hardened schema bootstrap (2026-09-29)

`scripts/init_schemas.py` now refuses to reconcile when a table name is declared in
more than one SQLAlchemy metadata set, instead of merging the columns into one
physical table and reporting success. It fires on two services, and in both cases
the models are genuinely wrong. Neither is fixed here: each is a model refactor
across a whole service, and picking the surviving definition is a schema decision.

**admin-service** — `app/models/admin.py:11` declares its own
`class Base(DeclarativeBase)`, separate from the `Base` at
`app/models/__init__.py:49`. Both registries declare the same five tables:
`admin_audit_logs`, `content_moderations`, `system_alerts`, `system_configs`,
`user_moderations`. Whichever `create_all` runs last determines the shape, and
reconcile was silently grafting both onto one table.

**billing-service** — `app/models/payout_ledger.py:11,16` and
`app/models/__init__.py:49,501` each define a `Base` and a `PayoutLedger` bound to
`payout_ledger`. These are two different models for one money table; a merge
reconciles contradictory representations of payout state.

Decide, per service: which definition is canonical, delete the other, and import
the shared `Base`. Until then those two services fail bootstrap loudly rather than
reporting a green run against a table neither model describes correctly.

A human should also confirm whether `docs/GO_LIVE.md:53-54`, which points
production operators at this script, should keep doing so given it can now refuse.
