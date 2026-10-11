"""The gunicorn worker: uvicorn's, with proxy headers believed ONLY from the proxies named in TRUSTED_PROXY_IPS (hotfix 2026-10-07).

uvicorn's own proxy handling is switched on only when that list is non-empty, and then it trusts exactly that list; with the list
empty it is off, so `X-Forwarded-For` and `X-Forwarded-Proto` from a client are never believed. The application's own
`client_ip()` (app/core/middleware.py) applies the same rule, so the two cannot disagree.
"""
import os

from uvicorn.workers import UvicornWorker

TRUSTED = os.getenv("TRUSTED_PROXY_IPS", "").strip()


class Worker(UvicornWorker):
    CONFIG_KWARGS = {**UvicornWorker.CONFIG_KWARGS, "proxy_headers": bool(TRUSTED)}
