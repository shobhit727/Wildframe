# Spawn Board — 100-agent read-only UX wave (adversarial, browser-level)

## READ FIRST — four traps that have already cost this session real time

1. **The site must be up.** `curl -sk -o /dev/null -w '%{http_code}' https://localhost:3000/`
   must not be 000. If it is, stop and report; do not file a finding.
2. **Host port 8000 is the api-gateway. 8001..8014 are individual services and are
   NOT service names.** Probing the wrong one 404s and reads like broken routing.
3. **Protected routes 307 to /login when unauthenticated. That is correct.**
   Use `EXPECT_LAND=/login` for those, one route per invocation.
4. **`/health` returning 200 means nothing.** It skips the repository; a service can
   be 200 on /health and 500 on every real route. Test real endpoints.

## Rules

- **READ-ONLY. Do not edit, create or delete any file. No git commands at all.**
- One harness, one route, one question per agent. Do not run all four harnesses.
- Report what the tool printed, not what you concluded. Quote the status code.
- A 404 on a path that is not in the route list is NOT a finding — report it as
  'route not found' with the exact path and move on.
- If a command is missing or the browser will not launch, say so in one line.
  Do not manufacture a finding.

## A-001 `/` — render
```
node scripts/browser-check.mjs /  # does it render, hydrate, and log no CSP/JS errors?
```

## A-002 `/` — prerender
```
node scripts/verify-csp-nonce.mjs /  # served per-request (nonce) or build-time prerendered?
```

## A-003 `/` — unauth-bounce
```
Is the unauthenticated response for / a correct 307 to /login (or 200 for a public route)? Report the code, and EXPECT_LAND=/login if protected.
```

## A-004 `/` — http-methods
```
Probe / with GET, POST, HEAD, OPTIONS, PUT, DELETE. Report any method that should be rejected but is not, and any 500.
```

## A-005 `/` — traversal
```
Probe / with ../, %2e%2e/, null byte, and a 10KB path. Report any 500 or reflected input.
```

## A-006 `/` — notfound
```
Is there a not-found page for a bogus sibling of /? Does it render, or does it 500/blank?
```

## A-007 `/login` — render
```
node scripts/browser-check.mjs /login  # does it render, hydrate, and log no CSP/JS errors?
```

## A-008 `/login` — prerender
```
node scripts/verify-csp-nonce.mjs /login  # served per-request (nonce) or build-time prerendered?
```

## A-009 `/login` — unauth-bounce
```
Is the unauthenticated response for /login a correct 307 to /login (or 200 for a public route)? Report the code, and EXPECT_LAND=/login if protected.
```

## A-010 `/login` — http-methods
```
Probe /login with GET, POST, HEAD, OPTIONS, PUT, DELETE. Report any method that should be rejected but is not, and any 500.
```

## A-011 `/login` — traversal
```
Probe /login with ../, %2e%2e/, null byte, and a 10KB path. Report any 500 or reflected input.
```

## A-012 `/login` — notfound
```
Is there a not-found page for a bogus sibling of /login? Does it render, or does it 500/blank?
```

## A-013 `/signup` — render
```
node scripts/browser-check.mjs /signup  # does it render, hydrate, and log no CSP/JS errors?
```

## A-014 `/signup` — prerender
```
node scripts/verify-csp-nonce.mjs /signup  # served per-request (nonce) or build-time prerendered?
```

## A-015 `/signup` — unauth-bounce
```
Is the unauthenticated response for /signup a correct 307 to /login (or 200 for a public route)? Report the code, and EXPECT_LAND=/login if protected.
```

## A-016 `/signup` — http-methods
```
Probe /signup with GET, POST, HEAD, OPTIONS, PUT, DELETE. Report any method that should be rejected but is not, and any 500.
```

## A-017 `/signup` — traversal
```
Probe /signup with ../, %2e%2e/, null byte, and a 10KB path. Report any 500 or reflected input.
```

## A-018 `/signup` — notfound
```
Is there a not-found page for a bogus sibling of /signup? Does it render, or does it 500/blank?
```

## A-019 `/browse` — render
```
node scripts/browser-check.mjs /browse  # does it render, hydrate, and log no CSP/JS errors?
```

## A-020 `/browse` — prerender
```
node scripts/verify-csp-nonce.mjs /browse  # served per-request (nonce) or build-time prerendered?
```

## A-021 `/browse` — unauth-bounce
```
Is the unauthenticated response for /browse a correct 307 to /login (or 200 for a public route)? Report the code, and EXPECT_LAND=/login if protected.
```

## A-022 `/browse` — http-methods
```
Probe /browse with GET, POST, HEAD, OPTIONS, PUT, DELETE. Report any method that should be rejected but is not, and any 500.
```

## A-023 `/browse` — traversal
```
Probe /browse with ../, %2e%2e/, null byte, and a 10KB path. Report any 500 or reflected input.
```

## A-024 `/browse` — notfound
```
Is there a not-found page for a bogus sibling of /browse? Does it render, or does it 500/blank?
```

## A-025 `/account` (protected) — render
```
node scripts/browser-check.mjs /account  # does it render, hydrate, and log no CSP/JS errors?
```

## A-026 `/account` (protected) — prerender
```
node scripts/verify-csp-nonce.mjs /account  # served per-request (nonce) or build-time prerendered?
```

## A-027 `/account` (protected) — unauth-bounce
```
Is the unauthenticated response for /account a correct 307 to /login (or 200 for a public route)? Report the code, and EXPECT_LAND=/login if protected.
```

## A-028 `/account` (protected) — http-methods
```
Probe /account with GET, POST, HEAD, OPTIONS, PUT, DELETE. Report any method that should be rejected but is not, and any 500.
```

## A-029 `/account` (protected) — traversal
```
Probe /account with ../, %2e%2e/, null byte, and a 10KB path. Report any 500 or reflected input.
```

## A-030 `/account` (protected) — notfound
```
Is there a not-found page for a bogus sibling of /account? Does it render, or does it 500/blank?
```

## A-031 `/my-list` (protected) — render
```
node scripts/browser-check.mjs /my-list  # does it render, hydrate, and log no CSP/JS errors?
```

## A-032 `/my-list` (protected) — prerender
```
node scripts/verify-csp-nonce.mjs /my-list  # served per-request (nonce) or build-time prerendered?
```

## A-033 `/my-list` (protected) — unauth-bounce
```
Is the unauthenticated response for /my-list a correct 307 to /login (or 200 for a public route)? Report the code, and EXPECT_LAND=/login if protected.
```

## A-034 `/my-list` (protected) — http-methods
```
Probe /my-list with GET, POST, HEAD, OPTIONS, PUT, DELETE. Report any method that should be rejected but is not, and any 500.
```

## A-035 `/my-list` (protected) — traversal
```
Probe /my-list with ../, %2e%2e/, null byte, and a 10KB path. Report any 500 or reflected input.
```

## A-036 `/my-list` (protected) — notfound
```
Is there a not-found page for a bogus sibling of /my-list? Does it render, or does it 500/blank?
```

## A-037 `/billing` (protected) — render
```
node scripts/browser-check.mjs /billing  # does it render, hydrate, and log no CSP/JS errors?
```

## A-038 `/billing` (protected) — prerender
```
node scripts/verify-csp-nonce.mjs /billing  # served per-request (nonce) or build-time prerendered?
```

## A-039 `/billing` (protected) — unauth-bounce
```
Is the unauthenticated response for /billing a correct 307 to /login (or 200 for a public route)? Report the code, and EXPECT_LAND=/login if protected.
```

## A-040 `/billing` (protected) — http-methods
```
Probe /billing with GET, POST, HEAD, OPTIONS, PUT, DELETE. Report any method that should be rejected but is not, and any 500.
```

## A-041 `/billing` (protected) — traversal
```
Probe /billing with ../, %2e%2e/, null byte, and a 10KB path. Report any 500 or reflected input.
```

## A-042 `/billing` (protected) — notfound
```
Is there a not-found page for a bogus sibling of /billing? Does it render, or does it 500/blank?
```

## A-043 `/watch/[id]` (protected) — render
```
node scripts/browser-check.mjs /watch/[id]  # does it render, hydrate, and log no CSP/JS errors?
```

## A-044 `/watch/[id]` (protected) — prerender
```
node scripts/verify-csp-nonce.mjs /watch/[id]  # served per-request (nonce) or build-time prerendered?
```

## A-045 `/watch/[id]` (protected) — unauth-bounce
```
Is the unauthenticated response for /watch/[id] a correct 307 to /login (or 200 for a public route)? Report the code, and EXPECT_LAND=/login if protected.
```

## A-046 `/watch/[id]` (protected) — http-methods
```
Probe /watch/[id] with GET, POST, HEAD, OPTIONS, PUT, DELETE. Report any method that should be rejected but is not, and any 500.
```

## A-047 `/watch/[id]` (protected) — traversal
```
Probe /watch/[id] with ../, %2e%2e/, null byte, and a 10KB path. Report any 500 or reflected input.
```

## A-048 `/watch/[id]` (protected) — notfound
```
Is there a not-found page for a bogus sibling of /watch/[id]? Does it render, or does it 500/blank?
```

## A-049 `/creator` — render
```
node scripts/browser-check.mjs /creator  # does it render, hydrate, and log no CSP/JS errors?
```

## A-050 `/creator` — prerender
```
node scripts/verify-csp-nonce.mjs /creator  # served per-request (nonce) or build-time prerendered?
```

## A-051 `/creator` — unauth-bounce
```
Is the unauthenticated response for /creator a correct 307 to /login (or 200 for a public route)? Report the code, and EXPECT_LAND=/login if protected.
```

## A-052 `/creator` — http-methods
```
Probe /creator with GET, POST, HEAD, OPTIONS, PUT, DELETE. Report any method that should be rejected but is not, and any 500.
```

## A-053 `/creator` — traversal
```
Probe /creator with ../, %2e%2e/, null byte, and a 10KB path. Report any 500 or reflected input.
```

## A-054 `/creator` — notfound
```
Is there a not-found page for a bogus sibling of /creator? Does it render, or does it 500/blank?
```

## A-055 `/admin` (protected) — render
```
node scripts/browser-check.mjs /admin  # does it render, hydrate, and log no CSP/JS errors?
```

## A-056 `/admin` (protected) — prerender
```
node scripts/verify-csp-nonce.mjs /admin  # served per-request (nonce) or build-time prerendered?
```

## A-057 `/admin` (protected) — unauth-bounce
```
Is the unauthenticated response for /admin a correct 307 to /login (or 200 for a public route)? Report the code, and EXPECT_LAND=/login if protected.
```

## A-058 `/admin` (protected) — http-methods
```
Probe /admin with GET, POST, HEAD, OPTIONS, PUT, DELETE. Report any method that should be rejected but is not, and any 500.
```

## A-059 `/admin` (protected) — traversal
```
Probe /admin with ../, %2e%2e/, null byte, and a 10KB path. Report any 500 or reflected input.
```

## A-060 `/admin` (protected) — notfound
```
Is there a not-found page for a bogus sibling of /admin? Does it render, or does it 500/blank?
```

## A-061 `/admin/users` (protected) — render
```
node scripts/browser-check.mjs /admin/users  # does it render, hydrate, and log no CSP/JS errors?
```

## A-062 `/admin/users` (protected) — prerender
```
node scripts/verify-csp-nonce.mjs /admin/users  # served per-request (nonce) or build-time prerendered?
```

## A-063 `/admin/users` (protected) — unauth-bounce
```
Is the unauthenticated response for /admin/users a correct 307 to /login (or 200 for a public route)? Report the code, and EXPECT_LAND=/login if protected.
```

## A-064 `/admin/users` (protected) — http-methods
```
Probe /admin/users with GET, POST, HEAD, OPTIONS, PUT, DELETE. Report any method that should be rejected but is not, and any 500.
```

## A-065 `/admin/users` (protected) — traversal
```
Probe /admin/users with ../, %2e%2e/, null byte, and a 10KB path. Report any 500 or reflected input.
```

## A-066 `/admin/users` (protected) — notfound
```
Is there a not-found page for a bogus sibling of /admin/users? Does it render, or does it 500/blank?
```

## A-067 `/admin/audit` (protected) — render
```
node scripts/browser-check.mjs /admin/audit  # does it render, hydrate, and log no CSP/JS errors?
```

## A-068 `/admin/audit` (protected) — prerender
```
node scripts/verify-csp-nonce.mjs /admin/audit  # served per-request (nonce) or build-time prerendered?
```

## A-069 `/admin/audit` (protected) — unauth-bounce
```
Is the unauthenticated response for /admin/audit a correct 307 to /login (or 200 for a public route)? Report the code, and EXPECT_LAND=/login if protected.
```

## A-070 `/admin/audit` (protected) — http-methods
```
Probe /admin/audit with GET, POST, HEAD, OPTIONS, PUT, DELETE. Report any method that should be rejected but is not, and any 500.
```

## A-071 `/admin/audit` (protected) — traversal
```
Probe /admin/audit with ../, %2e%2e/, null byte, and a 10KB path. Report any 500 or reflected input.
```

## A-072 `/admin/audit` (protected) — notfound
```
Is there a not-found page for a bogus sibling of /admin/audit? Does it render, or does it 500/blank?
```

## A-073 `/admin/config` (protected) — render
```
node scripts/browser-check.mjs /admin/config  # does it render, hydrate, and log no CSP/JS errors?
```

## A-074 `/admin/config` (protected) — prerender
```
node scripts/verify-csp-nonce.mjs /admin/config  # served per-request (nonce) or build-time prerendered?
```

## A-075 `/admin/config` (protected) — unauth-bounce
```
Is the unauthenticated response for /admin/config a correct 307 to /login (or 200 for a public route)? Report the code, and EXPECT_LAND=/login if protected.
```

## A-076 `/admin/config` (protected) — http-methods
```
Probe /admin/config with GET, POST, HEAD, OPTIONS, PUT, DELETE. Report any method that should be rejected but is not, and any 500.
```

## A-077 `/admin/config` (protected) — traversal
```
Probe /admin/config with ../, %2e%2e/, null byte, and a 10KB path. Report any 500 or reflected input.
```

## A-078 `/admin/config` (protected) — notfound
```
Is there a not-found page for a bogus sibling of /admin/config? Does it render, or does it 500/blank?
```

## A-079 `/admin/alerts` (protected) — render
```
node scripts/browser-check.mjs /admin/alerts  # does it render, hydrate, and log no CSP/JS errors?
```

## A-080 `/admin/alerts` (protected) — prerender
```
node scripts/verify-csp-nonce.mjs /admin/alerts  # served per-request (nonce) or build-time prerendered?
```

## A-081 `/admin/alerts` (protected) — unauth-bounce
```
Is the unauthenticated response for /admin/alerts a correct 307 to /login (or 200 for a public route)? Report the code, and EXPECT_LAND=/login if protected.
```

## A-082 `/admin/alerts` (protected) — http-methods
```
Probe /admin/alerts with GET, POST, HEAD, OPTIONS, PUT, DELETE. Report any method that should be rejected but is not, and any 500.
```

## A-083 `/admin/alerts` (protected) — traversal
```
Probe /admin/alerts with ../, %2e%2e/, null byte, and a 10KB path. Report any 500 or reflected input.
```

## A-084 `/admin/alerts` (protected) — notfound
```
Is there a not-found page for a bogus sibling of /admin/alerts? Does it render, or does it 500/blank?
```

## A-085 `/admin/flags` (protected) — render
```
node scripts/browser-check.mjs /admin/flags  # does it render, hydrate, and log no CSP/JS errors?
```

## A-086 `/admin/flags` (protected) — prerender
```
node scripts/verify-csp-nonce.mjs /admin/flags  # served per-request (nonce) or build-time prerendered?
```

## A-087 `/admin/flags` (protected) — unauth-bounce
```
Is the unauthenticated response for /admin/flags a correct 307 to /login (or 200 for a public route)? Report the code, and EXPECT_LAND=/login if protected.
```

## A-088 `/admin/flags` (protected) — http-methods
```
Probe /admin/flags with GET, POST, HEAD, OPTIONS, PUT, DELETE. Report any method that should be rejected but is not, and any 500.
```

## A-089 `/admin/flags` (protected) — traversal
```
Probe /admin/flags with ../, %2e%2e/, null byte, and a 10KB path. Report any 500 or reflected input.
```

## A-090 `/admin/flags` (protected) — notfound
```
Is there a not-found page for a bogus sibling of /admin/flags? Does it render, or does it 500/blank?
```

## A-091 cross-cutting — site-wide hydration
```
node scripts/browser-check.mjs  # default route set; report each
```

## A-092 cross-cutting — auth flow end to end
```
node scripts/auth-flow-check.mjs  # can a real user register and stay signed in?
```

## A-093 cross-cutting — causation
```
node scripts/causation-check.mjs  # does the UI reflect the events that should cause it?
```

## A-094 cross-cutting — CSP across the app
```
node scripts/verify-csp-nonce.mjs  # all routes; separate prerender failures from 307 bounces
```

## A-095 cross-cutting — root layout only
```
node scripts/browser-check.mjs /  # the most basic page; if this fails everything fails
```

## A-096 cross-cutting — first paint
```
Measure time-to-first-byte and first contentful paint for https://localhost:3000/
```

## A-097 cross-cutting — console noise
```
Load / and /browse and report every console error or warning, verbatim.
```

## A-098 cross-cutting — network failures
```
Load /browse and report every failed request (4xx/5xx) with its URL.
```

## A-099 cross-cutting — mobile viewport
```
Load /, /browse and /login at 375x667 and report anything that overflows or is unreadable.
```

## A-100 cross-cutting — keyboard only
```
Tab through /login and /signup and report any control you cannot reach or activate.
```
