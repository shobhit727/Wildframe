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
./run_tests.sh
# or
pytest -q
```

- [ ] Open and inspect service code you want to work on

```bash
ls -la services
code .
rg "uvicorn|FastAPI|if __name__ == \"__main__\"" -S --hidden || true
```

_Saved from session: May 27, 2026_
