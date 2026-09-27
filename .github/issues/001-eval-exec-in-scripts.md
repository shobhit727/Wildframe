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


---

## Documentation verification — 2026-09-27

Rechecked against the Wildframe source tree at commit `70e04e6417e83a15fe2ca91d993a2c9408adf0a8` (the `main` baseline used for this documentation refresh). Repository paths and referenced project structure are maintained against source; when this document conflicts with code, the code is authoritative.
