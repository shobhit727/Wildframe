---
name: verify-against-running-stack
description: Use when a change in Wildframe passes its tests but you need to know it actually works. Traces the frontend, API gateway and 15 backend services end to end, and explains why a green CI run is not evidence. Triggers on "does this actually work", "verify the fix", "test the running app", "the tests pass but", or after any change to auth, CSP, schema, dependencies, or Docker/compose.
---

# Verify a change against the running stack

A green CI run in this repo is not evidence the application works. That is not
theory: CI was fully green — 39 jobs, zero failures — at the exact moment the website
rendered a blank page, account creation silently failed, and five services could not
start.

The reason is structural. The Playwright suite **mocks the API**, so mock-versus-real
drift is invisible to it. The Docker jobs **build images without running one**. So the
first place a plumbing bug is observable is a running service.

## Before you start

```bash
cd /home/ph03n1x/Wildframe
docker compose -f deployments/docker-compose.dev.yml ps          # is it even up?
```

Caddy serves HTTPS with a **self-signed dev cert**. Always `curl -k`, and use
`https://` — port 3000 rejects plaintext with a 400 by design.

Ports: frontend `:3000`, HTTPS API gateway `:8000`, services `:8001`–`:8014`.

## The three harnesses

Run from the repo root. All exit non-zero on failure.

```bash
# does each page actually render in a browser?
node scripts/browser-check.mjs

# WHY does it break — strips one response header, reports if the symptom moves
node scripts/causation-check.mjs /signup content-security-policy

# can a real user register and stay signed in?
NODE_EXTRA_CA_CERTS=apps/web/certificates/localhost.pem node scripts/auth-flow-check.mjs
```

`browser-check.mjs` fails on a navigation error, any status >= 400, a redirect away
from the requested route, an empty body, a CSP violation, and uncaught JS errors. It
uses a **fresh unauthenticated context per route**, so default to public routes only;
pass protected ones explicitly with `EXPECT_LAND=/browse`.

`causation-check.mjs` exits `0` cause supported, `1` not supported, `3` nothing to
explain. **`3` on a healthy page is the correct answer** — it means there is no fault
to find there.

## Hit a real endpoint

`/health` proves almost nothing. Three services answered it with 200 while every real
route 500'd, because the health path skipped the middleware where the bug lived.

```bash
curl -sk https://localhost:8003/api/v1/content          # a real route
docker compose -f deployments/docker-compose.dev.yml logs <svc> --tail 200
```

## Prove the build is current

A `docker compose build` has been observed printing `CACHED`, exiting 0, and shipping
pre-fix code. And `up -d` has failed to pick up a newly built image entirely.

```bash
docker compose -f deployments/docker-compose.dev.yml build --no-cache <svc>
docker compose -f deployments/docker-compose.dev.yml up -d --no-deps --force-recreate <svc>
# then grep the built artifact for what you expect to find
```

## Prove causation, not correlation

When you infer a cause, try to break it on purpose. Strip the one header, comment out
the one line, restore the old value — confirm the symptom appears and disappears with
it. `causation-check.mjs` does this for headers. If you cannot make the bug come back
on demand, you have a theory, not a diagnosis, and you should say so.

## Check the harness itself

A harness that cannot fail is worse than none — it sends the next person after a defect
that is not there. Before you cite one as evidence:

- **What would it report if the bug were still present?**
- **Can you make it fail for a reason unrelated to the product?** If so it will.
- **Does the documented invocation actually run?** A tool that exits 2 on its own
  usage line is worse than a missing one, because it is trusted.

Two harnesses in this repo shipped broken: one reported OK for a 404, another reached
"cause confirmed" only when the cause was absent. Both were cited as proof.

## When you finish

- If anything still needs a human, put it in `oner-task.md` with the evidence.
- If you deliberately did not fix something, say so there and say why.
- Never put it in `/tmp` — see `AGENT_COORDINATION.md` 23.6.
