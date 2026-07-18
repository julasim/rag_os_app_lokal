"""
Duplikat-Erkennung (Hochrisiko — Bestätigung erforderlich, CLAUDE.md §7).

Findet Dokumente mit gleichem doc_hash.
Legt DuplicateSuggestion an (status=pending) — kein automatisches Löschen.
"""
from __future__ import annotations

from sqlalchemy import select, text

from db.models import DuplicateSuggestion
from db.session import get_session
from logger import log


async def detect_duplicates() -> int:
    """
    Sucht Hashes, die mehr als einmal vorkommen.
    Für jedes neue Paar (keep, remove) wird eine DuplicateSuggestion angelegt.
    Gibt Anzahl neuer Suggestions zurück.
    """
    async with get_session() as s:
        result = await s.execute(
            text(
                """
                SELECT doc_hash,
                       array_agg(id       ORDER BY uploaded_at ASC) AS ids,
                       array_agg(file_name ORDER BY uploaded_at ASC) AS names
                FROM documents
                WHERE status = 'indexed'
                GROUP BY doc_hash
                HAVING count(*) > 1
                """
            )
        )
        rows = result.all()

    created = 0
    for row in rows:
        doc_hash = row.doc_hash
        ids      = row.ids
        # Ältestes Dokument = keep, alle jüngeren = potential remove
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
