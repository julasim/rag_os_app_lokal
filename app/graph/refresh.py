"""Voller Graph-Refresh (Track D): L1 → L2 → Analyse, in dieser Reihenfolge.

L1 legt die Nodes/Struktur-Kanten an, L2 die Ähnlichkeitskanten (setzt L1-Nodes
voraus), die Analyse rechnet Communities/PageRank/Participation über das Ganze.
Genutzt vom Admin-Endpoint `POST /api/graph/rebuild` (von Julius **manuell**
ausgelöst) und vom Nachtlauf.

Zum Schluss wird der Graph als flache `graph.json` in den Vault exportiert — die
EINZIGE Lesequelle der Graph-Visualisierung (`GET /api/graph`). So sieht ein Leser
dieselbe Datei, ohne eigenen Sync/Import. Der Export ist **Writer-only**: ein Leser
hat keinen Chunk-Layer und würde sonst eine leere Datei über die gute im Vault legen.
"""
from __future__ import annotations

import json
import os

from sqlalchemy import select

from config import settings
from db.models import GraphEdge, GraphNode
from db.session import get_session
from graph.analyze import analyze_graph
from graph.build import build_l1
from graph.l2 import build_l2
from graph.store import invalidate_snapshot
from logger import log


async def _export_graph_json() -> str | None:
    """Serialisiert graph_nodes/graph_edges nach `settings().graph_json_path` (atomar).

    Writer-only (No-op auf dem Leser). Document-Nodes tragen ihren `folder` mit — die
    Basis der serverseitigen ACL beim Lesen. Rückgabe: geschriebener Pfad oder None.
    """
    if settings().is_reader:
        log.info("graph.export.skipped_reader")
        return None

    async with get_session() as s:
        nrows = (
            await s.execute(
                select(
                    GraphNode.node_key, GraphNode.node_type, GraphNode.label,
                    GraphNode.community_id, GraphNode.pagerank, GraphNode.doc_id,
                    GraphNode.folder_paths,
                )
            )
        ).all()
        erows = (
            await s.execute(
                select(
                    GraphEdge.src_key, GraphEdge.tgt_key, GraphEdge.relation, GraphEdge.w_eff,
                )
            )
        ).all()

    nodes = []
    for n in nrows:
        # Nur document-Nodes tragen einen ACL-relevanten Ordner (build_l1: genau einer).
        folder = (n.folder_paths[0] if n.folder_paths else "/") if n.node_type == "document" else None
        nodes.append({
            "node_key": n.node_key,
            "node_type": n.node_type,
            "label": n.label,
            "community_id": n.community_id,
            "pagerank": float(n.pagerank or 0.0),
            "doc_id": str(n.doc_id) if n.doc_id is not None else None,
            "folder": folder,
        })
    edges = [
        {"src_key": e.src_key, "tgt_key": e.tgt_key, "relation": e.relation, "w_eff": float(e.w_eff or 1.0)}
        for e in erows
    ]

    path = settings().graph_json_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)  # atomarer Swap — Leser sehen nie eine halb geschriebene Datei
    log.info("graph.export.done", path=str(path), nodes=len(nodes), edges=len(edges))
    return str(path)


async def refresh_graph() -> dict:
    l1 = await build_l1()
    l2 = await build_l2()
    an = await analyze_graph()
    # Der Retrieval-RAM-Snapshot ist jetzt veraltet → beim nächsten Zugriff neu laden.
    invalidate_snapshot()
    # Flache Lesequelle für die Visualisierung frisch in den Vault schreiben.
    exported = await _export_graph_json()
    return {"l1": l1._asdict(), "l2": l2._asdict(), "analyze": an._asdict(), "graph_json": exported}
