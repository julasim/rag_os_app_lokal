"""
Async SQLAlchemy — **zwei** SQLite-DBs (Multi-Vault-Split):

  * LOKAL  `credentials.sqlite`  — `ui_users` + `api_keys`  → `get_local_session()`
  * VAULT  `<vault>/.ragos/state.sqlite` — Content (Dokumente/Chunks/Graph/Logs/Jobs)
           → `get_session()` (unverändert für die meisten Aufrufer)

Warum getrennt: Credentials bleiben lokal pro Rechner (nie auf NAS), der Content lebt
im Vault, damit eine Firma = ein portabler Ordner ist. Siehe Plan „Multi-Vault".

`init_db()` legt beide Schemata an (idempotent), migriert eine ggf. vorhandene alte
Einzel-`appstate.sqlite` (db/migrate.py) und bootstrappt den Admin (lokal).
"""
from __future__ import annotations

import asyncio
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
_local_engine = create_async_engine(settings().credentials_db_url, echo=False, poolclass=NullPool)
_vault_engine = create_async_engine(settings().vault_db_url, echo=False, poolclass=NullPool)


def _pragma(journal_mode: str):
    """SQLite-Härtung pro Verbindung. `journal_mode` je Engine unterschiedlich:
    lokal WAL (nebenläufige Leser), Vault DELETE (WAL über SMB/Netzlaufwerk ist
    unzuverlässig; der Vault-Schreiber ist Single-Writer)."""
    def _apply(dbapi_conn, _record) -> None:  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute(f"PRAGMA journal_mode={journal_mode}")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    return _apply


event.listens_for(_local_engine.sync_engine, "connect")(_pragma("WAL"))
event.listens_for(_vault_engine.sync_engine, "connect")(_pragma("DELETE"))

_local_factory = async_sessionmaker(bind=_local_engine, expire_on_commit=False, class_=AsyncSession)
_vault_factory = async_sessionmaker(bind=_vault_engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def _wrap(factory) -> AsyncGenerator[AsyncSession, None]:
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Vault-DB (Content: Dokumente/Chunks/Graph/Logs/Jobs). Der Default für fast alles."""
    async with _wrap(_vault_factory) as s:
        yield s


@asynccontextmanager
async def get_local_session() -> AsyncGenerator[AsyncSession, None]:
    """Lokale Credentials-DB (nur `ui_users` + `api_keys`). Für Auth-Code."""
    async with _wrap(_local_factory) as s:
        yield s


async def init_db() -> None:
    """Beide Schemata anlegen (idempotent), Alt-appstate migrieren, Admin bootstrappen.

    Reihenfolge kritisch: create_all → **Migration** (importiert Alt-Nutzer/Keys +
    Content) → **erst dann** `ensure_admin_user` (sonst UNIQUE-Konflikt auf die
    migrierte Admin-Email). Der Leser legt die Vault-DB NICHT an (er liest die vom
    Sync gezogene Cache-Kopie)."""
    from db.models import Base, LocalBase  # zirkuläre Imports vermeiden

    log.info("db.init.start", local=settings().credentials_db_url, vault=settings().vault_db_url)
    settings().credentials_db_path.parent.mkdir(parents=True, exist_ok=True)
    settings().vault_db_path.parent.mkdir(parents=True, exist_ok=True)

    async with _local_engine.begin() as conn:
        await conn.run_sync(LocalBase.metadata.create_all)
    if not settings().is_reader:
        async with _vault_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    from db.migrate import run_migration_if_needed
    await asyncio.to_thread(run_migration_if_needed)

    from auth.users import ensure_admin_user
    await ensure_admin_user()

    log.info("db.init.done")


async def dispose() -> None:
    await _local_engine.dispose()
    await _vault_engine.dispose()
