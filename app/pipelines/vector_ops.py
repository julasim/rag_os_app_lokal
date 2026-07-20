"""
Store-Punkt-Operationen (LanceDB), die an mehreren Stellen exakt gleich laufen.

Delete + Folder-Update laufen über den `doc_id`-Spaltenfilter der `chunks`-
Tabelle (LanceDB, `pipelines/store.py`) — nicht mehr über die alte Qdrant-
Content-Hash-Punkt-ID (die ≠ `doc_id` war und ein `delete_documents([doc_id])`
ins Leere laufen ließ → Split-Brain/DSGVO). `store.delete_by_doc_id` /
`store.update_folder` sind zeilenbasiert und treffen deshalb zuverlässig.

Alle blockierenden Aufrufe → vom Aufrufer in `asyncio.to_thread()`.
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select, update


def delete_chunks(doc_id: uuid.UUID) -> int:
    """Löscht ALLE Chunks eines Dokuments aus dem Store (per `doc_id`-Spalte).
    Gibt die Anzahl gelöschter Zeilen zurück. Synchron — in einem Thread aufrufen."""
    from pipelines import store
    return store.delete_by_doc_id(doc_id)


def _update_store_folder(doc_id: uuid.UUID, new_folder: str) -> None:
    """Setzt `folder`/`folder_path` aller Chunks eines Dokuments (neue LanceDB-
    Version, kein Re-Embedding). Synchron — in einem Thread aufrufen."""
    from pipelines import store
    store.update_folder(doc_id, new_folder)


async def move_document(doc_id: uuid.UUID, new_folder: str) -> str:
    """
    Verschiebt ein Dokument **atomar** in `new_folder`: SQLite `Document.folder_path`
    UND `DocumentChunk.folder_path` UND die Store-Spalten (`folder`/`folder_path`).
    Heilt den `patch_document`-Split-Brain (früher nur SQLite, der Store-`folder`
    blieb alt → verschobenes Doc unauffindbar).

    Transaktions-Disziplin: SQLite wird geflusht, DANN der Store aktualisiert; scheitert
    der Store, rollt der Session-Contextmanager SQLite zurück (kein halber Move). Gibt
    den normalisierten Zielordner zurück. No-op, wenn schon dort.

    Gemeinsame Move-Funktion für ALLE Move-Pfade (patch_document, Track-F-Reorg/
    apply_suggestions).
    """
    from auth.folders import normalize_folder
    from db.models import Document, DocumentChunk
    from db.session import get_session

    nf = normalize_folder(new_folder)
    async with get_session() as s:
        doc = (
            await s.execute(select(Document).where(Document.id == doc_id))
        ).scalar_one_or_none()
        if doc is None:
            raise ValueError(f"document {doc_id} not found")
        if doc.folder_path == nf:
            return nf
        doc.folder_path = nf
        await s.execute(
            update(DocumentChunk)
            .where(DocumentChunk.doc_id == doc_id)
            .values(folder_path=nf)
        )
        await s.flush()
        # Store-Update — bei Fehler rollt der Contextmanager SQLite zurück.
        await asyncio.to_thread(_update_store_folder, doc_id, nf)
    return nf
