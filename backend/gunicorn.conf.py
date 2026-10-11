"""gunicorn settings (ADR 0008). Everything comes from the environment; run as: gunicorn -c gunicorn.conf.py app.main:app

Linux only (gunicorn does not run on Windows): in Docker, or on the server. On a Windows dev machine use uvicorn.
"""
import os

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
workers = int(os.getenv("WEB_CONCURRENCY", "2"))
worker_class = "app.core.server_worker.Worker"  # uvicorn's worker; proxy headers only from TRUSTED_PROXY_IPS
timeout = int(os.getenv("WORKER_TIMEOUT", "120"))  # a worker silent this long is replaced (plans stream, so it is rarely silent)
graceful_timeout = int(os.getenv("GRACEFUL_TIMEOUT", "30"))  # on a deploy, running plans get this long to finish
keepalive = 5
# Only our own proxies may say who the caller is (never "*"). Empty means nobody: gunicorn then falls back to 127.0.0.1 for its own
# scheme headers, and the worker above turns uvicorn's proxy handling off.
forwarded_allow_ips = os.getenv("TRUSTED_PROXY_IPS", "").strip() or "127.0.0.1"
accesslog = None
errorlog = "-"
