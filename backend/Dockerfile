# Sorted backend — production image (API and worker share one image; the
# command decides the role).
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production

WORKDIR /app

# Build deps for psycopg/cryptography wheels are not needed with the binary
# wheels we pin, so the image stays slim. curl is for the container healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Dependencies first so application edits don't invalidate the layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Never run as root: a container escape should not land on uid 0.
RUN useradd --create-home --uid 10001 sorted \
 && chown -R sorted:sorted /app
USER sorted

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# Production server: gunicorn supervises uvicorn workers, so one crashed
# worker is replaced instead of taking the service down. No --reload.
# The worker container overrides this with: python -m worker
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "2", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "120", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
