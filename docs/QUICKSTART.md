# 🚀 Wildframe Quick Start

Get the Wildframe platform running on your local machine in under 10 minutes.

**Last Updated**: August 9, 2026
**Version**: 1.0.0

---

## Prerequisites

| Tool | Minimum Version | Check |
|---|---|---|
| Docker | 24.0+ | `docker --version` |
| Docker Compose | v2.20+ | `docker compose version` |
| Python | 3.11+ | `python3 --version` |
| Node.js | 18+ | `node --version` |
| curl | any | `curl --version` |

You should also have at least **8 GB of free RAM** and **20 GB of free disk** for images and volumes.

---

## 1. Clone the Repository

```bash
git clone https://github.com/wildframe/platform.git
cd platform
```

If you already have the project locally, just `cd` into it.

---

## 2. Configure Environment

Service settings are loaded from env vars with sensible dev defaults
(`services/<svc>/app/core/settings.py`). For the dockerized stack, database
users/passwords are defined in `infrastructure/database/init-databases.sql`
and the compose file — no `.env` file is required for local development.

---

## 3. Start the Platform

```bash
docker compose -f deployments/docker-compose.dev.yml up --build -d
```

This launches **15 microservices + infrastructure containers**:

- **15 microservices** (host ports 8000–8014, see table below)
- **PostgreSQL** (one database per service, 16 total)
- **Redis** (cache + sessions)
- **Kafka** + Zookeeper (event streaming)
- **Elasticsearch** (search index)
- **Prometheus** + Grafana (metrics)
- **Jaeger** (tracing)
- **Loki** (logs)
- **postgres-exporter** + **redis-exporter** (metrics)

> ⏱️ First boot takes 3–5 minutes while Docker pulls images and runs database init scripts.

Wait for everything to become healthy:

```bash
docker compose -f deployments/docker-compose.dev.yml ps
```

All services should show `Up` / `healthy`.

---

## 4. Verify the Platform

```bash
# API gateway health
curl https://localhost:8000/health

# Auth service health
curl https://localhost:8001/health

# Content service health
curl https://localhost:8003/health
```

A healthy response looks like:

```json
{ "status": "ok", "service": "auth-service", "version": "1.0.0" }
```

---

## 5. Run the Frontend (Optional)

The repo is an npm-workspaces monorepo — install once from the repo root:

```bash
# From the repo root
npm install --legacy-peer-deps

cd apps/web
npm run dev
```

Open https://localhost:3000.

---

## 6. Run the Test Suite

Backend tests must run **per service** (every service has its own top-level
`app` package, so a combined `pytest services/` sweep breaks on shadowed
imports):

```bash
# All 15 services + SDK
for svc in services/*/; do
  (cd "$svc" && pytest tests --asyncio-mode=auto) || exit 1
done

# One service
cd services/auth-service
pytest tests --asyncio-mode=auto

# Frontend
cd apps/web
npx vitest run
```

See [TEST_GUIDE.md](../docs/TEST_GUIDE.md) for the full testing playbook.

---

## 7. Tear Down

```bash
# Stop containers (keep data)
docker compose -f deployments/docker-compose.dev.yml stop

# Stop and remove containers (keep volumes)
docker compose -f deployments/docker-compose.dev.yml down

# Full reset (drop volumes too)
docker compose -f deployments/docker-compose.dev.yml down -v
```

---

## Quick Port Reference

| Port | Service |
|---:|---|
| 8000 | API Gateway |
| 8001 | Auth |
| 8002 | User |
| 8003 | Content |
| 8004 | Streaming |
| 8005 | Search |
| 8006 | Admin |
| 8007 | Recommendation |
| 8008 | Billing |
| 8009 | Analytics |
| 8010 | Notification |
| 8011 | Media Pipeline |
| 8012 | Creators |
| 8013 | Moderation |
| 8014 | Uploads |
| 3000 | Frontend (Next.js) |
| 9090 | Prometheus |
| 3001 | Grafana (monitoring) |
| 16686 | Jaeger |

---

## Troubleshooting

**Services won't start?**
```bash
docker compose -f deployments/docker-compose.dev.yml logs -f auth-service
```

**Port already in use?**
Edit the host port mapping in `deployments/docker-compose.dev.yml`.

**Stale Docker state?**
```bash
docker system prune -a --volumes
docker builder prune -a
```

**Need to nuke everything?**
```bash
docker compose -f deployments/docker-compose.dev.yml down -v
./start_services.sh
```

---

## Next Steps

- [TEST_GUIDE.md](TEST_GUIDE.md) — How to write and run tests
- [ARCHITECTURE.md](ARCHITECTURE.md) — How the platform is structured
- [SERVICE_ARCHITECTURE_PATTERN.md](SERVICE_ARCHITECTURE_PATTERN.md) — Patterns every service follows
- [FRONTEND_ARCHITECTURE.md](FRONTEND_ARCHITECTURE.md) — Frontend structure and conventions
- [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) — Promote to staging/production
