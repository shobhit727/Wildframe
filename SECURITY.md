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

## Known issues (public, tracked)

These are disclosed here because they are already public in the repository's own
issue tracker. They are listed so an operator can assess exposure without
reading 900+ issues.

### Authentication bypass via legacy HS256 verification — #941

**Status: open. Treat as a release blocker for any shared or production
deployment.**

auth-service signs **RS256** and publishes JWKS. Eight services still verify
with the legacy path — inline `jwt.decode(token, settings.JWT_SECRET_KEY,
algorithms=["HS256"])` — and the shared secret is committed to the repository.
An attacker who has read the repo can mint an HS256 token carrying any `sub` and
`role: "admin"`, and those services accept it. The same services also reject
genuine RS256 tokens, so the split breaks legitimate authentication too.

Affected: `analytics`, `creators`, `media-pipeline`, `notification`,
`recommendation`, `search`, `uploads`, `content`. Using scheme A:
`admin-service`, `streaming-service`.

**Operator guidance until it is fixed:** do not expose this stack on an
untrusted network, and prefer an environment that fails closed — an unset
`JWT_SECRET_KEY` yields `503` (no authentication possible) rather than accepting
the committed default. Do not rely on this as a mitigation; it is a stop-gap
only. See `docs/ARCHITECTURE.md` → *Token Verification Schemes*.

### Cleartext full-API listener in the dev stack — #975

`infrastructure/caddy/Caddyfile` binds `http://:8080`, which serves the entire
API without TLS on **every** interface. HSTS does not apply over plain HTTP.
Dev-stack only, but it is the same network developers use real credentials on.

