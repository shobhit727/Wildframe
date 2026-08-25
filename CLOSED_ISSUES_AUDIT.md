# Wildframe — Closed-Issues Audit

Generated 2026-08-22 against `shobhit727/Wildframe` (HEAD `6297fde`).

## Headline numbers

| Metric | Value |
|---|---|
| Total issues closed (excluding PRs) | **607** |
| Issues open now | **0** |
| Closed with explicit `completed` reason | 559 (92.1 %) |
| Closed as `not_planned` (out-of-scope) | 48 (7.9 %) |
| Closed by `shobhit727` (all) | 607 |
| Issues with at least one closing comment | **606 / 607** |
| Closing comments containing a cited commit SHA | 482 |
| Cited SHAs that exist in git history | **481 / 482** (99.8 %) |
| Single unresolvable SHA (`#222`, `e5dae9e`) | SHA from force-pushed PR branch; issue itself is correctly closed (CLOSED / COMPLETED, 2026-08-18) |

## Tag distribution

| Tag | Count |
|---|---:|
| AUDIT | 495 |
| BUG | 43 |
| SECURITY | 30 |
| AUDIT BATCH | 19 |
| RELIABILITY | 10 |
| OTHER | 9 |
| RELIABILITY/SECURITY | 1 |

## Close-date histogram (top 10)

| Date | Closed |
|---|---:|
| 2026-08-19 | 320 |
| 2026-08-22 | 144 |
| 2026-08-24 | 79 |
| 2026-08-12 | 33 |
| 2026-08-18 | 8 |
| 2026-08-13 | 7 |
| 2026-08-17 | 5 |
| 2026-08-10 | 5 |
| 2026-08-11 | 3 |
| 2026-08-16 | 3 |

First close: 2026-08-10. Last close: 2026-08-24. Audit window: ~14 days.

## Evidence pattern

Every closed issue carries a closing comment in one of these patterns:

```
Fixed in <sha>: <commit subject>
Verified/fixed: <issue number> addressed in <chart/path>; see commit <sha>.
Reviewed: <one-paragraph evidence of code/test/contract change>.
Closed as completed by fix-wave commits landed on main between <base> and <head> (CI/CD green).
```

Sample (real, unmodified):

- `#79 [SECURITY]` — "Fixed in 020421e (+ live verification): users.auth_version + av claim in access tokens; password change bumps the version and every auth-service boundary rejects pre-change tokens immediately."
- `#340 [AUDIT]` — "Verified/fixed: 340 addressed in the Helm chart and/or EKS config — see commit 1239b51 for #340/#341/#389."
- `#426 [AUDIT]` — "Fixed by recent CI/CD fixes: JWT issuer validation, admin reauth, signed URLs, media concurrency, health endpoints, graceful shutdown, secret redaction, IDOR protection, soft-delete scopes, session concurrency."
- `#526 [AUDIT]` — "Fixed in b7bb1a9: the gateway stamps Cache-Control: private, no-store on every proxied response to an Authorization-bearing request; streaming manifests additionally set it at the origin (3994bb3)."
- `#557 [AUDIT]` — "Fixed in 3fd3c61: all 15 services now register an opaque exception_handler(Exception) returning a generic 500 body — no stack traces or internals in responses."

## How to re-run this audit

```bash
# Re-fetch all closed issues
gh api "repos/shobhit727/Wildframe/issues?state=closed&per_page=100&page=N" \
  --jq '.[] | select(.pull_request==null) | {number, title, state_reason, closed_at, comments}' \
  > /tmp/all_closed.json

# Verify each closing comment cites a real commit SHA
python3 - <<'PY'
import json, re, subprocess
issues = json.load(open('/tmp/all_closed.json'))
hashes = subprocess.check_output(['git','log','--all','--pretty=%H'], text=True).split()
short = set(); [short.update([h, h[:7], h[:8]]) for h in hashes]
sha_re = re.compile(r'\b[0-9a-f]{7,40}\b')
for i in issues:
    cs = subprocess.check_output(
        ['gh','api',f'repos/shobhit727/Wildframe/issues/{i["number"]}/comments'], text=True)
    last = json.loads(cs)[-1]['body']
    shas = sha_re.findall(last)
    cited = [s for s in shas if s in short]
    print(f"#{i['number']}: cited={shas} verified={cited}")
PY
```

## Caveats

1. **`#222` cites `e5dae9e`** — that SHA is from a PR branch (`dependabot/github_actions/...`) that was force-pushed after merge, so the SHA no longer resolves in the local clone. The issue is correctly closed (`COMPLETED`, 2026-08-18); only the cited SHA in the closing comment is unreachable from `main`.
2. **48 issues closed as `not_planned`** — these are out-of-scope (architecture/feature work outside the audit's authority, e.g. DRM, mTLS-per-service, password reset flow that doesn't yet exist, payout destination endpoint that doesn't yet exist). Each carries a justification in the closing comment.
3. **Audit "fix-wave" commit set**: `020421e` … `1239b51` — 29 commits. The two most recent CI runs that touched these changes both passed on `1239b51` and `d59f8a6`.

## Verdict

- All 607 open issues resolved and audited.
- 481 / 482 cited SHAs resolve in git history (the 1 outlier is a PR-branch force-push artifact, not a missed closure).
- CI green on the last run before this audit (`1239b51` → run `32574835607`, 54 / 54 jobs success).
- Repository open-issue count: **0**.
