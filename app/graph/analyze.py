"""Graph-Analyse (Track D / M3d) — Louvain + PageRank + Participation.

Läuft im Nachtlauf (nach L1+L2) und schreibt die Analyse-Kennzahlen zurück in
`graph_nodes` (community_id, pagerank, participation) + `graph_communities`.

Bewusste Dep-Entscheidung: **nur networkx** (Louvain-Community-Detection) +
**numpy** (PageRank per Power-Iteration, deterministisch). **Kein scipy** — hält
das torch-freie Slim-Serving-Image schlank (M1.3). networkx' `pagerank` würde
scipy ziehen; die Power-Iteration hier ist wenige Zeilen und bei der Büro-Brain-
Korpusgröße völlig ausreichend.

Determinismus (→ reproduzierbare/idempotente Analyse):
- Louvain mit fixem `seed`.
- PageRank: feste Initialisierung + feste Knotenreihenfolge.

Richtung: PageRank auf dem **gerichteten** Graph (referenzierte Normen sammeln
In-Links → hohe PageRank = „God-Nodes"). Community-Detection + Participation auf
der **ungerichteten** Projektion (Louvain braucht ungerichtet).
"""
from __future__ import annotations

import hashlib
from typing import NamedTuple

import networkx as nx
import numpy as np
from sqlalchemy import bindparam, select

from db.models import GraphCommunity, GraphEdge, GraphNode
from db.session import get_session
from logger import log

__all__ = ["AnalyzeStats", "analyze_graph"]

_LOUVAIN_SEED = 1801


class AnalyzeStats(NamedTuple):
    nodes: int
    edges: int
    communities: int


def _pagerank(nodes: list[str], edges_dir: list[tuple[str, str, float]],
              damping: float = 0.85, iters: int = 100, tol: float = 1e-9) -> dict[str, float]:
    """Gewichtete PageRank per Power-Iteration (gerichtet, Dangling-sicher)."""
    n = len(nodes)
    if n == 0:
        return {}
    idx = {node: i for i, node in enumerate(nodes)}
    m = np.zeros((n, n), dtype=np.float64)   # m[j, i] = Masse von i nach j
    for u, v, w in edges_dir:
        m[idx[v], idx[u]] += w
    col = m.sum(axis=0)
    dangling = col == 0.0
    col[dangling] = 1.0
    m /= col
    teleport = np.full(n, 1.0 / n)
    r = teleport.copy()
    for _ in range(iters):
        dangling_mass = r[dangling].sum() if dangling.any() else 0.0
        r_new = (1.0 - damping) * teleport + damping * (m @ r + dangling_mass * teleport)
        if np.abs(r_new - r).sum() < tol:
            r = r_new
            break
        r = r_new
    return {node: float(r[idx[node]]) for node in nodes}


def _participation(g: nx.Graph, community_of: dict[str, int]) -> dict[str, float]:
    """Participation-Coefficient je Knoten: 1 - Σ_c (k_ic/k_i)² (gewichtet).

    Nahe 1 = Brücke zwischen vielen Communities, nahe 0 = tief in einer Community.
    """
    out: dict[str, float] = {}
    for node in g.nodes():
        per_comm: dict[int, float] = {}
        k_i = 0.0
        for _, nbr, data in g.edges(node, data=True):
            w = float(data.get("weight", 1.0))
            k_i += w
            c = community_of.get(nbr)
            if c is not None:
                per_comm[c] = per_comm.get(c, 0.0) + w
        if k_i <= 0.0:
            out[node] = 0.0
        else:
            out[node] = 1.0 - sum((kc / k_i) ** 2 for kc in per_comm.values())
    return out


async def analyze_graph() -> AnalyzeStats:
    """Louvain + PageRank + Participation über den aktuellen Graph, persistiert."""
    log.info("graph.analyze.start")

    async with get_session() as s:
        node_keys = (await s.execute(select(GraphNode.node_key))).scalars().all()
        edge_rows = (
            await s.execute(select(GraphEdge.src_key, GraphEdge.tgt_key, GraphEdge.w_eff))
        ).all()

    if not node_keys:
        log.info("graph.analyze.done", nodes=0, edges=0, communities=0)
        return AnalyzeStats(0, 0, 0)

    # Gerichteter Graph (PageRank) + ungerichtete Projektion (Louvain/Participation).
    g = nx.Graph()
    g.add_nodes_from(node_keys)
    edges_dir: list[tuple[str, str, float]] = []
    for src, tgt, w in edge_rows:
        w = float(w or 1.0)
        edges_dir.append((src, tgt, w))
        if g.has_edge(src, tgt):
            g[src][tgt]["weight"] += w
        else:
            g.add_edge(src, tgt, weight=w)

    # Louvain (deterministisch); isolierte Knoten bilden Ein-Knoten-Communities.
    communities = nx.community.louvain_communities(
        g, weight="weight", seed=_LOUVAIN_SEED
    )
    community_of: dict[str, int] = {}
    for cid, members in enumerate(communities):
        for node in members:
            community_of[node] = cid

    pagerank = _pagerank(list(node_keys), edges_dir)
    participation = _participation(g, community_of)

    # Community-Metadaten: Conductance (nur wenn echte Teilmenge) + Fingerprint +
    # provisorisches Label = canonical_key des PageRank-stärksten Mitglieds (kein LLM).
    n_total = g.number_of_nodes()
    comm_rows: list[dict] = []
    node_label = {}
    async with get_session() as s:
        for nk, ck in (await s.execute(select(GraphNode.node_key, GraphNode.canonical_key))).all():
            node_label[nk] = ck
    for cid, members in enumerate(communities):
        mem = set(members)
        if 0 < len(mem) < n_total:
            cond = float(nx.conductance(g, mem, weight="weight"))
        else:
            cond = None
        top = max(mem, key=lambda x: pagerank.get(x, 0.0))
        fingerprint = hashlib.sha1(
            "\x1f".join(sorted(mem)).encode("utf-8")
        ).hexdigest()
        comm_rows.append({
            "community_id": cid,
            "label": node_label.get(top),
            "conductance": cond,
            "member_fingerprint": fingerprint,
        })

    node_updates = [
        {
            "b_key": nk,
            "b_comm": community_of.get(nk),
            "b_pr": pagerank.get(nk, 0.0),
            "b_part": participation.get(nk, 0.0),
        }
        for nk in node_keys
    ]

    async with get_session() as s:
        upd = (
            GraphNode.__table__.update()
            .where(GraphNode.__table__.c.node_key == bindparam("b_key"))
            .values(
                community_id=bindparam("b_comm"),
                pagerank=bindparam("b_pr"),
                participation=bindparam("b_part"),
            )
        )
        await s.execute(upd, node_updates)
        await s.execute(GraphCommunity.__table__.delete())
        if comm_rows:
            await s.execute(GraphCommunity.__table__.insert(), comm_rows)

    stats = AnalyzeStats(
        nodes=len(node_keys), edges=len(edge_rows), communities=len(communities)
    )
    log.info("graph.analyze.done", **stats._asdict())
    return stats
