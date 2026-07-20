"""
Dokumenten-Verwaltung: Upload (immer async über die Queue), Liste, Details,
Löschen, Re-Indexieren, Bulk-Job-Status.

Upload-Modi (Track C3b — Worker-Split: der rag-api-Container ingestet NICHT mehr
selbst; die schwere Ingest-Last läuft im separaten rag-ingest-Worker):
  - Dateien → 1..n Files ins geteilte Staging-Volume + Ingest-Queue,
              Antwort BulkUploadResponse (202), Status via GET jobs/{job_id}
  - ZIP     → Server entpackt mit Safety-Checks, jedes File in die Queue
"""
from __future__ import annotations

import asyncio
import io
import tempfile
import uuid
import zipfile
from pathlib import Path
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select

from api.schemas import (
    BulkUploadResponse,
    DocumentPatchRequest,
    DocumentResponse,
    IngestJobResponse,
)
from auth.dependencies import AuthContext, require_any_auth, require_ui_admin
from auth.folders import accessible_folder_paths, is_within, normalize_folder
from config import settings
from db.models import Document
from db.session import get_session
from ingest.queue import enqueue_files, get_job_status
from logger import log
from pipelines.vector_ops import delete_chunks, move_document

router = APIRouter(prefix="/api/documents", tags=["documents"])


# ---------------------------------------------------------------------------
# Unterstützte Dateiformate — muss mit ingest/parsers.py:parse_file() übereinstimmen.
# Dateien mit anderen Suffixen werden VOR dem Queuing verworfen (Early-Reject).
# ---------------------------------------------------------------------------
_INGEST_SUPPORTED_SUFFIXES: frozenset[str] = frozenset({
    ".pdf",
    ".docx",
    ".xlsx", ".xlsm",
    ".txt", ".log",
    ".md", ".markdown",
    ".html", ".htm",
})
_SUPPORTED_FORMATS_HINT = ", ".join(sorted(_INGEST_SUPPORTED_SUFFIXES))

# Konstanten für ZIP-Sicherheit
_ZIP_MAX_FILES = 5000
_ZIP_MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024     # 1 GB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_response(d: Document) -> DocumentResponse:
    return DocumentResponse(
        id=d.id,
        folder_path=d.folder_path,
        file_name=d.file_name,
        mime_type=d.mime_type,
        size_bytes=d.size_bytes,
        tags=list(d.tags or []),
        status=d.status,
        chunk_count=d.chunk_count,
        error_msg=d.error_msg,
        uploaded_at=d.uploaded_at,
        indexed_at=d.indexed_at,
        doc_type=d.doc_type,
        norm_id=d.norm_id,
        doc_version=d.doc_version,
        issued_date=d.issued_date,
        issuer=d.issuer,
        language=d.language,
        valid_status=d.valid_status,
        superseded_by=d.superseded_by,
    )


def _require_write_scope(ctx: AuthContext) -> None:
    if not ctx.has_scope("write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing 'write' scope")


def _require_folder_access(ctx: AuthContext, folder_path: str) -> None:
    """
    Ordner-Zugriffskontrolle für Einzel-Dokument-Endpunkte.

    Ohne diese Prüfung könnte ein API-Key mit eingeschränkten `allowed_folders`
    per bekannter/erratbarer doc_id auf Dokumente fremder Ordner zugreifen
    (IDOR). UI-User und Keys ohne Ordner-Einschränkung haben Vollzugriff —
    die Logik steckt in AuthContext.can_access_folder().
    """
    if not ctx.can_access_folder(folder_path):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Kein Zugriff auf diesen Ordner",
        )


async def _write_upload_to_tempfile(file: UploadFile) -> Path:
    """Streamt einen Upload ins geteilte Staging-Volume und gibt den Pfad zurück.

    Track C3b: NICHT mehr container-lokales /tmp — sonst kann der separate
    rag-ingest-Worker die Datei nicht lesen (FileNotFoundError je Queue-Job).
    `settings().staging_dir` ist ein geteiltes Bind-Mount (api + rag-ingest).
    """
    staging = settings().staging_dir
    staging.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "upload").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=staging) as tmp:
        tmp_path = Path(tmp.name)
        while chunk := await file.read(1024 * 1024):
            tmp.write(chunk)
    return tmp_path


# ---------------------------------------------------------------------------
# Upload — immer asynchron über die Ingest-Queue (Track C3b — Worker-Split)
# ---------------------------------------------------------------------------
@router.post(
    "",
    operation_id="upload_documents",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BulkUploadResponse,
)
async def upload_documents(
    files: list[UploadFile] = File(..., description="1..n Dateien"),
    folder_path: str = Form("/"),
    tags: str = Form(""),  # komma-getrennt
    ctx: AuthContext = Depends(require_any_auth),
):
    """
    File-Upload (1..n Dateien) — IMMER asynchron.

    Alle Dateien werden ins geteilte Staging-Volume geschrieben und in die
    Ingest-Queue gestellt; der separate rag-ingest-Worker verarbeitet sie.
    Antwort = BulkUploadResponse (202); der Fortschritt wird über
    GET /api/documents/jobs/{job_id} gepollt. Kein synchroner Ingest mehr im
    rag-api-Prozess (Serving bleibt ingest-frei).
    """
    _require_write_scope(ctx)
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No files provided")

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    uploaded_by = ctx.ui_user.id if ctx.ui_user else None

    # --- Staging + Enqueue (gilt für 1..n Dateien) ---------------------------
    job_id = uuid.uuid4()
    queued: list[tuple[Path, str]] = []
    skipped: list[str] = []
    try:
        for file in files:
            fname = file.filename or "upload"
            suffix = Path(fname).suffix.lower()
            if suffix not in _INGEST_SUPPORTED_SUFFIXES:
                skipped.append(f"{fname}: Format '{suffix}' nicht unterstützt")
                log.info("documents.upload.skipped", filename=fname, suffix=suffix)
                continue
            tmp_path = await _write_upload_to_tempfile(file)
            queued.append((tmp_path, fname))
    except Exception:
        for p, _ in queued:
            p.unlink(missing_ok=True)
        raise

    if not queued:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Keine unterstützten Dateien hochgeladen. "
                f"Unterstützte Formate: {_SUPPORTED_FORMATS_HINT}. "
                f"Übersprungen: {', '.join(s.split(':')[0] for s in skipped[:10])}"
            ),
        )

    await enqueue_files(
        job_id=job_id,
        folder_path=folder_path,
        files=queued,
        tags=tag_list,
        uploaded_by=uploaded_by,
    )
    log.info(
        "documents.upload.queued",
        job_id=str(job_id),
        count=len(queued),
        skipped=len(skipped),
    )
    return BulkUploadResponse(job_id=job_id, total=len(queued), skipped=skipped)


# ---------------------------------------------------------------------------
# Upload — ZIP (entpackt mit Safety, dann Queue)
# ---------------------------------------------------------------------------
def _is_safe_zip_member(member: zipfile.ZipInfo, extract_root: Path) -> tuple[bool, str]:
    """
    Sicherheits-Check für einen ZIP-Eintrag.
    Returns (ok, reason). reason ist leer wenn ok=True.
    """
    name = member.filename
    if member.is_dir():
        return True, ""

    # Keine absoluten Pfade
    if name.startswith("/") or (len(name) >= 2 and name[1] == ":"):
        return False, f"absolute path: {name}"

    # Keine .. -Komponenten
    parts = Path(name).parts
    if ".." in parts:
        return False, f"parent-traversal: {name}"

    # Final-Pfad muss in extract_root liegen
    final = (extract_root / name).resolve()
    try:
        final.relative_to(extract_root.resolve())
    except ValueError:
        return False, f"escapes extract root: {name}"

    # Keine Symlinks (Mode-Bits via external_attr für UNIX-Symlinks)
    # In ZIP: external_attr >> 16 = Mode. 0o120000 = Symlink.
    mode = member.external_attr >> 16
    if mode and (mode & 0o170000) == 0o120000:
        return False, f"symlink not allowed: {name}"

    return True, ""


def _zip_extract_path_to_folder(zip_member_name: str, base_folder: str) -> str:
    """
    Mappt den relativen Pfad eines ZIP-Eintrags auf einen `folder_path`.

    Beispiele (base_folder='/Steuer/'):
      'BVH/Plaene/x.pdf' → '/Steuer/BVH/Plaene/'
      'x.pdf'            → '/Steuer/'
    """
    base = base_folder.strip("/")
    rel_dir = str(Path(zip_member_name).parent).replace("\\", "/").strip("/")
    if rel_dir == ".":
        rel_dir = ""

    parts = [p for p in (base, rel_dir) if p]
    return "/" + "/".join(parts) + "/" if parts else "/"


@router.post("/zip", status_code=status.HTTP_202_ACCEPTED, response_model=BulkUploadResponse)
async def upload_zip(
    file: UploadFile = File(..., description="ZIP-Archiv mit Dokumenten"),
    folder_path: str = Form("/", description="Basis-Ordner; ZIP-Hierarchie hängt sich darunter"),
    tags: str = Form(""),
    ctx: AuthContext = Depends(require_any_auth),
):
    """
    ZIP-Upload mit Folder-Hierarchie. Server entpackt mit harten
    Safety-Checks (Pfad-Traversal, Zip-Bomb, Symlinks, max 5000 Files,
    max 1 GB uncompressed) und stellt jede Datei einzeln in die Queue.

    Die relative Pfad-Hierarchie im ZIP wird unter `folder_path` (Form-Param)
    angehängt: `BVH/Plaene/x.pdf` im ZIP + `folder_path=/Steuer/` →
    `folder_path=/Steuer/BVH/Plaene/`.
    """
    _require_write_scope(ctx)
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Erwarte .zip-Datei")

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    uploaded_by = ctx.ui_user.id if ctx.ui_user else None

    # ZIP + Entpack-Ziel ins geteilte Staging-Volume (Track C3b): der rag-ingest-
    # Worker liest die entpackten Dateien — container-lokales /tmp wäre für ihn
    # unsichtbar. staging_dir wird von _write_upload_to_tempfile bereits angelegt.
    zip_tmp = await _write_upload_to_tempfile(file)
    job_id = uuid.uuid4()
    extract_root = Path(
        tempfile.mkdtemp(prefix=f"rag-zip-{job_id}-", dir=settings().staging_dir)
    )

    try:
        # Sync wrapper für stdlib zipfile — sind blocking I/O, in to_thread aber zu klein für den Aufwand
        try:
            zf = zipfile.ZipFile(zip_tmp, "r")
        except zipfile.BadZipFile as e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Kaputtes ZIP: {e}"
            )

        members = zf.infolist()
        if len(members) > _ZIP_MAX_FILES:
            zf.close()
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"ZIP hat >{_ZIP_MAX_FILES} Einträge",
            )

        total_uncompressed = sum(m.file_size for m in members)
        if total_uncompressed > _ZIP_MAX_UNCOMPRESSED_BYTES:
            zf.close()
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"ZIP entpackt >1 GB ({total_uncompressed} bytes)",
            )

        queued: list[tuple[Path, str]] = []
        skipped: list[str] = []
        per_file_folder: dict[str, str] = {}   # tmp-Pfad → folder_path

        for m in members:
            if m.is_dir():
                continue

            ok, reason = _is_safe_zip_member(m, extract_root)
            if not ok:
                log.warning("zip.skip_unsafe", file=m.filename, reason=reason)
                skipped.append(f"{m.filename}: {reason}")
                continue

            suffix = Path(m.filename).suffix.lower()
            if suffix not in _INGEST_SUPPORTED_SUFFIXES:
                skipped.append(f"{Path(m.filename).name}: Format '{suffix}' nicht unterstützt")
                log.info("zip.skip_unsupported", file=m.filename, suffix=suffix)
                continue

            # Extrahieren in extract_root, dann als (file_path, original_name) in Queue
            target_rel = Path(m.filename)
            target_abs = extract_root / target_rel
            target_abs.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(m, "r") as src, open(target_abs, "wb") as dst:
                # Begrenzen, gegen on-the-fly Bomb (Member-Größe schon validiert)
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)

            per_file_folder[str(target_abs)] = _zip_extract_path_to_folder(
                m.filename, folder_path
            )
            queued.append((target_abs, target_rel.name))

        zf.close()
        zip_tmp.unlink(missing_ok=True)

        if not queued:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Keine verwertbaren Dateien im ZIP. Skipped: {skipped[:10]}",
            )

        # Da unterschiedliche Files in unterschiedlichen Sub-Ordnern landen,
        # rufen wir enqueue pro distinct folder_path auf. Das hält die
        # Queue-Tabelle einfach (eine folder_path-Spalte pro Row).
        groups: dict[str, list[tuple[Path, str]]] = {}
        for path, original in queued:
            fp = per_file_folder[str(path)]
            groups.setdefault(fp, []).append((path, original))

        for fp, group_files in groups.items():
            await enqueue_files(
                job_id=job_id,
                folder_path=fp,
                files=group_files,
                tags=tag_list,
                uploaded_by=uploaded_by,
            )

        log.info(
            "documents.zip_upload.queued",
            job_id=str(job_id),
            count=len(queued),
            skipped=len(skipped),
        )
        return BulkUploadResponse(job_id=job_id, total=len(queued), skipped=skipped)

    except Exception:
        # Cleanup bei jedem Fehler (HTTP-Validation oder unerwartete Exception)
        zip_tmp.unlink(missing_ok=True)
        import shutil
        shutil.rmtree(extract_root, ignore_errors=True)
        raise


# ---------------------------------------------------------------------------
# Ingest-Job-Status (Polling-Endpunkt)
# ---------------------------------------------------------------------------
@router.get(
    "/jobs/{job_id}",
    response_model=IngestJobResponse,
    tags=["ingest"],
    summary="Aggregierter Status eines Bulk- oder ZIP-Upload-Jobs",
)
async def ingest_job_status(
    job_id: UUID,
    ctx: AuthContext = Depends(require_any_auth),
):
    status_dict = await get_job_status(job_id)
    if not status_dict:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job nicht gefunden")
    return IngestJobResponse(**status_dict)


# ---------------------------------------------------------------------------
# Liste
# ---------------------------------------------------------------------------
@router.get("/folders", operation_id="list_folders")
async def list_folders(
    ctx: AuthContext = Depends(require_any_auth),
):
    """Ordner-Übersicht: {folder_path: Anzahl Dokumente}."""
    allowed = ctx.api_key.allowed_folders if ctx.api_key else None
    async with get_session() as s:
        acl_paths = await accessible_folder_paths(allowed, None, s)
        stmt = (
            select(Document.folder_path, func.count().label("n"))
            .group_by(Document.folder_path)
        )
        if acl_paths is not None:
            if not acl_paths:
                return {}
            stmt = stmt.where(Document.folder_path.in_(acl_paths))

        result = await s.execute(stmt)
        return {row.folder_path: row.n for row in result}


@router.get("", operation_id="list_documents", response_model=list[DocumentResponse])
async def list_documents(
    http_response: Response,
    folder: str | None = None,
    folder_prefix: str | None = Query(default=None),
    status_filter: str | None = None,
    search: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    ctx: AuthContext = Depends(require_any_auth),
):
    allowed = ctx.api_key.allowed_folders if ctx.api_key else None
    async with get_session() as s:
        # Kanonische Ordner-ACL (None = unrestringiert, [] = nichts zugänglich).
        acl_paths = await accessible_folder_paths(allowed, None, s)
        if acl_paths is not None and not acl_paths:
            http_response.headers["X-Total-Count"] = "0"
            return []

        def _filter(stmt):
            if acl_paths is not None:
                stmt = stmt.where(Document.folder_path.in_(acl_paths))
            if folder:
                stmt = stmt.where(Document.folder_path == folder)
            if folder_prefix:
                stmt = stmt.where(Document.folder_path.startswith(folder_prefix))
            if status_filter:
                stmt = stmt.where(Document.status == status_filter)
            if search:
                stmt = stmt.where(Document.file_name.ilike(f"%{search}%"))
            return stmt

        total = await s.scalar(_filter(select(func.count()).select_from(Document))) or 0
        http_response.headers["X-Total-Count"] = str(total)

        result = await s.execute(
            _filter(select(Document))
            .order_by(Document.uploaded_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [_to_response(d) for d in result.scalars().all()]


# ---------------------------------------------------------------------------
# Einzel-Dokument
# ---------------------------------------------------------------------------
@router.get("/{doc_id}", response_model=DocumentResponse)
async def get_document(
    doc_id: UUID, ctx: AuthContext = Depends(require_any_auth)
):
    async with get_session() as s:
        result = await s.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    _require_folder_access(ctx, doc.folder_path)
    return _to_response(doc)


# ---------------------------------------------------------------------------
# Patch (Ordner ändern, Tags ändern)
# ---------------------------------------------------------------------------
@router.patch("/{doc_id}", response_model=DocumentResponse)
async def patch_document(
    doc_id: UUID,
    payload: DocumentPatchRequest,
    ctx: AuthContext = Depends(require_any_auth),
):
    # 1) Auth + Existenz prüfen (Quell- und ggf. Ziel-Ordner).
    async with get_session() as s:
        doc = (
            await s.execute(select(Document).where(Document.id == doc_id))
        ).scalar_one_or_none()
        if not doc:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        if not ctx.has_scope("write"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing 'write' scope")
        _require_folder_access(ctx, doc.folder_path)
        if payload.folder_path is not None:
            _require_folder_access(ctx, payload.folder_path)

    # 2) Ordner-Move ATOMAR über die gemeinsame Move-Funktion (SQLite + LanceDB).
    #    Früher wurde nur SQLite.folder_path gesetzt → LanceDB meta.folder blieb
    #    alt → das verschobene Dokument wurde unauffindbar (Split-Brain).
    if payload.folder_path is not None:
        await move_document(doc_id, payload.folder_path)

    # 3) Tags separat aktualisieren.
    if payload.tags is not None:
        async with get_session() as s:
            doc = (
                await s.execute(select(Document).where(Document.id == doc_id))
            ).scalar_one()
            doc.tags = payload.tags

    async with get_session() as s:
        doc = (
            await s.execute(select(Document).where(Document.id == doc_id))
        ).scalar_one()
        return _to_response(doc)


# ---------------------------------------------------------------------------
# Löschen
# ---------------------------------------------------------------------------
@router.delete("/folder", operation_id="delete_folder", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_path: str = Query(...),
    ctx: AuthContext = Depends(require_ui_admin),
):
    """Löscht alle Dokumente in einem Ordner (inkl. Unterordner).

    Track E (HOCH-Fix 3): nur Web-UI-Admin (`require_ui_admin`).
    """
    fp = normalize_folder(folder_path)

    # MITTEL-Fix 9: Ziel-Ordner über die kanonische, segmentgrenzbewusste ACL
    # (`is_within`) auflösen — NICHT über rohes `LIKE '{fp}%'`. Das LIKE würde
    # bei Ordnernamen mit SQL-Wildcards (`_`, `%`) fremde Ordner mitlöschen.
    async with get_session() as s:
        all_folders = [
            row[0]
            for row in (await s.execute(select(Document.folder_path).distinct())).all()
        ]
        target_folders = [f for f in all_folders if is_within(f, fp)]
        if not target_folders:
            log.info("delete_folder.done", folder=fp, count=0)
            return
        docs = (
            await s.execute(
                select(Document).where(Document.folder_path.in_(target_folders))
            )
        ).scalars().all()

    for doc in docs:
        try:
            await asyncio.to_thread(delete_chunks, doc.id)
        except Exception as e:
            log.warning("delete_folder.chunks_failed", doc_id=str(doc.id), error=str(e))

        try:
            Path(doc.file_path).unlink(missing_ok=True)
        except Exception as e:
            log.warning("delete_folder.file_failed", doc_id=str(doc.id), error=str(e))

    async with get_session() as s:
        await s.execute(
            delete(Document).where(Document.folder_path.in_(target_folders))
        )

    log.info("delete_folder.done", folder=fp, count=len(docs))


@router.delete("/{doc_id}", operation_id="delete_document", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: UUID, ctx: AuthContext = Depends(require_ui_admin)
):
    # Track E (HOCH-Fix 3): Löschen ist DSGVO-relevant und nur der Web-UI-Admin
    # vorbehalten (`require_ui_admin`). API-Keys/OAuth erreichen diesen Endpunkt
    # nicht mehr — der frühere delete-Scope für Bearer-Keys ist damit wirkungslos.
    async with get_session() as s:
        result = await s.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one_or_none()
        if not doc:
            raise HTTPException(status.HTTP_404_NOT_FOUND)

        try:
            await asyncio.to_thread(delete_chunks, doc.id)
        except Exception as e:
            log.warning("delete.chunks_failed", doc_id=str(doc.id), error=str(e))

        try:
            Path(doc.file_path).unlink(missing_ok=True)
        except Exception as e:
            log.warning("delete.file_failed", doc_id=str(doc.id), error=str(e))

        await s.execute(delete(Document).where(Document.id == doc_id))


# ---------------------------------------------------------------------------
# Re-Indexieren
# ---------------------------------------------------------------------------
@router.post("/{doc_id}/reindex", operation_id="reindex_document", response_model=DocumentResponse)
async def reindex_document_endpoint(
    doc_id: UUID, ctx: AuthContext = Depends(require_any_auth)
):
    async with get_session() as s:
        result = await s.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if not ctx.has_scope("write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing 'write' scope")
    _require_folder_access(ctx, doc.folder_path)

    src = Path(doc.file_path)
    if not src.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Originaldatei nicht auf Disk gefunden")

    # Lazy-Import (Dep-Severance C3b): ingest.pipeline zieht die schwere
    # Parsing/Embedding-Last — sie darf nicht schon beim Modul-Import des
    # rag-api-Serving-Prozesses geladen werden. Reindex läuft hier vorerst
    # NOCH synchron im api-Prozess (bekannte Folge-Story: auch Reindex über die
    # Queue an den rag-ingest-Worker auslagern).
    from ingest.pipeline import reindex_document_file

    await reindex_document_file(
        doc_id=doc.id,
        path=src,
        folder_path=doc.folder_path,
        tags=list(doc.tags or []),
    )

    async with get_session() as s:
        result = await s.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one()
    return _to_response(doc)


# ---------------------------------------------------------------------------
# Chunks aus LanceDB abrufen
# ---------------------------------------------------------------------------
class _ChunkResponse(BaseModel):
    id: str
    content: str
    page: int | None
    section_title: str | None


@router.get("/{doc_id}/chunks", operation_id="get_document_chunks", response_model=list[_ChunkResponse])
async def get_document_chunks(
    doc_id: UUID, ctx: AuthContext = Depends(require_any_auth)
):
    async with get_session() as s:
        result = await s.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    _require_folder_access(ctx, doc.folder_path)

    from pipelines import store
    chunks = await asyncio.to_thread(
        store.filter_by_meta,
        {
            "operator": "AND",
            "conditions": [{"field": "meta.doc_id", "operator": "==", "value": str(doc_id)}],
        },
    )
    return [
        _ChunkResponse(
            id=str(c.id or ""),
            content=c.content or "",
            page=c.meta.get("page") if c.meta else None,
            section_title=c.meta.get("section_title") if c.meta else None,
        )
        for c in chunks
    ]


# ---------------------------------------------------------------------------
# Download — Original oder als PDF (Welle 5)
# ---------------------------------------------------------------------------
@router.get("/{doc_id}/download", operation_id="download_document")
async def download_document(
    doc_id: UUID,
    fmt: str | None = Query(default=None, alias="format", description="'pdf' für Konvertierung, sonst Original"),
    ctx: AuthContext = Depends(require_any_auth),
):
    """
    Lädt ein Dokument herunter.
    - Ohne `format`: immer die Originaldatei.
    - `format=pdf`: PDF-Konvertierung (PDF-Originale werden direkt durchgereicht).
    """
    async with get_session() as s:
        result = await s.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    _require_folder_access(ctx, doc.folder_path)

    src = Path(doc.file_path)
    if not src.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Datei nicht auf Disk gefunden")

    if fmt == "pdf":
        from export.pdf import to_pdf_bytes
        pdf_bytes = await to_pdf_bytes(src, title=doc.file_name)
        stem = Path(doc.file_name).stem
        return StreamingResponse(
            iter([pdf_bytes]),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{stem}.pdf"'},
        )

    media_type = doc.mime_type or "application/octet-stream"
    return FileResponse(
        path=str(src),
        filename=doc.file_name,
        media_type=media_type,
    )


# ---------------------------------------------------------------------------
# Bulk-Export als ZIP (Welle 5)
# ---------------------------------------------------------------------------
class _ExportRequest(BaseModel):
    ids: list[UUID]
    format: str = "original"  # "original" | "pdf"


@router.post("/export", operation_id="export_documents")
async def export_documents(
    payload: _ExportRequest,
    ctx: AuthContext = Depends(require_any_auth),
):
    """
    Exportiert mehrere Dokumente als ZIP.
    `format="original"` → Originaldateien (default).
    `format="pdf"` → alle in PDF konvertiert.
    """
    if not ctx.has_scope("read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing 'read' scope")
    # Track E: Bulk-Export ist ein Massen-Datenabfluss → für normale UI-User
    # (role=user) gesperrt (die lesen über MCP, nicht per ZIP-Export). Admins
    # und Bearer-Keys mit read-Scope bleiben zulässig (per-Doc-ACL greift unten).
    if ctx.is_ui and not ctx.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Export nur für Admins")
    if not payload.ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keine IDs angegeben")
    if len(payload.ids) > 200:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Max 200 Dokumente pro Export")

    async with get_session() as s:
        result = await s.execute(
            select(Document).where(Document.id.in_(payload.ids))
        )
        docs = result.scalars().all()

    # Ordner-Zugriffskontrolle: ohne diese Prüfung könnte ein Key mit
    # eingeschränkten allowed_folders per bekannter doc_id fremde Dokumente
    # exportieren (IDOR). Nicht-erlaubte IDs still überspringen (nicht offenbaren).
    docs = [d for d in docs if ctx.can_access_folder(d.folder_path)]

    use_pdf = payload.format == "pdf"
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc in docs:
            src = Path(doc.file_path)
            if not src.exists():
                log.warning("export.file_missing", doc_id=str(doc.id), path=str(src))
                continue
            if use_pdf:
                from export.pdf import to_pdf_bytes
                data = await to_pdf_bytes(src, title=doc.file_name)
                arc_name = Path(doc.file_name).stem + ".pdf"
            else:
                data = await asyncio.to_thread(src.read_bytes)
                arc_name = doc.file_name
            zf.writestr(arc_name, data)

    zip_bytes = buf.getvalue()
    log.info("export.done", count=len(docs), format=payload.format, size_kb=len(zip_bytes) // 1024)
    return StreamingResponse(
        iter([zip_bytes]),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="export.zip"'},
    )
