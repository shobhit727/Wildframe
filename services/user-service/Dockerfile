# Multi-stage Dockerfile for user-service

# Stage 1: Builder
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    pkg-config \
    libsasl2-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies with pip (gunicorn is the production server)
COPY services/user-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "gunicorn==23.0.0"

# Stage 2: Development
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS development

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY services/user-service /app

# Copy SDK directly into app
COPY packages/sdk/wildframe_observability /app/wildframe_observability

# Create logs directory
RUN mkdir -p logs

# Health check
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Stage 3: Production
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS production

WORKDIR /app

# Create non-root user
RUN useradd -m -u 1000 appuser

# Copy Python dependencies from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Remove pip (package manager) from the final image
RUN rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13 \
    && rm -rf /usr/local/lib/python3.13/site-packages/pip \
    /usr/local/lib/python3.13/site-packages/pip-*.dist-info

# Copy application code
COPY services/user-service /app

# Copy SDK directly into app
COPY packages/sdk/wildframe_observability /app/wildframe_observability

# Create logs directory and set permissions
RUN mkdir -p logs && chown -R appuser:appuser /app

# Runtime tuning: no bytecode writes (read-only-rootfs friendly), unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Switch to non-root user
USER appuser

# Health check (stdlib urllib — no curl required in the image)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5).read()"]

# Expose port
EXPOSE 8000

# Run with gunicorn for production
CMD ["gunicorn", \
     "--workers", "4", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "app.main:app"]
