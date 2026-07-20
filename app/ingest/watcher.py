"""
Folder-Watcher — überwacht den lokalen Überwachungsordner (`settings().upload_dir`)
und indexiert neue Dateien automatisch.

Ordnerpfad wird aus dem Dateipfad abgeleitet:

    <upload_dir>/<folder_path>/<file_name>

**PollingObserver** (nicht der native Observer): watchdog-Events sind über SMB/
Netzlaufwerke unzuverlässig, und der Vault kann auf der NAS liegen. Polling ist
etwas träger, aber robust — lokal wie über SMB. Der LanceDB-Store serialisiert
gleichzeitige Writes (Watcher + Queue-Worker) über seinen Lock → Single-Writer bleibt.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from config import settings
from logger import log

# `ingest_file` wird bewusst LAZY in `_process` importiert (nicht auf Modulebene):
# `ingest.pipeline` zieht die schwere Writer-Last (docling/torch/Legacy-Parser).
# Der Watcher läuft nur im Writer, aber so bleibt allein der Import des Moduls
# (z.B. beim Import-Graph-Test des schlanken Lesers) frei von diesen Deps.

# Starke Referenzen auf laufende _process-Tasks halten — sonst kann der GC einen
# per create_task gestarteten Task vor Abschluss einsammeln (die 2 s-„Ruhe"-Pause
# öffnet ein reales Fenster) → auto-ingestete Datei würde still gedroppt.
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """Läuft im Loop-Thread (via call_soon_threadsafe) → hier ist create_task sicher."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


class _Handler(FileSystemEventHandler):
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self.loop.call_soon_threadsafe(_spawn, _process(Path(event.src_path)))

    def on_modified(self, event: FileSystemEvent) -> None:
        # Ignorieren — sonst re-indexiert jede Speicherung
        pass


async def _process(path: Path) -> None:
    try:
        # Warten, bis Datei "ruhig" liegt (rclone/Upload könnte noch schreiben)
        await asyncio.sleep(2.0)
        if not path.exists():
            return

        rel = path.relative_to(settings().upload_dir)
        parts = rel.parts
        if len(parts) < 1:
            log.warning("watcher.skip_no_path", path=str(path))
            return

        # Ordnerpfad aus dem relativen Pfad ableiten (ohne Dateiname)
        folder = "/" + "/".join(parts[:-1]) + "/" if len(parts) > 1 else "/"

        from ingest.pipeline import ingest_file   # lazy: schwere Writer-Last

        log.info("watcher.ingest", folder=folder, file=path.name)
        await ingest_file(
            src_path=path,
            folder_path=folder,
            tags=[],
            keep_source=True,
        )
    except Exception as e:
        log.exception("watcher.error", path=str(path), error=str(e))


class FolderWatcher:
    """Startet einen Hintergrund-PollingObserver, der settings.upload_dir beobachtet."""

    def __init__(self) -> None:
        self._observer: PollingObserver | None = None

    def start(self) -> None:
        # get_running_loop() statt get_event_loop() — vermeidet DeprecationWarning
        loop = asyncio.get_running_loop()
        observer = PollingObserver()
        observer.schedule(_Handler(loop), str(settings().upload_dir), recursive=True)
        observer.start()
        self._observer = observer
        log.info("watcher.started", path=str(settings().upload_dir))

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
            log.info("watcher.stopped")
