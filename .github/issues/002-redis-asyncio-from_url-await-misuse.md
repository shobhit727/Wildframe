Title: Potential misuse of `redis.asyncio.from_url` with `await`

Description:
Several services call `await redis.from_url(...)` or `await redis_async.from_url(...)`. Depending on the `redis` package version, `Redis.from_url()` may be a synchronous constructor and should not be awaited. Awaiting a non-coroutine will raise at runtime.

Severity: High (stability/runtime)

Affected files (observed):
- services/admin-service/app/main.py
- services/analytics-service/app/main.py
- services/api-gateway/app/main.py
- services/billing-service/app/main.py
- services/creators-service/app/main.py
- services/media-pipeline/app/main.py
- services/notification-service/app/main.py
- services/recommendation-service/app/main.py
- services/recommendation-service/app/services.py
- services/uploads-service/app/main.py

Recommendations:
- Verify the `redis` package API in the environment and ensure `from_url` is used correctly (no `await` if synchronous).
- Add unit tests or startup checks to catch incorrect await usage.

Notes: Project docs and AGENTS.md recommend `redis.asyncio` over `aioredis` — confirm the exact API contract for the pinned dependency version.
