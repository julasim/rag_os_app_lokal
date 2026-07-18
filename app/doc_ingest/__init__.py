"""doc_ingest — layout-aware Parsing/Chunking-Layer (Docling-basiert).

Public API:
    from doc_ingest import ingest, ingest_batch, IngestConfig, IngestResult

`ingest(path, config)` parst eine Datei layout-bewusst (Tabellen als Markdown +
verlustfreies HTML), chunkt Parent-Child und liefert ein `IngestResult` mit
document- + parent- + child-Records (SPEC §8). Der RAG-Adapter
(`app/ingest/docling_ingest.py`, Track C2) mappt die Child-Records auf die
bestehende `{text, metadata}`-Chunk-Form.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

from .config import IngestConfig
from .convert import convert
from .chunk import chunk_document
from .schema import Chunk, IngestResult, logical_id, sha256_hex

__all__ = ["ingest", "ingest_batch", "IngestConfig", "IngestResult", "Chunk"]


def _parser_version() -> str:
    try:
        from importlib.metadata import version
        return f"docling=={version('docling')}+core=={version('docling-core')}"
    except Exception:
        return "docling==unknown"


def _detect_language(text: str) -> tuple[str | None, float | None]:
    try:
        import py3langid
        lang, score = py3langid.classify(text[:4000])
        return lang, float(score)
    except Exception:
        return None, None


def _doc_title(dl_doc, fallback: str) -> str:
    for attr in ("name", "title"):
        v = getattr(dl_doc, attr, None)
        if v:
            return str(v)
    return fallback


def _num_pages(dl_doc) -> int | None:
    for attr in ("num_pages",):
        v = getattr(dl_doc, attr, None)
        if callable(v):
            try:
                return int(v())
            except Exception:
                pass
        elif v is not None:
            return int(v)
    pages = getattr(dl_doc, "pages", None)
    return len(pages) if pages else None


def ingest(path: str | Path, config: IngestConfig | None = None) -> IngestResult:
    config = config or IngestConfig()
    p = Path(path)
    content = p.read_bytes()
    doc_id = sha256_hex(content)

    dl_doc = convert(p, config)

    # Volltext fuer Sprach-Detektion (und spaeter Autotag/Metadaten im Adapter)
    try:
        full_text = dl_doc.export_to_markdown()
    except Exception:
        full_text = ""

    language, lang_conf = (_detect_language(full_text) if config.lang_detect else (None, None))
    mimetype = mimetypes.guess_type(p.name)[0]

    base_meta = {
        "source": p.name,
        "mimetype": mimetype,
        "source_type": p.suffix.lstrip(".").lower() or "unknown",
    }
    chunks = chunk_document(dl_doc, doc_id, config, base_meta)

    n_children = sum(1 for c in chunks if c.level == "child")
    n_parents = sum(1 for c in chunks if c.level == "parent")

    document = {
        "doc_id": doc_id,
        "logical_id": logical_id(str(p)),
        "parser_version": _parser_version(),
        "metadata": {
            "source": p.name,
            "mimetype": mimetype,
            "doc_title": _doc_title(dl_doc, p.stem),
            "num_pages": _num_pages(dl_doc),
            "language": language,
            "language_confidence": lang_conf,
        },
    }
    report = {
        "children": n_children,
        "parents": n_parents,
        "chars": len(full_text),
        "warnings": _quality_warnings(chunks),
    }
    return IngestResult(document=document, chunks=chunks, report=report)


def _quality_warnings(chunks: list[Chunk]) -> list[str]:
    """Minimaler Quality-Gate (SPEC §9.3): Tabelle erkannt, aber Text leer."""
    warnings: list[str] = []
    for c in chunks:
        et = c.metadata.get("element_types") or []
        if "table" in et and not c.text.strip():
            warnings.append(f"leere Tabelle in chunk {c.chunk_id[:8]}")
    return warnings


def ingest_batch(paths, config: IngestConfig | None = None):
    """Mehrere Dateien; einzelne Fehler crashen den Batch nicht (SPEC §10.4)."""
    out = []
    for pth in paths:
        try:
            out.append(ingest(pth, config))
        except Exception as exc:  # pro-Datei-Fehler isolieren
            out.append(IngestResult(
                document={"source": str(pth), "error": str(exc)},
                chunks=[], report={"error": str(exc)}))
    return out
