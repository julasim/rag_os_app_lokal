"""Maintenance-API: Logs, Duplikat-Vorschläge, Undo, manueller Run."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select

from auth.dependencies import AuthContext, require_any_auth
from db.models import Document, DuplicateSuggestion, FolderSuggestion, MaintenanceLog
from db.session import get_session
from maintenance.engine import run_maintenance
from maintenance.tag_consolidation import undo_tag_merge

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


def _require_admin(ctx: AuthContext) -> None:
    if ctx.is_ui and ctx.ui_user and ctx.ui_user.role == "admin":
        return
    if ctx.api_key and "admin" in ctx.api_key.scopes:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin required")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class MaintenanceLogResponse(BaseModel):
    id: UUID
    created_at: datetime
    expires_at: datetime
    action_type: str
    summary: str
    undo_applied: bool


class DuplicateSuggestionResponse(BaseModel):
    id: UUID
    created_at: datetime
    doc_id_keep: UUID
    doc_id_remove: UUID
    doc_hash: str
    reason: str
    status: str


class MaintenanceRunResponse(BaseModel):
    started_at: str
    tag_merges: int
    new_duplicate_suggestions: int


class FolderSuggestionResponse(BaseModel):
    id: UUID
    created_at: datetime
    doc_id: UUID
    current_folder: str
    suggested_folder: str
    community_id: int | None
    reason: str
    status: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post(
    "/run",
    operation_id="run_maintenance",
    response_model=MaintenanceRunResponse,
    summary="Maintenance-Lauf manuell starten (Admin)",
)
async def trigger_maintenance(
    ctx: AuthContext = Depends(require_any_auth),
):
    _require_admin(ctx)
    result = await run_maintenance()
    return MaintenanceRunResponse(**result)


@router.post(
    "/reorg/rebuild",
    operation_id="rebuild_folder_suggestions",
    summary="Ordner-Reorg-Vorschläge aus den D-Communities neu bauen (Admin)",
)
async def rebuild_reorg(
    ctx: AuthContext = Depends(require_any_auth),
):
    """
    Baut die **pending** Ordner-Reorg-Vorschläge (Track F / M4) aus den aktuellen
    Wissensgraph-Communities neu. Deterministische Gruppierung, LLM nur für
    Ordner-NAMEN. Bewegt nichts — Verschieben ist admin-bestätigt (`/accept`).
    """
    _require_admin(ctx)
    from graph.reorg import build_folder_suggestions
    return (await build_folder_suggestions())._asdict()


@router.get(
    "/suggestions/folders",
    operation_id="list_folder_suggestions",
    response_model=list[FolderSuggestionResponse],
    summary="Ausstehende Ordner-Reorg-Vorschläge (Admin)",
)
async def list_folder_suggestions(
    ctx: AuthContext = Depends(require_any_auth),
):
    _require_admin(ctx)
    async with get_session() as s:
        rows = (await s.execute(
            select(FolderSuggestion)
            .where(FolderSuggestion.status == "pending")
            .order_by(FolderSuggestion.community_id, FolderSuggestion.current_folder)
        )).scalars().all()
    return [
        FolderSuggestionResponse(
            id=r.id, created_at=r.created_at, doc_id=r.doc_id,
            current_folder=r.current_folder, suggested_folder=r.suggested_folder,
            community_id=r.community_id, reason=r.reason, status=r.status,
        )
        for r in rows
    ]


@router.post(
    "/suggestions/folders/{suggestion_id}/accept",
    operation_id="accept_folder_suggestion",
    summary="Ordner-Vorschlag anwenden — Doc verschieben (Admin, reversibel)",
)
async def accept_folder_suggestion(
    suggestion_id: UUID,
    ctx: AuthContext = Depends(require_any_auth),
):
    """
    Verschiebt das Dokument via atomarer `move_document()` (M0.2) — Postgres,
    DocumentChunk UND Qdrant konsistent. Admin-only + **per-Doc-ACL** (Quell- UND
    Zielordner). Reversibel über den `MaintenanceLog`-Undo (`folder_move`).
    """
    _require_admin(ctx)
    async with get_session() as s:
        sug = await s.scalar(
            select(FolderSuggestion).where(FolderSuggestion.id == suggestion_id)
        )
    if not sug or sug.status != "pending":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vorschlag nicht gefunden oder bereits verarbeitet")
    # Per-Doc-ACL: der Admin muss BEIDE Ordner (Quelle + Ziel) dürfen.
    if not ctx.can_access_folder(sug.current_folder) or not ctx.can_access_folder(sug.suggested_folder):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Kein Zugriff auf Quell- oder Zielordner")

    from graph.reorg import accept_suggestion
    result = await accept_suggestion(suggestion_id)
    if result is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Vorschlag zwischenzeitlich verarbeitet")
    return {"accepted": True, **result}


@router.post(
    "/suggestions/folders/{suggestion_id}/reject",
    operation_id="reject_folder_suggestion",
    summary="Ordner-Vorschlag ablehnen (Admin)",
)
async def reject_folder_suggestion(
    suggestion_id: UUID,
    ctx: AuthContext = Depends(require_any_auth),
):
    _require_admin(ctx)
    from graph.reorg import reject_suggestion
    if not await reject_suggestion(suggestion_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vorschlag nicht gefunden oder bereits verarbeitet")
    return {"rejected": True}


@router.get(
    "/log",
    operation_id="list_maintenance_log",
    response_model=list[MaintenanceLogResponse],
    summary="Letzte Wartungs-Aktionen (Admin)",
)
async def list_log(
    limit: int = 50,
    ctx: AuthContext = Depends(require_any_auth),
):
    _require_admin(ctx)
    async with get_session() as s:
        result = await s.execute(
            select(MaintenanceLog)
            .where(MaintenanceLog.expires_at > datetime.now(timezone.utc))
            .order_by(MaintenanceLog.created_at.desc())
            .limit(limit)
        )
        entries = result.scalars().all()
    return [
        MaintenanceLogResponse(
            id=e.id,
            created_at=e.created_at,
            expires_at=e.expires_at,
            action_type=e.action_type,
            summary=e.summary,
            undo_applied=e.undo_applied,
        )
        for e in entries
    ]


@router.post(
    "/log/{log_id}/undo",
    operation_id="undo_maintenance_action",
    summary="Automatische Aktion rückgängig machen (Admin)",
)
async def undo_action(
    log_id: UUID,
    ctx: AuthContext = Depends(require_any_auth),
):
    _require_admin(ctx)
    # Dispatch nach action_type: Ordner-Moves (Track F) gehen zurück über
    # move_document, Tag-Merges über array_replace.
    async with get_session() as s:
        entry = await s.scalar(select(MaintenanceLog).where(MaintenanceLog.id == log_id))
    if not entry:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Aktion unbekannt")
    if entry.action_type == "folder_move":
        from graph.reorg import undo_folder_move
        ok = await undo_folder_move(log_id)
    else:
        ok = await undo_tag_merge(log_id)
    if not ok:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Aktion nicht rückgängig machbar (bereits angewendet, abgelaufen oder unbekannt)",
        )
    return {"undone": True}


@router.get(
    "/suggestions/duplicates",
    operation_id="list_duplicate_suggestions",
    response_model=list[DuplicateSuggestionResponse],
    summary="Ausstehende Duplikat-Vorschläge (Admin)",
)
async def list_duplicates(
    ctx: AuthContext = Depends(require_any_auth),
):
    _require_admin(ctx)
    async with get_session() as s:
        result = await s.execute(
            select(DuplicateSuggestion)
            .where(DuplicateSuggestion.status == "pending")
            .order_by(DuplicateSuggestion.created_at.desc())
        )
        suggestions = result.scalars().all()
    return [
        DuplicateSuggestionResponse(
            id=s.id,
            created_at=s.created_at,
            doc_id_keep=s.doc_id_keep,
            doc_id_remove=s.doc_id_remove,
            doc_hash=s.doc_hash,
            reason=s.reason,
            status=s.status,
        )
        for s in suggestions
    ]


@router.post(
    "/suggestions/duplicates/{suggestion_id}/accept",
    operation_id="accept_duplicate_suggestion",
    summary="Duplikat löschen (Admin, irreversibel)",
)
async def accept_duplicate(
    suggestion_id: UUID,
    ctx: AuthContext = Depends(require_any_auth),
):
    _require_admin(ctx)
    async with get_session() as s:
        suggestion = await s.scalar(
            select(DuplicateSuggestion).where(DuplicateSuggestion.id == suggestion_id)
        )
        if not suggestion or suggestion.status != "pending":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Suggestion nicht gefunden oder bereits verarbeitet")

        doc = await s.scalar(
            select(Document).where(Document.id == suggestion.doc_id_remove)
        )
        if doc:
            # Qdrant-Chunks ZUERST löschen — gemeinsame Funktion wie überall
            # (`meta.doc_id`-Filter, nicht die Punkt-ID). Scheitert das, brechen
            # wir ab und lassen Postgres-Row + Datei stehen: sonst bliebe der
            # Vektor durchsuchbar, während das Doc "gelöscht" ist (Split-Brain,
            # DSGVO Art. 17) — genau der Fehler, den das alte `except`+weiterlaufen
            # verschluckte (zudem existierte `store.delete_by_filter` gar nicht).
            from pipelines.vector_ops import delete_qdrant_chunks
            try:
                await asyncio.to_thread(delete_qdrant_chunks, doc.id)
            except Exception as e:
                from logger import log
                log.error(
                    "maintenance.duplicate.qdrant_delete_failed",
                    doc_id=str(doc.id), error=str(e),
                )
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "Qdrant-Löschung fehlgeschlagen — Dokument NICHT gelöscht (kein Split-Brain)",
                ) from e

            import pathlib
            pathlib.Path(doc.file_path).unlink(missing_ok=True)
            await s.execute(delete(Document).where(Document.id == doc.id))

        suggestion.status = "accepted"
        suggestion.resolved_at = datetime.now(timezone.utc)

    return {"accepted": True, "removed_doc_id": str(suggestion.doc_id_remove)}


@router.post(
    "/suggestions/duplicates/{suggestion_id}/reject",
    operation_id="reject_duplicate_suggestion",
    summary="Duplikat-Vorschlag ablehnen (Admin)",
)
async def reject_duplicate(
    suggestion_id: UUID,
    ctx: AuthContext = Depends(require_any_auth),
):
    _require_admin(ctx)
    async with get_session() as s:
        suggestion = await s.scalar(
            select(DuplicateSuggestion).where(DuplicateSuggestion.id == suggestion_id)
        )
        if not suggestion or suggestion.status != "pending":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Suggestion nicht gefunden")
        suggestion.status = "rejected"
        suggestion.resolved_at = datetime.now(timezone.utc)

    return {"rejected": True}
