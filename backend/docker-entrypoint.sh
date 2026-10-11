#!/bin/sh
set -e
# Apply database migrations, then start the API: gunicorn with uvicorn workers (ADR 0008).
# SERVER=uvicorn is the fallback if gunicorn ever needs to be bypassed.
alembic upgrade head
if [ "${SERVER:-gunicorn}" = "uvicorn" ]; then
  # X-Forwarded-* is believed only from TRUSTED_PROXY_IPS; with none set, proxy headers are off
  if [ -n "${TRUSTED_PROXY_IPS:-}" ]; then
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="${TRUSTED_PROXY_IPS}" --workers "${WEB_CONCURRENCY:-2}"
  fi
  exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --no-proxy-headers --workers "${WEB_CONCURRENCY:-2}"
fi
exec gunicorn -c gunicorn.conf.py app.main:app
