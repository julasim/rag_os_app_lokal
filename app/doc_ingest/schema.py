"""Output-Schema: Document- + Parent- + Child-Records (SPEC §8).

Wichtig (Review-Fix, Masterplan §C2b/NIEDRIG): `chunk_id` ist **inhaltsbasiert
und meta-unabhaengig** — Hash aus `doc_id + section_path + content_hash(text)`.
So bleiben Parent/Child-Referenzen stabil, wenn sich nur Metadaten aendern
(z.B. ein Ordner-Move) — nur der Payload wird ueberschrieben, die ID bricht nicht.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash(text: str) -> str:
    return "sha256:" + sha256_hex(text.encode("utf-8"))


def make_chunk_id(doc_id: str, section_path: list[str], text: str) -> str:
    """Inhaltsbasierte, meta-unabhaengige Chunk-ID (SPEC §8)."""
    joined = doc_id + "\x1f" + "/".join(section_path) + "\x1f" + content_hash(text)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def logical_id(source_path: str) -> str:
    """Stabile Dokumentidentitaet fuer Upsert — auch wenn sich die Bytes aendern."""
    return hashlib.sha1(source_path.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class Chunk:
    level: str                       # "parent" | "child"
    chunk_id: str
    doc_id: str
    parent_id: str | None
    ordinal: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    prev_id: str | None = None
    next_id: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "parent_id": self.parent_id,
            "ordinal": self.ordinal,
            "prev_id": self.prev_id,
            "next_id": self.next_id,
            "text": self.text,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class IngestResult:
    document: dict[str, Any]
    chunks: list[Chunk]
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def children(self) -> list[Chunk]:
        return [c for c in self.chunks if c.level == "child"]

    @property
    def parents(self) -> list[Chunk]:
        return [c for c in self.chunks if c.level == "parent"]

    def iter_records(self):
        """Yields document-, parent-, child-Records (SPEC §8, JSONL-tauglich)."""
        yield {"level": "document", "schema_version": SCHEMA_VERSION, **self.document}
        for c in self.chunks:
            yield c.to_record()
