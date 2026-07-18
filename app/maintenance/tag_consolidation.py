"""
Tag-Konsolidierung (Niedrigrisiko — autonom, CLAUDE.md §7).

Kriterium: Levenshtein-Distanz ≤ 2 zwischen zwei Tags.
Aktion: selteneren Tag durch häufigeren ersetzen (array_replace in Postgres).
Audit: jede Aktion landet in maintenance_log mit 30-Tage-Undo-Fenster.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from db.models import Document, MaintenanceLog
from db.session import get_session
from logger import log


def _replace_in_tags(tags: list[str] | None, old: str, new: str) -> list[str]:
    """Ersetzt `old` durch `new` und dedupliziert, Reihenfolge stabil (JSON-Liste)."""
    out: list[str] = []
    for t in (tags or []):
        repl = new if t == old else t
        if repl not in out:
            out.append(repl)
    return out


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


async def consolidate_tags() -> list[uuid.UUID]:
    """
    Merged ähnliche Tags global. Gibt Liste der Log-IDs zurück.
    """
    async with get_session() as s:
        stmt = select(Document.id, Document.tags)
        rows = (await s.execute(stmt)).all()

    # Tags global sammeln: {norm_tag: [doc_id, ...]}
    tag_map: dict[str, list[uuid.UUID]] = {}
    for doc_id, tags in rows:
        for tag in (tags or []):
            norm = tag.strip().lower()
            if norm:
                tag_map.setdefault(norm, []).append(doc_id)

    log_ids: list[uuid.UUID] = []
    all_tags = list(tag_map.keys())
    merged: set[str] = set()

    for i, tag_a in enumerate(all_tags):
        if tag_a in merged:
            continue
        for tag_b in all_tags[i + 1:]:
            if tag_b in merged or tag_a == tag_b:
                continue
            if _levenshtein(tag_a, tag_b) > 2:
                continue

            freq_a = len(tag_map[tag_a])
            freq_b = len(tag_map[tag_b])
            winner, loser = (tag_a, tag_b) if freq_a >= freq_b else (tag_b, tag_a)

            lid = await _merge(winner, loser)
            if lid:
                log_ids.append(lid)
                merged.add(loser)

    return log_ids


async def _merge(winner: str, loser: str) -> uuid.UUID | None:
    async with get_session() as s:
        # Betroffene Docs Python-seitig finden (tags ist JSON, kein ARRAY → kein
        # `.contains`/`ANY` auf SQLite) und die Tag-Liste neu schreiben.
        rows = (await s.execute(select(Document.id, Document.tags))).all()
        affected = [did for did, tags in rows if loser in (tags or [])]
        if not affected:
            return None

        for did in affected:
            doc = await s.get(Document, did)
            doc.tags = _replace_in_tags(doc.tags, loser, winner)  # Neuzuweisung → JSON-dirty

        log_entry = MaintenanceLog(
            action_type="tag_merge",
            summary=f"Tag '{loser}' → '{winner}' ({len(affected)} Dok.)",
            undo_payload={
                "winner": winner,
                "loser": loser,
                "doc_ids": [str(d) for d in affected],
            },
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        s.add(log_entry)
        await s.flush()
        lid = log_entry.id

    log.info(
        "maintenance.tag_merge",
        winner=winner,
        loser=loser,
        docs=len(affected),
    )
    return lid


async def undo_tag_merge(log_id: uuid.UUID) -> bool:
    async with get_session() as s:
        entry = await s.scalar(
            select(MaintenanceLog).where(MaintenanceLog.id == log_id)
        )
        if not entry or entry.undo_applied or entry.action_type != "tag_merge":
            return False

        p = entry.undo_payload
        ids = {uuid.UUID(x) if not isinstance(x, uuid.UUID) else x for x in p["doc_ids"]}
        for did in ids:
            doc = await s.get(Document, did)
            if doc and p["winner"] in (doc.tags or []):
                doc.tags = _replace_in_tags(doc.tags, p["winner"], p["loser"])
        entry.undo_applied = True

    log.info("maintenance.tag_merge.undone", log_id=str(log_id))
    return True
