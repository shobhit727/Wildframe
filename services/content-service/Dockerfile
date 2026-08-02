# Multi-stage Dockerfile for content-service

# Stage 1: Builder
FROM python:3.14-slim as builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends     gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY services/content-service/pyproject.toml .
RUN pip install --no-cache-dir poetry &&     poetry config virtualenvs.create false &&     poetry install --no-dev --no-interaction --no-ansi

# Stage 2: Development
FROM python:3.14-slim as development

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends     postgresql-client curl && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY services/content-service /app
RUN mkdir -p logs

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8003/health || exit 1

EXPOSE 8003

# Stage 3: Production
FROM python:3.14-slim as production

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends     postgresql-client curl && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY services/content-service /app
RUN mkdir -p logs &&     adduser --disabled-password --gecos '' appuser &&     chown -R appuser:appuser /app

USER appuser

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8003/health || exit 1

EXPOSE 8003

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
