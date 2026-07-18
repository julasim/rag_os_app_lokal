"""In-RAM-Snapshot des Wissensgraphs für das Retrieval (Track D / M3e).

Der Graph wird **nicht pro Request** aus Postgres gelesen, sondern als
immutabler Snapshot in den Prozess-Speicher geladen und über ein billiges
Versions-Token (Node-/Edge-Zahl + jüngste Zeitstempel) invalidiert. Zwischen
zwei Checks liegt mindestens `graph_cache_ttl` Sekunden — im Hot-Path fällt
damit i.d.R. gar kein DB-Roundtrip an.

Sicherheitskern (Track D / M3f): `visible_doc_nodes` bildet die **Schnittmenge**
der Document-Nodes mit der aufgelösten Caller-ACL (`folder_paths` aus
`auth/folders.py`). Alle Graph-Augmentierungen (Fastpath, PPR) dürfen
**ausschließlich** über diese Menge Docs an den Aufrufer zurückgeben — ein
eingeschränkter User erreicht so über keine L2-Kante (similar_to/near_dup) ein
fremdes Dokument. Entity-Nodes (norm/legal/tag/folder/issuer) sind reine
Struktur (kein Doc-Inhalt) und dürfen als Zwischenknoten dienen, werden aber nie
als Ergebnis serialisiert.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import func, select

from auth.folders import normalize_folder
from config import settings
from db.models import GraphEdge, GraphNode
from db.session import get_session
from logger import log

__all__ = ["GraphSnapshot", "get_snapshot", "invalidate_snapshot"]


@dataclass(frozen=True)
class GraphSnapshot:
    """Immutabler Graph-Snapshot. Nach dem Bau nur noch lesend genutzt."""

    version: tuple
    # node_key → node_type (document | norm | legal | tag | folder | issuer)
    node_type: dict[str, str] = field(default_factory=dict)
    # document-node_key → normalisierter Ordner des Docs (ACL-Basis)
    doc_folder: dict[str, str] = field(default_factory=dict)
    # entity-node_key → Set der referenzierenden document-node_keys (Fastpath)
    referencing_docs: dict[str, set[str]] = field(default_factory=dict)
    # ungerichtete gewichtete Adjazenz (PPR): node_key → list[(nbr, w_eff)]
    adj: dict[str, list[tuple[str, float]]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.node_type

    def doc_node_keys(self) -> set[str]:
        return set(self.doc_folder.keys())

    def visible_doc_nodes(self, folder_paths: list[str] | None) -> set[str]:
        """Schnittmenge der Document-Nodes mit der Caller-ACL (Sicherheitskern).

        `folder_paths` ist das aufgelöste ACL-Ergebnis aus `auth/folders.py`:
          * ``None``  → keine Einschränkung → **alle** Document-Nodes.
          * ``[..]``  → nur Docs, deren Ordner in dieser (bereits segment- und
            unterordner-aufgelösten) Liste liegt.

        `folder_paths == []` darf hier nicht ankommen — der Aufrufer fängt „nichts
        zugänglich" vorher ab (leere Antwort). Zur Sicherheit: leere Liste ⇒ leere
        Menge (niemals „alles").
        """
        if folder_paths is None:
            return self.doc_node_keys()
        allowed = {normalize_folder(fp) for fp in folder_paths}
        if not allowed:
            return set()
        return {nk for nk, folder in self.doc_folder.items() if folder in allowed}

    def ppr_candidate_docs(
        self,
        seed_doc_keys: list[str],
        visible_docs: set[str],
        alpha: float,
        iters: int,
        top_docs: int,
    ) -> list[str]:
        """Personalized PageRank über den **ACL-restringierten** Subgraph → doc_ids.

        Sicherheitskern (M3f): der Subgraph enthält NUR Entity-Nodes (Struktur,
        kein Doc-Inhalt) und die **sichtbaren** Document-Nodes. Nicht-sichtbare
        Docs werden komplett ausgeschlossen — PPR kann sie weder als Ergebnis
        liefern NOCH als Zwischenknoten durchlaufen. Damit ist die
        near_dup/similar_to-Sichtbarkeit die **Schnittmenge** mit der Caller-ACL
        (nicht die Vereinigung): erreicht ein sichtbares Doc über eine L2-Kante
        ein fremdes Doc, ist dessen Node gar nicht im Subgraph.

        Liefert die höchstbewerteten sichtbaren Document-Nodes (ohne die Seeds
        selbst), maximal `top_docs`, als reine doc_id-Strings.
        """
        seeds = [s for s in seed_doc_keys if s in visible_docs]
        if not seeds:
            return []

        # Erlaubter Knotenraum = Entity-Nodes ∪ sichtbare Document-Nodes.
        allowed_nodes = {
            nk for nk, ntype in self.node_type.items()
            if ntype != "document" or nk in visible_docs
        }
        # Restringierte Adjazenz (beide Endpunkte erlaubt).
        radj: dict[str, list[tuple[str, float]]] = {}
        for u in allowed_nodes:
            nbrs = [(v, w) for (v, w) in self.adj.get(u, ()) if v in allowed_nodes]
            radj[u] = nbrs

        nodes = list(allowed_nodes)
        idx = {nk: i for i, nk in enumerate(nodes)}
        n = len(nodes)
        deg = np.zeros(n, dtype=np.float64)
        for u in nodes:
            for _v, w in radj[u]:
                deg[idx[u]] += w

        p = np.zeros(n, dtype=np.float64)
        for s in seeds:
            p[idx[s]] += 1.0
        p /= p.sum()

        r = p.copy()
        for _ in range(iters):
            r_new = np.zeros(n, dtype=np.float64)
            dangling = 0.0
            for u in nodes:
                iu = idx[u]
                if deg[iu] == 0.0:
                    dangling += r[iu]
                    continue
                share = r[iu] / deg[iu]
                for v, w in radj[u]:
                    r_new[idx[v]] += share * w
            # Personalisierter Teleport (Restart + Dangling-Masse zurück auf Seeds).
            r_new = (1.0 - alpha) * (r_new + dangling * p) + alpha * p
            if np.abs(r_new - r).sum() < 1e-9:
                r = r_new
                break
            r = r_new

        seed_set = set(seeds)
        scored = [
            (nk, float(r[idx[nk]]))
            for nk in nodes
            if self.node_type.get(nk) == "document"
            and nk in visible_docs
            and nk not in seed_set
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [nk.split(":", 1)[1] for nk, _score in scored[:top_docs]]


# --- Prozess-weiter Cache mit Versions-Guard + TTL --------------------------
_snapshot: GraphSnapshot | None = None
_last_check: float = 0.0
_lock = asyncio.Lock()


async def _current_version() -> tuple:
    """Billiges Versions-Token: (n_nodes, max node.updated_at, n_edges, max edge.created_at).

    Scalar-Subqueries statt zwei Tabellen in einem FROM — sonst Kreuzprodukt
    (falsche Counts; kollabiert auf 0, sobald eine Tabelle leer ist).
    """
    async with get_session() as s:
        row = (
            await s.execute(
                select(
                    select(func.count()).select_from(GraphNode).scalar_subquery(),
                    select(func.max(GraphNode.updated_at)).scalar_subquery(),
                    select(func.count()).select_from(GraphEdge).scalar_subquery(),
                    select(func.max(GraphEdge.created_at)).scalar_subquery(),
                )
            )
        ).one()
    return (row[0], str(row[1]), row[2], str(row[3]))


async def _load(version: tuple) -> GraphSnapshot:
    """Liest Nodes + Kanten und baut den immutablen Snapshot."""
    async with get_session() as s:
        node_rows = (
            await s.execute(
                select(
                    GraphNode.node_key,
                    GraphNode.node_type,
                    GraphNode.doc_id,
                    GraphNode.folder_paths,
                )
            )
        ).all()
        edge_rows = (
            await s.execute(
                select(
                    GraphEdge.src_key,
                    GraphEdge.tgt_key,
                    GraphEdge.relation,
                    GraphEdge.w_eff,
                )
            )
        ).all()

    node_type: dict[str, str] = {}
    doc_folder: dict[str, str] = {}
    for nk, ntype, doc_id, folders in node_rows:
        node_type[nk] = ntype
        if ntype == "document":
            # document-Node trägt genau EINEN Ordner (build_l1: doc.folder_path);
            # normalisiert für den ACL-Vergleich.
            folder = folders[0] if folders else "/"
            doc_folder[nk] = normalize_folder(folder)

    referencing_docs: dict[str, set[str]] = {}
    adj: dict[str, list[tuple[str, float]]] = {}
    for src, tgt, relation, w in edge_rows:
        w = float(w or 1.0)
        # ungerichtete Adjazenz (PPR arbeitet auf der Projektion)
        adj.setdefault(src, []).append((tgt, w))
        adj.setdefault(tgt, []).append((src, w))
        # references: document → entity. Reverse-Index für den Fastpath.
        if relation == "references" and node_type.get(src) == "document":
            referencing_docs.setdefault(tgt, set()).add(src)

    snap = GraphSnapshot(
        version=version,
        node_type=node_type,
        doc_folder=doc_folder,
        referencing_docs=referencing_docs,
        adj=adj,
    )
    log.info(
        "graph.snapshot.loaded",
        nodes=len(node_type),
        documents=len(doc_folder),
        edges=len(edge_rows),
    )
    return snap


async def get_snapshot() -> GraphSnapshot:
    """Liefert den aktuellen Snapshot (lädt/aktualisiert nur bei Bedarf).

    Innerhalb `graph_cache_ttl` Sekunden wird der gecachte Snapshot ohne
    DB-Zugriff zurückgegeben. Danach wird das Versions-Token neu geprüft und nur
    bei Änderung neu geladen. Fehler beim Laden werden **laut geloggt** und als
    leerer Snapshot zurückgegeben (Retrieval fällt sauber auf den reinen
    Hybrid-Pfad zurück, statt zu crashen) — kein verschluckter Fehler.
    """
    global _snapshot, _last_check
    now = time.monotonic()
    if _snapshot is not None and (now - _last_check) < settings().graph_cache_ttl:
        return _snapshot

    async with _lock:
        now = time.monotonic()
        if _snapshot is not None and (now - _last_check) < settings().graph_cache_ttl:
            return _snapshot
        try:
            version = await _current_version()
            if _snapshot is None or _snapshot.version != version:
                _snapshot = await _load(version)
            _last_check = time.monotonic()
        except Exception as e:
            log.warning("graph.snapshot.load_failed", error=str(e))
            if _snapshot is None:
                _snapshot = GraphSnapshot(version=())
            _last_check = time.monotonic()
        return _snapshot


def invalidate_snapshot() -> None:
    """Erzwingt einen Reload beim nächsten `get_snapshot` (nach Graph-Rebuild)."""
    global _last_check
    _last_check = 0.0
