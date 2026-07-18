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

# Deutsch/Englisch-Stoppwörter (klein halten; nur die häufigsten Füllwörter).
_STOP = {
    "der", "die", "das", "und", "ist", "für", "mit", "den", "von", "nicht", "eine",
    "auch", "dem", "sich", "auf", "werden", "bei", "als", "aus", "sind", "wird",
    "einer", "einem", "eines", "oder", "zum", "zur", "über", "nach", "durch", "kann",
    "muss", "sowie", "diese", "dieser", "dieses", "wenn", "aber", "nur", "wie", "vor",
    "the", "and", "for", "with", "that", "this", "are", "from", "which", "shall",
    "have", "been", "not", "all", "any", "may", "such", "per", "into", "than",
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

        # 2. Häufigste Inhaltswörter (Term-Frequenz, Stoppwörter/kurze Tokens raus).
        for word, _n in _keywords(head):
            _add(word)
            if len(tags) >= _MAX_TAGS:
                break

        log.info("autotag.generated", count=len(tags), tags=tags)
        return tags[:_MAX_TAGS]
    except Exception as e:  # noqa: BLE001 — Ingest darf hier nie abbrechen
        log.warning("autotag.failed", error=str(e))
        return []


def _keywords(text: str, top: int = 10) -> list[tuple[str, int]]:
    """Term-Frequenz-Ranking der Inhaltswörter (≥4 Zeichen, keine Stoppwörter,
    keine reinen Zahlen). Deutsch profitiert von der Substantiv-Großschreibung —
    wir zählen case-insensitiv, geben aber das häufigste Schriftbild zurück."""
    counts: Counter[str] = Counter()
    forms: dict[str, Counter[str]] = {}
    for tok in re.findall(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-]{3,}", text):
        low = tok.lower()
        if low in _STOP or len(low) < 4:
            continue
        counts[low] += 1
        forms.setdefault(low, Counter())[tok] += 1
    out: list[tuple[str, int]] = []
    for low, n in counts.most_common(top * 2):
        if n < 2:
            continue  # Einmalvorkommen sind zu schwach für ein Tag
        out.append((low, n))
        if len(out) >= top:
            break
    return out
