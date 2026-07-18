"""
Maintenance-Engine: orchestriert alle Wartungs-Läufe.

Einstiegspunkt: `run_maintenance()`.
Nachtlauf: wird von main.py täglich um 03:00 UTC getriggert.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from logger import log
from maintenance.duplicate_detection import detect_duplicates
from maintenance.tag_consolidation import consolidate_tags


async def run_maintenance() -> dict:
    """Führt alle Wartungs-Checks aus. Gibt Zusammenfassung zurück."""
    t0 = datetime.now(timezone.utc)
    log.info("maintenance.run.start")

    tag_merges = len(await consolidate_tags())
    dup_suggestions = await detect_duplicates()

    # Wissensgraph (Track D) neu bauen + analysieren. Fehler hier dürfen die
    # übrige Wartung nicht kippen — aber laut/geloggt, nicht verschluckt.
    graph_summary: dict | None = None
    try:
        from graph.refresh import refresh_graph
        graph_summary = await refresh_graph()
    except Exception as e:
        log.exception("maintenance.graph_refresh.failed", error=str(e))

    # Ordner-Reorg-Vorschläge (Track F / M4) aus den frischen D-Communities.
    # NACH dem Graph-Refresh (braucht community_id). Nur Vorschläge (pending) —
    # bewegt nichts; das Verschieben ist admin-bestätigt. Fehler laut/geloggt.
    reorg_summary: dict | None = None
    try:
        from graph.reorg import build_folder_suggestions
        reorg_summary = (await build_folder_suggestions())._asdict()
    except Exception as e:
        log.exception("maintenance.reorg.failed", error=str(e))

    summary = {
        "started_at": t0.isoformat(),
        "tag_merges": tag_merges,
        "new_duplicate_suggestions": dup_suggestions,
        "graph": graph_summary,
        "reorg": reorg_summary,
    }
    log.info("maintenance.run.done", **summary)
    return summary


async def nightly_maintenance_loop(stop_event: asyncio.Event) -> None:
    """
    Asyncio-Task: läuft täglich um 03:00 UTC.
    Wird vom Lifespan in main.py gestartet.
    """
    log.info("maintenance.nightly.started")
    while not stop_event.is_set():
        now = datetime.now(timezone.utc)
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)

        sleep_s = (next_run - now).total_seconds()
        log.info("maintenance.nightly.waiting", next_run=next_run.isoformat())
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_s)
            break  # stop_event gesetzt → sauber beenden
        except asyncio.TimeoutError:
            pass

        try:
            await run_maintenance()
        except Exception as e:
            log.exception("maintenance.nightly.failed", error=str(e))

    log.info("maintenance.nightly.stopped")
