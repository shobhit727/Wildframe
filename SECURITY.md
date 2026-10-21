# Security Policy

## Supported Versions

Wildframe is currently under active development and does not make production-version support guarantees yet.

| Version | Supported |
| --- | --- |
| `main` | Yes, for security issues affecting the current development version |
| Older releases | No formal security support |

If you are running a release that is not based on the current `main` branch, upgrade to the latest version before reporting an issue whenever possible.

## Reporting a Vulnerability

**Do not report security vulnerabilities in public GitHub issues or pull requests.**

Please use GitHub's **private vulnerability reporting** feature for this repository when available. If private reporting is unavailable, contact the repository maintainers through a private GitHub channel and include enough information to reproduce and assess the issue.

Please include:

- A concise description of the vulnerability and its impact.
- The affected service, endpoint, package, or configuration.
- Reproduction steps or a minimal proof of concept that does not expose real credentials or private data.
- The affected commit, version, or deployment configuration, if known.
- Any suggested mitigation, if you have one.

### What to expect

- Reports will be reviewed privately.
- Maintainers will acknowledge receipt when practical and may request additional reproduction details.
- Confirmed vulnerabilities will be tracked privately until a fix or mitigation is available.
- Public disclosure should be coordinated with the maintainers so that users have a reasonable opportunity to apply a fix.

### Sensitive data

Never include passwords, API keys, access tokens, private user data, production database contents, or other secrets in a report. Redact sensitive values from logs and proof-of-concept material before submitting them.

---

## Resolved security issues (were public, now fixed)

These were disclosed here because they were public in the repository's own
issue tracker. Both are **closed**; they are listed so an operator can see what
happened and check that the fix is still holding, not because exposure
remains. For anything currently open, read the tracker.

### ~~Authentication bypass via legacy HS256 verification~~ — #941 — **FIXED**

**Status: fixed, closed 2026-09-28.** Retained here because the incident is
worth remembering, not because the vulnerability is live.

The original report: eight services verified tokens with inline
`jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])` against a
shared secret that was committed to the repository, so anyone with a clone
could mint a token carrying any `sub` and `role: "admin"`.

**Current state, verified 2026-10-14:**

- There are exactly three `jwt.decode` call sites outside tests, and all three
  pass a **JWK resolved from the JWKS by `kid`** — never a shared secret:
  `packages/sdk/wildframe_auth/verifier.py`,
  `services/billing-service/app/core/jwt_verifier.py`, and
  `services/auth-service/app/security/__init__.py` (the issuer).
- The `algorithms` set is closed to `{"RS256"}` in every verifier, and the
  header `alg` is checked before the signature, so `alg: none` / HS256
  substitution fails.
- `tests/contract/test_no_shared_secret_jwt_verification.py` probes every
  service for exactly this property. **17 tests, all passing** — this is the
  regression test that keeps it fixed.

`JWT_SECRET_KEY` still appears in `deployments/docker-compose.dev.yml` and is
still validated against a `KNOWN_INSECURE_JWT_SECRETS` denylist, but it is no
longer used for verification. See `docs/ARCHITECTURE.md` →
*Token Verification — single scheme (JWKS/RS256)*.

### ~~Cleartext full-API listener in the dev stack~~ — #975 — **FIXED**

**Status: fixed, issue closed.** `infrastructure/caddy/Caddyfile` previously
bound `http://:8080`, serving the whole API in cleartext on every interface.
It now binds `http://localhost:8080` — loopback only, and explicitly labelled
in the file as a dev-only entry for tooling that cannot trust the self-signed
cert. HSTS is irrelevant to it because it is not reachable off-host.
