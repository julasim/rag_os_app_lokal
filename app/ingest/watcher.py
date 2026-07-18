"""
Folder-Watcher — überwacht optional einen Ordner im Dateisystem und
indexiert automatisch neue Dateien.

Die Zuordnung zu Ordnerpfad wird aus dem Dateipfad abgeleitet:

    /data/uploads/<folder_path>/<file_name>

So kann man per rclone ein OneDrive-Verzeichnis nach /data/uploads/...
syncen und es wird automatisch indexiert.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from config import settings
from ingest.pipeline import ingest_file
from logger import log

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
    """Startet einen Hintergrund-Observer, der settings.upload_dir beobachtet."""

    def __init__(self) -> None:
        self._observer: Observer | None = None

    def start(self) -> None:
        # get_running_loop() statt get_event_loop() — vermeidet DeprecationWarning
        loop = asyncio.get_running_loop()
        observer = Observer()
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
