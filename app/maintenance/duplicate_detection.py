"""
Duplikat-Erkennung (Hochrisiko — Bestätigung erforderlich, CLAUDE.md §7).

Findet Dokumente mit gleichem doc_hash.
Legt DuplicateSuggestion an (status=pending) — kein automatisches Löschen.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select

from db.models import Document, DocumentStatus, DuplicateSuggestion
from db.session import get_session
from logger import log


async def detect_duplicates() -> int:
    """
    Sucht Hashes, die mehr als einmal vorkommen.
    Für jedes neue Paar (keep, remove) wird eine DuplicateSuggestion angelegt.
    Gibt Anzahl neuer Suggestions zurück.

    Gruppierung Python-seitig (SQLite kennt kein `array_agg`); Reihenfolge nach
    `uploaded_at` → ältestes Dokument je Hash = keep.
    """
    async with get_session() as s:
        rows = (
            await s.execute(
                select(Document.id, Document.doc_hash)
                .where(Document.status == DocumentStatus.INDEXED.value)
                .order_by(Document.uploaded_at.asc())
            )
        ).all()

    groups: dict[str, list] = defaultdict(list)
    for did, doc_hash in rows:
        groups[doc_hash].append(did)

    created = 0
    for doc_hash, ids in groups.items():
        if len(ids) < 2:
            continue
        keep_id = ids[0]
        for remove_id in ids[1:]:
            async with get_session() as s:
                existing = await s.scalar(
                    select(DuplicateSuggestion).where(
                        DuplicateSuggestion.doc_id_keep   == keep_id,
                        DuplicateSuggestion.doc_id_remove == remove_id,
                    )
                )
                if existing:
                    continue
                s.add(
                    DuplicateSuggestion(
                        doc_id_keep=keep_id,
                        doc_id_remove=remove_id,
                        doc_hash=doc_hash,
                        reason="exact_hash",
                    )
                )
                created += 1

    if created:
        log.info("maintenance.duplicates.found", count=created)
    return created
