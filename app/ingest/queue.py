"""
In-Process-Ingest-Queue (SQLite `ingest_queue` in appstate.sqlite).

Lokale Variante: **ein Prozess, ein Schreiber** → kein `FOR UPDATE SKIP LOCKED`,
kein separater `worker.py`, kein Redis/Celery. Die Queue ist eine kleine Tabelle
in der lokalen `appstate.sqlite`; ein async-Task im Lifespan (`queue_worker_loop`)
zieht die offenen Rows der Reihe nach. Alle Abfragen sind ORM/SQLite-tauglich
(kein Postgres-SQL: kein `now()`/`FILTER`/`STRING_AGG`).

Pickup: älteste `queued` Row → `running` → `ingest_file` → `done`/`failed`.
Backpressure: Worker schläft 5 s, wenn die Queue leer ist.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update

from db.models import Document, DocumentStatus, IngestQueueEntry
from db.session import get_session
from logger import log

# Wie lange der Worker schläft, wenn die Queue leer ist.
_IDLE_SLEEP_SECONDS = 5


async def enqueue_files(
    job_id: uuid.UUID,
    folder_path: str,
    files: list[tuple[Path, str]],   # (abs_path_on_disk, original_filename)
    tags: list[str],
    uploaded_by: uuid.UUID | None,
) -> int:
    """Legt N Rows in der Queue an. Gibt die Anzahl angelegter Rows zurück.

    `files` kommt als Liste (abs-Pfad, original-Filename) — der Caller hat die
    Dateien schon ins Filesystem geschrieben (Temp- oder Zielort)."""
    if not files:
        return 0
    async with get_session() as s:
        for path, original in files:
            s.add(
                IngestQueueEntry(
                    job_id=job_id,
                    folder_path=folder_path,
                    file_path=str(path),
                    original_filename=original,
                    tags=tags,
                    status="queued",
                    uploaded_by=uploaded_by,
                )
            )
    log.info("ingest.queue.enqueued", job_id=str(job_id), count=len(files))
    return len(files)


async def _pick_one() -> dict | None:
    """Holt die älteste `queued` Row und markiert sie als `running`.

    Single-Writer → kein SKIP LOCKED nötig. Die benötigten Felder werden INNERHALB
    der Session in ein dict kopiert (kein DetachedInstance-Zugriff danach)."""
    async with get_session() as s:
        entry = await s.scalar(
            select(IngestQueueEntry)
            .where(IngestQueueEntry.status == "queued")
            .order_by(IngestQueueEntry.created_at.asc())
            .limit(1)
        )
        if entry is None:
            return None
        entry.status = "running"
        entry.started_at = datetime.now(timezone.utc)
        entry.attempts = (entry.attempts or 0) + 1
        await s.flush()
        return {
            "id": entry.id,
            "file_path": entry.file_path,
            "folder_path": entry.folder_path,
            "original_filename": entry.original_filename,
            "tags": list(entry.tags or []),
            "uploaded_by": entry.uploaded_by,
        }


async def _set_status(entry_id: uuid.UUID, status: str, error_msg: str | None = None) -> None:
    async with get_session() as s:
        await s.execute(
            update(IngestQueueEntry)
            .where(IngestQueueEntry.id == entry_id)
            .values(
                status=status,
                finished_at=datetime.now(timezone.utc),
                error_msg=(error_msg[:2000] if error_msg else None),
            )
        )


async def _process_one(entry: dict) -> None:
    """Verarbeitet eine Row: ruft `ingest_file` auf, markiert done/failed."""
    # Lazy-Import: `ingest.pipeline` zieht die schwere Parsing/Embedding-Last
    # (docling/torch) — nur hier laden, nicht schon beim Import der Queue.
    from ingest.pipeline import ingest_file

    entry_id = entry["id"]
    try:
        doc_id = await ingest_file(
            src_path=Path(entry["file_path"]),
            folder_path=entry["folder_path"],
            tags=list(entry["tags"] or []),
            uploaded_by=entry["uploaded_by"],
            keep_source=False,                # Queue-Files sind Temp-Kopien
            original_filename=entry["original_filename"],
        )
        # `_run_ingest_job` fängt Ingest-Fehler INTERN ab (setzt Document.status=
        # 'failed', re-raised NICHT) → frischen Doc-Status nachladen und die Row
        # ehrlich auf failed setzen, sonst meldet sie fälschlich 'done'.
        doc_status: str | None = None
        doc_err: str | None = None
        if doc_id is not None:
            async with get_session() as s:
                row = (
                    await s.execute(
                        select(Document.status, Document.error_msg).where(
                            Document.id == doc_id
                        )
                    )
                ).first()
                if row:
                    doc_status, doc_err = row[0], row[1]

        if doc_status == DocumentStatus.FAILED.value:
            await _set_status(entry_id, "failed", doc_err or "ingest failed")
            log.warning("ingest.queue.doc_failed", entry_id=str(entry_id),
                        doc_id=str(doc_id), error=doc_err)
        else:
            await _set_status(entry_id, "done")
            log.info("ingest.queue.done", entry_id=str(entry_id))
    except Exception as e:
        log.exception("ingest.queue.failed", entry_id=str(entry_id), error=str(e))
        await _set_status(entry_id, "failed", str(e))
    finally:
        # Staging-Datei IMMER aufräumen (Erfolg, Dedup-Orphan UND Hard-Failure).
        # Nach erfolgreichem Move ist der Pfad weg → No-op (idempotent).
        Path(entry["file_path"]).unlink(missing_ok=True)


async def queue_worker_loop(stop_event: asyncio.Event) -> None:
    """Endlos-Schleife. Vom Lifespan als Task gestartet, via `stop_event.set()`
    beim Shutdown sauber beendet."""
    log.info("ingest.queue.worker_started")
    while not stop_event.is_set():
        try:
            entry = await _pick_one()
        except Exception as e:
            log.warning("ingest.queue.pick_failed", error=str(e))
            entry = None

        if entry is None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        await _process_one(entry)

    log.info("ingest.queue.worker_stopped")


# ---------------------------------------------------------------------------
# Aggregierter Job-Status (für GET /api/ingest/jobs/{job_id})
# ---------------------------------------------------------------------------
async def get_job_status(job_id: uuid.UUID) -> dict | None:
    """Aggregiert über alle Rows gleicher `job_id` (Python-seitig, SQLite-tauglich)."""
    async with get_session() as s:
        entries = (
            await s.execute(
                select(IngestQueueEntry).where(IngestQueueEntry.job_id == job_id)
            )
        ).scalars().all()

    if not entries:
        return None

    total = len(entries)
    done = sum(1 for e in entries if e.status == "done")
    failed = sum(1 for e in entries if e.status == "failed")
    pending = sum(1 for e in entries if e.status in ("queued", "running"))
    errors = " | ".join(e.error_msg for e in entries if e.error_msg)
    finished = [e.finished_at for e in entries if e.finished_at]

    if pending > 0:
        status = "running"
    elif failed > 0 and done > 0:
        status = "partial"
    elif failed > 0:
        status = "failed"
    else:
        status = "done"

    return {
        "job_id": job_id,
        "status": status,
        "folder_path": min((e.folder_path for e in entries), default="/") or "/",
        "total": total,
        "processed": done,
        "failed": failed,
        "error_msg": (errors[:500] if errors else None),
        "created_at": min(e.created_at for e in entries),
        "finished_at": (max(finished) if finished else None),
    }
