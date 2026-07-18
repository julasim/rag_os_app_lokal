"""Health-Check + System-Info + Backup + Monitoring-Metriken."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, text

from api.schemas import HealthResponse
from auth.dependencies import AuthContext, require_any_auth
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
    return HealthResponse(
        status="ok" if ok else "degraded",
        version=VERSION,
        services=services,
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
    """
    _require_admin(ctx)
    from graph.refresh import refresh_graph
    return await refresh_graph()
