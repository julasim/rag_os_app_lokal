"""
Backup-Engine (lokale Variante, M7): Vault-Index-Snapshot + appstate-Kopie + Publish.

Kein pg_dump / Qdrant-Snapshot mehr (beides entfällt mit SQLite+LanceDB). Der
Nachtlauf:
  1. `publish()` — die neueste Dataset-Version als `current` taggen (atomar).
  2. **Vault-Index-Snapshot** — Kopie von `.ragos/index.lance` → backup_dir/
     `index_<ts>.lance` (immutable Versionen inkl. Tags reisen mit).
  3. **appstate-Kopie** — `appstate.sqlite` → backup_dir/`appstate_<ts>.sqlite`
     (Keys/Users/Query-Log; lokal, nicht im Vault).
  4. Cleanup alter Snapshots (`backup_keep_days`) + Query-Log-Retention (DSGVO).

Tiefste Wiederherstellung ist ohnehin **Rebuild-aus-Dokumenten** (`reindex_all`):
die Roh-Dateien im Vault sind die Quelle, der Index ist abgeleitet.
"""
from __future__ import annotations

import asyncio
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import settings
from logger import log


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# Vault-Index-Snapshot (LanceDB-Dataset-Verzeichnis)
# ---------------------------------------------------------------------------
def backup_vault_index() -> Path | None:
    """Kopiert das LanceDB-Dataset in den backup_dir. None, wenn (noch) keins existiert."""
    s = settings()
    src = Path(s.lancedb_uri)
    if not src.exists():
        log.info("backup.vault.skip_no_dataset", path=str(src))
        return None
    s.backup_dir.mkdir(parents=True, exist_ok=True)
    out = s.backup_dir / f"index_{_ts()}.lance"
    shutil.copytree(src, out)
    size_mb = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1024 / 1024
    log.info("backup.vault.done", path=str(out), size_mb=round(size_mb, 1))
    return out


# ---------------------------------------------------------------------------
# appstate.sqlite-Snapshot (konsistent via SQLite-Backup-API, WAL-sicher)
# ---------------------------------------------------------------------------
def _snapshot_sqlite(src: Path, out: Path) -> Path | None:
    """Konsistenter SQLite-Snapshot via Backup-API (WAL-/laufende-Writes-sicher)."""
    if not src.exists():
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(out))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()
    return out


def backup_databases() -> list[Path]:
    """Snapshot BEIDER DBs (Multi-Vault-Split): credentials.sqlite (lokal) + der
    Vault-`state.sqlite` (Content der aktiven Firma)."""
    s = settings()
    ts = _ts()
    outs: list[Path] = []
    for name, src in (("credentials", s.credentials_db_path), ("state", s.vault_db_path)):
        out = _snapshot_sqlite(src, s.backup_dir / f"{name}_{ts}.sqlite")
        if out:
            outs.append(out)
            log.info("backup.db.done", which=name, path=str(out),
                     size_mb=round(out.stat().st_size / 1024 / 1024, 2))
        else:
            log.info("backup.db.skip_no_db", which=name, path=str(src))
    return outs


# ---------------------------------------------------------------------------
# Cleanup alter Backups
# ---------------------------------------------------------------------------
def cleanup_old_backups() -> int:
    s = settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=s.backup_keep_days)
    removed = 0
    for pattern in ("index_*.lance", "appstate_*.sqlite", "credentials_*.sqlite", "state_*.sqlite"):
        for f in s.backup_dir.glob(pattern):
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                if f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
                else:
                    f.unlink(missing_ok=True)
                removed += 1
                log.info("backup.cleanup.removed", file=f.name)
    return removed


# ---------------------------------------------------------------------------
# Query-Log-Retention (DSGVO Speicherbegrenzung)
# ---------------------------------------------------------------------------
async def cleanup_old_query_logs() -> int:
    """Löscht query_log-Einträge älter als QUERY_LOG_KEEP_DAYS. 0 = nie löschen."""
    s = settings()
    if s.query_log_keep_days <= 0:
        return 0
    from sqlalchemy import delete

    from db.models import QueryLog
    from db.session import get_session

    cutoff = datetime.now(timezone.utc) - timedelta(days=s.query_log_keep_days)
    async with get_session() as sess:
        res = await sess.execute(delete(QueryLog).where(QueryLog.created_at < cutoff))
    deleted = res.rowcount or 0
    log.info("query_log.cleanup", deleted=deleted, keep_days=s.query_log_keep_days)
    return deleted


# ---------------------------------------------------------------------------
# Kombinierter Lauf
# ---------------------------------------------------------------------------
async def run_backup() -> dict:
    t0 = datetime.now(timezone.utc)
    log.info("backup.run.start")
    result: dict = {"started_at": t0.isoformat(), "published": None,
                    "index": None, "appstate": None, "cleaned": 0, "error": None}

    try:
        from pipelines.publish import prune_versions, publish
        result["published"] = (await asyncio.to_thread(publish)).get("published")
        await asyncio.to_thread(prune_versions)   # Kompaktierung + Retention (best-effort)
    except Exception as e:
        log.warning("backup.publish.failed", error=str(e))
        result["error"] = str(e)

    try:
        idx = await asyncio.to_thread(backup_vault_index)
        result["index"] = idx.name if idx else None
    except Exception as e:
        log.warning("backup.vault.failed", error=str(e))
        result["error"] = result["error"] or str(e)

    try:
        dbs = await asyncio.to_thread(backup_databases)
        result["appstate"] = [p.name for p in dbs]
    except Exception as e:
        log.warning("backup.databases.failed", error=str(e))
        result["error"] = result["error"] or str(e)

    try:
        result["cleaned"] = await asyncio.to_thread(cleanup_old_backups)
    except Exception as e:
        log.warning("backup.cleanup.failed", error=str(e))

    try:
        result["query_logs_pruned"] = await cleanup_old_query_logs()
    except Exception as e:
        log.warning("query_log.cleanup.failed", error=str(e))

    log.info("backup.run.done", **result)
    return result


# ---------------------------------------------------------------------------
# Datei-Liste (für API)
# ---------------------------------------------------------------------------
def list_backup_files() -> list[dict]:
    s = settings()
    if not s.backup_dir.exists():
        return []
    files = []
    for pattern in ("index_*.lance", "appstate_*.sqlite"):
        for f in sorted(s.backup_dir.glob(pattern), reverse=True):
            stat = f.stat()
            size = (sum(x.stat().st_size for x in f.rglob("*") if x.is_file())
                    if f.is_dir() else stat.st_size)
            files.append({
                "name": f.name,
                "size_mb": round(size / 1024 / 1024, 2),
                "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
    return files


# ---------------------------------------------------------------------------
# Nacht-Scheduler (02:00 UTC)
# ---------------------------------------------------------------------------
async def nightly_backup_loop(stop_event: asyncio.Event) -> None:
    log.info("backup.nightly.started")
    while not stop_event.is_set():
        now = datetime.now(timezone.utc)
        next_run = now.replace(hour=2, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)

        sleep_s = (next_run - now).total_seconds()
        log.info("backup.nightly.waiting", next_run=next_run.isoformat())
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_s)
            break
        except asyncio.TimeoutError:
            pass

        try:
            await run_backup()
        except Exception as e:
            log.exception("backup.nightly.failed", error=str(e))

    log.info("backup.nightly.stopped")
