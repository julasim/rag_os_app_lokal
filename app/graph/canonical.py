"""Kanonische Identität — die EINZIGE Normalisierungsquelle für Graph-Knoten.

Muss von L1 (Regex-Extraktion), L3 (LLM-Merge) UND dem Query-Fastpath
**identisch** genutzt werden. Divergierende Normalisierung erzeugt „Ghost Nodes"
(eine Entität zerfällt in unverbundene Knoten) — die dokumentierte Drift-Bug-
Klasse (vgl. der `startswith`-ACL-Bug in §13). Deshalb: kein Copy-Paste des
Rezepts, nur Import aus diesem Modul.

`normalize_key` ist parameterfrei und **idempotent**
(`normalize_key(normalize_key(s)) == normalize_key(s)`). Für strukturierte
Bezeichner (Normen, §/Art.) gibt es eine domänenspezifische Vor-Kanonisierung,
weil das generische Rezept sonst versagt (z.B. `§` verschwindet, `B1801` ≠
`B 1801`, Ausgabejahr landet fälschlich im Key).
"""
from __future__ import annotations

import re
import unicodedata

__all__ = ["normalize_key", "canonical_norm_id", "canonical_legal_ref"]


def normalize_key(s: str) -> str:
    """Kanonische Form eines Schlüssels. Idempotent.

    NFKC (Unicode-Komposition vereinheitlichen) → Nicht-Wort-Läufe zu einem `_`
    (re.UNICODE: ä/ö/ü/ß + akzentuierte/nicht-lateinische Buchstaben überleben) →
    Underscore-Läufe kollabieren → Ränder trimmen → casefold (faltet auch ß→ss).
    """
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[^\w]+", "_", s, flags=re.UNICODE)
    s = re.sub(r"_+", "_", s)
    return s.strip("_").casefold()


# Transliterations-/Schreibvarianten von Norm-Präfixen (kuratiert, klein halten).
_NORM_PREFIXES = {"oenorm": "önorm", "onorm": "önorm"}

# Symbol-Präfixe, die normalize_key sonst verschluckt (Reihenfolge: längste zuerst).
_LEGAL_SYMBOLS = [("§§", "par"), ("§", "par"), ("artikel", "art"),
                  ("art.", "art"), ("abs.", "abs")]


def canonical_norm_id(raw: str) -> tuple[str, str | None]:
    """Liefert (canonical_key, version) für eine Normnummer.

    Das **Ausgabejahr** wird abgetrennt (nicht identitätsstiftend → separat als
    `version`), damit alle Fassungen einer Norm **einen** Knoten teilen (verknüpft
    über `supersedes`). Die **Teilnummer** (`-1`, `-2`) bleibt Teil des Keys.
    """
    s = unicodedata.normalize("NFKC", raw).strip()
    version: str | None = None
    # Ausgabejahr NUR bei explizitem Trenner abtrennen (`:2022`, `(2022)`,
    # `Ausgabe 2022`). Ein bloßes trailing `\s\d{4}` wäre mehrdeutig — bei
    # `EN 1992`/`ISO 9001` ist die 4-stellige Zahl die NORMNUMMER, kein Jahr.
    m = re.search(r"[:\(](\d{4})\)?\s*$", s)
    if not m:
        m = re.search(r"\b(?:Ausgabe|Edition|Fassung|Ed\.?)\s+(\d{4})\s*$", s, re.IGNORECASE)
    if m:
        version, s = m.group(1), s[: m.start()]
    # Buchstabengruppe ↔ erste Zahlengruppe IMMER mit genau einem Space trennen
    # ('B1801' -> 'B 1801'), damit Schreibvarianten kollabieren.
    s = re.sub(r"([A-Za-zÄÖÜäöü]+)\s*(\d)", r"\1 \2", s)
    tokens = s.split()
    if tokens and normalize_key(tokens[0]) in _NORM_PREFIXES:
        tokens[0] = _NORM_PREFIXES[normalize_key(tokens[0])]
    return normalize_key(" ".join(tokens)), version


def canonical_legal_ref(raw: str) -> str:
    """§/Art./Abs.-Verweis → Key (`§ 12` -> `par_12`, `Art. 17` -> `art_17`)."""
    s = unicodedata.normalize("NFKC", raw).strip().casefold()
    for sym, repl in _LEGAL_SYMBOLS:
        if s.startswith(sym):
            s = repl + " " + s[len(sym):]
            break
    return normalize_key(s)
