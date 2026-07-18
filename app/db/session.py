"""
Async SQLAlchemy-Engine + Session-Factory (lokale Variante: SQLite/aiosqlite).

Die lokale App-DB (`appstate.sqlite`) hält Keys/Users/Query-Log/Job-Status —
NIE im Vault. Korpus/Chunks/Graph wandern in M3 nach LanceDB.

Verwendung:
    async with get_session() as session:
        result = await session.execute(...)

`init_db()` legt das Schema an (idempotent, `create_all`) + bootstrappt den Admin.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from config import settings
from logger import log

# NullPool: SQLite ist single-writer; ein Verbindungspool bringt keinen Vorteil
# und provoziert nur „database is locked". Jede Session öffnet frisch.
_engine = create_async_engine(
    settings().appstate_db_url,
    echo=False,
    poolclass=NullPool,
)


# SQLite-Härtung pro Verbindung: WAL (nebenläufige Leser), busy_timeout gegen
# transiente Locks, foreign_keys=ON (sonst greifen ON DELETE CASCADE/SET NULL nicht).
@event.listens_for(_engine.sync_engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


_session_factory = async_sessionmaker(
    bind=_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async-Context-Manager für eine DB-Session."""
    session = _session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_db() -> None:
    """Schema anlegen (idempotent) + Admin-User sicherstellen.

    Lokale Variante: reines `create_all` (Zielschema). Die früheren Postgres-
    DO-Block-Migrationen, `pgcrypto` und GIN-Indizes entfallen — SQLite legt das
    volle Schema direkt aus den Modellen an.
    """
    from db.models import Base  # zirkuläre Imports vermeiden

    log.info("db.init.start", db=settings().appstate_db_url)
    settings().appstate_db_path.parent.mkdir(parents=True, exist_ok=True)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Admin-User bootstrappen
    from auth.users import ensure_admin_user
    await ensure_admin_user()

    log.info("db.init.done")


async def dispose() -> None:
    await _engine.dispose()
