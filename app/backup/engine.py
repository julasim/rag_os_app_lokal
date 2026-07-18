"""
Backup-Engine: Postgres-Dump + Qdrant-Snapshot + Cleanup.

Postgres: pg_dump (custom format, komprimiert) → /data/backups/postgres_<ts>.dump
Qdrant:   Collection-Snapshot von `rag_documents`, heruntergeladen nach
          /data/backups/<name>.snapshot (per Upload-API wiederherstellbar).

Cleanup: Dateien älter als BACKUP_KEEP_DAYS werden gelöscht
         (Postgres-Dumps UND Qdrant-Snapshots).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from config import settings
from logger import log
from pipelines.factory import COLLECTION_NAME


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# Postgres-Dump
# ---------------------------------------------------------------------------
async def backup_postgres() -> Path:
    s = settings()
    s.backup_dir.mkdir(parents=True, exist_ok=True)
    out = s.backup_dir / f"postgres_{_ts()}.dump"

    cmd = [
        "pg_dump",
        "-h", s.postgres_host,
        "-p", str(s.postgres_port),
        "-U", s.postgres_user,
        "-d", s.postgres_db,
        "-F", "c",            # custom format (komprimiert, wiederherstellbar)
        "-f", str(out),
    ]
    env = {**os.environ, "PGPASSWORD": s.postgres_password}

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            env=env,
            capture_output=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode()[:500])
        log.info("backup.postgres.done", path=str(out), size_mb=round(out.stat().st_size / 1024 / 1024, 1))
        return out
    except Exception as e:
        out.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump failed: {e}") from e


# ---------------------------------------------------------------------------
# Qdrant-Snapshot (alle Collections)
# ---------------------------------------------------------------------------
async def backup_qdrant() -> str:
    """
    Triggert einen Full-Snapshot in Qdrant UND lädt ihn ins Bind-Mount
    `/data/backups` herunter.

    Wichtig: Der Snapshot liegt zunächst nur im Qdrant-internen Storage
    (qdrant-data Volume). Würde man ihn dort lassen, wäre er bei
    `docker compose down -v` mitsamt den Daten weg — das "Backup" böte dann
    NULL Disaster-Recovery. Darum laden wir die Snapshot-Datei sofort in das
    von außen sicherbare Bind-Mount-Verzeichnis herunter.

    Gibt den Snapshot-Namen zurück.
    """
    s = settings()
    s.backup_dir.mkdir(parents=True, exist_ok=True)
    headers = {"api-key": s.qdrant_api_key} if s.qdrant_api_key else {}
    base = f"{s.qdrant_url}/collections/{COLLECTION_NAME}/snapshots"
    async with httpx.AsyncClient(timeout=600) as c:
        r = await c.post(base, headers=headers)
        r.raise_for_status()
        name = r.json().get("result", {}).get("name", "unknown")

        # Snapshot-Datei ins Bind-Mount herunterladen (offsite-sicherbar,
        # per Upload-API wiederherstellbar — siehe scripts/restore.sh).
        out = s.backup_dir / name
        async with c.stream("GET", f"{base}/{name}", headers=headers) as resp:
            resp.raise_for_status()
            with open(out, "wb") as fh:
                async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                    fh.write(chunk)

    log.info(
        "backup.qdrant.done",
        snapshot=name,
        downloaded_to=str(out),
        size_mb=round(out.stat().st_size / 1024 / 1024, 1),
    )
    return name


# ---------------------------------------------------------------------------
# Cleanup alter Backups (Postgres-Dumps + Qdrant-Snapshots)
# ---------------------------------------------------------------------------
def cleanup_old_backups() -> int:
    s = settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=s.backup_keep_days)
    removed = 0
    for pattern in ("postgres_*.dump", "*.snapshot"):
        for f in s.backup_dir.glob(pattern):
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                f.unlink()
                removed += 1
                log.info("backup.cleanup.removed", file=f.name)
    return removed


# ---------------------------------------------------------------------------
# Query-Log-Retention (DSGVO Speicherbegrenzung)
# ---------------------------------------------------------------------------
async def cleanup_old_query_logs() -> int:
    """
    Löscht query_log-Einträge älter als QUERY_LOG_KEEP_DAYS.

    query_log speichert query_text (potenziell personenbezogene Suchbegriffe)
    und retrieved_doc_ids dauerhaft. Ohne Retention wächst das unbegrenzt —
    DSGVO Art. 5 (Speicherbegrenzung). 0 = nie löschen (dann bewusst so setzen).
    """
    s = settings()
    if s.query_log_keep_days <= 0:
        return 0
    # Lokale Imports: vermeidet Circular-Import beim Modul-Load (engine wird
    # im main-Lifespan importiert, bevor die DB-Schicht bereit ist).
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
    result: dict = {"started_at": t0.isoformat(), "postgres": None, "qdrant": None, "cleaned": 0, "error": None}

    try:
        pg_path = await backup_postgres()
        result["postgres"] = str(pg_path.name)
    except Exception as e:
        log.warning("backup.postgres.failed", error=str(e))
        result["error"] = str(e)

    try:
        snap = await backup_qdrant()
        result["qdrant"] = snap
    except Exception as e:
        log.warning("backup.qdrant.failed", error=str(e))
        if not result["error"]:
            result["error"] = str(e)

    try:
        result["cleaned"] = await asyncio.to_thread(cleanup_old_backups)
    except Exception as e:
        log.warning("backup.cleanup.failed", error=str(e))

    try:
        result["query_logs_pruned"] = await cleanup_old_query_logs()
    except Exception as e:
        log.warning("query_log.cleanup.failed", error=str(e))

    try:
        from mcp_server.oauth import cleanup_expired as oauth_cleanup
        result["oauth_tokens_pruned"] = await oauth_cleanup()
    except Exception as e:
        log.warning("oauth.cleanup.failed", error=str(e))

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
    for f in sorted(s.backup_dir.glob("postgres_*.dump"), reverse=True):
        stat = f.stat()
        files.append({
            "name": f.name,
            "size_mb": round(stat.st_size / 1024 / 1024, 2),
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
