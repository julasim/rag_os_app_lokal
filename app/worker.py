"""
Standalone-Entrypoint für den rag-ingest-Container (Track C3b — Worker-Split).

Führt NUR den Queue-Worker + Folder-Watcher aus — keine HTTP-API. Damit kann
die schwere Ingest-Last (Docling/torch) in einen eigenen Container wandern und
das Serving-Image (rag-api) schlank bleiben.

Start (im Container):  python worker.py   (SERVICE_ROLE=ingest)

Bewusst NICHT hier: init_db()/Migration + Qdrant-Collection-Anlage — das macht
der rag-api-Container beim Boot (Single-Writer fürs Schema, keine Migrations-Race).
Der Worker konsumiert nur die bestehende `ingest_queue` (FOR UPDATE SKIP LOCKED →
race-frei ggü. weiteren Workern) und den Upload-Ordner.
"""
from __future__ import annotations

import asyncio
import signal

from config import settings
from db.session import dispose
from ingest.queue import queue_worker_loop
from ingest.watcher import FolderWatcher
from logger import log, setup_logging


async def _run() -> None:
    setup_logging()
    settings().upload_dir.mkdir(parents=True, exist_ok=True)
    log.info("ingest.worker.boot", service_role=settings().service_role,
             backend=settings().ingest_backend)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # z.B. Windows
            pass

    # Folder-Watcher (auto-ingest von /data/uploads/<folder>/<file>)
    watcher = FolderWatcher()
    try:
        watcher.start()
    except Exception as e:
        log.warning("watcher.start_failed", error=str(e))

    worker_task = asyncio.create_task(queue_worker_loop(stop), name="ingest-queue-worker")

    await stop.wait()

    # Sauberer Shutdown
    stop.set()
    try:
        await asyncio.wait_for(worker_task, timeout=15)
    except asyncio.TimeoutError:
        worker_task.cancel()
        log.warning("ingest.worker.force_stopped")
    watcher.stop()
    await dispose()
    log.info("ingest.worker.shutdown")


if __name__ == "__main__":
    asyncio.run(_run())
