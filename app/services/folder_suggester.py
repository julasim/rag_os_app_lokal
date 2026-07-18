"""
KI-basierte Ordnerstruktur-Vorschläge.

Analysiert Dateiinhalte via Ollama-LLM und schlägt eine logische
Ordnerhierarchie vor. Robust gegen LLM-Fehler — gibt im Fehlerfall
die aktuellen Ordner als Vorschlag zurück.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field

from config import global_config
from logger import log
from pipelines.factory import get_generator


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


_PROMPT = """\
Du bist ein intelligenter Dokumenten-Organizer für ein deutschsprachiges Unternehmen.
Analysiere die folgenden Dokumente und schlage für jedes Dokument einen logischen Ordnerpfad vor.

Regeln für Ordnerpfade:
- Beginnen immer mit / und enden immer mit /
- Maximal 3 Hierarchie-Ebenen  (z.B. /Steuer/2024/ oder /Vertraege/Miete/)
- Deutsche Bezeichnungen, KEINE Umlaute in Pfaden (ä→ae, ö→oe, ü→ue, ß→ss)
- Jahreszahlen als eigene Unterebene, wenn zeitlich relevant
- Ähnliche Dokumente in denselben Ordner gruppieren
- Keine unnötige Tiefe — ein flacher Ordner ist besser als drei leere Ebenen

Dokumente:
{documents_json}

Antworte NUR mit gültigem JSON — kein Markdown-Block, kein erklärender Text:
{{
  "suggestions": [
    {{
      "filename": "beispiel.pdf",
      "suggested_folder": "/Steuer/2024/",
      "reason": "Einkommensteuererklärung 2024"
    }}
  ]
}}"""


async def suggest_folders(docs: list[DocInfo]) -> list[FolderSuggestion]:
    """
    Schlägt für jedes DocInfo einen Ordner vor.
    Wirft nie eine Exception — Fallback ist der jeweilige current_folder.
    """
    if not docs:
        return []

    doc_list = []
    for i, doc in enumerate(docs, 1):
        tags_str = ", ".join(doc.tags) if doc.tags else "keine"
        snippet = (doc.text_snippet or "").strip()[:300] or "(kein Inhalt verfügbar)"
        doc_list.append({
            "nr": i,
            "filename": doc.filename,
            "aktueller_ordner": doc.current_folder,
            "tags": tags_str,
            "inhalt_vorschau": snippet,
        })

    prompt = _PROMPT.format(
        documents_json=json.dumps(doc_list, ensure_ascii=False, indent=2)
    )

    try:
        cfg = global_config()
        generator = get_generator(cfg.llm)
        result = await asyncio.to_thread(generator.run, prompt=prompt)
        reply = (result.get("replies") or [""])[0].strip()
        parsed = _parse_reply(reply, docs)
        log.info("folder_suggester.success", count=len(parsed))
        return parsed
    except Exception as exc:
        log.warning("folder_suggester.failed", error=str(exc))
        return _fallback(docs)


# ---------------------------------------------------------------------------
# Internes Parsing
# ---------------------------------------------------------------------------

def _normalize(path: str) -> str:
    p = path.strip()
    # Umlaute ersetzen (Prompt-Vorgabe: keine Umlaute in Pfaden)
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


def _parse_reply(reply: str, docs: list[DocInfo]) -> list[FolderSuggestion]:
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if not match:
        raise ValueError("No JSON object in LLM reply")
    data = json.loads(match.group(0))
    raw_items = data.get("suggestions", [])

    by_name = {d.filename: d for d in docs}
    result: list[FolderSuggestion] = []
    covered: set[str] = set()

    for item in raw_items:
        fname = item.get("filename", "")
        doc = by_name.get(fname)
        if not doc:
            # Fuzzy: LLM manchmal kürzt den Namen
            for key, d in by_name.items():
                if fname in key or key in fname:
                    doc = d
                    break
        if not doc:
            continue
        result.append(FolderSuggestion(
            doc_id=doc.doc_id,
            filename=doc.filename,
            current_folder=doc.current_folder,
            suggested_folder=_normalize(item.get("suggested_folder", doc.current_folder)),
            reason=item.get("reason", ""),
        ))
        covered.add(doc.filename)

    # Dokumente ohne LLM-Antwort: current_folder beibehalten
    for doc in docs:
        if doc.filename not in covered:
            result.append(FolderSuggestion(
                doc_id=doc.doc_id,
                filename=doc.filename,
                current_folder=doc.current_folder,
                suggested_folder=doc.current_folder,
                reason="",
            ))

    return result


def _fallback(docs: list[DocInfo]) -> list[FolderSuggestion]:
    return [
        FolderSuggestion(
            doc_id=d.doc_id,
            filename=d.filename,
            current_folder=d.current_folder,
            suggested_folder=d.current_folder,
            reason="KI-Analyse nicht verfügbar",
        )
        for d in docs
    ]
