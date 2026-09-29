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
| `compose-smoke.sh` | Does the whole stack come up? |

All are read-only against the API except `auth-flow-check.mjs`, which creates two
throwaway accounts per run (`agent-api-*` and `agent-ui-*` at `example.com`).

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
