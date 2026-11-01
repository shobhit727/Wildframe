Title: Unsafe use of `eval()` in development scripts

Description:
One or more development helper scripts call `eval()` to interpret user-provided input. This can lead to arbitrary code execution when those scripts are run locally or in CI by untrusted inputs.

Severity: High (security)

Affected files:
- scripts/dev/hack.sh: uses `python3 -c "... print(eval(sys.argv[1]))"` (jq_get)

Resolution:
- Fixed in `scripts/dev/hack.sh` by replacing `eval()` with a restricted dict/list selector parser that only accepts `d['field']` and numeric list indexes.
- The script still remains a local developer helper, but it no longer executes arbitrary expressions from JSON paths.

Follow-up:
- Audit other scripts and CI tasks for similar patterns.

Notes: This was a local developer helper, but it was still worth removing the arbitrary-expression execution path before any shared or automated use.
