"""Health-Check + System-Info + Backup + Monitoring-Metriken."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, text

from api.schemas import GraphEdgeDTO, GraphNodeDTO, GraphResponse, HealthResponse
from auth.dependencies import AuthContext, require_any_auth
from auth.folders import key_allows_folder, user_allows_folder
from config import settings
from db.models import Document, DocumentStatus, QueryLog
from db.session import get_session

router = APIRouter(prefix="/api", tags=["system"])


def _require_admin(ctx: AuthContext) -> None:
    if ctx.is_ui and ctx.ui_user and ctx.ui_user.role == "admin":
        return
    if ctx.api_key and "admin" in ctx.api_key.scopes:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin required")

VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse)
async def health():
    services = {
        "sqlite": await _check_db(),
        "lancedb": await asyncio.to_thread(_check_lancedb),
    }
    ok = all(services.values())
    vault = settings().vault_path
    return HealthResponse(
        status="ok" if ok else "degraded",
        version=VERSION,
        services=services,
        role="reader" if settings().is_reader else "writer",
        vault_path=str(vault),
        vault_label=vault.name or str(vault),
    )


async def _check_db() -> bool:
    """Lokale appstate.sqlite erreichbar?"""
    try:
        async with get_session() as s:
            await s.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _check_lancedb() -> bool:
    """LanceDB-Wissensspeicher öffenbar? (count_rows/leere Tabelle zählt als ok)."""
    try:
        from pipelines import store
        store.count()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Monitoring-Metriken (Welle 9)
# ---------------------------------------------------------------------------
@router.get("/metrics", tags=["system"], summary="System-Metriken (Admin)")
async def metrics(ctx: AuthContext = Depends(require_any_auth)):
    _require_admin(ctx)
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    since_7d  = datetime.now(timezone.utc) - timedelta(days=7)

    async with get_session() as s:
        queries_24h = await s.scalar(
            select(func.count()).select_from(QueryLog)
            .where(QueryLog.created_at >= since_24h)
        )
        queries_7d = await s.scalar(
            select(func.count()).select_from(QueryLog)
            .where(QueryLog.created_at >= since_7d)
        )
        avg_latency = await s.scalar(
            select(func.avg(QueryLog.latency_ms))
            .where(QueryLog.created_at >= since_7d)
        )
        indexed = await s.scalar(
            select(func.count()).select_from(Document)
            .where(Document.status == DocumentStatus.INDEXED.value)
        )
        failed = await s.scalar(
            select(func.count()).select_from(Document)
            .where(Document.status == DocumentStatus.FAILED.value)
        )
        total_docs = await s.scalar(select(func.count()).select_from(Document))

    return {
        "queries_last_24h": int(queries_24h or 0),
        "queries_last_7d":  int(queries_7d or 0),
        "avg_latency_ms_7d": round(float(avg_latency or 0)),
        "documents_indexed": int(indexed or 0),
        "documents_failed":  int(failed or 0),
        "documents_total":   int(total_docs or 0),
        "ingest_success_rate": round(
            int(indexed or 0) / max(int(total_docs or 1), 1) * 100, 1
        ),
    }


# ---------------------------------------------------------------------------
# Backup-Endpoints (Welle 9)
# ---------------------------------------------------------------------------
@router.get("/backups", tags=["system"], summary="Backup-Liste (Admin)")
async def list_backups(ctx: AuthContext = Depends(require_any_auth)):
    _require_admin(ctx)
    from backup.engine import list_backup_files
    return await asyncio.to_thread(list_backup_files)


@router.post("/backups", tags=["system"], summary="Backup jetzt erstellen (Admin)")
async def create_backup(ctx: AuthContext = Depends(require_any_auth)):
    _require_admin(ctx)
    from backup.engine import run_backup
    result = await run_backup()
    return result


@router.post("/reindex-all", tags=["system"], summary="Alle Dokumente neu indexieren (Admin)")
async def reindex_all_endpoint(
    reset: bool = True,
    reparse_missing: bool = True,
    ctx: AuthContext = Depends(require_any_auth),
):
    """
    Re-indexiert alle Dokumente aus der kanonischen Postgres-Chunk-Schicht
    (`document_chunks`) — ohne Re-Parse. `reset=true` (default) legt die
    Qdrant-Collection neu an — nötig nach der Hybrid-Umstellung (Sparse-Vektor).
    `reparse_missing=true` (default) parst Dokumente OHNE kanonische Chunks
    (Pre-C2b) als Fallback voll neu; `false` überspringt sie. Kann dauern.
    """
    _require_admin(ctx)
    from ingest.pipeline import reindex_all
    return await reindex_all(reset=reset, reparse_missing=reparse_missing)


@router.post("/graph/rebuild", tags=["system"], summary="Wissensgraph neu bauen (Admin)")
async def graph_rebuild_endpoint(
    ctx: AuthContext = Depends(require_any_auth),
):
    """
    Baut den Wissensgraph (Track D) neu — **L1 → L2 → Analyse**.

    L1 (deterministisch) aus der kanonischen Chunk-Schicht: Regex-Normverweise
    (ÖNORM/EN/ISO/DIN/§) + supersedes/issued_by/has_tag/in_folder. L2 (Ähnlichkeit):
    similar_to (Doc-Zentroid-Cosine, mutual-kNN) + near_dup (MinHash-LSH). Analyse:
    Louvain-Communities + PageRank (God-Nodes) + Participation. Kein Re-Embed. L1 vor
    L2 (Nodes vor Ähnlichkeitskanten), Analyse zuletzt. Liefert alle Statistiken.

    Nur auf dem Schreiber — der Leser hat keinen Chunk-Layer (der Bau liefe leer und
    wuerde die gute `graph.json` im Vault ueberschreiben).
    """
    _require_admin(ctx)
    if settings().is_reader:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Graph-Bau nur auf dem Schreiber moeglich (Leser ist query-only).",
        )
    from graph.refresh import refresh_graph
    return await refresh_graph()


def _load_graph_json() -> dict:
    """Liest die flache `graph.json` aus dem Vault (die EINZIGE Lesequelle der
    Visualisierung; blockierendes File-IO → vom Aufrufer in `asyncio.to_thread`).

    Fehlt/kaputt die Datei → leerer Graph (kein Crash). Der Graph entsteht erst mit
    dem ersten (manuell ausgeloesten) Rebuild auf dem Schreiber."""
    path = settings().graph_json_path
    if not path.exists():
        return {"nodes": [], "edges": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — defekte Datei nie fatal, leer statt 500
        return {"nodes": [], "edges": []}


def _doc_folder_visible(ctx: AuthContext, folder: str) -> bool:
    """Darf dieser Aufrufer ein Dokument in `folder` sehen? Rein ueber die kanonischen
    Praedikate aus `auth/folders.py` (keine DB-Query → identisch auf Schreiber/Leser).

    Bearer-API-Key: leere `allowed_folders` = alles. UI/OAuth-User: fail-safe
    (leer/kein `access_all` = nichts). Segmentgrenzbewusst (kein nacktes startswith)."""
    if ctx.api_key is not None:
        return key_allows_folder(ctx.api_key.allowed_folders, folder)
    if ctx.ui_user is not None:
        u = ctx.ui_user
        return user_allows_folder(u.access_all, u.allowed_folders, folder)
    return False


@router.get("/graph", response_model=GraphResponse, tags=["system"],
            summary="Wissensgraph als Knoten + Kanten (per-User ACL)")
async def get_graph(
    ctx: AuthContext = Depends(require_any_auth),
    types: str | None = None,
    limit: int = 4000,
):
    """
    Liefert den Wissensgraphen (Track D) fuer die UI-Visualisierung, **pro Aufrufer
    ACL-gefiltert** (§13): jeder sieht nur seinen erlaubten Subgraphen.

    Sicherheitskern (Schnittmenge, nie Vereinigung): sichtbar sind nur Dokument-Nodes
    in erlaubten Ordnern; eine Entitaet (Norm/Tag/Ordner/Aussteller) bleibt nur, wenn
    sie an einem sichtbaren Doc haengt; eine Kante nur, wenn **beide** Endpunkte
    behalten werden — so offenbart keine `similar_to`/`near_dup`/`supersedes`-Kante ein
    fremdes Doc. Erst ACL, **dann** `types`-Filter + `limit` (Top-N nach PageRank).
    """
    wanted = {t.strip() for t in types.split(",") if t.strip()} if types else None
    raw = await asyncio.to_thread(_load_graph_json)
    nodes_raw = raw.get("nodes", [])
    edges_raw = raw.get("edges", [])
    node_type = {n["node_key"]: n["node_type"] for n in nodes_raw}

    # 1) Sichtbare Document-Nodes = Schnittmenge mit der Caller-ACL.
    visible_docs = {
        n["node_key"] for n in nodes_raw
        if n["node_type"] == "document" and _doc_folder_visible(ctx, n.get("folder") or "/")
    }
    # 2) Entitaeten nur behalten, wenn sie an einem sichtbaren Doc haengen.
    keep = set(visible_docs)
    for e in edges_raw:
        s_key, t_key = e["src_key"], e["tgt_key"]
        if s_key in visible_docs and node_type.get(t_key) != "document":
            keep.add(t_key)
        if t_key in visible_docs and node_type.get(s_key) != "document":
            keep.add(s_key)
    # 3) Typ-Filter (optional) — auf der ACL-Menge, nie davor.
    kept_nodes = [
        n for n in nodes_raw
        if n["node_key"] in keep and (wanted is None or n["node_type"] in wanted)
    ]
    keep = {n["node_key"] for n in kept_nodes}
    # 4) Kante nur, wenn BEIDE Endpunkte behalten werden (kein haengender Knoten, kein Leak).
    kept_edges = [e for e in edges_raw if e["src_key"] in keep and e["tgt_key"] in keep]

    # Ehrliche Gesamtzahlen VOR der Kappung (bezogen auf das ACL-/Typ-gefilterte Set).
    total_nodes, total_edges = len(kept_nodes), len(kept_edges)

    # 5) Kappung nach PageRank (Top-N), Kanten auf die gekappte Menge nachziehen.
    kept_nodes.sort(key=lambda n: n.get("pagerank") or 0.0, reverse=True)
    shown = kept_nodes[: max(1, limit)]
    shown_keys = {n["node_key"] for n in shown}
    shown_edges = [e for e in kept_edges if e["src_key"] in shown_keys and e["tgt_key"] in shown_keys]

    nodes = [
        GraphNodeDTO(
            id=n["node_key"], type=n["node_type"], label=n.get("label") or n["node_key"],
            community=n.get("community_id"), pagerank=float(n.get("pagerank") or 0.0),
            doc_id=str(n["doc_id"]) if n.get("doc_id") else None,
        )
        for n in shown
    ]
    edges = [
        GraphEdgeDTO(source=e["src_key"], target=e["tgt_key"], relation=e["relation"],
                     weight=float(e.get("w_eff") or 1.0))
        for e in shown_edges
    ]
    communities = {n.community for n in nodes if n.community is not None}
    return GraphResponse(
        nodes=nodes,
        edges=edges,
        stats={
            "nodes": len(nodes), "edges": len(edges), "communities": len(communities),
            "total_nodes": total_nodes, "total_edges": total_edges,
            "truncated": int(total_nodes > len(nodes)),
        },
    )
