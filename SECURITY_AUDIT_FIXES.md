# Security Audit Fixes

> **Made by ChatGPT.**
>
> This file tracks the first independently reviewable remediation pass from the Wildframe security audit. It is intentionally temporary and should be removed or converted into issue references once the fixes are merged.

## Scope

This PR starts a new remediation branch from `main`. Only fixes that can be implemented and reviewed from repository evidence are included. Findings that require infrastructure/runtime confirmation remain GitHub issues.

## Initial remediation

- Harden the API gateway public-route matcher so public prefixes cannot accidentally bypass authentication.
- Add regression coverage for exact public routes, legitimate child routes, trailing slashes, and malicious prefix lookalikes.

## Review requirements

- Do not weaken CI checks to obtain a green build.
- Verify the affected tests in GitHub Actions.
- Review each security change independently before merging.
- Do not treat unresolved audit issues as fixed merely because this PR exists.
