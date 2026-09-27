Title: Hardcoded demo credentials and insecure default secrets present in repository

Description:
The codebase contains numerous hardcoded demo passwords and insecure default JWT secrets and other "dev" secrets. These are present in demo scripts, docs, and service `settings.py` defaults. While some are intended for local dev or tests, they increase risk if accidentally used in staging/CI or copied to production.

Severity: Medium → High (risk depends on usage)

Representative occurrences:
- scripts/dev/* (DemoPass123! used in demos and e2e scripts)
- scripts/seed_demo.py (DEMO_PASSWORD = "DemoPass123!")
- docs/QUICKSTART.md and other docs referencing `DemoPass123!`
- Many services' `app/core/settings.py` files define `JWT_SECRET_KEY` defaults like `your-secret-key-change-in-production` or `dev-secret-key-change-in-production` (services: admin-service, auth-service, api-gateway, billing-service, content-service, creators-service, media-pipeline, moderation-service, notification-service, recommendation-service, search-service, streaming-service, uploads-service, user-service)
- tests and integration fixtures define REGISTER_PASSWORD / JWT defaults (tests/integration/conftest.py)

Recommendations:
- Replace demo credentials with environment-only configuration, and remove demo secrets from committed scripts.
- Ensure CI and deployment pipelines refuse to run with default insecure JWT secrets.
- Consider automated scanning to fail PRs that introduce new hardcoded secrets.

Notes: Some defaults are intentionally benign for local development, but the repo already contains guidance to change them — this issue groups remaining occurrences for remediation planning.


---

## Documentation verification — 2026-09-27

Rechecked against the Wildframe source tree at commit `70e04e6417e83a15fe2ca91d993a2c9408adf0a8` (the `main` baseline used for this documentation refresh). Repository paths and referenced project structure are maintained against source; when this document conflicts with code, the code is authoritative.
