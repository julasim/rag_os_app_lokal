"""
Strukturierte Metadaten-Extraktion via LLM (Norm-/Standard-/Anleitungs-Kontext).

Extrahiert Dokumenttyp, Normnummer, Version, Datum, Herausgeber, Sprache aus den
ersten ~3000 Zeichen. Gleiche Robustheit wie [autotag.py](autotag.py): bei
JEDEM Fehler `{}` zurück — der Ingest darf hier niemals abbrechen. Lokales
Qwen 2.5 3B; striktes JSON, konservativ (null statt raten).
"""
from __future__ import annotations

import json
import re

from config import global_config
from logger import log
from pipelines.factory import get_generator

_ALLOWED_DOC_TYPES = {
    "norm", "richtlinie", "anleitung", "vertrag", "protokoll",
    "angebot", "bericht", "sonstiges",
}

_PROMPT = """Extrahiere strukturierte Metadaten aus dem Dokument-Ausschnitt.
Antworte NUR als JSON-Objekt, kein weiterer Text, kein Markdown-Codeblock.

Felder (wenn unbekannt: null — nichts erfinden):
- doc_type: genau einer von [norm, richtlinie, anleitung, vertrag, protokoll, angebot, bericht, sonstiges]
- norm_id: Norm-/Standard-Kennung falls vorhanden, z.B. "ÖNORM B 1801-1", "EN 1992", "ISO 9001", "DIN 276". Sonst null.
- doc_version: Ausgabe/Version/Fassung, z.B. "2022-05-01", "Ausgabe 2015". Sonst null.
- issued_date: Datum oder Jahr der Ausgabe, z.B. "2022" oder "2022-05-01". Sonst null.
- issuer: Herausgeber, z.B. "Austrian Standards", "CEN", "ISO". Sonst null.
- language: ISO-639-1 Sprachcode, z.B. "de", "en".

Dokument-Ausschnitt:
---
{text}
---

JSON:"""


def extract_metadata(text: str, max_chars: int = 3000) -> dict:
    """Liefert ein dict mit den erkannten Feldern (nur gesetzte Keys). Bei Fehler {}."""
    if not text or not text.strip():
        return {}
    snippet = text[:max_chars].strip()
    try:
        cfg = global_config()
        generator = get_generator(cfg.llm)
        result = generator.run(prompt=_PROMPT.format(text=snippet))
        reply = (result.get("replies") or [""])[0].strip()
        meta = _clean(_parse_json_obj(reply))
        log.info("metadata.extracted", fields={k: v for k, v in meta.items()})
        return meta
    except Exception as e:
        log.warning("metadata.extract_failed", error=str(e))
        return {}


def _parse_json_obj(reply: str) -> dict:
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _clean(d: dict) -> dict:
    """Normalisiert/validiert die Rohfelder — verwirft Platzhalter/Unsinn."""
    out: dict = {}
    _EMPTY = {"", "null", "none", "unbekannt", "n/a", "keine angabe"}

    def _str(key: str, maxlen: int) -> None:
        v = d.get(key)
        if isinstance(v, str):
            v = v.strip()
            if v and v.lower() not in _EMPTY:
                out[key] = v[:maxlen]

    _str("norm_id", 128)
    _str("doc_version", 64)
    _str("issued_date", 32)
    _str("issuer", 128)

    lang = d.get("language")
    if isinstance(lang, str) and lang.strip() and lang.strip().lower() not in _EMPTY:
        out["language"] = lang.strip().lower()[:16]

    dt = d.get("doc_type")
    if isinstance(dt, str) and dt.strip().lower() in _ALLOWED_DOC_TYPES:
        out["doc_type"] = dt.strip().lower()

    # Deterministische Korrektur: Ein Dokument MIT Norm-/Standard-Kennung ist
    # eine Norm — das kleine lokale Modell verwechselt das sonst gern mit
    # "anleitung"/"richtlinie".
    if out.get("norm_id"):
        out["doc_type"] = "norm"

    return out


def version_year(value: str | None) -> int | None:
    """Grober Versions-Vergleichsschlüssel: erste 4-stellige Jahreszahl (1900–2099)."""
    if not value:
        return None
    m = re.search(r"(19|20)\d{2}", value)
    return int(m.group(0)) if m else None
