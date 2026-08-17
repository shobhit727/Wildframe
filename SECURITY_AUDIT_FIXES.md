# Security Audit Fixes

> **Made by ChatGPT.**
>
> This file tracks the first independently reviewable remediation pass from the Wildframe security audit. It is intentionally temporary and should be removed or converted into issue references once the fixes are merged.

## Scope

This PR starts a new remediation branch from `main`. Only fixes that can be implemented and reviewed from repository evidence are included. Findings that require infrastructure/runtime confirmation remain GitHub issues.

## Initial remediation

- Harden the API gateway public-route matcher so public prefixes cannot accidentally bypass authentication.
- Add regression coverage for exact public routes, legitimate child routes, trailing slashes, and malicious prefix lookalikes.

## Later remediation (Aug 2026, tracked per GitHub issue)

Each item closed with unit tests plus live verification against the running HTTPS stack (see `STATUS.md`):

- **JWT audience verification across services** (#41, #57, #58): auth-issued tokens carry `aud: "wildframe-api"`. Every service that verifies auth tokens now decodes with `audience=settings.JWT_AUDIENCE` (`"wildframe-api"`). The api-gateway remains the single exception (it is a transparent proxy that rate-limits but does not reject proxied requests). Previously, all authenticated calls to streaming/content/admin/billing/creators/notification/search/media-pipeline failed with 401.
- **Media-pipeline endpoint auth** (#41): `POST /api/v1/pipeline/jobs/{upload_session_id}/start` and `GET /api/v1/pipeline/jobs/{job_id}` were callable without a token; both now require a verified JWT.
- **Analytics authorization** (#63): analytics routes enforce creator/content ownership (`require_creator_access`, `require_content_access`) with fail-closed behavior on unknown content.
- **Auth fail-closed behavior** (#57): auth-service rejects invalid/expired/malformed tokens deterministically.
- **Gateway rate limiting** (#55): 429 enforcement in `proxy_request` keyed by user `sub` or IP, with regression tests.
- **Trending/fallback robustness** (#60): content-service trending endpoint no longer 500s when ranking data is missing.
- **Event outbox / atomicity** (#61): moderation/media-pipeline state changes commit before event publication; publisher survives subscriber failures (#62, aiokafka 0.14-compatible).
- **search-service `/ready`** (#41): returned a raw tuple and 500'd; fixed to a proper JSONResponse.
- **Billing webhook + schema drift** (#41): `POST /api/v1/billing/webhooks/stripe` verifies the Stripe signature, is idempotent (replay returns `idempotent:true`, exactly one PAID invoice row), and the live `invoices` table was repaired by hand (no migration framework) with `stripe_invoice_id`, `currency`, `refunded_amount`.
- **Live-stack integration suite** (#41): `tests/integration/` — 87 tests covering the gateway auth matrix + 429 flood, token lifecycle, cross-service authorization, billing webhook idempotency, contract schemas, health/readiness, and pipeline idempotency. Skips itself when the stack is down; excluded from the CI unit matrix.

Open/backlog: #43 (authorization test sweep), #44 (contract tests), #45 (DRM scope — backlog by decision).

## Review requirements

- Do not weaken CI checks to obtain a green build.
- Verify the affected tests in GitHub Actions.
- Review each security change independently before merging.
- Do not treat unresolved audit issues as fixed merely because this PR exists.
