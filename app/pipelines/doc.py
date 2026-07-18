"""
Leichtgewichtiges Retrieval-Dokument — ersetzt `haystack.Document` im
Retrieval-/Rerank-Pfad (die lokale Variante trägt kein Haystack mehr).

Bietet exakt die Schnittstelle, die `pipelines/query.py:_doc_to_chunk` und
`pipelines/reranker.py` nutzen: `.content`, `.meta` (dict), `.score`, `.id`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RetrievedDoc:
    content: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    id: str | None = None
