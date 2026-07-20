"""
Publish/Versionierung über LanceDBs native Versionierung + Tags (M7).

Der Schreiber baut neue Versionen direkt ins Vault-Dataset (append/merge → MVCC
erzeugt automatisch eine neue Version; laufende Leser sehen die alte unbeeinflusst).
`publish()` ist der **atomare Veröffentlichungs-Schritt**: die neueste Version wird
mit dem Tag `current` markiert, die vorige nach `prev` gerollt.

Retention K=2: `current` + `prev` sind getaggt und damit HART vor Cleanup geschützt
(im Python-Binding gibt es KEIN `error_if_tagged_old_versions` → Cleanup wirft, wenn
eine getaggte Version im Fenster liegt; wir fangen das ab). Ungetaggte Alt-Versionen
räumt `optimize(cleanup_older_than=grace)` best-effort.

Leser (andere Rechner): `sync_reader_cache()` kopiert das getaggte Dataset in den
lokalen Cache (SMB nur Transport); `checkout_current()` pinnt die `current`-Version
für die Query-Dauer. Ein Publish während einer laufenden Leser-Query stört nicht
(MVCC — der Leser bleibt auf seiner gepinnten Version, bis er neu eincheckt).
"""
from __future__ import annotations

import os
import shutil
from datetime import timedelta
from pathlib import Path

from config import settings
from logger import log
from pipelines import store

CURRENT = "current"
PREV = "prev"


def _set_tag(tbl, name: str, version: int) -> None:
    """Tag anlegen oder verschieben (idempotent)."""
    if name in tbl.tags.list():
        tbl.tags.update(name, version)
    else:
        tbl.tags.create(name, version)


def publish() -> dict:
    """Veröffentlicht die neueste Dataset-Version: `current`←latest, `prev`←alt-current.

    NUR Tag-Rolling (exakt, schnell) — KEINE Kompaktierung, weil `optimize` selbst
    neue Versionen erzeugt und `current` sonst hinterherhinkt. Retention/Kompaktierung
    macht `prune_versions()` (Nachtlauf). No-op ohne Tabelle."""
    tbl = store._open()
    if tbl is None:
        log.info("publish.no_table")
        return {"published": None, "tags": {}}
    latest = tbl.version
    tags = dict(tbl.tags.list())
    old_current = (tags.get(CURRENT) or {}).get("version")
    if old_current is not None and old_current != latest:
        _set_tag(tbl, PREV, old_current)
    _set_tag(tbl, CURRENT, latest)
    result = {"published": latest, "tags": {k: v["version"] for k, v in tbl.tags.list().items()}}
    log.info("publish.done", **result)
    return result


def prune_versions() -> dict:
    """Kompaktiert + räumt best-effort ungetaggte Alt-Versionen; getaggte (`current`/
    `prev`) sind HART geschützt (Cleanup wirft sonst → abfangen). `optimize` kann eine
    neue, kompaktierte Version erzeugen → `current` darauf nachziehen."""
    tbl = store._open()
    if tbl is None:
        return {"pruned": False, "reason": "no_table"}
    grace = timedelta(days=settings().publish_cleanup_grace_days)
    try:
        tbl.optimize(cleanup_older_than=grace)
    except Exception as e:  # noqa: BLE001 — Retention ist best-effort, nie fatal
        log.info("publish.prune_skipped", reason=str(e)[:160])
        return {"pruned": False, "reason": str(e)[:160]}
    _set_tag(tbl, CURRENT, tbl.version)   # current auf die kompaktierte Version ziehen
    log.info("publish.prune_done", version=tbl.version)
    return {"pruned": True, "version": tbl.version}


def checkout_current(tbl):
    """Pinnt die `current`-Version (read-only) für die Query-Dauer. Ohne Tag: latest."""
    if CURRENT in tbl.tags.list():
        tbl.checkout(CURRENT)
    else:
        tbl.checkout_latest()
    return tbl


def refresh_reader_cache() -> str:
    """Reader (M8e): zieht das veröffentlichte Vault-Dataset frisch in den lokalen
    Cache und verwirft das Store-Handle, damit die nächste Query die neue Version
    sieht. Blockierend → vom Aufrufer in `asyncio.to_thread`. Gibt den Cache-Pfad."""
    path = sync_reader_cache()
    store.invalidate()
    return path


def sync_reader_cache() -> str:
    """Leser: kopiert das (getaggte) Vault-Dataset in den lokalen Cache und gibt den
    Cache-Pfad zurück. SMB nur Transport; Live-Query läuft NIE über SMB.

    Kopie in ein `.tmp`, dann rename-Swap (kurzes Fenster ohne Ziel — der Leser
    retryt). Kein Rebuild der Indizes nötig (M0-Spike belegt: Kopie → read-only
    öffnen → FTS+norm_id ohne Rebuild)."""
    src = Path(settings().lancedb_uri)
    dst = Path(settings().reader_cache_uri)
    if not src.exists():
        raise FileNotFoundError(f"Vault-Dataset fehlt: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    old = dst.with_name(dst.name + ".old")
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(src, tmp)
    if dst.exists():
        os.replace(dst, old)
    os.replace(tmp, dst)
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    log.info("publish.reader_cache_synced", cache=str(dst))
    return str(dst)
