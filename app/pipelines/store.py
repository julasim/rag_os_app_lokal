"""
Vektor-Store — LanceDB (der EINZIGE Wissensspeicher, ersetzt Qdrant + Haystack).

Eine Tabelle `chunks`: dense-Vektor (bge-m3, 1024) + `text` (für FTS/BM25) +
die frühere Qdrant-Payload als Spalten. Retrieval = Hybrid (dense + FTS + RRF)
+ exaktes `norm_id`-WHERE. Metadaten-Quelle-der-Wahrheit bleibt (vorerst) die
SQLite `documents`/`document_chunks` — der Store liefert Kandidaten + Payload.

Alle Aufrufe sind synchron/blockierend → vom Aufrufer in `asyncio.to_thread()`.
"""
from __future__ import annotations

import os
import threading
from functools import lru_cache
from typing import Any

import lancedb
import pyarrow as pa

from config import settings
from logger import log
from pipelines.doc import RetrievedDoc

TABLE = "chunks"

# Payload-Spalten (frühere Qdrant meta.*), die der Store zurückliefert.
_META_COLS = (
    "doc_id", "file_name", "folder", "folder_path", "page",
    "section_title", "section_path", "doc_type", "norm_id",
    "doc_version", "language", "tags",
)

# Kanonisches, stabiles Tabellen-Schema (alle Writes projizieren darauf → keine
# pyarrow-Typ-Inferenz aus dem ersten Batch, keine Schema-Drift bei `add`).
_COLS = (
    "point_id", "doc_id", "file_name", "folder", "folder_path", "text", "vector",
    "page", "section_title", "section_path", "doc_type", "norm_id",
    "doc_version", "language", "tags", "chunk_id", "table_html",
)


def _arrow_schema(vector_dim: int) -> pa.Schema:
    """Explizites Arrow-Schema; `vector`-Dimension aus den Daten (bge-m3=1024,
    Test-Modell bge-small=384) → modellunabhängig."""
    return pa.schema([
        ("point_id", pa.string()),
        ("doc_id", pa.string()),
        ("file_name", pa.string()),
        ("folder", pa.string()),
        ("folder_path", pa.string()),
        ("text", pa.string()),
        ("vector", pa.list_(pa.float32(), vector_dim)),
        ("page", pa.int64()),
        ("section_title", pa.string()),
        ("section_path", pa.string()),
        ("doc_type", pa.string()),
        ("norm_id", pa.string()),
        ("doc_version", pa.string()),
        ("language", pa.string()),
        ("tags", pa.list_(pa.string())),
        ("chunk_id", pa.string()),
        ("table_html", pa.string()),
    ])

_lock = threading.Lock()


@lru_cache(maxsize=1)
def _db():
    os.makedirs(settings().ragos_dir, exist_ok=True)
    return lancedb.connect(settings().lancedb_uri)


def _open():
    """Öffnet die chunks-Tabelle oder None, wenn noch nicht angelegt."""
    db = _db()
    if TABLE not in db.table_names():
        return None
    return db.open_table(TABLE)


def _ensure_fts(tbl) -> None:
    """FTS/BM25-Index auf `text` sicherstellen (idempotent)."""
    try:
        tbl.create_fts_index("text", use_tantivy=False, replace=True)
    except Exception as e:  # noqa: BLE001
        log.warning("store.fts_index_failed", error=str(e))


# ---------------------------------------------------------------------------
# Filter-Übersetzung: Haystack/Qdrant-Filter-Dict → LanceDB-SQL-WHERE
# ---------------------------------------------------------------------------
def _sql_literal(v: Any) -> str:
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def build_where(filters: dict | None) -> str | None:
    """Übersetzt die bisherigen Qdrant-Filter-Dicts in einen LanceDB-WHERE-String.

    Erwartet {"operator":"AND","conditions":[{"field":"meta.folder","operator":"in",
    "value":[...]}, ...]}. `meta.X` → Spalte `X`. Unterstützt ==, in.
    """
    if not filters:
        return None
    conds = filters.get("conditions") or []
    parts: list[str] = []
    for c in conds:
        field = str(c["field"]).removeprefix("meta.")
        op = c["operator"]
        val = c["value"]
        if op == "in":
            if not val:
                continue
            joined = ", ".join(_sql_literal(x) for x in val)
            parts.append(f"{field} IN ({joined})")
        elif op == "==":
            parts.append(f"{field} = {_sql_literal(val)}")
        else:
            log.warning("store.filter_op_unsupported", op=op)
    if not parts:
        return None
    glue = " AND " if filters.get("operator", "AND").upper() == "AND" else " OR "
    return glue.join(parts)


# ---------------------------------------------------------------------------
# Zeile → RetrievedDoc
# ---------------------------------------------------------------------------
def _row_to_doc(row: dict) -> RetrievedDoc:
    meta = {k: row.get(k) for k in _META_COLS}
    meta["tags"] = list(meta.get("tags") or [])
    score = row.get("_relevance_score", row.get("_score", row.get("_distance")))
    return RetrievedDoc(
        content=row.get("text") or "",
        meta=meta,
        score=float(score) if score is not None else None,
        id=row.get("point_id"),
    )


# ---------------------------------------------------------------------------
# Öffentliche API (synchron)
# ---------------------------------------------------------------------------
def write(rows: list[dict]) -> int:
    """Schreibt Chunk-Zeilen (jede mit `vector`, `text`, `point_id` + Payload).

    Projiziert jede Zeile auf `_COLS` (fehlende Keys → null) und schreibt mit
    explizitem Arrow-Schema → stabil über alle Batches (`add` bricht sonst bei
    Typ-/Spalten-Drift).
    """
    if not rows:
        return 0
    db = _db()
    dim = len(rows[0]["vector"])
    clean = [{k: r.get(k) for k in _COLS} for r in rows]
    data = pa.Table.from_pylist(clean, schema=_arrow_schema(dim))
    with _lock:
        if TABLE not in db.table_names():
            tbl = db.create_table(TABLE, data=data)
        else:
            tbl = db.open_table(TABLE)
            tbl.add(data)
        _ensure_fts(tbl)
    return len(rows)


def search_hybrid(
    query_text: str,
    query_vector: list[float],
    top_k: int,
    filters: dict | None = None,
    hybrid: bool = True,
) -> list[RetrievedDoc]:
    """Hybrid (dense + FTS + RRF) bzw. reiner dense-Pfad. Gibt RetrievedDoc-Liste."""
    tbl = _open()
    if tbl is None:
        return []
    where = build_where(filters)
    try:
        if hybrid and query_text.strip():
            from lancedb.rerankers import RRFReranker
            q = (tbl.search(query_type="hybrid")
                 .vector(query_vector).text(query_text)
                 .rerank(RRFReranker()))
        else:
            q = tbl.search(query_vector)
        if where:
            q = q.where(where, prefilter=True)
        rows = q.limit(top_k).to_list()
        return [_row_to_doc(r) for r in rows]
    except Exception as e:  # noqa: BLE001 — Store-Fehler darf Retrieval nicht killen
        log.warning("store.search_failed", error=str(e))
        return []


def filter_by_meta(filters: dict | None, limit: int = 10000) -> list[RetrievedDoc]:
    """Reiner Metadaten-Filter (ersetzt Qdrant `filter_documents`)."""
    tbl = _open()
    if tbl is None:
        return []
    where = build_where(filters)
    q = tbl.search()
    if where:
        q = q.where(where)
    return [_row_to_doc(r) for r in q.limit(limit).to_list()]


def delete_by_doc_id(doc_id) -> int:
    """Löscht ALLE Chunks eines Dokuments (per `doc_id`-Spalte)."""
    tbl = _open()
    if tbl is None:
        return 0
    did = str(doc_id).replace("'", "''")
    before = tbl.count_rows(f"doc_id = '{did}'")
    if before:
        tbl.delete(f"doc_id = '{did}'")
    return before


def update_folder(doc_id, new_folder: str) -> int:
    """Setzt `folder`/`folder_path` ALLER Chunks eines Dokuments (per `doc_id`).

    LanceDB-`update` = neue immutable Version (MVCC), kein Re-Embedding, kein
    in-place-Payload-Patch wie früher bei Qdrant. Gibt die Zeilenzahl zurück.
    """
    tbl = _open()
    if tbl is None:
        return 0
    did = str(doc_id).replace("'", "''")
    n = tbl.count_rows(f"doc_id = '{did}'")
    if n:
        tbl.update(where=f"doc_id = '{did}'",
                   values={"folder": new_folder, "folder_path": new_folder})
    return n


def scan_dense_vectors():
    """Iteriert (doc_id, vector) über alle Chunks — für graph/l2 (Doc-Zentroide)."""
    tbl = _open()
    if tbl is None:
        return
    for r in tbl.search().select(["doc_id", "vector"]).limit(10_000_000).to_list():
        yield r["doc_id"], r["vector"]


def count() -> int:
    tbl = _open()
    return tbl.count_rows() if tbl is not None else 0


def reset() -> None:
    """Löscht die Collection (destruktiv) — für Reindex."""
    db = _db()
    if TABLE in db.table_names():
        db.drop_table(TABLE)
    log.info("store.reset")
