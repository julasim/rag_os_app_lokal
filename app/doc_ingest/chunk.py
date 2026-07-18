"""Chunking: DoclingDocument -> Parent-Child-Chunks.

Child-Grain kommt vom docling-core `HybridChunker` (tokenizer-aligned, merged
kleine Elemente bis `child_tokens`). Der Parent-Child-Assembler gruppiert
aufeinanderfolgende Children mit gleichem `section_path` zu Parents (bis
`parent_tokens`). Tabellen werden als Markdown (im Text) + verlustfreies
`table_html` (in der Metadata) gefuehrt (SPEC §9.1).

Die docling-core-API variiert zwischen Versionen; deshalb defensiver Zugriff
(getattr) mit Fallbacks. Im WSL-Container gegen die echte Version verifiziert.
"""
from __future__ import annotations

import logging
from typing import Any

from .config import IngestConfig
from .schema import Chunk, make_chunk_id

_log = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4


def _est_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _serializer_provider():
    """MarkdownTableSerializer erzwingen (SPEC §9.1) — Docling-Default ist ein
    verlustbehaftetes Triplet-Textformat fuer Tabellen. Gibt None zurueck, wenn
    die API der installierten docling-core-Version abweicht (Fallback)."""
    try:
        from docling_core.transforms.chunker.hierarchical_chunker import (
            ChunkingDocSerializer,
            ChunkingSerializerProvider,
        )
        from docling_core.transforms.serializer.markdown import MarkdownTableSerializer

        class _MDTableProvider(ChunkingSerializerProvider):
            def get_serializer(self, doc):
                return ChunkingDocSerializer(
                    doc=doc, table_serializer=MarkdownTableSerializer()
                )

        return _MDTableProvider()
    except Exception:
        return None


def _build_hybrid_chunker(config: IngestConfig):
    from docling.chunking import HybridChunker

    kwargs: dict[str, Any] = {}
    prov = _serializer_provider()
    if prov is not None:
        kwargs["serializer_provider"] = prov

    # Tokenizer auf das Ziel-Embedding-Modell ausrichten; faellt bei fehlendem
    # Tokenizer (offline, nicht gebacken) auf den docling-Default zurueck.
    try:
        from docling_core.transforms.chunker.tokenizer.huggingface import (
            HuggingFaceTokenizer,
        )

        tok = HuggingFaceTokenizer.from_pretrained(
            model_name=config.tokenizer, max_tokens=config.child_tokens
        )
        return HybridChunker(tokenizer=tok, **kwargs)
    except Exception as e:
        # NICHT still verschlucken (W2): unter offline=True bedeutet ein
        # fehlender/nicht gebackener bge-m3-Tokenizer, dass die Child-Grenzen
        # NICHT bge-m3-aligned sind (Retrieval-Qualität sinkt schleichend).
        # Laut loggen, damit ein defekter Bake auffällt statt lautlos zu driften.
        _log.warning(
            "doc_ingest.tokenizer_fallback model=%s error=%s "
            "(Fallback auf docling-Default — Chunk-Grenzen nicht bge-m3-aligned)",
            config.tokenizer, e,
        )
        return HybridChunker(max_tokens=config.child_tokens, **kwargs)


def _heading_path(chunk) -> list[str]:
    meta = getattr(chunk, "meta", None)
    headings = getattr(meta, "headings", None) if meta else None
    return [str(h) for h in headings] if headings else []


def _page_and_elements(chunk, dl_doc, tables_by_ref: dict) -> tuple[int | None, list[str], str | None]:
    """Ermittelt (Seite, element_types, table_html) aus den doc_items des Chunks.

    `chunk.meta.doc_items` sind leichte `DocItem`-Refs (mit `self_ref`); das echte
    `TableItem` mit `export_to_html` liegt in `dl_doc.tables` und wird ueber
    `self_ref` aufgeloest (SPEC §9.1: verlustfreies HTML fuer Merged Cells).
    """
    meta = getattr(chunk, "meta", None)
    items = getattr(meta, "doc_items", None) if meta else None
    page: int | None = None
    etypes: list[str] = []
    table_html: str | None = None
    for it in items or []:
        label = getattr(it, "label", None)
        lv = str(getattr(label, "value", label)) if label is not None else None
        if lv and lv not in etypes:
            etypes.append(lv)
        # Seite aus der ersten Provenienz
        prov = getattr(it, "prov", None)
        if page is None and prov:
            page = getattr(prov[0], "page_no", None)
        # verlustfreies Tabellen-HTML ueber self_ref -> dl_doc.tables
        if lv == "table" and table_html is None:
            tbl = tables_by_ref.get(getattr(it, "self_ref", None))
            if tbl is not None:
                try:
                    table_html = tbl.export_to_html(doc=dl_doc)
                except Exception:
                    table_html = None
    return page, etypes, table_html


def chunk_document(dl_doc, doc_id: str, config: IngestConfig,
                   base_meta: dict[str, Any]) -> list[Chunk]:
    chunker = _build_hybrid_chunker(config)

    # self_ref -> TableItem (fuer verlustfreies table_html)
    tables_by_ref = {
        getattr(t, "self_ref", None): t
        for t in (getattr(dl_doc, "tables", None) or [])
    }

    # 1) Child-Chunks erzeugen
    raw = list(chunker.chunk(dl_doc))
    children: list[Chunk] = []
    for idx, ch in enumerate(raw):
        # kontextualisierter Text (mit Ueberschriften-Praefix), sonst roher Text
        try:
            text = chunker.contextualize(ch)
        except Exception:
            text = getattr(ch, "text", "") or ""
        if not text.strip():
            continue
        section_path = _heading_path(ch)
        page, etypes, table_html = _page_and_elements(ch, dl_doc, tables_by_ref)
        meta = {
            **base_meta,
            "section_path": section_path,
            "element_types": etypes,
            "page": page,
            "token_count": _est_tokens(text),
        }
        if table_html:
            meta["table_html"] = table_html
        children.append(Chunk(
            level="child",
            chunk_id=make_chunk_id(doc_id, section_path, text),
            doc_id=doc_id,
            parent_id=None,       # wird beim Parent-Assembly gesetzt
            ordinal=idx,
            text=text,
            metadata=meta,
        ))

    # prev/next (Reading-Order)
    for i, c in enumerate(children):
        c.prev_id = children[i - 1].chunk_id if i > 0 else None
        c.next_id = children[i + 1].chunk_id if i < len(children) - 1 else None

    # 2) Parent-Assembly: aufeinanderfolgende Children mit gleichem section_path
    #    zu Parents bis parent_tokens buendeln.
    parents: list[Chunk] = []
    group: list[Chunk] = []
    group_key: tuple[str, ...] | None = None
    group_tokens = 0

    def flush_group():
        nonlocal group, group_tokens
        if not group:
            return
        sp = list(group_key or [])
        text = "\n\n".join(c.text for c in group)
        pid = make_chunk_id(doc_id, sp + ["__parent__", str(len(parents))], text)
        parent = Chunk(
            level="parent",
            chunk_id=pid,
            doc_id=doc_id,
            parent_id=None,
            ordinal=len(parents),
            text=text,
            metadata={**base_meta, "section_path": sp,
                      "child_count": len(group), "token_count": _est_tokens(text)},
        )
        for c in group:
            c.parent_id = pid
        parents.append(parent)
        group = []
        group_tokens = 0

    for c in children:
        key = tuple(c.metadata.get("section_path") or [])
        ctok = c.metadata.get("token_count", 0)
        if group and (key != group_key or group_tokens + ctok > config.parent_tokens):
            flush_group()
        group_key = key
        group.append(c)
        group_tokens += ctok
    flush_group()

    return parents + children
