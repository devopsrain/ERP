# syntax=docker/dockerfile:1

# ---- build stage: compile wheels so the runtime image stays slim ----
FROM python:3.12-slim AS build
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*
# requirements-data.txt (yfinance/pandas) is included so the same image can
# run the correlation-job compose service — see RUNBOOK for the size tradeoff.
COPY requirements.txt requirements-data.txt ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt -r requirements-data.txt

# ---- runtime stage ----
FROM python:3.12-slim
WORKDIR /srv

# Numpy is already compiled against OpenBLAS; cap its thread pool so a single
# pod doesn't try to grab every core on the R430 node out from under its
# neighbors. Override via the Deployment env if you want more.
ENV OMP_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=build /wheels /wheels
COPY requirements.txt requirements-data.txt ./
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt -r requirements-data.txt \
    && rm -rf /wheels

COPY app ./app
# default correlation-job config; compose bind-mounts ./config over this so
# edits on the host take effect without a rebuild
COPY config ./config

# /data/output must exist in the image owned by appuser so the named volume
# created from it is writable by uid 10001 (correlation-job writes there).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /data/output \
    && chown -R appuser:appuser /srv /data/output
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
