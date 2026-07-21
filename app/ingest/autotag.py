"""
Deterministische Tag-Generierung — KEIN LLM.

Graphify-inspiriert (LLM-frei): Tags = Issuer-/Norm-Familie (aus den Norm-Treffern
im Kopf) + häufigste Inhaltswörter (Term-Frequenz über einer Stoppwortliste). Rein
funktional, idempotent, keine Netzw/Modell-Abhängigkeit. Bei Leereingabe: [].

Bewusst konservativ: die Tags sind **Vorschläge**, in der Verwaltungs-UI prüf-/
korrigierbar (Human-in-the-loop) — kein Anspruch auf semantische Perfektion.
"""
from __future__ import annotations

import re
from collections import Counter

from ingest.graph_refs import norm_matches
from logger import log

_MAX_TAGS = 7

# Familien-Tag je Norm-Präfix (lowercase Anzeigeform).
_FAMILY_BY_PREFIX = {
    "önorm": "önorm", "oenorm": "önorm", "onorm": "önorm",
    "din": "din", "en": "en", "iso": "iso", "iec": "iec",
}
_OIB_RE = re.compile(r"\bOIB\b", re.IGNORECASE)
# Reine römische Ziffern (xvii/xxiv/…) — in AT-Gesetzesköpfen (GP/BGBl) allgegenwärtig.
_ROMAN_RE = re.compile(r"^[ivxlcdm]+$", re.IGNORECASE)

# Deutsch/Englisch-Stoppwörter + juristische Boilerplate. Der Kopf eines
# AT-Gesetzes ist eine BGBl-Novellenliste ("BGBl. I Nr. X/YYYY, XVII. GP") —
# ohne diese Liste dominieren bgbl/bundesgesetz/änderung/römische Ziffern die Tags.
_STOP = {
    "der", "die", "das", "und", "ist", "für", "mit", "den", "von", "nicht", "eine",
    "auch", "dem", "sich", "auf", "werden", "bei", "als", "aus", "sind", "wird",
    "einer", "einem", "eines", "oder", "zum", "zur", "über", "nach", "durch", "kann",
    "muss", "sowie", "diese", "dieser", "dieses", "wenn", "aber", "nur", "wie", "vor",
    "the", "and", "for", "with", "that", "this", "are", "from", "which", "shall",
    "have", "been", "not", "all", "any", "may", "such", "per", "into", "than",
    # --- juristische Boilerplate (AT-Gesetze/Verordnungen) ---
    "bgbl", "bundesgesetz", "bundesgesetzblatt", "bundesrecht", "gesetz", "gesetze",
    "artikel", "absatz", "paragraph", "paragraf", "fassung", "gemäß", "gemaess",
    "nummer", "ziffer", "litera", "abschnitt", "hauptstück", "hauptstueck", "anlage",
    "inkrafttreten", "verordnung", "novelle", "änderung", "aenderung", "geändert",
    "geaendert", "aufgehoben", "sinne", "jeweils", "geltenden", "erster", "zweiter",
    "dritter", "vierter", "fünfter", "beziehungsweise", "insbesondere", "gilt",
    "gelten", "welche", "welcher", "welches", "haben", "worden", "wurde", "wurden",
    "jahr", "jahre", "monat", "monate", "person", "personen", "fall", "fälle",
    "faelle", "teil", "teile", "republik", "österreich", "oesterreich",
}


def generate_tags(text: str, max_chars: int = 4000) -> list[str]:
    """Liefert bis zu 7 deterministische Tags. Reihenfolge: Familien-Tags zuerst,
    dann häufigste Inhaltswörter. Nie Exception — im Zweifel weniger Tags."""
    if not text or not text.strip():
        return []
    try:
        head = text[:max_chars]
        tags: list[str] = []
        seen: set[str] = set()

        def _add(t: str) -> None:
            t = t.strip().lower()[:64]
            if t and t not in seen:
                seen.add(t)
                tags.append(t)

        # 1. Norm-/Issuer-Familien aus dem Kopf.
        for m in norm_matches(head[:1500]):
            fam = _FAMILY_BY_PREFIX.get(m.canonical_key.split("_")[0])
            if fam:
                _add(fam)
        if _OIB_RE.search(head[:1500]):
            _add("oib")

        # 2. Häufigste Inhaltswörter über den GANZEN Text (nicht nur den Kopf —
        #    der ist bei Gesetzen Novellen-Boilerplate; der Inhalt kommt später).
        for word, _n in _keywords(text):
            _add(word)
            if len(tags) >= _MAX_TAGS:
                break

        log.info("autotag.generated", count=len(tags), tags=tags)
        return tags[:_MAX_TAGS]
    except Exception as e:  # noqa: BLE001 — Ingest darf hier nie abbrechen
        log.warning("autotag.failed", error=str(e))
        return []


def _keywords(text: str, top: int = 10, max_chars: int = 200_000) -> list[tuple[str, int]]:
    """Ranking der Inhaltswörter (≥5 Zeichen, keine Stoppwörter/Boilerplate, keine
    römischen Ziffern). Score = Häufigkeit × leichte Längen-Gewichtung — deutsche
    Komposita (Hauptmietzins, Betriebskosten) sind spezifischer als kurze Allerwelts-
    wörter und sollen vorne stehen."""
    counts: Counter[str] = Counter()
    for tok in re.findall(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-]{4,}", text[:max_chars]):
        low = tok.lower().strip("-")  # Trenn-Bindestriche aus Zeilenumbrüchen weg
        if len(low) < 5 or low in _STOP or _ROMAN_RE.match(low):
            continue
        counts[low] += 1
    scored = sorted(
        ((low, n) for low, n in counts.items() if n >= 2),
        key=lambda x: x[1] * (1 + min(len(x[0]), 16) / 20),
        reverse=True,
    )
    return scored[:top]
