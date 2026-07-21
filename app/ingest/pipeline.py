"""
High-Level Ingest-Pipeline.

Orchestriert den gesamten Weg Datei → LanceDB:
  1. Hash berechnen, Duplikat-Check
  2. Datei in <uploads>/<folder>/ ablegen
  3. Parsen (docling/legacy)
  4. Chunken
  5. Embedden (INT8-ONNX e5-large)
  6. Chunks kanonisch nach SQLite + Zeilen nach LanceDB (`chunks`)
  7. Status aktualisieren
"""
from __future__ import annotations

import asyncio
import hashlib
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import global_config, settings
from db.models import Document, DocumentStatus, IngestJob
from db.session import get_session
from ingest.chunker import chunk_document
from ingest.parsers import parse_file
from logger import log
from pipelines.vector_ops import delete_chunks


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_folder(folder_path: str) -> str:
    f = folder_path.strip()
    if not f.startswith("/"):
        f = "/" + f
    if not f.endswith("/"):
        f = f + "/"
    # doppelte Slashes killen
    while "//" in f:
        f = f.replace("//", "/")
    return f


def _target_path(folder_path: str, file_name: str) -> Path:
    folder = _normalize_folder(folder_path).strip("/")
    base = settings().upload_dir
    target_dir = base / folder if folder else base
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / file_name


# ---------------------------------------------------------------------------
# Haupt-Einstieg
# ---------------------------------------------------------------------------
async def reindex_document_file(
    doc_id: UUID,
    path: Path,
    folder_path: str,
    tags: list[str],
) -> None:
    """Re-runs the ingest job for an existing document after clearing its LanceDB chunks."""
    # Alte Chunks des Dokuments per doc_id aus LanceDB löschen (Orphan-Schutz
    # gegen Split-Brain/DSGVO). Siehe pipelines/vector_ops.py.
    try:
        await asyncio.to_thread(delete_chunks, doc_id)
    except Exception as exc:
        log.warning("reindex.chunks_clear_failed", doc_id=str(doc_id), error=str(exc))
    pcfg = global_config()
    await _run_ingest_job(doc_id, path, folder_path, tags, pcfg)


def _chunks_from_rows(doc: Document, rows: list) -> list[dict]:
    """Rekonstruiert die `{text, metadata}`-Chunk-Dicts aus der kanonischen
    SQLite-Chunk-Schicht — im EXAKTEN Ingest-Payload-Format.

    Parität zum Normal-Ingest ist zwingend: die LanceDB-Payloads müssen identisch
    zu denen aus `_run_ingest_job` (`base_meta`) + `docling_to_chunks` sein.
    Insbesondere:

    - `folder`/`folder_path` kommen PRO CHUNK aus `row.folder_path` (autoritativ,
      von `move_document` gepflegt) — NICHT aus `Document`. `meta.folder` ist der
      ACL-Filter im Retrieval; nimmt man den falschen Wert, bricht die Ordner-ACL.
    - `section_title` wird aus dem letzten `section_path`-Segment (" › ")
      rekonstruiert (wird nicht separat in `DocumentChunk` gehalten).
    - `tags/doc_type/norm_id/doc_version/language` leben auf `Document` (JOIN).
    """
    source_type = Path(doc.file_name).suffix.lstrip(".").lower() or "unknown"
    tags = list(doc.tags or [])
    out: list[dict] = []
    for row in rows:
        section_path = row.section_path
        section_title = section_path.split(" › ")[-1] if section_path else None
        meta = {
            # --- Basis (aus Document) ---
            "doc_id": str(doc.id),
            "folder_path": row.folder_path,
            "folder": row.folder_path,  # ACL-Filter-Alias — MUSS aus dem Chunk kommen
            "file_name": doc.file_name,
            "tags": tags,
            "source_type": source_type,
            "doc_type": doc.doc_type,
            "norm_id": doc.norm_id,
            "doc_version": doc.doc_version,
            "language": doc.language,
            # --- pro Chunk (aus DocumentChunk) ---
            "chunk_index": row.ordinal,
            "page": row.page,
            "section_title": section_title,
            "section_path": section_path,
            "chunk_id": row.chunk_id,
            "parent_id": row.parent_id,
            "element_types": row.element_types,
            "token_count": row.token_count,
        }
        if row.table_html is not None:
            meta["table_html"] = row.table_html
        out.append({"text": row.text, "metadata": meta})
    return out


async def reindex_all(reset: bool = True, reparse_missing: bool = True) -> dict:
    """
    Baut den LanceDB-Index aus der kanonischen SQLite-Chunk-Schicht
    (`document_chunks ⨝ documents`) neu auf — **ohne Re-Parse** (nur
    Re-Embedding). SQLite bleibt Source-of-Truth; LanceDB ist der abgeleitete
    Index. Das entkoppelt Reindex von den schweren Parse-Deps (Docling/PyMuPDF).

    Bei `reset=True` wird die `chunks`-Tabelle einmal neu angelegt (z.B. nach
    einem Embedding-Modell-/Dimensions-Wechsel). Bei `reset=False` werden pro
    Dokument erst die alten Chunks per `doc_id` gelöscht (Orphan-Schutz,
    idempotent), dann neu geschrieben.

    Dokumente OHNE kanonische Chunks (Pre-C2b-Ingest): mit `reparse_missing=True`
    (default) fällt der Lauf für genau diese Dokumente auf den vollen Re-Parse
    zurück (`reindex_document_file`), sonst werden sie übersprungen. Chunks werden
    NIE still gelöscht.
    """
    from db.models import DocumentChunk
    from pipelines.factory import reset_collection

    embed_model = global_config().embed_model

    if reset:
        await asyncio.to_thread(reset_collection)

    async with get_session() as s:
        result = await s.execute(
            select(Document).where(Document.status == DocumentStatus.INDEXED.value)
        )
        docs = result.scalars().all()

    total = len(docs)
    from_chunks = reparsed = skipped = failed = 0

    for doc in docs:
        # Kanonische Child-Chunks in Ingest-Reihenfolge laden (nur lesend).
        async with get_session() as s:
            rows = (
                await s.execute(
                    select(DocumentChunk)
                    .where(
                        DocumentChunk.doc_id == doc.id,
                        DocumentChunk.level == "child",
                    )
                    .order_by(DocumentChunk.ordinal)
                )
            ).scalars().all()

        if not rows:
            # Pre-C2b-Doc: keine kanonischen Chunks → nur hier ist Re-Parse nötig.
            src = Path(doc.file_path)
            if reparse_missing and src.exists():
                try:
                    await reindex_document_file(
                        doc_id=doc.id,
                        path=src,
                        folder_path=doc.folder_path,
                        tags=list(doc.tags or []),
                    )
                    # M1 (Review): reindex_document_file → _run_ingest_job fängt
                    # Fehler INTERN ab und re-raised NICHT (setzt nur status=failed).
                    # Ohne Nachladen zählte ein Reparse-Fehler (z.B. ImportError im
                    # Slim-Image) fälschlich als reparsed. Ehrlich am frischen Status.
                    async with get_session() as s:
                        st = (
                            await s.execute(
                                select(Document.status).where(Document.id == doc.id)
                            )
                        ).scalar_one_or_none()
                    if st == DocumentStatus.FAILED.value:
                        log.warning("reindex_all.reparse_failed", doc_id=str(doc.id))
                        failed += 1
                    else:
                        reparsed += 1
                except Exception as e:
                    # Harter Fehler (Exception dringt doch durch) → failed.
                    log.warning(
                        "reindex_all.reparse_failed", doc_id=str(doc.id), error=str(e)
                    )
                    failed += 1
            else:
                log.warning(
                    "reindex_all.skipped_no_chunks",
                    doc_id=str(doc.id),
                    reparse_missing=reparse_missing,
                    file_exists=src.exists(),
                )
                skipped += 1
            continue

        # From-chunks-Pfad: KEIN Re-Parse — nur Re-Embedding aus SQLite.
        try:
            chunks = _chunks_from_rows(doc, rows)
            if not reset:
                # Orphan-Schutz: alte Punkte dieses Docs gezielt entfernen
                # (bei reset ist die ganze Collection ohnehin frisch).
                await asyncio.to_thread(delete_chunks, doc.id)
            await _embed_and_store(chunks, embed_model)
            from_chunks += 1
        except Exception as e:
            log.warning(
                "reindex_all.from_chunks_failed", doc_id=str(doc.id), error=str(e)
            )
            failed += 1

    reindexed = from_chunks + reparsed
    log.info(
        "reindex_all.done",
        total=total,
        reindexed=reindexed,
        from_chunks=from_chunks,
        reparsed=reparsed,
        skipped=skipped,
        failed=failed,
    )
    return {
        "total": total,
        "reindexed": reindexed,
        "from_chunks": from_chunks,
        "reparsed": reparsed,
        "skipped": skipped,
        "failed": failed,
    }


async def ingest_file(
    src_path: Path,
    folder_path: str = "/",
    tags: list[str] | None = None,
    uploaded_by: UUID | None = None,
    keep_source: bool = True,
    original_filename: str | None = None,
) -> UUID:
    """
    Nimmt eine Datei, indexiert sie in LanceDB, schreibt SQLite-Eintrag.
    Gibt die Document-UUID zurück.

    Ist idempotent bzgl. doc_hash: bei Duplikat wird die
    existierende doc_id zurückgegeben.

    `original_filename` überschreibt den Namen, unter dem die Datei auf Disk
    abgelegt wird — wichtig für REST-Uploads, bei denen src_path eine
    anonyme Temp-Datei ist.
    """
    tags = tags or []
    folder_path = _normalize_folder(folder_path)

    cfg = global_config()

    # --- Datei-Größen-Limit ---
    size = src_path.stat().st_size
    if size > cfg.limits.max_file_mb * 1024 * 1024:
        raise ValueError(
            f"File too large ({size / 1024 / 1024:.1f} MB > "
            f"{cfg.limits.max_file_mb} MB)"
        )

    # --- Hash + Duplikat-Check (pro Ordner) ---
    doc_hash = _sha256_of(src_path)
    async with get_session() as s:
        existing = await _find_existing(s, doc_hash, folder_path)
        if existing:
            log.info("ingest.duplicate", doc_id=str(existing.id), folder=folder_path)
            return existing.id

    # --- Datei an Zielort kopieren/verschieben ---
    # Original-Dateiname bevorzugen (REST-Upload), sonst src_path (Folder-Watcher)
    target_filename = original_filename or src_path.name
    target = _target_path(folder_path, target_filename)
    try:
        src_resolved = src_path.resolve()
        tgt_resolved = target.resolve()
    except FileNotFoundError:
        src_resolved, tgt_resolved = src_path, target

    if src_resolved == tgt_resolved:
        # Datei liegt bereits am Zielort (typisch für Folder-Watcher).
        # Kein Kopieren nötig — sonst SameFileError.
        log.info("ingest.source_is_target", path=str(target))
    elif keep_source:
        shutil.copy2(src_path, target)
    else:
        shutil.move(str(src_path), target)

    # --- Document-Row anlegen ---
    doc_id = uuid.uuid4()
    async with get_session() as s:
        s.add(
            Document(
                id=doc_id,
                doc_hash=doc_hash,
                folder_path=folder_path,
                file_name=target.name,
                file_path=str(target),
                mime_type=None,  # wird nach Parsing gesetzt
                size_bytes=size,
                tags=tags,
                status=DocumentStatus.QUEUED.value,
                uploaded_by=uploaded_by,
            )
        )

    # --- Ingestion-Job starten ---
    await _run_ingest_job(doc_id, target, folder_path, tags, cfg)
    return doc_id


# ---------------------------------------------------------------------------
# Interne Arbeit
# ---------------------------------------------------------------------------
async def _find_existing(
    s: AsyncSession, doc_hash: str, folder_path: str
) -> Document | None:
    # Duplikat NUR innerhalb desselben Ordners — gleicher Inhalt in einem
    # anderen Ordner ist ein eigenständiges Dokument (siehe uq_folder_doc_hash).
    result = await s.execute(
        select(Document).where(
            Document.doc_hash == doc_hash,
            Document.folder_path == folder_path,
        )
    )
    return result.scalar_one_or_none()


async def _run_ingest_job(
    doc_id: UUID,
    path: Path,
    folder_path: str,
    tags: list[str],
    cfg,
) -> None:
    start = time.perf_counter()
    job_id = uuid.uuid4()

    # Job-Start protokollieren
    async with get_session() as s:
        s.add(IngestJob(id=job_id, doc_id=doc_id, status="processing"))
        await s.execute(
            Document.__table__.update()
            .where(Document.id == doc_id)
            .values(status=DocumentStatus.PROCESSING.value)
        )

    try:
        # 1. Parsen — Backend-Weiche (Feature-Flag ingest_backend, default legacy).
        #    legacy: PyMuPDF/python-docx + struktureller Chunker.
        #    docling: layout-aware doc_ingest (Tabellen verlustfrei, Parent-Child).
        backend = settings().ingest_backend
        di_result = None
        if backend == "docling":
            from ingest.docling_ingest import (
                docling_full_text,
                docling_to_chunks,
                run_docling,
            )
            di_result = await asyncio.to_thread(run_docling, path)
            full_text = docling_full_text(di_result)
            file_name = path.name
            mime_type = (di_result.document.get("metadata") or {}).get("mimetype")
        else:
            parsed = await asyncio.to_thread(parse_file, path)
            full_text = parsed.full_text
            file_name = parsed.file_name
            mime_type = parsed.mime_type

        # 2. Auto-Tags via LLM (ergänzen die vom User angegebenen Tags)
        from ingest.autotag import generate_tags
        from ingest.metadata_extract import extract_metadata, version_year
        auto_tags = await asyncio.to_thread(generate_tags, full_text)
        # Manuelle Tags zuerst, Auto-Tags danach, Duplikate entfernen
        merged_tags = list(dict.fromkeys([*tags, *auto_tags]))

        # 2b. Strukturierte Metadaten extrahieren (doc_type, norm_id, version, …)
        meta_fields = await asyncio.to_thread(extract_metadata, full_text)

        # 2c. Versions-/Ablöse-Logik: gleiche norm_id + Jahresvergleich.
        # Existiert eine neuere Fassung → diese hier ist 'superseded'; existieren
        # nur ältere → diese hier 'current' und die älteren werden 'superseded'.
        valid_status = "unknown"
        superseded_by: uuid.UUID | None = None
        norm_id = meta_fields.get("norm_id")
        this_year = version_year(meta_fields.get("doc_version") or meta_fields.get("issued_date"))
        if norm_id and this_year is not None:
            async with get_session() as s:
                others = (await s.execute(
                    select(Document).where(
                        Document.norm_id == norm_id, Document.id != doc_id
                    )
                )).scalars().all()
            older_ids: list[UUID] = []
            newest_newer: tuple[int, UUID] | None = None
            for o in others:
                oy = version_year(o.doc_version or o.issued_date)
                if oy is None:
                    continue
                if oy < this_year:
                    older_ids.append(o.id)
                elif oy > this_year and (newest_newer is None or oy > newest_newer[0]):
                    newest_newer = (oy, o.id)
            if newest_newer is not None:
                valid_status = "superseded"
                superseded_by = newest_newer[1]
            else:
                valid_status = "current"
                if older_ids:
                    async with get_session() as s:
                        await s.execute(
                            Document.__table__.update()
                            .where(Document.id.in_(older_ids))
                            .values(valid_status="superseded", superseded_by=doc_id)
                        )

        # 2a. Tags + MIME + Metadaten sofort persistieren — damit sie auch bei
        # späterem Embed-Failure erhalten bleiben (die LLM-Calls sind teuer).
        async with get_session() as s:
            await s.execute(
                Document.__table__.update()
                .where(Document.id == doc_id)
                .values(
                    tags=merged_tags,
                    mime_type=mime_type,
                    doc_type=meta_fields.get("doc_type"),
                    norm_id=norm_id,
                    doc_version=meta_fields.get("doc_version"),
                    issued_date=meta_fields.get("issued_date"),
                    issuer=meta_fields.get("issuer"),
                    language=meta_fields.get("language"),
                    valid_status=valid_status,
                    superseded_by=superseded_by,
                )
            )

        # 3. Chunken — synchron + CPU-intensiv → Thread auslagern
        base_meta = {
            "doc_id": str(doc_id),
            "folder_path": folder_path,
            "folder": folder_path,  # alias für Filter-Einfachheit
            "file_name": file_name,
            "tags": merged_tags,
            "source_type": path.suffix.lstrip(".").lower() or "unknown",
            "doc_type": meta_fields.get("doc_type"),
            "norm_id": norm_id,
            "doc_version": meta_fields.get("doc_version"),
            "language": meta_fields.get("language"),
        }
        if backend == "docling":
            chunks = docling_to_chunks(di_result, base_meta)
        else:
            chunks = await asyncio.to_thread(chunk_document, parsed, cfg.chunking, base_meta)

        if not chunks:
            raise ValueError("No extractable text / zero chunks")

        # 3b. Chunks KANONISCH nach SQLite (Track C2b) — VOR LanceDB (abgeleiteter Index).
        #     SQLite = Wahrheit; kein Split-Brain. Quelle für den Graphen (Track D).
        await _store_document_chunks(doc_id, folder_path, chunks)

        # 4. Embedden + in LanceDB schreiben
        await _embed_and_store(chunks, cfg.embed_model)

        # 5. SQLite-Status: indexed (Tags + MIME wurden schon oben geschrieben)
        duration_ms = int((time.perf_counter() - start) * 1000)
        async with get_session() as s:
            await s.execute(
                Document.__table__.update()
                .where(Document.id == doc_id)
                .values(
                    status=DocumentStatus.INDEXED.value,
                    chunk_count=len(chunks),
                    indexed_at=datetime.now(timezone.utc),
                )
            )
            await s.execute(
                IngestJob.__table__.update()
                .where(IngestJob.id == job_id)
                .values(
                    status="done",
                    finished_at=datetime.now(timezone.utc),
                    duration_ms=duration_ms,
                )
            )

        log.info(
            "ingest.success",
            doc_id=str(doc_id),
            chunks=len(chunks),
            ms=duration_ms,
        )

    except Exception as e:
        log.exception("ingest.failed", doc_id=str(doc_id), error=str(e))
        async with get_session() as s:
            await s.execute(
                Document.__table__.update()
                .where(Document.id == doc_id)
                .values(status=DocumentStatus.FAILED.value, error_msg=str(e)[:2000])
            )
            await s.execute(
                IngestJob.__table__.update()
                .where(IngestJob.id == job_id)
                .values(
                    status="failed",
                    finished_at=datetime.now(timezone.utc),
                    error_msg=str(e)[:2000],
                )
            )


def _gen_chunk_id(doc_id: str, section_path, text: str) -> str:
    """Inhaltsbasierte, meta-unabhängige Chunk-ID (für Legacy-Chunks ohne eigene ID)."""
    sp = section_path or ""
    return hashlib.sha1(f"{doc_id}\x1f{sp}\x1f{text}".encode("utf-8")).hexdigest()


async def _store_document_chunks(
    doc_id: UUID, folder_path: str, chunks: list[dict]
) -> None:
    """Schreibt die Chunks kanonisch nach SQLite (`document_chunks`).

    Idempotent: löscht bestehende Chunks des Dokuments und schreibt neu (Reindex/
    Re-Ingest-fest). SQLite = Source-of-Truth, LanceDB wird daraus abgeleitet.
    Dedup pro `chunk_id` (identischer Text im selben Abschnitt → gleiche ID).
    """
    from db.models import DocumentChunk

    rows: dict[str, DocumentChunk] = {}
    for i, c in enumerate(chunks):
        m = c.get("metadata") or {}
        text = c.get("text") or ""
        cid = m.get("chunk_id") or _gen_chunk_id(str(doc_id), m.get("section_path"), text)
        rows[cid] = DocumentChunk(
            chunk_id=cid,
            doc_id=doc_id,
            level="child",
            ordinal=i,
            parent_id=m.get("parent_id"),
            text=text,
            section_path=m.get("section_path"),
            element_types=m.get("element_types"),
            table_html=m.get("table_html"),
            token_count=m.get("token_count"),
            page=m.get("page"),
            folder_path=folder_path,
        )
    async with get_session() as s:
        await s.execute(
            DocumentChunk.__table__.delete().where(DocumentChunk.doc_id == doc_id)
        )
        for r in rows.values():
            s.add(r)


async def _embed_and_store(
    chunks: list[dict], embed_model: str
) -> None:
    """
    Embeddet die Chunk-Texte dicht (INT8-ONNX e5-large) und schreibt Text +
    Vektor + Payload als Zeilen in die LanceDB-`chunks`-Tabelle. Die lexikalische
    Seite (BM25/exakte Normnummern) übernimmt LanceDBs FTS-Index auf `text` —
    kein separater Sparse-Vektor mehr, kein Haystack/Ollama.

    Embedden ist CPU-intensiv → `asyncio.to_thread`, damit der Eventloop frei bleibt.
    """
    from pipelines import store
    from pipelines.factory import embed_texts

    texts = [c["text"] for c in chunks]
    vectors = await asyncio.to_thread(embed_texts, texts, embed_model)

    rows: list[dict] = []
    for c, vec in zip(chunks, vectors):
        m = c.get("metadata") or {}
        text = c.get("text") or ""
        point_id = m.get("chunk_id") or _gen_chunk_id(
            str(m.get("doc_id") or ""), m.get("section_path"), text
        )
        rows.append({
            "point_id": point_id,
            "chunk_id": m.get("chunk_id"),
            "doc_id": str(m["doc_id"]) if m.get("doc_id") is not None else None,
            "file_name": m.get("file_name"),
            "folder": m.get("folder") or m.get("folder_path"),
            "folder_path": m.get("folder_path"),
            "text": text,
            "vector": vec,
            "page": m.get("page"),
            "section_title": m.get("section_title"),
            "section_path": m.get("section_path"),
            "doc_type": m.get("doc_type"),
            "norm_id": m.get("norm_id"),
            "doc_version": m.get("doc_version"),
            "language": m.get("language"),
            "tags": list(m.get("tags") or []),
            "table_html": m.get("table_html"),
        })
    await asyncio.to_thread(store.write, rows)
