"""L1-Kantenbau des Wissensgraphs (Track D, deterministisch).

Baut die **L1**-Schicht des Graphs aus der kanonischen Chunk-Schicht
(`document_chunks ⨝ documents`) — rein deterministisch (Regex + kanonische
Normalisierung), ohne Modell, ohne Re-Embed, voll reproduzierbar.

Relationen (alle L1):
  references   document → norm|legal   (Regex über Chunk-Text, `ingest/graph_refs`)
  issued_by    document → issuer       (`Document.issuer`)
  has_tag      document → tag          (`Document.tags`)
  in_folder    document → folder       (`Document.folder_path`)
  supersedes   norm → norm             (`Document.superseded_by`, nur verschiedene Normnummern)

Node-Identität ist `f"{node_type}:{canonical_key}"` (siehe `graph/canonical.py`).
Voller L1-Rebuild: alle `layer='L1'`-Kanten werden gelöscht und neu gebaut
(deterministisch, gefahrlos). Nodes werden **geupsertet** — `pagerank`,
`community_id`, `participation` bleiben dabei unangetastet (die verwaltet die
Nachtlauf-Analyse, Increment d). Orphan-Nodes räumt ebenfalls die Analyse auf.
"""
from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import Document, DocumentChunk, DocumentStatus, GraphEdge, GraphNode
from db.session import get_session
from graph.canonical import canonical_norm_id, normalize_key
from ingest.graph_refs import extract_refs
from logger import log

__all__ = ["BuildStats", "build_l1"]


class BuildStats(NamedTuple):
    documents: int
    nodes: int
    edges: int
    skipped: int


def _node_key(node_type: str, canonical_key: str) -> str:
    return f"{node_type}:{canonical_key}"


async def build_l1() -> BuildStats:
    """Baut die L1-Schicht neu und schreibt sie nach graph_nodes/graph_edges."""
    log.info("graph.build.l1.start")

    # In-Memory-Aggregat: Nodes nach node_key, Kanten als deduplizierter Set.
    nodes: dict[str, dict] = {}
    edges: set[tuple[str, str, str]] = set()
    # doc_id → norm-node-key (für supersedes-Auflösung nach dem Doc-Loop).
    doc_norm_key: dict[str, str] = {}
    # (older_norm_key, newer_doc_id) für supersedes-Nachlauf.
    supersede_pending: list[tuple[str, str]] = []
    skipped = 0

    def _touch_node(node_type: str, canonical_key: str, label: str,
                    folder: str | None = None, doc_id=None) -> str:
        key = _node_key(node_type, canonical_key)
        n = nodes.get(key)
        if n is None:
            n = {
                "node_key": key,
                "node_type": node_type,
                "canonical_key": canonical_key,
                "label": label,
                "folder_paths": set(),
                "doc_id": doc_id,
            }
            nodes[key] = n
        if folder:
            n["folder_paths"].add(folder)
        if doc_id is not None and n["doc_id"] is None:
            n["doc_id"] = doc_id
        return key

    async with get_session() as s:
        docs = (
            await s.execute(
                select(Document).where(Document.status == DocumentStatus.INDEXED.value)
            )
        ).scalars().all()

    for doc in docs:
        folder = doc.folder_path or "/"
        doc_key = _touch_node(
            "document", str(doc.id), doc.file_name or str(doc.id),
            folder=folder, doc_id=doc.id,
        )

        # in_folder
        folder_key = _touch_node("folder", folder, folder, folder=folder)
        edges.add((doc_key, folder_key, "in_folder"))

        # has_tag
        for tag in (doc.tags or []):
            tkey = normalize_key(tag)
            if not tkey:
                continue
            tag_key = _touch_node("tag", tkey, tag, folder=folder)
            edges.add((doc_key, tag_key, "has_tag"))

        # issued_by
        if doc.issuer:
            ikey = normalize_key(doc.issuer)
            if ikey:
                issuer_key = _touch_node("issuer", ikey, doc.issuer, folder=folder)
                edges.add((doc_key, issuer_key, "issued_by"))

        # eigene Norm-Identität (für supersedes)
        if doc.norm_id:
            nkey, _ver = canonical_norm_id(doc.norm_id)
            if nkey:
                own_norm_key = _touch_node("norm", nkey, doc.norm_id, folder=folder)
                doc_norm_key[str(doc.id)] = own_norm_key
                if doc.superseded_by is not None:
                    supersede_pending.append((own_norm_key, str(doc.superseded_by)))

        # references (Regex über Chunk-Text)
        async with get_session() as cs:
            chunks = (
                await cs.execute(
                    select(DocumentChunk.text)
                    .where(
                        DocumentChunk.doc_id == doc.id,
                        DocumentChunk.level == "child",
                    )
                    .order_by(DocumentChunk.ordinal)
                )
            ).scalars().all()

        for text in chunks:
            for ref in extract_refs(text or ""):
                ref_key = _touch_node(ref.kind, ref.canonical_key, ref.raw, folder=folder)
                edges.add((doc_key, ref_key, "references"))

    # supersedes: älterer Norm-Node → Norm-Node des ablösenden Docs.
    # Selbst-Kanten (gleiche Normnummer, nur andere Ausgabe → gleicher canonical
    # Node) werden übersprungen — canonical_norm_id teilt Ausgaben bewusst EINEN
    # Node zu; ein Selbst-supersedes wäre bedeutungslos.
    for older_norm_key, newer_doc_id in supersede_pending:
        newer_norm_key = doc_norm_key.get(newer_doc_id)
        if newer_norm_key and newer_norm_key != older_norm_key:
            edges.add((newer_norm_key, older_norm_key, "supersedes"))

    node_rows = [
        {
            "node_key": n["node_key"],
            "node_type": n["node_type"],
            "canonical_key": n["canonical_key"],
            "label": n["label"],
            "folder_paths": sorted(n["folder_paths"]),
            "doc_id": n["doc_id"],
        }
        for n in nodes.values()
    ]
    edge_rows = [
        {"src_key": src, "tgt_key": tgt, "relation": rel, "layer": "L1"}
        for (src, tgt, rel) in edges
    ]

    async with get_session() as s:
        # L1-Kanten voll neu (L2/Analyse-Kanten unberührt lassen).
        await s.execute(GraphEdge.__table__.delete().where(GraphEdge.layer == "L1"))

        # Nodes upserten — pagerank/community_id/participation NICHT anfassen.
        for row in node_rows:
            stmt = pg_insert(GraphNode).values(**row)
            stmt = stmt.on_conflict_do_update(
                index_elements=[GraphNode.node_key],
                set_={
                    "node_type": stmt.excluded.node_type,
                    "canonical_key": stmt.excluded.canonical_key,
                    "label": stmt.excluded.label,
                    "folder_paths": stmt.excluded.folder_paths,
                    "doc_id": stmt.excluded.doc_id,
                    "updated_at": func.now(),
                },
            )
            await s.execute(stmt)

        if edge_rows:
            await s.execute(GraphEdge.__table__.insert(), edge_rows)

    stats = BuildStats(
        documents=len(docs), nodes=len(node_rows), edges=len(edge_rows), skipped=skipped
    )
    log.info(
        "graph.build.l1.done",
        documents=stats.documents, nodes=stats.nodes,
        edges=stats.edges, skipped=stats.skipped,
    )
    return stats
