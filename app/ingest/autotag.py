"""
Automatische Tag-Generierung via LLM.

Beim Ingest bekommt der LLM die ersten ~3000 Zeichen des Dokuments und
liefert 3-7 prägnante deutsche Tags zurück. Robust gegen LLM-Fehler:
wenn irgendwas schiefgeht, gibt die Funktion [] zurück und der Ingest
läuft einfach ohne Auto-Tags weiter.
"""
from __future__ import annotations

import json
import re

from config import global_config
from logger import log
from pipelines.factory import get_generator


_PROMPT = """Analysiere den folgenden Dokument-Ausschnitt und erzeuge 3 bis 7 kurze, prägnante Tags auf Deutsch, die Inhalt und Typ des Dokuments beschreiben.

Regeln:
- Jedes Tag 1 bis 3 Wörter, kleingeschrieben, Mehrwort-Begriffe mit Bindestrich
- Thematisch relevant: Projektname, Fachgebiet, Dokumenttyp, Eigenname
- KEINE Allgemeinplätze wie "dokument", "text", "information", "datei"
- Antwort NUR als JSON-Array, kein weiterer Text, kein Markdown-Codeblock

Beispiele guter Antworten:
["ausschreibung", "bvh-musterstraße", "wärmedämmung", "önorm", "leistungsverzeichnis"]
["bewerbung", "architektur", "praktikum", "katzianka"]
["prüfungsprotokoll", "tu-wien", "baustatik", "2023"]

Dokument-Ausschnitt:
---
{text}
---

Tags:"""


def generate_tags(text: str, max_chars: int = 3000) -> list[str]:
    """
    Liefert eine Liste Tags (3-7 Strings) für `text`.
    Bei jeglichem Fehler: leere Liste — der Ingest darf niemals an dieser
    Stelle abbrechen.
    """
    if not text or not text.strip():
        return []

    snippet = text[:max_chars].strip()

    try:
        cfg = global_config()
        generator = get_generator(cfg.llm)
        prompt = _PROMPT.format(text=snippet)
        result = generator.run(prompt=prompt)
        reply = (result.get("replies") or [""])[0].strip()
        tags = _parse_tags(reply)
        log.info("autotag.generated", count=len(tags), tags=tags)
        return tags
    except Exception as e:
        log.warning("autotag.failed", error=str(e))
        return []


def _parse_tags(reply: str) -> list[str]:
    """
    Extrahiert das Tag-Array aus der LLM-Antwort. Robust gegen Beifang
    wie Markdown-Codeblöcke oder Erklär-Text drumherum.
    """
    # Versuche JSON-Array in der Antwort zu finden
    match = re.search(r"\[.*?\]", reply, re.DOTALL)
    if not match:
        return []

    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []

    if not isinstance(raw, list):
        return []

    cleaned: list[str] = []
    seen: set[str] = set()
    for t in raw:
        if not isinstance(t, str):
            continue
        tag = t.strip().lower()[:64]
        if tag and tag not in seen:
            cleaned.append(tag)
            seen.add(tag)

    return cleaned[:7]
