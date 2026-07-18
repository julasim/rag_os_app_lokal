"""
Kanonische Ordner-Zugriffskontrolle — die **einzige** Quelle der Wahrheit.

Warum hier zentral?
    Vorher gab es drei divergierende Implementierungen (AuthContext,
    MCP-Server, Inline-Filter in documents.py) mit unterschiedlichem Verhalten
    — inklusive eines nackten `startswith`, das Ordnergrenzen ignorierte
    (`/Steuer` matchte auch `/Steuerberatung-Fremd/`). Alle Pfade nutzen jetzt
    ausschließlich die Funktionen hier.

Modell (CLAUDE.md §4): `folder_path` ist freier Text, VS-Code-artig nestbar.
Postgres ist Wahrheit über die vorhandenen Ordner; die tatsächlich
durchsuchbare Ordnerliste wird von dort aufgelöst (nicht aus Qdrant).

Zwei bewusst GETRENNTE Semantiken (Track E — nicht vermischen!):

  * **Bearer-API-Keys** (`key_allows_folder` / `accessible_folder_paths`):
    leere/None `allowed_folders` = **Vollzugriff**. Historisch gewachsen,
    bewusst so — ein Key ohne Ordner-Einschränkung darf alles.
  * **UI-/OAuth-User** (`user_allows_folder` / `user_accessible_folder_paths`):
    **fail-safe** über ein explizites `access_all`-Flag. `access_all=True` =
    Vollzugriff; sonst zählt NUR `allowed_folders`, und eine **leere Liste =
    NICHTS**. Ein neu angelegter User (access_all=false, []) sieht per Default
    nichts. Die „leer = alles"-Interpretation der Key-Funktionen wird hier
    NIEMALS wiederverwendet.

Beide Auflöser liefern ihr Ergebnis im selben Format, damit der nachgelagerte
Code semantik-einheitlich ist:
    ``None`` = keine Einschränkung · ``[]`` = nichts · ``[..]`` = genau diese.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Document


def normalize_folder(path: str) -> str:
    """Normalisiert auf führenden + abschließenden Slash, ohne Doppel-Slashes."""
    p = (path or "").strip()
    if not p.startswith("/"):
        p = "/" + p
    if not p.endswith("/"):
        p = p + "/"
    while "//" in p:
        p = p.replace("//", "/")
    return p


def is_within(path: str, allowed: str) -> bool:
    """
    True, wenn `path` gleich `allowed` oder ein echter Unterordner davon ist —
    **segmentgrenzbewusst**. Beide Seiten werden auf ein abschließendes `/`
    normalisiert, sodass `/Steuer/` NICHT `/Steuer2025-Neukunde/` matcht.
    """
    p = normalize_folder(path)
    a = normalize_folder(allowed)
    return p == a or p.startswith(a)


async def _existing_folders_under(
    requested_folder: str | None,
    restrict_to: list[str] | None,
    session: AsyncSession,
) -> list[str]:
    """
    Gemeinsamer Kern beider Auflöser: liefert die real existierenden
    ``Document.folder_path``, gefiltert auf „unter `requested_folder`" UND
    (falls `restrict_to` gesetzt) „unter mindestens einem `restrict_to`-Ordner".

    ``restrict_to is None`` = keine Ordner-Restriktion (nur `requested_folder`
    zählt). Diese Funktion trägt **keine** Semantik über leere Listen — die
    kapseln die öffentlichen Auflöser darüber.
    """
    result = await session.execute(select(Document.folder_path).distinct())
    all_folders = [row[0] for row in result.all()]

    out: list[str] = []
    for fp in all_folders:
        if requested_folder and not is_within(fp, requested_folder):
            continue
        if restrict_to is not None and not any(is_within(fp, af) for af in restrict_to):
            continue
        out.append(fp)
    return out


# ---------------------------------------------------------------------------
# Bearer-API-Key-Semantik (leer/None = Vollzugriff) — UNVERÄNDERT
# ---------------------------------------------------------------------------
def key_allows_folder(allowed_folders: list[str] | None, folder: str) -> bool:
    """
    Darf ein Bearer-Key mit diesen `allowed_folders` auf `folder` zugreifen?

    Leere/None-Liste = Vollzugriff (Bearer-Semantik). Sonst muss `folder` in
    mindestens einem erlaubten Ordner liegen (segmentgrenzbewusst).
    """
    if not allowed_folders:
        return True
    return any(is_within(folder, af) for af in allowed_folders)


async def accessible_folder_paths(
    allowed_folders: list[str] | None,
    requested_folder: str | None,
    session: AsyncSession,
) -> list[str] | None:
    """
    Bearer-Key-Auflösung (leere `allowed_folders` = Vollzugriff).

    Rückgabe (der Aufrufer MUSS alle drei Fälle behandeln):
      * ``None`` → keine Einschränkung → keinen Ordner-Filter bauen.
      * ``[]``   → nichts zugänglich → **leer** antworten, NICHT ungefiltert suchen.
      * ``[..]`` → konkrete Ordnerliste für einen Qdrant-``in``-Filter auf
        ``meta.folder``.
    """
    restricted = bool(allowed_folders)
    if not restricted and not requested_folder:
        return None
    restrict_to = list(allowed_folders) if restricted else None
    return await _existing_folders_under(requested_folder, restrict_to, session)


# ---------------------------------------------------------------------------
# UI-/OAuth-User-Semantik (fail-safe, access_all-Flag) — Track E
# ---------------------------------------------------------------------------
def user_allows_folder(
    access_all: bool, allowed_folders: list[str] | None, folder: str
) -> bool:
    """
    Darf ein User (Rolle+ACL) auf `folder` zugreifen? **Fail-safe:**
    `access_all=True` = Vollzugriff; sonst muss `folder` in `allowed_folders`
    liegen — **leere Liste = NICHTS** (nie Vollzugriff).
    """
    if access_all:
        return True
    if not allowed_folders:
        return False
    return any(is_within(folder, af) for af in allowed_folders)


async def user_accessible_folder_paths(
    access_all: bool,
    allowed_folders: list[str] | None,
    requested_folder: str | None,
    session: AsyncSession,
) -> list[str] | None:
    """
    User-Auflösung (fail-safe). Gleiche 3-Fälle-Rückgabe wie
    `accessible_folder_paths`, aber:
      * `access_all=True`  → wie unrestricted (``None``, bzw. auf `requested`
        gefiltert).
      * `access_all=False` → NUR `allowed_folders`; **leere Liste ⇒ ``[]``**
        (nichts) — niemals ungefiltert.
    """
    if access_all:
        if not requested_folder:
            return None
        return await _existing_folders_under(requested_folder, None, session)
    if not allowed_folders:
        return []
    return await _existing_folders_under(requested_folder, list(allowed_folders), session)
