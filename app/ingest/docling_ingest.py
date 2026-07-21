"""Adapter: doc_ingest (Docling, layout-aware) -> RAG-OS-Chunk-Dicts.

Bindet das `doc_ingest`-Paket am Seam `parse_file`+`chunk_document`
([pipeline.py](pipeline.py)) ein. Aktiv nur bei `ingest_backend='docling'`
(Feature-Flag in config, default 'legacy' -> Rollback jederzeit).

Der Adapter liefert dieselbe `{text, metadata}`-Chunk-Form, die
`_embed_and_store` erwartet, plus graph-ready Zusatzfelder (chunk_id/parent_id/
element_types/table_html/token_count) fuer Track C2b/D.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from doc_ingest import IngestConfig, IngestResult
from doc_ingest import ingest as _docling_ingest

# HF-Tokenizer-ID des Ziel-Embedding-Modells (multilingual-e5-large) — der
# HybridChunker budgetiert Chunks nach dessen Tokenizer, damit sie in das
# 512-Token-Fenster von e5 passen (kein Truncation beim Embedden).
_TOKENIZER = "intfloat/multilingual-e5-large"


def run_docling(path: Path) -> IngestResult:
    """Parst + chunkt eine Datei layout-bewusst (offline).

    artifacts_path + Chunk-Tokenizer kommen aus den gebündelten Modellen (der
    Schreiber-Installer legt Layout/TableFormer nach `models_dir/docling` und den
    e5-Tokenizer nach `models_dir/e5-tokenizer`). Sind sie da, lädt Docling NICHTS
    zur Laufzeit → kein HF-Download-Race, air-gapped. Fehlen sie (Dev ohne Bake),
    fällt es auf die Model-IDs zurück (First-Run-Download)."""
    from config import settings

    s = settings()
    ic = IngestConfig(
        ocr="off",              # born-digital; Scan-OCR = C1b (Modelle vorab backen)
        tokenizer=s.chunk_tokenizer_dir or _TOKENIZER,
        lang_detect=False,      # Sprache liefert metadata_extract (LLM) im RAG-Flow
        # offline=True (M8f): air-gapped. Layout/TableFormer + e5-Tokenizer sind vom
        # Installer nach %LOCALAPPDATA%\RAG-OS\models gebündelt; HF_HUB_OFFLINE/
        # TRANSFORMERS_OFFLINE (in main.py am Prozessstart gesetzt) sind die zweite
        # Absicherung. artifacts_path zeigt Docling explizit auf die lokalen Modelle.
        artifacts_path=s.docling_artifacts_dir,
        offline=True,
    )
    return _docling_ingest(path, ic)


def docling_full_text(result: IngestResult) -> str:
    """Geordnete Child-Texte -> Volltext fuer autotag/metadata_extract/suggest."""
    children = sorted(result.children, key=lambda c: c.ordinal)
    return "\n\n".join(c.text for c in children if c.text.strip())


def docling_to_chunks(result: IngestResult, base_meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Mappt Child-Records auf die RAG-Chunk-Dicts ({text, metadata}).

    section_path wird zum ' › '-String (kompatibel zum Legacy-Retrieval /
    _build_citation). Zusatzfelder (chunk_id/parent_id/element_types/table_html/
    token_count) reisen im Payload mit — Grundlage fuer C2b + Graph (Track D).
    """
    out: list[dict[str, Any]] = []
    for i, c in enumerate(sorted(result.children, key=lambda c: c.ordinal)):
        m = c.metadata
        sp = m.get("section_path") or []
        meta = {
            **base_meta,
            "chunk_index": i,
            "page": m.get("page"),
            "section_title": sp[-1] if sp else None,
            "section_path": " › ".join(sp) if sp else None,
            # graph-ready / kanonisch (Track C2b/D)
            "chunk_id": c.chunk_id,
            "parent_id": c.parent_id,
            "element_types": m.get("element_types"),
            "token_count": m.get("token_count"),
        }
        if "table_html" in m:
            meta["table_html"] = m["table_html"]
        out.append({"text": c.text, "metadata": meta})
    return out
