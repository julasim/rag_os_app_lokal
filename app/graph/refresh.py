"""Voller Graph-Refresh (Track D): L1 → L2 → Analyse, in dieser Reihenfolge.

L1 legt die Nodes/Struktur-Kanten an, L2 die Ähnlichkeitskanten (setzt L1-Nodes
voraus), die Analyse rechnet Communities/PageRank/Participation über das Ganze.
Genutzt vom Admin-Endpoint `POST /api/graph/rebuild` und vom Nachtlauf.
"""
from __future__ import annotations

from graph.analyze import analyze_graph
from graph.build import build_l1
from graph.l2 import build_l2
from graph.store import invalidate_snapshot


async def refresh_graph() -> dict:
    l1 = await build_l1()
    l2 = await build_l2()
    an = await analyze_graph()
    # Der Retrieval-RAM-Snapshot ist jetzt veraltet → beim nächsten Zugriff neu laden.
    invalidate_snapshot()
    return {"l1": l1._asdict(), "l2": l2._asdict(), "analyze": an._asdict()}
