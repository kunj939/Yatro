import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core import telemetry
from app.core.config import get_settings
from app.db import pool

_settings = get_settings()
_connect_args = {"check_same_thread": False, "timeout": 30} if _settings.is_sqlite else {}
POOL = pool.plan(_settings)
_in_memory = _settings.is_sqlite and (":memory:" in _settings.database_url or _settings.database_url.rstrip("/") == "sqlite:")
engine = create_engine(_settings.database_url, echo=_settings.db_echo, pool_pre_ping=True, connect_args=_connect_args, future=True,
                       **({} if _in_memory else {"pool_size": POOL.size, "max_overflow": POOL.overflow}))
for _w in POOL.warnings:
    logging.getLogger("tripai.db").warning(_w)

if _settings.is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_fk(dbapi_conn, _):  # pragma: no cover - sqlite only
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")  # readers never block the writer (a plan running in the background)
        cur.execute("PRAGMA busy_timeout=30000")  # wait up to 30 s for the other writer instead of failing
        cur.close()

telemetry.install_db_listener(engine)  # counts queries only while a plan is being measured

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()