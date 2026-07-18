"""
Postgres-basierte Ingest-Queue + Worker-Loop.

Bewusst KEIN Redis / Celery / arq: ein zusätzlicher Service-Container kostet
mehr Komplexität als wir bei der erwarteten Größenordnung (10k Docs) brauchen.
Die Queue lebt einfach in der `ingest_queue`-Tabelle in Postgres, und ein
async-Task im API-Lifespan zieht offene Rows.

Pickup-Strategie:
  - `FOR UPDATE SKIP LOCKED` auf die älteste `queued` Row, damit mehrere
    Worker-Instanzen sich nicht in die Quere kommen (auch wenn wir aktuell
    nur einen Worker im Container haben).
  - Bei Erfolg: status='done'.
  - Bei Fehler: status='failed' + error_msg. Kein Retry — das wäre Welle 8.

Backpressure:
  - Worker schläft 5 s wenn die Queue leer ist (kein Tight-Loop gegen DB).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text

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
    """
    Legt N Rows in der Queue an. Gibt die Anzahl angelegter Rows zurück.

    `files` kommt als Liste (abs-Pfad, original-Filename) — der Caller hat
    die Dateien schon ins Filesystem geschrieben (Temp- oder Zielort).
    """
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
    log.info(
        "ingest.queue.enqueued", job_id=str(job_id), count=len(files)
    )
    return len(files)


async def _pick_one() -> IngestQueueEntry | None:
    """
    Holt die älteste `queued` Row und markiert sie atomar als `running`.
    Nutzt Postgres' `FOR UPDATE SKIP LOCKED`.
    """
    async with get_session() as s:
        result = await s.execute(
            text(
                """
                SELECT id FROM ingest_queue
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """
            )
        )
        row = result.first()
        if not row:
            return None
        entry_id = row[0]

        await s.execute(
            text(
                """
                UPDATE ingest_queue
                SET status = 'running',
                    started_at = now(),
                    attempts = attempts + 1
                WHERE id = :id
                """
            ),
            {"id": entry_id},
        )

        fresh = await s.execute(
            select(IngestQueueEntry).where(IngestQueueEntry.id == entry_id)
        )
        return fresh.scalar_one()


async def _process_one(entry: IngestQueueEntry) -> None:
    """Verarbeitet eine Row: ruft `ingest_file` auf, markiert done/failed."""
    # Lazy-Import (Dep-Severance C3b): `ingest.pipeline` zieht die schwere
    # Parsing/Embedding-Last (torch/docling). Sie soll NUR im rag-ingest-Worker
    # geladen werden, nicht schon beim Import der Queue im rag-api-Serving-Prozess
    # (der `enqueue_files`/`get_job_status` importiert, aber nie ingestet).
    from ingest.pipeline import ingest_file

    try:
        doc_id = await ingest_file(
            src_path=Path(entry.file_path),
            folder_path=entry.folder_path,
            tags=list(entry.tags or []),
            uploaded_by=entry.uploaded_by,
            keep_source=False,                # Queue-Files sind Temp-Kopien
            original_filename=entry.original_filename,
        )
        # M-1: `_run_ingest_job` fängt Ingest-Fehler INTERN ab, setzt
        # Document.status='failed' und re-raised NICHT → ohne diese Prüfung würde
        # die Queue-Row fälschlich 'done' melden (verschluckter Fehler). Deshalb
        # den frischen Doc-Status nachladen und die Row ehrlich auf failed setzen.
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

        async with get_session() as s:
            if doc_status == DocumentStatus.FAILED.value:
                await s.execute(
                    text(
                        """
                        UPDATE ingest_queue
                        SET status = 'failed', finished_at = now(), error_msg = :err
                        WHERE id = :id
                        """
                    ),
                    {"id": entry.id, "err": (doc_err or "ingest failed")[:2000]},
                )
                log.warning(
                    "ingest.queue.doc_failed",
                    entry_id=str(entry.id),
                    doc_id=str(doc_id),
                    error=doc_err,
                )
            else:
                await s.execute(
                    text(
                        """
                        UPDATE ingest_queue
                        SET status = 'done', finished_at = now(), error_msg = NULL
                        WHERE id = :id
                        """
                    ),
                    {"id": entry.id},
                )
                log.info("ingest.queue.done", entry_id=str(entry.id))
    except Exception as e:
        log.exception("ingest.queue.failed", entry_id=str(entry.id), error=str(e))
        async with get_session() as s:
            await s.execute(
                text(
                    """
                    UPDATE ingest_queue
                    SET status = 'failed', finished_at = now(), error_msg = :err
                    WHERE id = :id
                    """
                ),
                {"id": entry.id, "err": str(e)[:2000]},
            )
    finally:
        # N-1: Staging-Datei IMMER aufräumen (Erfolg, Dedup-Orphan UND Hard-Failure,
        # z.B. „File too large" VOR dem Move). Nach erfolgreichem Move ist der Pfad
        # weg → No-op (idempotent). Sonst wächst das geteilte /data/staging.
        Path(entry.file_path).unlink(missing_ok=True)


async def queue_worker_loop(stop_event: asyncio.Event) -> None:
    """
    Endlos-Schleife. Wird vom Lifespan als Task gestartet und beim Shutdown
    via `stop_event.set()` sauber beendet.
    """
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
    """
    Aggregiert über alle Rows mit gleicher `job_id` und gibt einen
    Bulk-Job-Statusbericht zurück (oder None wenn job_id unbekannt).
    """
    async with get_session() as s:
        result = await s.execute(
            text(
                """
                SELECT
                    COUNT(*)                                         AS total,
                    COUNT(*) FILTER (WHERE status='done')            AS done,
                    COUNT(*) FILTER (WHERE status='failed')          AS failed,
                    COUNT(*) FILTER (WHERE status IN ('queued','running')) AS pending,
                    MIN(folder_path)                                 AS folder_path,
                    MIN(created_at)                                  AS created_at,
                    MAX(finished_at)                                 AS finished_at,
                    STRING_AGG(NULLIF(error_msg, ''), ' | ')        AS errors
                FROM ingest_queue
                WHERE job_id = :job_id
                """
            ),
            {"job_id": str(job_id)},
        )
        row = result.first()

    if not row or (row[0] or 0) == 0:
        return None

    total, done, failed, pending, folder_path, created_at, finished_at, errors = row

    if pending and pending > 0:
        status = "running"
    elif failed and failed > 0 and (done or 0) > 0:
        status = "partial"
    elif failed and failed > 0:
        status = "failed"
    else:
        status = "done"

    return {
        "job_id": job_id,
        "status": status,
        "folder_path": folder_path or "/",
        "total": int(total or 0),
        "processed": int(done or 0),
        "failed": int(failed or 0),
        "error_msg": (errors[:500] if errors else None),
        "created_at": created_at or datetime.now(timezone.utc),
        "finished_at": finished_at,
    }
