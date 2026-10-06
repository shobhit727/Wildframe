# Wildframe Comprehensive Audit — 2026-09-27 Checkpoint

Repository: `shobhit727/Wildframe`
Canonical remediation branch: `audit/comprehensive-issue-pr-fixes-2026-09-26`
Current canonical HEAD: `3c712ebb5e7d120fc6129616da77422b251eebd3`
Default branch: `main` at `e815bf35aaad1f6cb9e9e85eb0e83b6052ddcff1`

## Executive summary

This is a live checkpoint, not a claim that every tracker item is resolved.

The uploaded repository audit reported 21 branches, 155 open issues, 632 closed issues, 5 open PRs and 146 closed PRs at its September 27 checkpoint. The current GitHub branch inventory has since changed materially: only five branches are currently visible, and the PR search inventory currently contains 152 PRs, with three open PRs (#931, #938 and #939). The uploaded figures are historical evidence; current GitHub state is authoritative.

The canonical remediation branch is now 212 commits ahead of main, 1 commit behind by ancestry, and changes 151 files. The single behind commit is main's #840 gateway conflict-resolution commit; the canonical branch already contains the later RS256/JWKS gateway implementation, so blindly merging that one commit would risk regressing gateway authentication behavior.

CI has been triggered for the current canonical HEAD (run #1526) and is queued. No local full-suite result is claimed here.

## Authentication validation

The Argon2id migration is implemented in the canonical branch.

- `argon2-cffi` is declared by auth-service.
- New passwords are hashed with Argon2id.
- Existing bcrypt hashes remain verifiable.
- `needs_rehash()` treats bcrypt hashes as migration candidates.
- Successful login persists a new Argon2id hash before the login transaction is committed.
- Password input is bounded at 128 characters without truncating bytes.
- Unknown-user timing uses an Argon2 dummy hash.
- The login path explicitly requests `include_inactive=True`, allowing suspended accounts to return the intended 403 instead of being indistinguishable from nonexistent accounts.
- The repository's normal `get_by_email(email, include_inactive=False)` API remains intact.

Static verification confirms the migration path exists end-to-end. CI/test execution is still pending.

## New remediation applied in this checkpoint

### #934 — aiokafka DLQ retention API

Status: FIXED IN BRANCH.

`ConfigResource.Type.TOPIC` was replaced with the stable `ConfigResource("topic", topic)` form, and `alter_configs()` continues to receive a collection of ConfigResource objects.

A regression test was added for the collection/resource shape.

### #935 — JWKS refresh on unknown kid

Status: FIXED IN BRANCH.

The shared JWKS cache now accepts an optional required key ID. A cache entry that is still within TTL is returned only when it contains that key ID. Otherwise the cache refreshes immediately.

The API gateway now extracts the token `kid` and asks the cache for a JWKS set containing that key ID. This removes the five-minute key-rotation outage described by the issue without shortening the normal cache TTL.

A regression test was added for an unknown-key refresh.

### #937 — Kafka TLS verification regression

Status: FIXED IN BRANCH.

The SDK publisher, subscriber and DLQ-retention adapter now default `KAFKA_SSL_INSECURE` to `false`. The Helm deployment template also defaults it to `false`.

Development Compose configuration may still explicitly opt into insecure TLS for local development; that explicit override is not the same as a production default.

Regression coverage was added for publisher/subscriber TLS defaults.

### #940 — Redis dependency contract

Status: FIXED IN BRANCH, pending dependency-resolution CI.

The service-level Poetry declarations that used `redis = "^5.0.0"` or `^5.0.1` were aligned to `>=5,<9`, matching the root project's current lockfile resolution of Redis 8.1.0. This fixes the declared-range/lockfile contradiction without claiming runtime compatibility has been locally tested.

## Existing remediation verified by inspection

The canonical branch retains earlier work for authentication/JWT hardening, rate-limit hashing, production secret validation, gateway documentation and age-gate restrictions, correlation IDs, billing currency/milestone/payout corrections, moderation persistence and DMCA handling, analytics fail-closed parsing, bounded FFmpeg diagnostics, Kafka DLQ configuration, admin model/repository import identity, CI supply-chain controls and test-collection gates.

These are source inspections, not substitutes for successful current CI.

## Current high-impact issue review

The live tracker still contains open findings that must not be marked fixed merely because a remediation branch exists.

| Issue | Current classification |
|---|---|
| #786 | FIXED IN BRANCH / CI verification pending |
| #787 | FIXED IN BRANCH / CI verification pending |
| #841 | FIXED IN BRANCH / CI verification pending |
| #842 | FIXED IN BRANCH / CI verification pending |
| #843 | FIXED IN BRANCH / CI verification pending |
| #844 | FIXED IN BRANCH / CI verification pending |
| #889 | REQUIRES FURTHER DEV-STACK WORK |
| #891 | REQUIRES DEPENDENCY/INFRASTRUCTURE DECISION |
| #892 | NEEDS CURRENT DOCKER BUILD VERIFICATION |
| #893 | REQUIRES DEV KAFKA TOPIC/ACL WORK |
| #894 | NEEDS CURRENT GATEWAY/KAFKA VERIFICATION |
| #906 | FIXED IN BRANCH / CI verification pending |
| #907 | FIXED IN BRANCH / CI verification pending |
| #922 | UPDATED IN BRANCH; documentation verification pending |
| #923 | UPDATED IN BRANCH; CI verification pending |
| #924 | UPDATED IN BRANCH; checker execution pending |
| #925 | UPDATED IN BRANCH; review pending |
| #926 | UPDATED IN BRANCH; review pending |
| #927 | FIXED IN BRANCH; deployment-environment validation pending |
| #928 | NEEDS CURRENT SCRIPT/DOC VERIFICATION |
| #929 | NEEDS DOCUMENTATION CONSOLIDATION REVIEW |
| #930 | ARCHITECTURAL WORK REMAINS; not safe to auto-refactor |
| #932 | NEEDS SMALL CODE CLEANUP/TEST REVIEW |
| #933 | REQUIRES PERMANENT CHUNKED-BODY REGRESSION TEST |
| #934 | FIXED IN BRANCH |
| #935 | FIXED IN BRANCH |
| #936 | NEEDS DEAD-CONFIG REMOVAL REVIEW |
| #937 | FIXED IN BRANCH |
| #940 | FIXED IN BRANCH; dependency-resolution CI pending |

This is not presented as the final all-issue matrix.

## Closed issue audit

`CLOSED_ISSUES_AUDIT_FULL.md` is the historical evidence set for the previously audited 607 closed issues. It records closure comments, cited commits, SHA verification and classifications.

The live tracker has changed since that document was generated. Therefore:

- GitHub's closed state is not treated as proof of a code fix.
- The historical 607-issue corpus remains useful evidence.
- Newly created/closed issues require a refreshed per-issue ledger before this document can be called final.
- Historical secret-removal issues are not considered completely fixed merely because the secret is absent from the current tree.

## Pull request audit

Current PR search inventory: 152 PRs.

Relevant open PRs:

| PR | State | Classification |
|---|---|---|
| #931 | Open | Canonical comprehensive remediation branch |
| #938 | Open | Separate large test-coverage/remediation workstream; not safe to merge blindly |
| #939 | Open | Documentation/cleanup instructions; requires separate review |

PR #835 was closed after confirming its SHA-256 rate-limit hashing fix is represented in the canonical remediation line.

PR #938 contains 209 changed files and 16 commits. A direct branch comparison shows substantial overlap in gateway, auth, Helm and SDK files. It was briefly tested as a possible base-consolidation path, but GitHub reported it non-mergeable against the canonical branch. Its base was restored to `main`; it remains an independent workstream.

Historical PRs #36, #22, #640, #836 and #840 explain old branch residue and already-landed work.

A final per-PR matrix with every PR's reviews/comments/changed files remains required before final closure.

## Branch audit

Only five branches are currently visible:

| Branch | Current relation to main | Disposition |
|---|---|---|
| `main` | baseline | KEEP |
| `audit/comprehensive-issue-pr-fixes-2026-09-26` | 212 ahead / 1 behind; 151 changed files | KEEP — canonical remediation |
| `audit/fix-open-github-issues` | 16 unique commits; 209 changed files | KEEP FOR REVIEW |
| `alert-autofix-161` | 1 unique commit | SUPERSEDED; PR #835 closed |
| `audit/code-audit-20260918` | 0 ahead / 28 behind | SAFE TO DELETE when branch-deletion access is available |

No destructive branch deletion was performed.

## External/manual actions

1. Historical Git secrets/private keys require credential rotation and, where necessary, Git-history rewriting under maintainer control.
2. Production Kafka ACLs/TLS certificates and broker configuration require infrastructure validation.
3. Production PostgreSQL/Redis/Kubernetes configuration requires deployment-environment validation.
4. Stripe/provider credentials and webhook configuration require provider-side validation.
5. DNS/CDN/Cloudflare/CloudFront configuration must be checked externally where relevant.
6. The dev Kafka stack (#889/#893) requires an end-to-end Compose run with the actual broker and topic/ACL setup.
7. Redis dependency alignment (#940) requires Poetry lock/install validation in CI.

## Verification state

Verified by current GitHub source inspection:
- Auth Argon2id migration path.
- Current branch ancestry and changed-file counts.
- #934 source correction.
- #935 cache-refresh implementation.
- #937 TLS-default correction.
- #940 dependency-contract correction.
- PR #835 closure.
- Current five-branch inventory.

Not yet verified in this checkpoint:
- Current full repository test suite.
- Current service-by-service test suite after latest commits.
- Docker Compose full-stack startup.
- Production Kubernetes/Helm deployment.
- Kafka broker ACL/TLS behavior against a live broker.
- Full refreshed per-issue ledger.
- Full 152-PR review/comment/changed-file matrix.

## Next gate

Wait for CI run #1526 on canonical HEAD `3c712ebb5e7d120fc6129616da77422b251eebd3`.

If CI fails, fix the concrete failure on this branch before any merge decision. Then refresh the issue and PR ledgers from GitHub and replace this checkpoint with the final audit matrix.
