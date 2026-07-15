# ── Stage 1: Build dependencies ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# System libs needed to compile psycopg2 and other C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Runtime image ────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Only the runtime lib needed by psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY web/ ./web/
COPY models/ ./web/models/
COPY core/ ./web/core/
COPY multicompany_demo_setup.py ./web/
COPY aws-deployment/init_db.sql ./aws-deployment/init_db.sql
COPY requirements.txt .

# Create non-root user for security
RUN groupadd --gid 1001 ebms && \
    useradd --uid 1001 --gid ebms --shell /bin/bash --no-create-home ebms && \
    chown -R ebms:ebms /app

USER ebms

WORKDIR /app/web

EXPOSE 8000

# Default: run the full web + SSR app.
# Override CMD in docker-compose to run the standalone API service.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--log-level", "info"]
