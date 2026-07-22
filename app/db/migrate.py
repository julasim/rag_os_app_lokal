"""Einmal-Migration (Multi-Vault-Split, K5).

Alte Einzel-`appstate.sqlite` (alle Tabellen in einer DB) → aufgeteilt auf:
  * `credentials.sqlite` (lokal) — `ui_users` + `api_keys`
  * `<aktueller vault>/.ragos/state.sqlite` — Content (Dokumente/Chunks/Graph/Logs/Jobs)

Eigenschaften:
  * **Idempotent** — importiert nur, wenn die Zieltabelle leer ist.
  * **Korrekt bei mehreren Vaults** — nach Erfolg wird die Alt-DB in `.migrated`
    umbenannt, damit ein späterer, NEUER (leerer) Firmen-Vault NICHT den Content der
    ersten Firma reimportiert.
  * **Reversibel** — die Alt-DB wird nur umbenannt, nie gelöscht.
  * **Robust gegen Schema-Drift** — kopiert spaltengenau (nur gemeinsame Spalten).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from config import settings
from db.models import Base, LocalBase
from logger import log


def _count(db_path: Path, table: str) -> int:
    if not db_path.exists():
        return 0
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        con.close()


def _copy_tables(dst_path: Path, old_path: Path, tables: list[str]) -> int:
    """Kopiert `tables` (Namen in FK-Reihenfolge) aus `old_path` nach `dst_path` via
    ATTACH — spaltengenau (Schnittmenge der Spalten). Gibt die Gesamtzeilenzahl."""
    con = sqlite3.connect(str(dst_path))
    try:
        cur = con.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.execute("ATTACH DATABASE ? AS old", (str(old_path),))
        total = 0
        for t in tables:
            new_cols = [r[1] for r in cur.execute(f'PRAGMA main.table_info("{t}")').fetchall()]
            old_cols = [r[1] for r in cur.execute(f'PRAGMA old.table_info("{t}")').fetchall()]
            common = [c for c in new_cols if c in old_cols]
            if not common:
                continue  # Tabelle fehlt in der Alt-DB oder keine gemeinsame Spalte
            cols = ",".join(f'"{c}"' for c in common)
            cur.execute(f'INSERT INTO main."{t}" ({cols}) SELECT {cols} FROM old."{t}"')
            total += max(cur.rowcount, 0)
        con.commit()
        cur.execute("DETACH DATABASE old")
        return total
    finally:
        con.close()


def run_migration_if_needed() -> None:
    """Läuft in `init_db()` NACH `create_all` (Zieltabellen existieren) und VOR
    `ensure_admin_user` (sonst UNIQUE-Konflikt auf die migrierte Admin-Email)."""
    old = settings().appstate_db_path
    if not old.exists():
        return  # Frische Installation ODER bereits migriert (Alt-DB heißt .migrated)

    cred = settings().credentials_db_path
    vault = settings().vault_db_path
    ok = True

    # a) Credentials → lokal (nur wenn Ziel leer). ui_users VOR api_keys (FK-Reihenfolge).
    try:
        if _count(cred, "ui_users") == 0 and _count(old, "ui_users") > 0:
            n = _copy_tables(cred, old, [t.name for t in LocalBase.metadata.sorted_tables])
            log.info("db.migrate.credentials.done", rows=n)
    except Exception as e:  # noqa: BLE001 — nie fatal; Retry beim nächsten Start
        ok = False
        log.warning("db.migrate.credentials.failed", error=str(e)[:200])

    # b) Content → AKTUELLER Vault (nur wenn Ziel leer und Alt-DB Content hat).
    if not settings().is_reader:
        try:
            if _count(vault, "documents") == 0 and _count(old, "documents") > 0:
                n = _copy_tables(vault, old, [t.name for t in Base.metadata.sorted_tables])
                log.info("db.migrate.vault.done", vault=str(vault), rows=n)
        except Exception as e:  # noqa: BLE001
            ok = False
            log.warning("db.migrate.vault.failed", error=str(e)[:200])

    # c) Nur bei vollem Erfolg als erledigt markieren → kein Reimport in neue Vaults.
    if ok:
        try:
            old.rename(old.with_name("appstate.sqlite.migrated"))
            log.info("db.migrate.old_renamed", to="appstate.sqlite.migrated")
        except OSError as e:  # noqa: BLE001
            log.warning("db.migrate.rename_failed", error=str(e)[:200])
