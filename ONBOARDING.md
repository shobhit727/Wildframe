# Onboarding: working on Wildframe as an agent

**Read `AGENTS.md` for the rules. This file is the path — what to do, in what order,
and what will waste your time if you don't know it.**

If you read only one thing: [the green pipeline lies](#the-green-pipeline-lies). Every
serious bug found during the last audit pass was invisible to a fully green CI run.

---

## 1. What this is

A streaming platform. 15 independent FastAPI backend services, a Next.js frontend, 4
shared Python SDKs, and a local Docker Compose stack behind Caddy. There is no monolith
and no shared database: every service owns its own Postgres schema and talks to others
only over HTTP or Kafka.

```
services/           15 independent services (own DB each)
apps/web/           Next.js frontend
packages/sdk/       wildframe_auth, _events, _observability, _compliance
deployments/        docker-compose.dev.yml — the local stack
infrastructure/     Caddy, Helm, Terraform, DB bootstrap SQL, monitoring
scripts/            init_schemas.py (schema authority), verification helpers
tests/              cross-service integration + contract tests
```

Each service runs from **its own directory** (`cd services/auth-service && pytest`).
A repo-root pytest sweep causes `app.*` import shadowing and will lie to you.

---

## 2. First hour

```bash
# 1. Read the rules, in this order
cat AGENTS.md                      # the rules
cat oner-task.md                   # what a human still owes — READ THIS
cat Message-board.md               # who is working on what right now

# 2. Bring the stack up (see docs/QUICKSTART.md for full detail)
python scripts/generate-dev-certs.sh
docker compose -f deployments/docker-compose.dev.yml up -d

# 3. Confirm it is actually up before you touch anything
docker compose -f deployments/docker-compose.dev.yml ps
curl -sk https://localhost:3000/          # frontend, expect 200
curl -sk https://localhost:8000/health    # gateway
```

Ports: frontend `:3000`, HTTPS API gateway `:8000`, then `:8001`–`:8014` for services
in order. Caddy serves HTTPS with a self-signed dev cert, so **always `curl -k`**.

A healthy service answering `/health` proves almost nothing — three of them returned
200 on `/health` while every real route 500'd. **Always hit a real endpoint.**

---

## 3. The green pipeline lies

This is the most important thing on this page.

CI was fully green — 39 jobs, zero failures — at the exact moment the website rendered
a blank page, account creation was broken, and five services could not start. The
Playwright suite passed 119/121 because **every spec mocks the API**. The Docker jobs
build images and never run one.

So:

- **A passing test suite is not evidence the app works.** Drive the real thing.
- **A passing build is not evidence the image is current.** See trap 1 below.
- **Mocked tests cannot catch mock-vs-real drift.** Two real bugs lived only because
  the E2E fixtures are typed against a hand-main DTO that nothing compares to the
  backend. A 0–10 `audience_score` in fixtures against a 0–100 field; a
  `content.price_usd` column in the model that did not exist in the database.

When you write a test, ask: *would this have failed before the fix, and for the right
reason?* Not "does it pass."

---

## 4. Traps that will cost you time

### 1. Your build may ship old code

Observed directly: `docker compose build` printed `COPY apps/web ./apps/web — CACHED`,
exited 0, and the resulting image contained pre-fix code. Separately, `up -d` did not
pick up a newly built image at all.

```bash
docker compose -f deployments/docker-compose.dev.yml build --no-cache <service>
docker compose -f deployments/docker-compose.dev.yml up -d --no-deps --force-recreate <service>
# then grep the artifact for what you expect to find
```

### 2. "No output" is not success

A push can succeed while the branch has moved underneath you. A `grep` can miss and
make a clean file look missing. **Verify by content**, e.g.
`git show origin/<branch>:<path> | grep -c '<distinctive string>'`, and re-check after
every pull — the tree is shared and moves.

### 3. The board is one shared file, so rebase silently eats appends

Resolve a conflict by taking `origin`'s version and your appended entry is gone, but
`rebase --continue` still commits the reduced file. **Re-append inside the retry loop,
after each rebase** — not once before it.

### 4. `cancel-in-progress` will erase your CI verdicts

`ci-cd.yml` uses a per-ref concurrency group with `cancel-in-progress: true`. Under
multi-agent push rates you get **zero verdicts** — no failures, no signal. Batch your
pushes; don't silence the guard to make the dashboard look greener.

### 5. Install before you type-check or test

CI runs `poetry install --no-interaction --with dev` per service. Running mypy or
pytest against an empty or stale venv produces confident, entirely fictional errors —
one real incident produced an `import-untyped` storm across 13 services from a package
that was never installed.

### 6. A scratch venv invents vulnerabilities

"redis 5.3.1 has 2 HIGH CVEs" came from `msgpack` and `setuptools` in a hand-built
scratch venv, not from redis. **Confirm the package name in the finding before
reporting it.** For real supply-chain signal use `.github/scripts/verify-supply-chain.py`;
Trivy over the working tree flags locally generated dev certs, which are gitignored.

### 7. Dev certs and generated files

`apps/web/certificates/*.pem` are gitignored and never committed. A local Trivy
non-zero exit over them is not a CI result.

### 8. `apps/web/AGENTS.md` points at a directory that does not exist

It tells you to read `node_modules/next/dist/docs/` before writing Next.js code. It is
not in this install. Don't burn time looking — check the version, then the installed
source under `node_modules/next/dist/`.

### 9. `bash seq` is `FIRST INCREMENT LAST`

`seq 10 60 10` means "start 10, step 60, stop 10" and yields a single value. A watcher
built on that ran one poll and reported nine minutes of quiet. **Prove a loop
iterates** before trusting its silence.

### 10. Schema drift is invisible until you query

`create_all` is `checkfirst`: it creates missing *tables*, never touches existing ones.
In content-service that meant **9 of 20 tables were never created on a fresh volume**.
`scripts/init_schemas.py` is the schema authority and now has an additive reconcile
pass — but it only *adds* columns. Removals, retypes and `NOT NULL` without a default
still need a human `ALTER TABLE`.

---

## 5. Working here

**Claim before you edit.** Post a `Files:` entry to `Message-board.md` and check for an
existing claim. Overlap is the main way work gets lost in a shared tree.

**Never:** merge to `main`, force-push a protected branch, commit credentials, or
weaken a test to make something pass.

**When you finish:** if you deliberately did not fix something, say so in
`oner-task.md` and say why. Leaving it only in a commit message is how it becomes a
surprise three commits later.

**When you are done and something still needs a human** — a review, a product
decision, a credential ruling — that is not a failure. `oner-task.md` is the home for
it, and `AGENTS.md` §23.3 requires you to record it there.

### Testing, per area

```bash
# a single service (from ITS directory - this matters)
cd services/auth-service && poetry install --with dev && pytest tests --asyncio-mode=auto

# shared SDKs
pip install -e packages/sdk/wildframe_auth -e packages/sdk/wildframe_events \
            -e packages/sdk/wildframe_observability -e packages/sdk/wildframe_compliance
PYTHONPATH="$PWD/packages/sdk" python -m pytest -c pyproject.toml packages/sdk/tests/ -q

# frontend
cd apps/web && npm run test && npx playwright test

# contract / integration
pytest tests/contract -q
```

The Playwright suite runs `NODE_ENV=development`, where the strict CSP is never
applied — so it **structurally cannot** catch CSP/hydration bugs. `scripts/
verify-csp-nonce.mjs` exists for those and is deliberately outside that suite.

---

## 6. Current state, and the best thing you can pick up

**Branch `audit/fix-open-github-issues`, PR #938 open and unreviewed.** `main` is
protected. Nothing is merged.

Fixed and verified live: the blank-page outage (#981), session persistence, the 502 on
reload, content-service 500s (#980), OTel 500s (#978).

**The highest-value open item, and it is unowned:**

```bash
cd apps/web && npx tsc --noEmit    # 22 errors
```

22 pre-existing TypeScript errors from commit `e8b6666b` (content-normalization types
in `e2e/fixtures.ts`, `api/client.ts`, `client.test.ts`), plus 3 vitest failures. This
fails the CI `Build` step and `docker compose build web`, so **it blocks the pipeline for
everybody** — and it is a small, well-defined job.

Do not "fix" it with `typescript.ignoreBuildErrors`. Agents have used that as a
temporary local workaround to get an image built, and reverted it each time.

Also open: Kafka ACLs are not reproducible from the repo (#893 — services are healthy
only because topics exist in the running volume; a fresh volume fails the same way),
and `services/content-service/tests/conftest.py` currently fails collection.

Everything a human still owes is in **`oner-task.md`**.

---

## 7. Ask the running system

You can talk to the app. Do it before you theorise.

```bash
# what does a real route actually return?
curl -sk https://localhost:8003/api/v1/content

# what did the service actually say?
docker compose -f deployments/docker-compose.dev.yml logs <service> --tail 200

# drive a real browser
#   playwright-core: node_modules/playwright-core
#   chromium:       ~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome
#   use ignoreHTTPSErrors: true — the dev cert is self-signed
```

Example scripts from a previous session are in `/tmp/opencode/uitest/` (ephemeral) and
`scripts/verify-csp-nonce.mjs` (committed). A useful pattern from that work: prove
causation by stripping one header in the browser and seeing the page come back, rather
than reasoning about what *should* be blocking it.

---

## 8. If you remember one thing

The bugs worth finding here are plumbing bugs, not logic bugs — a value that should
have arrived somewhere and didn't. A nonce that never reached a script. A base URL
that pointed at the wrong container. A column that existed in a model and not in a
database. A cap that rejected valid input.

**None of them are visible to a green test suite, because the tests mock the layer
where the value is supposed to arrive.** So when something works in a test and not in
the app, believe the app.
