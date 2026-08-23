# Web Audit Report

## Runtime status summary

- HTTPS homepage: reachable at https://localhost:3000/ and returning HTTP 200
- Protected browse route: https://localhost:3000/browse returns HTTP 307 redirect to `/login` when unauthenticated
- Authenticated live session: the browse page loads successfully in the browser after login, confirming the route is working as intended
- Overall status: the app is live and the auth gate is functioning; the remaining issues are development-hardening items, not a broken site

## Executive summary

The local Wildframe web app is reachable at https://localhost:3000/ and returns an HTTP 200 response. The current landing page is a dark-themed streaming marketing page with a sign-in and Get Started flow; the HTML and headers confirm the app is serving the expected Next.js app shell.

The app is not fully production-ready, and the browser trust warning for the self-signed local certificate is expected for a development environment. The more important issues are in the security layer: the project has security utilities and tests, but the actual runtime CSP header is not being enforced in the app middleware, so the app is relying on static response headers rather than a full CSP policy.

## Verified evidence

- HTTPS status check: `curl -k -I https://localhost:3000/` returned `HTTP/1.1 200 OK`.
- Browse route check: `curl -k -I https://localhost:3000/browse` returned `HTTP/1.1 307 Temporary Redirect` with `location: /login` for an anonymous request.
- Browser verification: the live authenticated session at https://localhost:3000/browse loaded successfully and reported the page title `Wildframe - Stream Movies & Shows`, confirming the protected route is reachable after login.
- Response headers included:
  - `X-Frame-Options: DENY`
  - `X-Content-Type-Options: nosniff`
  - `X-XSS-Protection: 1; mode=block`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- Frontend security lint/test check: `cd apps/web && npm test -- --run src/__tests__/security.test.ts`
  - Result: `1 passed (1)` and `4 passed (4)`

## Page state at https://localhost:3000/

The site is serving the public landing screen implemented in [apps/web/src/app/page.tsx](apps/web/src/app/page.tsx). It includes:

- branded hero section with WILDFRAME styling
- sign-in and sign-up CTAs
- feature cards and FAQ
- footer and account entry flow

The page title is `Wildframe - Stream Movies & Shows`, and the HTML includes the expected dark cinematic design and marketing copy.

## Browse route state at https://localhost:3000/browse

The protected browse route behaves correctly for authentication: an unauthenticated request redirects to `/login` with a `307 Temporary Redirect`, which is the expected gate for a secure app route. Once a session is active, the browser successfully resolves the route and renders the browse experience with the same app title and shell, confirming the auth flow is operational in the live environment.

## Findings

### 1. Local HTTPS certificate trust issue is expected but still a real developer friction point

The app is served with a local self-signed certificate, which causes browsers to show a trust warning such as `ERR_CERT_AUTHORITY_INVALID` unless the certificate is explicitly trusted.

This is not a broken app; it is a local environment setup issue. The dev script in [apps/web/package.json](apps/web/package.json) uses the project’s self-signed certificate:

- `next dev --experimental-https --experimental-https-key certificates/localhost-key.pem --experimental-https-cert certificates/localhost.pem`

Recommendation:

- trust the local CA/certificate in the browser or accept the certificate exception for localhost
- keep this isolated to development; avoid using the self-signed cert in production

### 2. Security headers are present, but a real CSP is not enforced at runtime

The project includes a CSP builder utility in [apps/web/src/utils/csp.ts](apps/web/src/utils/csp.ts), which is well-structured and contains a strict production policy with nonce support. There is also a test in [apps/web/src/__tests__/security.test.ts](apps/web/src/__tests__/security.test.ts) verifying that `Content-Security-Policy` is not set in the Next config, which suggests the team intentionally moved CSP enforcement out of the config layer.

However, the actual runtime app does not appear to apply CSP through the request middleware. [apps/web/src/middleware.ts](apps/web/src/middleware.ts) handles auth and route protection, but it does not set a `Content-Security-Policy` header. The app also does not appear to attach the generated CSP header in any route or top-level layout.

Impact:

- the project has the right idea and utilities, but the browser is not actually receiving a strict CSP at runtime
- this reduces XSS defense in the real deployed page

Recommendation:

- attach the CSP header in middleware or via a server response helper using `buildCspHeader()`
- make sure `script-src` includes the nonce and that dev HMR requirements are handled separately

### 3. Auth/session design is mostly sound, but browser trust and token persistence still need careful handling

The refresh token flow is implemented in [apps/web/src/app/auth-session/route.ts](apps/web/src/app/auth-session/route.ts) and uses an `HttpOnly` cookie (`wf_refresh`) rather than storing refresh tokens in localStorage. That is good security hygiene and reduces client-side XSS exposure.

The client also strips legacy localStorage auth keys in [apps/web/src/api/client.ts](apps/web/src/api/client.ts), which is a good cleanup step for older builds.

The main caveat is that the app still depends on a local HTTPS stack with a self-signed certificate and a dev-only trust model; a browser that does not trust that certificate cannot complete the secure-cookie flow cleanly.

### 4. Landing page is functional, but it is still a prototype/marketing page rather than a finished product surface

The public homepage is a polished, dark-themed landing page in [apps/web/src/app/page.tsx](apps/web/src/app/page.tsx). It reads like a conceptual streaming storefront and clearly says in the FAQ that the project is not production-ready yet.

This is acceptable for early product presentation, but it is not yet a complete consumer-facing app experience. It should be treated as a prototype interface while the backend and product stack mature.

### 5. App is live, but some backend/frontend integration expectations remain unclear

The web app depends on its API base URL configured in [apps/web/src/api/client.ts](apps/web/src/api/client.ts) and [apps/web/src/config/index.ts](apps/web/src/config/index.ts), defaulting to `https://localhost:8000`.

The homepage works as a static marketing page without a backend connection, but authenticated browsing and playback flows depend on the upstream services being available and the gateway being reachable. The app will still need the full stack to be healthy for full functional validation.

## Security posture assessment

Current posture: moderate for a local prototype, not strong enough for production.

Positive points:

- security headers are configured in [apps/web/next.config.ts](apps/web/next.config.ts)
- HttpOnly refresh-token cookie handling exists in [apps/web/src/app/auth-session/route.ts](apps/web/src/app/auth-session/route.ts)
- classic localStorage credential keys are actively cleaned up in [apps/web/src/api/client.ts](apps/web/src/api/client.ts)
- security tests are in place and passing

Remaining gaps:

- missing runtime CSP enforcement
- self-signed dev certificate friction
- marketing prototype page still indicates the project is not production-ready

## Recommendation summary

1. enforce the CSP at runtime through middleware or a route layer
2. trust the local dev certificate for browser testing or document the certificate import flow clearly
3. treat the current homepage as a prototype and continue hardening auth and browser security before public launch
4. continue verifying the full stack behind the app (gateway, auth, content, playback) before calling the platform production-ready

## Overall verdict

The website at https://localhost:3000/ is live and returns the expected page, but it should be classified as a development-stage, partially hardened web app. The current codebase demonstrates good intentions around security, but the site still needs final enforcement work for production readiness.
