# Quick Local Setup Checklist

Add this checklist to keep an easy reference of immediate actions you can run locally.

- [ ] Read core docs and README

```bash
sed -n '1,200p' README.md
sed -n '1,200p' docs/DOCUMENTATION_GUIDE.md
```

- [ ] Start local infra with Docker Compose

```bash
docker compose -f deployments/docker-compose.dev.yml up --build
```

- [ ] Resolve Docker layer/blob error (if encountered)

```bash
docker system prune -a --volumes
docker builder prune -a
sudo systemctl restart docker   # or restart Docker Desktop
```

- [ ] Bring up `admin-service` and verify health

```bash
docker compose -f deployments/docker-compose.dev.yml up --build admin-service
curl https://localhost:8006/health
```

- [ ] Run unit tests (project-wide)

```bash
# All 15 services + SDK
for svc in services/*/; do
  (cd "$svc" && pytest tests --asyncio-mode=auto) || exit 1
done

# Or single service
cd services/auth-service && pytest tests --asyncio-mode=auto
```

- [ ] Open and inspect service code you want to work on

```bash
ls -la services
code .
rg "uvicorn|FastAPI|if __name__ == \"__main__\"" -S --hidden || true
```

- [ ] Run frontend tests

```bash
cd apps/web
npm run test            # Vitest unit tests
npx playwright test    # Playwright E2E tests
```

- [ ] Run integration tests (needs compose stack up)

```bash
poetry run pytest tests/integration -q    # ~12 min
```

- [ ] Run contract tests

```bash
pytest tests/contract -q
```

- [ ] Verify CI status

```bash
gh run list --workflow=ci-cd.yml --branch main --limit 1
```

---

_Saved from session: September 7, 2026_