Title: Unsafe use of `eval()` in development scripts

Description:
One or more development helper scripts call `eval()` to interpret user-provided input. This can lead to arbitrary code execution when those scripts are run locally or in CI by untrusted inputs.

Severity: High (security)

Affected files:
- scripts/dev/hack.sh: uses `python3 -c "... print(eval(sys.argv[1]))"` (jq_get)

Recommendations (do NOT implement here):
- Replace `eval()` with a safe parser or explicit lookup/whitelist.
- Audit other scripts and CI tasks for similar patterns.

Notes: This file appears to be a local developer helper but still merits removal or protection before CI or shared demo runs.
