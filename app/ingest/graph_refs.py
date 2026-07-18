"""Deterministische Referenz-Extraktion für den Wissensgraph (Track D, L1).

Findet Norm- und Rechtsverweise im Chunk-Text per Regex und normalisiert sie
**ausschließlich** über `app/graph/canonical.py`. Kein eigenes Normalisierungs-
Rezept — divergierende Normalisierung erzeugt „Ghost Nodes" (eine Entität
zerfällt in unverbundene Knoten). Reine Funktion: keine DB, keine Config, kein I/O.

Der Regex fängt bewusst einen **Superset** — die eigentliche Kanonisierung
(Ausgabejahr abtrennen, `B1801`→`B 1801`, Präfix-Varianten) macht
`canonical_norm_id` / `canonical_legal_ref`.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from graph.canonical import canonical_legal_ref, canonical_norm_id

__all__ = ["Ref", "NormMatch", "extract_refs", "norm_matches", "NORM_RE"]


class Ref(NamedTuple):
    kind: str          # "norm" | "legal"
    canonical_key: str  # normalisierter Key aus canonical.py
    raw: str            # roher Treffer (für Label/Debug)


class NormMatch(NamedTuple):
    canonical_key: str      # normalisierter Key (Jahr abgetrennt)
    raw: str                # roher Treffer
    version: str | None     # abgetrenntes Ausgabejahr, falls vorhanden


# Norm-Präfixe (auch verkettet: „ÖNORM EN 1992", „DIN EN ISO 9001").
_NORM_PREFIX = r"(?:ÖNORM|OENORM|ONORM|DIN|EN|ISO|IEC)"

# Ein Norm-Verweis: ein oder mehrere verkettete Präfixe + Kennung (optionaler
# Buchstabe + ≥2-stellige Zahl + optionale Teilnummern) + optionales Ausgabejahr.
# Öffentlich, damit die Eigen-Identitäts-Erkennung (ingest/metadata_extract)
# DIESELBE Regex nutzt — kein Duplikat, keine Drift.
NORM_RE = re.compile(
    rf"\b{_NORM_PREFIX}(?:\s+{_NORM_PREFIX})*"
    r"\s+[A-Z]?\s*\d{2,}(?:\s*[-–]\s*\d+)*"
    r"(?:\s*[:(]\s*\d{4}\)?)?",
    re.IGNORECASE | re.UNICODE,
)

# Rechtsverweise: § / §§ / Art. / Artikel + Nummer (optionaler Buchstaben-Suffix).
_LEGAL_RE = re.compile(
    r"(?:§§?\s*\d+[a-z]?|\bArt(?:ikel|\.)?\s*\d+[a-z]?)",
    re.IGNORECASE | re.UNICODE,
)


def extract_refs(text: str) -> list[Ref]:
    """Norm-/Rechtsverweise aus einem Chunk-Text → deduplizierte Ref-Liste.

    Reihenfolge stabil (erstes Vorkommen). Treffer, die nach der Kanonisierung
    leer bleiben (nicht verwertbar), werden übersprungen — kein verschluckter
    Fehler, reine String-Operationen ohne Exception-Pfad.
    """
    if not text:
        return []

    seen: set[tuple[str, str]] = set()
    refs: list[Ref] = []

    for m in NORM_RE.finditer(text):
        raw = m.group(0).strip()
        key, _version = canonical_norm_id(raw)
        if not key:
            continue
        ident = ("norm", key)
        if ident not in seen:
            seen.add(ident)
            refs.append(Ref("norm", key, raw))

    for m in _LEGAL_RE.finditer(text):
        raw = m.group(0).strip()
        key = canonical_legal_ref(raw)
        if not key:
            continue
        ident = ("legal", key)
        if ident not in seen:
            seen.add(ident)
            refs.append(Ref("legal", key, raw))

    return refs


def norm_matches(text: str) -> list[NormMatch]:
    """ALLE Norm-Treffer in Reihenfolge (mit Version, nicht dedupliziert).

    Für die Eigen-Identitäts-Erkennung eines Dokuments (dominante/erste Norm im
    Kopf) — braucht die Häufigkeit je Norm UND das Ausgabejahr, die `extract_refs`
    (dedupliziert, ohne Version) verwirft.
    """
    out: list[NormMatch] = []
    for m in NORM_RE.finditer(text or ""):
        raw = m.group(0).strip()
        key, version = canonical_norm_id(raw)
        if key:
            out.append(NormMatch(key, raw, version))
    return out
