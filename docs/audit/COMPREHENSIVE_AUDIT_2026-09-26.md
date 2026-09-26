# Wildframe Comprehensive Audit and Remediation — 2026-09-26

## Scope

This audit covers the current repository state of `shobhit727/Wildframe`, all currently open issues, the historical closed-issue audit corpus, and all currently open pull requests. Closed findings are treated as audit targets rather than trusted as permanently fixed.

Baseline:
- Default branch: `main`
- Remediation branch: `audit/comprehensive-issue-pr-fixes-2026-09-26`
- Historical closed-issue corpus: 607 issues, audited previously against an older repository head.
- Current open issue corpus: 100 issues (#751–#881, with gaps from closed/intervening numbers).
- Current open PRs found: #835, #838, #839.

## Branch remediation

The remediation branch is currently 105 commits ahead of `main` at the point this report was written, with 85 changed files. It contains targeted fixes and regression-prevention changes; `main` has not been modified by this audit.

### Confirmed fixes applied on this branch

Authentication / security:
- #40 / #849: new password hashing migrated to Argon2id, while existing bcrypt hashes remain verifiable and can be transparently migrated.
- #843 / #844 / #127: shared JWT verifier now requires critical claims and rejects omitted/mismatched issuer or audience.
- #851: DSAR token-type mismatch returns HTTP 401 rather than surfacing as an internal error.
- #852: latest refresh-token lookup is deterministic and does not fail when multiple sessions exist.
- #853: FastAPI tracing attaches to an application instance rather than calling the instrumentor class incorrectly.
- #874: login explicitly retrieves inactive accounts so the intended suspended-account response is reachable, while ordinary lookups remain active-only.
- #835: Redis rate-limit scope hashing uses SHA-256.
- #792: existing rate-limit code already fails closed when Redis is unavailable; no additional weakening was introduced.

Configuration / CI:
- #751–#762: removed invalid `await redis.from_url(...)` usage across affected services/SDK code.
- #841: observability wiring no longer creates an unprotected duplicate `/metrics` route in billing, analytics, and media-pipeline.
- #845: admin stats response schema accepts unavailable cross-service user counts instead of converting them into a response-validation 500.
- #871 / #872: production configuration rejects empty/whitespace Stripe and JWT secrets.
- #808 / #864: documentation endpoints are public only outside production and return 404 in production.
- #865: gateway shared-client initialization failure is mapped to an explicit 503.
- #809, #806, #807 were already implemented in the current CI workflow and were verified during the audit.

Billing / payments:
- #842: invoice amount conversion now uses ISO-4217 minor-unit handling instead of an unconditional divisor of 100.
- #854: milestone tranche amounts are rounded to cents with the final tranche assigned the exact remainder.
- #855: payout transition mapping now matches the persisted `PayoutStatus` state machine.
- #786 / #787 / #797 / #798: current billing routes and webhook handlers already enforce server-side identity/canonical pricing, durable webhook claiming, authoritative payment reconciliation, and invoice-linked refund handling; no duplicate replacement was applied.

Moderation / content / notification:
- #856: notification delivery-error JSON scalars are handled as malformed data instead of raising `AttributeError`.
- #857: notification tag stripping no longer uses the greedy form identified by the audit.
- #858: cast association requests provide a non-null role and the service persists it.
- #859: creator onboarding duplicate submissions return 409 after rollback.
- #869: moderation flag saves re-attach the entity to the session before flush.
- #870: missing DMCA counter-notice targets return 404.
- #878: ad creation uses a Pydantic request model instead of an ORM object as a FastAPI request body.
- #880: unreachable notification `email_address` handling was removed.
- #881: explicitly supplied moderation flag IDs are preserved by the repository insert.
- #873: analytics content ownership resolution fails closed when content-service returns a non-object JSON payload.
- #876: user and notification internal-error responses propagate `X-Correlation-ID`.

Media / compliance:
- #846: compliance parent-policy merging preserves the requested child jurisdiction enum.
- #860: creators inbound-event poll interval is now a real settings field.
- #862: ffmpeg pipe-capture retains the chunk that crosses the capture cap instead of discarding its diagnostic bytes.
- #847: aiokafka DLQ retention passes the required ConfigResource collection to `alter_configs`.
- Existing #861 and #863 handling was verified in the current code: circuit-breaker-open paths fail the job and clean up; recommendation shutdown explicitly closes subscribers/clients/database. Streaming still has a conventional lifespan shutdown path.

## Current open PR audit

### #835
One-line SHA-256 replacement for the Redis rate-limit scope hash. The same change is applied on this remediation branch. The PR is therefore superseded by the audit branch rather than needing a separate merge.

### #838
Backup/stash PR. It is currently reported non-mergeable and contains malformed changes to `pyproject.toml` plus unrelated auth-route edits. It should not be merged as a unit.

### #839
Backup/stash PR. It is currently reported non-mergeable and changes 79 files / thousands of lines. It mixes supply-chain changes, CI edits, and unrelated repository state. It should not be merged as a unit; relevant individual hardening changes were audited separately.

## Findings that remain external or require additional architectural work

These are not marked “fixed” merely because code was changed:
- #788: purging a committed private key from full Git history requires coordinated history rewriting and credential rotation, not just a forward commit.
- #790: the repository already uses asymmetric RS256/JWKS in the auth service, but a complete migration requires verifying every issuer/verifier boundary and deployment configuration.
- #794: production admin step-up authentication is an application/security design requirement and needs end-to-end confirmation of the actual destructive-operation policy.
- #795: Kafka TLS/SASL and broker ACL enforcement depend partly on broker/cluster configuration. Application-side security configuration is present, but broker-side ACL correctness must be verified in the deployment.
- #796: managed PostgreSQL/Redis is a deployment topology requirement, not something a source-only patch can safely manufacture.
- #45: DRM remains an external licensing/provider integration requirement; the repository status documentation explicitly identifies it as remaining production work.
- Any finding whose acceptance criteria depend on live AWS/EKS/Stripe/Kafka/DRM infrastructure requires live environment verification after the branch is deployed.

## Historical closed issues

The repository contains a generated full audit of 607 closed issues. That older audit reported 481/482 cited commit SHAs verified in history and documented closing evidence for essentially the entire closed corpus. This audit deliberately does not assume those closures are permanent: several later/open findings demonstrate that regressions can reintroduce previously “fixed” conditions.

The practical treatment here is:
1. Existing closed-finding implementations were reused where current code still satisfies their acceptance criteria.
2. New regressions were fixed on the remediation branch instead of reopening every historical duplicate one-by-one.
3. Duplicate issue families are tracked by root-cause fix rather than creating multiple contradictory implementations.

## Validation state

The branch has not been executed in a local checkout during this audit because the container environment could not clone the repository. Source-level verification was performed through the GitHub repository interface.

PR #931 is the CI execution vehicle. GitHub Actions run #1458 is currently pending on the latest branch head. No merge into `main` should occur until that run and any follow-up fixes are reviewed.

## Known audit limitation

GitHub's connected search action caps a single PR search at 100 records. Current open PRs were individually inspected; the historical PR corpus was reviewed through the repository's current code/history and the active PR set rather than pretending that one paginated “all PRs” response was available from the connector.
