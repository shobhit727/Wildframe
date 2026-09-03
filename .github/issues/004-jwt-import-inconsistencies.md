Title: Inconsistent JWT library imports (`import jwt` vs `from jose import jwt`)

Description:
The project uses `python-jose` (the `jose` namespace). Some files incorrectly `import jwt` (PyJWT import), which can cause runtime ImportError or subtle behavior differences if `PyJWT` is not installed. AGENTS.md documents this risk.

Severity: High (startup/runtime)

Observed occurrences:
- `services/api-gateway/app/middleware.py` imports `import jwt` (PyJWT-style)
- Multiple services correctly use `from jose import jwt` (analytics, auth, moderation, recommendation, search, uploads, user, etc.)

Recommendations:
- Standardize on `from jose import jwt` across the codebase.
- Add linter or import checks to catch accidental `import jwt` usage.

Notes: There are historical audit entries referencing startup crashes caused by this mismatch.
