"""
Chunking.

Strategie:
  - Arbeitet pro Seite/Abschnitt (damit Seitenzahl/Titel erhalten bleibt)
  - Split zuerst an Absätzen, dann an Sätzen, dann hart an Zeichen
  - Ziel-Größe + Überlappung konfigurierbar aus projects.yml

Jeder Chunk ist ein Dict mit Text + vollständiger Metadata.
"""
from __future__ import annotations

import re
from typing import Any

from config import ChunkingConfig
from ingest.parsers import ParsedDocument


# Grobe Token-Schätzung: 1 Token ≈ 4 Zeichen (gilt für Deutsch recht gut)
_CHARS_PER_TOKEN = 4

# Minimale Chunk-Länge: Chunks kürzer als dieser Wert werden nicht indexiert.
# Verhindert, dass reine Titel/Überschriften ohne Inhalt als Chunks landen
# (z.B. PDF-Seiten die nur "Projektmanagement" als Text haben weil der Rest
# aus Diagrammen/Grafiken besteht).
_MIN_CHUNK_CHARS = 80


def _tokens_to_chars(n: int) -> int:
    return n * _CHARS_PER_TOKEN


def _normalize_text(text: str) -> str:
    """
    Bereinigt häufige PDF-Artefakte vor dem Chunking:
    - Silbentrennung: "Pla-\nnung" → "Planung"
    - Einzelne Zeilenumbrüche innerhalb eines Satzes → Leerzeichen
      (nur wenn nächste Zeile mit Kleinbuchstabe oder Sonderzeichen beginnt,
       d.h. kein neuer Satz/Absatz)
    - Mehrfach-Leerzeichen kollabieren
    """
    # Silbentrennung am Zeilenende entfernen
    text = re.sub(r"-\n(?=[a-zäöüA-ZÄÖÜ])", "", text)
    # Einzelne Newline (kein Paragraph-Break) → Leerzeichen wenn kein neuer Satz
    text = re.sub(r"(?<!\n)\n(?!\n)(?=[a-zäöü(])", " ", text)
    # Mehrfach-Leerzeichen kollabieren
    text = re.sub(r" {2,}", " ", text)
    return text


def _split_sentences(text: str) -> list[str]:
    # Simpler Satz-Splitter, robust genug für Alltag.
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])", text.strip())
    return [p for p in parts if p.strip()]


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]


def _pack(units: list[str], max_chars: int, overlap_chars: int) -> list[str]:
    """Packt String-Einheiten in Chunks knapp unter max_chars, mit Overlap."""
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0

    for u in units:
        if buf_len + len(u) + 1 > max_chars and buf:
            chunks.append("\n".join(buf))
            # Overlap: letzte Einheiten behalten, die in overlap_chars passen
            overlap_buf: list[str] = []
            overlap_len = 0
            for prev in reversed(buf):
                if overlap_len + len(prev) > overlap_chars:
                    break
                overlap_buf.insert(0, prev)
                overlap_len += len(prev)
            buf = overlap_buf
            buf_len = overlap_len
        buf.append(u)
        buf_len += len(u) + 1

    if buf:
        chunks.append("\n".join(buf))
    return chunks


def chunk_document(
    doc: ParsedDocument,
    config: ChunkingConfig,
    base_metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Zerlegt ein ParsedDocument in Chunks mit vollständiger Metadata.

    base_metadata sollte enthalten:
        doc_id, project, folder_path, file_name, tags, source_type
    """
    max_chars = _tokens_to_chars(config.size)
    overlap_chars = _tokens_to_chars(config.overlap)

    chunks: list[dict[str, Any]] = []
    chunk_idx = 0
    heading_stack: list[str] = []   # Breadcrumb der Überschriften-Kette (nach Ebene)

    for page in doc.pages:
        if not page.text.strip():
            continue

        # Überschriften-Kette pflegen: eine Überschrift mit Ebene aktualisiert den
        # Stack an ihrer Position; eine Überschrift ohne Ebene gilt als Blatt.
        if page.title and page.title_level:
            lvl = max(1, page.title_level)
            del heading_stack[lvl - 1:]
            while len(heading_stack) < lvl - 1:
                heading_stack.append("")
            heading_stack.append(page.title)
        crumb = [h for h in heading_stack if h]
        if page.title and not page.title_level:
            crumb = crumb + [page.title]
        section_path = " › ".join(crumb) if crumb else None

        # Absatz-basiert splitten, wenn ein Absatz zu groß ist: Satz-basiert
        paragraphs = _split_paragraphs(_normalize_text(page.text))
        atomic: list[str] = []
        for p in paragraphs:
            if len(p) <= max_chars:
                atomic.append(p)
            else:
                atomic.extend(_split_sentences(p))

        # Falls immer noch Einzelteile zu groß: hart schneiden
        safe: list[str] = []
        for unit in atomic:
            if len(unit) <= max_chars:
                safe.append(unit)
            else:
                for i in range(0, len(unit), max_chars):
                    safe.append(unit[i : i + max_chars])

        page_chunks = _pack(safe, max_chars=max_chars, overlap_chars=overlap_chars)

        for text in page_chunks:
            # Zu kurze Chunks (reine Titel, leere Seiten, Grafik-Seiten ohne Text)
            # nicht indexieren — sie liefern keinen semantischen Mehrwert und
            # verschlechtern die Suchergebnisse durch falsche Keyword-Matches.
            if len(text.strip()) < _MIN_CHUNK_CHARS:
                continue
            chunks.append(
                {
                    "text": text,
                    "metadata": {
                        **base_metadata,
                        "chunk_index": chunk_idx,
                        "page": page.number,
                        "section_title": page.title,
                        "section_path": section_path,
                    },
                }
            )
            chunk_idx += 1

    return chunks
