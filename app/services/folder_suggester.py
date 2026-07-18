"""
Deterministische Ordnerstruktur-Vorschläge (pro Dokument) — KEIN LLM.

Leichter Helfer für die Upload-/Einzeldokument-Ansicht (die community-basierte
Reorg macht `graph/reorg.py`). Regel, ohne Modell:
- erkennbare Norm-Familie (ÖNORM/DIN/EN/ISO/OIB) → `/Normen/<FAMILIE>/`
- sonst dominanter Tag → `/<Tag>/`
- sonst aktueller Ordner (No-op).

Umlautfreie Pfade (wie zuvor). Wirft nie — Fallback ist der `current_folder`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ingest.graph_refs import norm_matches
from logger import log

_FAMILY = {"önorm": "OENORM", "oenorm": "OENORM", "onorm": "OENORM",
           "din": "DIN", "en": "EN", "iso": "ISO", "iec": "IEC"}


@dataclass
class DocInfo:
    filename: str
    current_folder: str
    text_snippet: str
    doc_id: str | None = None      # None bei Pre-Upload-Analyse
    tags: list[str] = field(default_factory=list)


@dataclass
class FolderSuggestion:
    filename: str
    current_folder: str
    suggested_folder: str
    reason: str
    doc_id: str | None = None


async def suggest_folders(docs: list[DocInfo]) -> list[FolderSuggestion]:
    """Schlägt für jedes DocInfo deterministisch einen Ordner vor. Kein LLM, async
    nur wegen der Signatur-Kompatibilität mit dem Endpoint."""
    out: list[FolderSuggestion] = []
    for d in docs:
        target, reason = _suggest_one(d)
        out.append(FolderSuggestion(
            doc_id=d.doc_id,
            filename=d.filename,
            current_folder=d.current_folder,
            suggested_folder=target,
            reason=reason,
        ))
    log.info("folder_suggester.done", count=len(out))
    return out


def _suggest_one(d: DocInfo) -> tuple[str, str]:
    # 1. Norm-Familie aus Dateiname + Snippet.
    text = f"{d.filename}\n{d.text_snippet or ''}"
    for m in norm_matches(text[:1500]):
        fam = _FAMILY.get(m.canonical_key.split("_")[0])
        if fam:
            return _normalize(f"/Normen/{fam}/"), f"Norm-Familie {fam} erkannt"
    if re.search(r"\bOIB\b", text, re.IGNORECASE):
        return _normalize("/Normen/OIB/"), "OIB-Richtlinie erkannt"

    # 2. Dominanter Tag.
    tag = next((t.strip() for t in d.tags if t and t.strip()), None)
    if tag:
        return _normalize(f"/{tag}/"), f"nach Tag '{tag}'"

    # 3. Fallback: unverändert.
    return _normalize(d.current_folder or "/"), "keine eindeutige Zuordnung"


def _normalize(path: str) -> str:
    p = path.strip()
    for old, new in [
        ("ä", "ae"), ("ö", "oe"), ("ü", "ue"),
        ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"),
        ("ß", "ss"), ("ẞ", "SS"),
    ]:
        p = p.replace(old, new)
    p = p.replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    if not p.endswith("/"):
        p += "/"
    while "//" in p:
        p = p.replace("//", "/")
    return p
