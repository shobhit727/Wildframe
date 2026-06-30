# ---------------------------------------------------------------------------
# Wildframe Moderation Service — Multi-stage Docker build
# ---------------------------------------------------------------------------
# Development stage: hot-reload, debug logging
# Production stage:  slim image, optimized for Kubernetes
# ---------------------------------------------------------------------------

# ---- Base ----
FROM python:3.14-slim AS base

WORKDIR /app

RUN pip install --no-cache-dir poetry>=1.7.0

COPY services/moderation-service/pyproject.toml services/moderation-service/poetry.lock* ./
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --only main

# ---- Development ----
FROM base AS development

RUN poetry install --no-interaction --no-ansi --with dev

COPY services/moderation-service/ .

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8010/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010", "--reload"]

# ---- Production ----
FROM base AS production

COPY services/moderation-service/ .

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8010/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "4"]
