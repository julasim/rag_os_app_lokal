"""
Qdrant-Punkt-Operationen, die an mehreren Stellen exakt gleich laufen müssen.

Zentral, weil hier ein subtiler, DSGVO-relevanter Fehler lauert: Die Qdrant-
Punkt-ID ist ein **Content-Hash**, NICHT die Postgres-`doc_id`. Ein
`delete_documents(document_ids=[doc_id])` läuft deshalb ins Leere — der Vektor
bleibt erhalten und weiter durchsuchbar (Löschung wirkungslos, Split-Brain
zwischen Postgres und Qdrant). Darum: erst per `meta.doc_id`-Filter die echten
Punkte holen, dann deren IDs löschen.

Alle Funktionen sind synchron/blockierend → vom Aufrufer in
`asyncio.to_thread()` aufrufen.
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select, update

from pipelines.factory import COLLECTION_NAME, get_vector_store


def delete_qdrant_chunks(doc_id: uuid.UUID) -> int:
    """
    Löscht ALLE Qdrant-Chunks eines Dokuments über den Metadaten-Filter
    `meta.doc_id` (nicht über die Punkt-ID). Gibt die Anzahl gelöschter Punkte
    zurück. Synchron — in einem Thread aufrufen.
    """
    store = get_vector_store()
    hits = store.filter_documents(
        filters={
            "operator": "AND",
            "conditions": [
                {"field": "meta.doc_id", "operator": "==", "value": str(doc_id)}
            ],
        }
    )
    point_ids = [h.id for h in hits]
    if point_ids:
        store.delete_documents(document_ids=point_ids)
    return len(point_ids)


def update_qdrant_folder(doc_id: uuid.UUID, new_folder: str) -> None:
    """
    Aktualisiert `meta.folder` + `meta.folder_path` ALLER Qdrant-Chunks eines
    Dokuments in-place (per `meta.doc_id`-Filter) — ohne Re-Embedding. `key="meta"`
    merged die beiden Felder in das bestehende `meta`-Objekt (andere Felder wie
    doc_id/file_name/norm_id bleiben erhalten). Synchron — in einem Thread aufrufen.
    """
    from qdrant_client import QdrantClient, models

    from config import settings

    client = QdrantClient(url=settings().qdrant_url, api_key=settings().qdrant_api_key)
    try:
        client.set_payload(
            collection_name=COLLECTION_NAME,
            payload={"folder": new_folder, "folder_path": new_folder},
            key="meta",
            points=models.Filter(
                must=[
                    models.FieldCondition(
                        key="meta.doc_id",
                        match=models.MatchValue(value=str(doc_id)),
                    )
                ]
            ),
            wait=True,
        )
    finally:
        client.close()


async def move_document(doc_id: uuid.UUID, new_folder: str) -> str:
    """
    Verschiebt ein Dokument **atomar** in `new_folder`: Postgres `Document.folder_path`
    UND `DocumentChunk.folder_path` UND der Qdrant-Payload (`meta.folder`/
    `meta.folder_path`). Heilt den `patch_document`-Split-Brain (früher nur Postgres,
    Qdrant-`meta.folder` blieb alt → verschobenes Doc unauffindbar).

    Transaktions-Disziplin: Postgres wird geflusht, DANN Qdrant aktualisiert; scheitert
    Qdrant, rollt der Session-Contextmanager Postgres zurück (kein halber Move). Gibt
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
        # Qdrant zuerst — bei Fehler rollt der Contextmanager Postgres zurück.
        await asyncio.to_thread(update_qdrant_folder, doc_id, nf)
    return nf
