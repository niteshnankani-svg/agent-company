FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Runtime dependencies only (see requirements.txt). For Postgres checkpoints, install
# requirements-postgres.txt instead. Nothing here compiles from source, so no build tools are needed.
COPY requirements.txt .
RUN pip install -r requirements.txt

# A non-root user owns the app and the one writable directory.
RUN useradd --system --uid 10001 --home-dir /app app && mkdir -p /app/data
COPY --chown=app:app . .
RUN chown app:app /app/data

# Where runs waiting for a person are stored when CHECKPOINT_DB_URL (Postgres) is not set.
# Mount a volume here, or they are lost with the container.
ENV CHECKPOINT_DB_PATH=/app/data/checkpoints.sqlite
VOLUME ["/app/data"]
USER app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"

# One worker: the answer cache is per process. Configuration (service addresses, API_KEYS, ...) comes from the
# environment; see .env.example.
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080"]
