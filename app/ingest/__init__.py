"""Ingest: Datei → geparster Text → Chunks → LanceDB.

Re-Exports laufen LAZY über ``__getattr__`` (Dep-Severance C3b): das bloße
``import ingest`` (Package-Init) zieht damit NICHT mehr ``ingest.pipeline`` bzw.
``ingest.watcher`` — und deren schwere torch/docling-Last — in den Prozess. Der
rag-api-Serving-Prozess bleibt so ingest-frei; nur der rag-ingest-Worker lädt die
Pipeline. Produktive Aufrufer importieren ohnehin direkt aus dem Submodul
(``from ingest.pipeline import ingest_file`` etc.); die Top-Level-Symbole hier
bleiben als bequemer, aber lazy aufgelöster Zugang erhalten.
"""
from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "FolderWatcher",
    "ParsedDocument",
    "ParsedPage",
    "chunk_document",
    "ingest_file",
    "parse_file",
]

# Symbol → (Submodul, Attribut). Import erst bei tatsächlichem Zugriff.
_LAZY: dict[str, tuple[str, str]] = {
    "FolderWatcher": ("ingest.watcher", "FolderWatcher"),
    "ParsedDocument": ("ingest.parsers", "ParsedDocument"),
    "ParsedPage": ("ingest.parsers", "ParsedPage"),
    "parse_file": ("ingest.parsers", "parse_file"),
    "chunk_document": ("ingest.chunker", "chunk_document"),
    "ingest_file": ("ingest.pipeline", "ingest_file"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module 'ingest' has no attribute {name!r}") from None
    return getattr(importlib.import_module(module_name), attr)
