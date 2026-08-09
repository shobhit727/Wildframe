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
