"""Community-basierte Ordner-Reorg-Vorschläge (Track F / M4).

**Deterministische Gruppierung** aus den D-Communities (`graph_nodes.community_id`);
das LLM benennt **nur** den Zielordner — und auch das nur, wenn eine Community
wirklich über mehrere Ordner verstreut ist OHNE dominanten Heimatordner. Hat die
Community einen dominanten Ordner (Anteil ≥ `reorg_dominant_folder_ratio`), ist
**DAS** das Ziel (deterministisch, minimale Bewegung, kein LLM).

Erzeugt/ersetzt die **pending**-Zeilen in `folder_suggestions`; accepted/rejected
bleiben als Historie (bereits abgelehnte identische Moves werden nicht erneut
vorgeschlagen). **Bewegt nichts** — das macht erst der Accept-Endpoint über die
atomare `move_document()` (M0.2). Keine ACL-Entscheidung hier (Systemlauf); die
ACL erzwingt der Accept-Endpoint pro Dokument.
"""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import select

from auth.folders import normalize_folder
from config import settings
from db.models import (
    Document,
    DocumentStatus,
    FolderSuggestion,
    GraphCommunity,
    GraphNode,
    MaintenanceLog,
)
from db.session import get_session
from logger import log
from pipelines.vector_ops import move_document

__all__ = [
    "ReorgStats",
    "build_folder_suggestions",
    "accept_suggestion",
    "reject_suggestion",
    "undo_folder_move",
]


class ReorgStats(NamedTuple):
    communities_considered: int
    suggestions: int


class _DocRow(NamedTuple):
    doc_id: str
    folder: str
    file_name: str
    tags: list[str]
    norm_id: str | None


def _deterministic_name(docs: list[_DocRow], fallback_label: str | None) -> str:
    """Namensableitung ohne LLM: dominanter Tag → norm-Präfix → Community-Label."""
    tags: Counter[str] = Counter()
    for d in docs:
        for t in d.tags:
            if t.strip():
                tags[t.strip()] += 1
    if tags:
        return tags.most_common(1)[0][0]
    for d in docs:
        if d.norm_id:
            # erstes Wort der Normnummer (z.B. "ÖNORM B 1801-1" → "ÖNORM")
            return d.norm_id.split()[0]
    if fallback_label:
        return fallback_label
    return "Sammlung"


async def build_folder_suggestions() -> ReorgStats:
    """Baut die pending-Ordner-Vorschläge aus den aktuellen D-Communities neu."""
    cfg = settings()
    if not cfg.reorg_enabled:
        log.info("reorg.disabled")
        return ReorgStats(0, 0)

    log.info("reorg.build.start")
    async with get_session() as s:
        rows = (
            await s.execute(
                select(
                    GraphNode.doc_id,
                    GraphNode.community_id,
                    Document.folder_path,
                    Document.file_name,
                    Document.tags,
                    Document.norm_id,
                )
                .join(Document, Document.id == GraphNode.doc_id)
                .where(
                    GraphNode.node_type == "document",
                    GraphNode.community_id.isnot(None),
                    Document.status == DocumentStatus.INDEXED.value,
                )
            )
        ).all()
        rejected = (
            await s.execute(
                select(FolderSuggestion.doc_id, FolderSuggestion.suggested_folder)
                .where(FolderSuggestion.status == "rejected")
            )
        ).all()
        comm_labels = dict(
            (await s.execute(select(GraphCommunity.community_id, GraphCommunity.label))).all()
        )

    rejected_set = {(str(did), sf) for did, sf in rejected}

    by_comm: dict[int, list[_DocRow]] = defaultdict(list)
    for doc_id, cid, folder, fname, tags, norm_id in rows:
        by_comm[cid].append(
            _DocRow(str(doc_id), normalize_folder(folder or "/"),
                    fname or str(doc_id), list(tags or []), norm_id)
        )

    considered = 0
    new_rows: list[dict] = []
    for cid, docs in by_comm.items():
        if len(docs) < cfg.reorg_min_community_docs:
            continue
        distinct = {d.folder for d in docs}
        if len(distinct) < 2:
            continue  # bereits kohärent (ein Ordner) → nichts vorzuschlagen
        considered += 1

        counts = Counter(d.folder for d in docs)
        top_folder, top_n = counts.most_common(1)[0]
        if top_n / len(docs) >= cfg.reorg_dominant_folder_ratio:
            target = top_folder
            reason = f"Community {cid}: Konsolidierung in den dominanten Ordner {target}"
        else:
            name = _deterministic_name(docs, comm_labels.get(cid))
            target = normalize_folder(name)
            reason = f"Community {cid}: verstreut auf {len(distinct)} Ordner → Sammelordner {target}"

        for d in docs:
            if d.folder == target:
                continue
            if (d.doc_id, target) in rejected_set:
                continue
            new_rows.append({
                "doc_id": d.doc_id,
                "current_folder": d.folder,
                "suggested_folder": target,
                "community_id": cid,
                "reason": reason,
                "status": "pending",
            })

    async with get_session() as s:
        await s.execute(
            FolderSuggestion.__table__.delete().where(FolderSuggestion.status == "pending")
        )
        if new_rows:
            await s.execute(FolderSuggestion.__table__.insert(), new_rows)

    stats = ReorgStats(communities_considered=considered, suggestions=len(new_rows))
    log.info("reorg.build.done", **stats._asdict())
    return stats


# ---------------------------------------------------------------------------
# Anwenden / Ablehnen / Undo (Track F / M4-2)
# ---------------------------------------------------------------------------
async def accept_suggestion(suggestion_id: uuid.UUID) -> dict | None:
    """Verschiebt das Dokument der Suggestion **atomar** (`move_document`, M0.2).

    Schreibt einen `MaintenanceLog`-Undo-Eintrag (`folder_move`, 30-Tage-Fenster)
    und setzt die Suggestion auf `accepted`. Gibt `None`, wenn die Suggestion
    nicht (mehr) `pending` ist (Doppel-Accept-Schutz). Die ACL-Prüfung macht der
    Aufrufer (Endpoint) VOR dem Aufruf — hier keine ACL-Entscheidung.
    """
    async with get_session() as s:
        sug = await s.scalar(
            select(FolderSuggestion).where(FolderSuggestion.id == suggestion_id)
        )
        if not sug or sug.status != "pending":
            return None
        doc_id = sug.doc_id
        current = sug.current_folder
        target = sug.suggested_folder

    # Atomarer Move (eigene Transaktion; SQLite+Chunks+LanceDB konsistent).
    new_folder = await move_document(doc_id, target)

    async with get_session() as s:
        sug = await s.scalar(
            select(FolderSuggestion).where(FolderSuggestion.id == suggestion_id)
        )
        if sug and sug.status == "pending":
            sug.status = "accepted"
            sug.resolved_at = datetime.now(timezone.utc)
        s.add(MaintenanceLog(
            action_type="folder_move",
            summary=f"Ordner: {current} → {new_folder} ({doc_id})",
            undo_payload={
                "doc_id": str(doc_id), "from": current, "to": new_folder,
                "suggestion_id": str(suggestion_id),
            },
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        ))
    log.info("reorg.accept", doc_id=str(doc_id), to=new_folder)
    return {"doc_id": str(doc_id), "moved_to": new_folder}


async def reject_suggestion(suggestion_id: uuid.UUID) -> bool:
    """Markiert die Suggestion als `rejected` (wird künftig nicht erneut vorgeschlagen)."""
    async with get_session() as s:
        sug = await s.scalar(
            select(FolderSuggestion).where(FolderSuggestion.id == suggestion_id)
        )
        if not sug or sug.status != "pending":
            return False
        sug.status = "rejected"
        sug.resolved_at = datetime.now(timezone.utc)
    log.info("reorg.reject", suggestion_id=str(suggestion_id))
    return True


async def undo_folder_move(log_id: uuid.UUID) -> bool:
    """Macht einen `folder_move`-Log rückgängig: verschiebt das Doc zurück (`move_document`).

    Contract wie `undo_tag_merge`: nur wenn der Log existiert, noch nicht
    angewendet wurde und vom Typ `folder_move` ist. Idempotent über `undo_applied`.
    """
    async with get_session() as s:
        entry = await s.scalar(select(MaintenanceLog).where(MaintenanceLog.id == log_id))
        if not entry or entry.undo_applied or entry.action_type != "folder_move":
            return False
        p = entry.undo_payload
        doc_id = uuid.UUID(p["doc_id"])
        back = p["from"]
        sug_id = p.get("suggestion_id")

    await move_document(doc_id, back)

    async with get_session() as s:
        entry = await s.scalar(select(MaintenanceLog).where(MaintenanceLog.id == log_id))
        if entry:
            entry.undo_applied = True
        # Die zugehörige Suggestion wieder auf pending (Move ist rückgängig).
        if sug_id:
            sug = await s.scalar(
                select(FolderSuggestion).where(FolderSuggestion.id == uuid.UUID(sug_id))
            )
            if sug and sug.status == "accepted":
                sug.status = "pending"
                sug.resolved_at = None
    log.info("reorg.undo", doc_id=str(doc_id), to=back)
    return True
