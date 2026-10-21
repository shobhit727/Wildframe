---
name: wildframe-service-change
description: Use when adding or changing anything in a Wildframe backend service - a route, a schema, a repository query, or a cross-service call. Traces the actual layering the 15 services use, and warns that the layout is deliberately NOT uniform so a recipe that assumes it will create directories that do not exist.
---

# Change a Wildframe backend service

## Check the layout first — it is not uniform

Six services use a **flat** `app/api/*_routes.py`. Nine use `app/api/routes/`. Six have
`app/models.py`; nine have `app/models/`. A recipe that assumes the packaged layout
will create directories the target service does not have.

```bash
ls services/<service>/app/
```

The canonical flow where it applies:

```
HTTP route -> validation/schema -> business service -> repository/model
```

- **Schema** in `app/schemas/` — request and response separately. Never return a
  password hash, a token, or internal auth state in a response model.
- **Repository** in `app/repositories/` — all queries here. Routes stay free of SQL.
- **Service** in `app/services/` — business rules; the only place that composes repositories.
- **Route** in `app/api/routes/` (or the flat `*_routes.py`) — parse, authorize, call
  the service, map errors to status codes.

**Register the route in the service's router aggregation** and confirm it with
`curl -sk https://localhost:<port>/openapi.json`. A route you forget to register
returns 404 while the code looks correct.

**Authorization happens before state-changing side effects** — in the route or the
service, never after the write.

## Calling another service

Use the service's own settings. Follow the existing convention in
`app/core/settings.py` — several services already carry
`AUTH_SERVICE_URL: http://auth-service:8000` and `JWT_JWKS_URL`, and that is the pattern.

**Be explicit about which side of the trust boundary the code runs on.** One variable
cannot serve both. Browser code needs the public host; in-container server code needs
the docker-internal name, because `localhost:8000` inside a container *is that
container*. Merging them shipped a 502 on every request.

## Schema changes

`Base.metadata.create_all` is **`checkfirst`**: it creates missing tables and **never
alters existing ones**. So a new column on a table that already exists simply will not
appear, and the service 500s with `UndefinedColumnError` on the first request that
selects it.

```bash
python scripts/init_schemas.py     # the schema authority
```

**Know its limits before you rely on it.** It `ADD COLUMN`s only — removals, retypes,
and `NOT NULL` without a server default still need a human `ALTER TABLE`. It does not
create indexes, foreign keys, or unique constraints, so a column declared
`unique=True` lands unenforced. And it will re-add a column that was dropped along with
its data, reporting success. If your change touches one of those, this is a
`oner-task.md` item, not a routine fix.

## Tests

Run from the service's **own directory** — these use top-level `app` packages, and a
repo-root pytest sweep causes import shadowing.

```bash
cd services/<service>
poetry install --no-interaction --with dev     # install BEFORE testing, always
pytest tests --asyncio-mode=auto
```

An uninstalled venv produces confident, entirely fictional errors — an
`import-untyped` storm across 13 services from one package that was never installed.

A regression test must **fail without your fix**. Revert, run it, watch it go red,
restore, watch it go green. If you never saw it red you do not know what it tests.

Do not mock the layer the defect lives in. A dependency-injection test proves your code
calls what you told it to call; it cannot prove the real thing works.

## When you finish

- Hit a **real** endpoint, not `/health` — see the `verify-against-running-stack` skill.
- If a schema changed, run `scripts/init_schemas.py` and verify on a **fresh volume**.
- If a dependency moved, `pip check` must be clean and the whole family moves together.
- Anything a human still owes goes in `oner-task.md`.
