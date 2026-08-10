# Load Tests (Locust)

Distributed load tests for the Wildframe API gateway. Simulates realistic
viewer traffic: health checks, login/register, catalog browsing, playback
session starts, and search — all through the gateway on `:8000`.

## Prerequisites

Bring up the full dev stack first:

```bash
docker compose -f deployments/docker-compose.dev.yml up --build -d
```

Install Locust into the dev venv:

```bash
pip install locust
```

## Run

```bash
# Local run: 50 users ramping 10/s, auto-quit after 60s, HTML report
locust -f load-tests/locustfile.py --host http://localhost:8000 \
  --autostart --autoquit 60 -u 50 -r 10 --html load-test-report.html
```

Interactive (headless UI on http://localhost:8089):

```bash
locust -f load-tests/locustfile.py --host http://localhost:8000
```

Seed the catalog first for realistic browse/stream results:

```bash
python scripts/seed_demo.py   # if available against the dev stack
```

## Scenarios

| User class | Flow |
|------------|------|
| `HealthUser` | `GET /health` + content upstream reachability every 1–3s (watchdog) |
| `ViewerUser` | Login/register → browse catalog (Bearer) → start playback session (Bearer, self-owned `user_id`) → search (public) |

Key details baked into the flows:

- **Auth**: login returns JWT access tokens; the `sub` claim is decoded from
  the token and reused as `user_id` so the streaming 403 self-ownership check
  passes.
- **Registration**: on a 401 from login (fresh database), the flow registers
  once; subsequent logins use the same per-worker email
  (`viewer-<id>@loadtest.wildframe.local`).
- **Content ids**: scraped from the browse response so `POST
  /streaming/api/v1/playback-sessions` uses real catalog entries; falls back
  to placeholder UUIDs when the catalog is empty.
- **Rate limiting**: the gateway rate-limiter is exercised naturally (per-user
  key once authenticated, IP before that); keep `-u` modest on a fresh Redis
  to avoid 429s dominating results.

## Distributed mode (optional)

```bash
# master
locust -f load-tests/locustfile.py --master --host http://localhost:8000
# workers (one per core)
locust -f load-tests/locustfile.py --worker --master-host=localhost
```

## Interpreting results

- `POST login` and `POST register` latencies reflect auth-service + DB.
- `GET browse catalog` and `GET search query` exercise content/search service.
- `POST start playback` exercises streaming-service (DB write + manifest).
- 429s mean the gateway rate limiter kicked in (raise `RATE_LIMIT_*` settings
  or reduce concurrency); 403 on stream start means a mismatched `user_id`.
