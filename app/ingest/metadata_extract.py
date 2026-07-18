"""
Deterministische Metadaten-Extraktion (Norm-/Standard-/Anleitungs-Kontext) — KEIN LLM.

Leitet die **Eigen-Identität** eines Dokuments allein aus dem Kopf/Titel (erste
~1500 Zeichen) ab: `norm_id` (Anzeigeform OHNE Ausgabejahr), `doc_version`/
`issued_date` (das Jahr), `doc_type`, `issuer`, `language`. Norm-Erkennung nutzt
DIESELBE Regex wie der Graph (`ingest/graph_refs.NORM_RE` → `graph/canonical`),
damit die Kanonisierung nicht driftet.

Regeln:
- Die **eigene** norm_id ist die DOMINANTE Norm im Kopf (Häufigkeit; bei Gleichstand
  die erste). Das Ausgabejahr wird abgetrennt → `doc_version`, damit alle Fassungen
  EINER Norm dieselbe `norm_id` teilen (Voraussetzung für die Supersede-Logik).
- `norm_id` gesetzt ⇒ `doc_type='norm'` (deterministisch).
- Bei JEDEM Fehler `{}` — der Ingest darf hier nie abbrechen.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from ingest.graph_refs import norm_matches
from logger import log

_ALLOWED_DOC_TYPES = {
    "norm", "richtlinie", "anleitung", "vertrag", "protokoll",
    "angebot", "bericht", "sonstiges",
}

# doc_type-Heuristik (Kopf/Dateiname), Reihenfolge = Priorität. Norm gewinnt separat.
_DOCTYPE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("richtlinie", ("richtlinie", "oib-richtlinie", "leitlinie", "guideline")),
    ("vertrag", ("vertrag", "vereinbarung", "contract", "agb")),
    ("protokoll", ("protokoll", "besprechung", "sitzung", "minutes")),
    ("angebot", ("angebot", "offerte", "kostenvoranschlag", "leistungsverzeichnis")),
    ("anleitung", ("anleitung", "handbuch", "manual", "bedienungsanleitung")),
    ("bericht", ("bericht", "gutachten", "report", "prüfbericht", "pruefbericht")),
]

# Präfix → Herausgeber (Issuer).
_ISSUER_BY_PREFIX = {
    "önorm": "Austrian Standards", "oenorm": "Austrian Standards",
    "onorm": "Austrian Standards", "oib": "OIB",
    "din": "DIN", "en": "CEN", "iso": "ISO", "iec": "IEC",
}

# OIB ist keine „Norm" im NORM_RE-Sinn → separat als Issuer/Familie erkennen.
_ISSUER_RE = re.compile(r"\b(ÖNORM|OENORM|ONORM|OIB|DIN|EN|ISO|IEC)\b", re.IGNORECASE)


def extract_metadata(text: str, head_chars: int = 1500) -> dict:
    """Liefert ein dict mit den erkannten Feldern (nur gesetzte Keys). Bei Fehler {}."""
    if not text or not text.strip():
        return {}
    try:
        head = text[:head_chars]
        out: dict = {}

        norm_id, inline_ver = _own_norm(head)
        if norm_id:
            out["norm_id"] = norm_id
            out["doc_type"] = "norm"
            hv_year, hv_date = _head_version(head)
            version = inline_ver or hv_year
            if version:
                out["doc_version"] = version
                out["issued_date"] = hv_date or version

        issuer = _issuer(head, norm_id)
        if issuer:
            out["issuer"] = issuer

        if "doc_type" not in out:
            out["doc_type"] = _doc_type(head)

        lang = _language(text[:4000])
        if lang:
            out["language"] = lang

        log.info("metadata.extracted", fields=dict(out))
        return out
    except Exception as e:  # noqa: BLE001 — Ingest darf hier nie abbrechen
        log.warning("metadata.extract_failed", error=str(e))
        return {}


# ---------------------------------------------------------------------------
# Eigen-Identität (dominante Norm im Kopf)
# ---------------------------------------------------------------------------
def _own_norm(head: str) -> tuple[str | None, str | None]:
    matches = norm_matches(head)
    if not matches:
        return None, None
    counts = Counter(m.canonical_key for m in matches)
    top_key = counts.most_common(1)[0][0]
    first = next(m for m in matches if m.canonical_key == top_key)
    return _display_norm(first.raw), first.version


# Ausgabe/Fassung im Kopf, wenn die Norm selbst KEIN inline-Jahr trägt (häufig
# steht das Datum in einer eigenen „Ausgabe:"-/Cover-Zeile). Bewusst KEIN bloßes
# trailing-Jahr (das wäre bei „EN 1992" die Normnummer, kein Jahr).
_DATE_RE = re.compile(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b")
_EDITION_RE = re.compile(
    r"(?:Ausgabe|Ausgabedatum|Edition|Fassung|Stand|gültig\s+ab)\b[^\d]{0,12}((?:19|20)\d{2})",
    re.IGNORECASE,
)


def _head_version(head: str) -> tuple[str | None, str | None]:
    """(Jahr, volles Datum) aus dem Kopf — für Fassungen mit separatem Ausgabedatum."""
    m = _DATE_RE.search(head)
    if m:
        return m.group(1), m.group(0)
    m = _EDITION_RE.search(head)
    if m:
        return m.group(1), m.group(1)
    return None, None


def _display_norm(raw: str) -> str:
    """Roher Norm-Treffer → saubere Anzeigeform OHNE Ausgabejahr.

    Muss zum Jahr-Abtrennen von `canonical_norm_id` passen (gleiche Trenner), damit
    `norm_id` fassungsübergreifend stabil ist. Präfix-Varianten → 'ÖNORM'.
    """
    s = unicodedata.normalize("NFKC", raw).strip()
    s = re.sub(r"[:\(]\s*\d{4}\)?\s*$", "", s)
    s = re.sub(r"\b(?:Ausgabe|Edition|Fassung|Ed\.?)\s+\d{4}\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip().upper()
    s = re.sub(r"^(OENORM|ONORM)\b", "ÖNORM", s)
    # 'B1801' → 'B 1801' (Buchstabengruppe ↔ erste Zahl), wie in canonical.
    s = re.sub(r"([A-ZÄÖÜ]+)\s*(\d)", r"\1 \2", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:128]


def _issuer(head: str, norm_id: str | None) -> str | None:
    if norm_id:
        first = norm_id.split()[0].lower()
        if first in _ISSUER_BY_PREFIX:
            return _ISSUER_BY_PREFIX[first]
    m = _ISSUER_RE.search(head)
    if m:
        return _ISSUER_BY_PREFIX.get(m.group(1).lower())
    return None


def _doc_type(head: str) -> str:
    low = head.lower()
    for dt, keywords in _DOCTYPE_KEYWORDS:
        if any(k in low for k in keywords):
            return dt
    return "sonstiges"


# ---------------------------------------------------------------------------
# Sprache (billige de/en-Heuristik, kein LLM/Modell)
# ---------------------------------------------------------------------------
_DE_STOP = {"der", "die", "das", "und", "ist", "für", "mit", "den", "von", "nicht",
            "eine", "auch", "dem", "sich", "auf", "werden", "bei", "ÖNORM"}
_EN_STOP = {"the", "and", "for", "with", "that", "this", "are", "from", "which",
            "shall", "have", "been", "not", "requirements"}


def _language(sample: str) -> str | None:
    words = re.findall(r"[A-Za-zÄÖÜäöüß]+", sample.lower())
    if len(words) < 20:
        return None
    ws = set(words)
    de = len(ws & {w.lower() for w in _DE_STOP})
    en = len(ws & _EN_STOP)
    if de == en:
        # Umlaut/ß als Tiebreaker → Deutsch
        return "de" if re.search(r"[äöüß]", sample) else None
    return "de" if de > en else "en"


def version_year(value: str | None) -> int | None:
    """Grober Versions-Vergleichsschlüssel: erste 4-stellige Jahreszahl (1900–2099)."""
    if not value:
        return None
    m = re.search(r"(19|20)\d{2}", value)
    return int(m.group(0)) if m else None
