"""
Ordnerstruktur-Vorschläge via KI.

Endpunkte:
  POST /api/suggest/from-docs  → analysiert bestehende Dokumente via LanceDB-Snippets
  POST /api/suggest/from-zip   → extrahiert + analysiert ZIP (kein Ingest)
  POST /api/suggest/apply      → bestätigte Vorschläge auf bestehende Dokumente anwenden
  POST /api/suggest/apply-zip  → analysiertes ZIP mit bestätigten Ordnern ingestieren
"""
from __future__ import annotations

import asyncio
import io
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from auth.dependencies import AuthContext, require_any_auth
from config import settings
from db.models import Document
from db.session import get_session
from ingest.queue import enqueue_files
from logger import log
from pipelines.vector_ops import move_document
from services.folder_suggester import DocInfo, FolderSuggestion, suggest_folders

router = APIRouter(prefix="/api/suggest", tags=["suggest"])

# In-Memory-Store für ZIP-Sessions (temp_id → Session-Dict)
# Single-Process ist für den aktuellen Setup ausreichend.
# Bei Horizontal-Scaling: in Redis/DB auslagern.
_ZIP_SESSION_TTL = 3600          # 1 Stunde
_zip_sessions: dict[str, dict] = {}

# Temp-Dirs aus angewendeten Sessions — werden erst nach dem Ingest-Worker gelöscht.
# key = absoluter Pfad, value = Zeitpunkt des Enqueue (unix timestamp).
_deferred_cleanup_dirs: dict[str, float] = {}
_DEFERRED_CLEANUP_DELAY = 7200   # 2 Stunden: genug Zeit für den Worker


def _cleanup_sessions() -> None:
    """Räumt abgelaufene ZIP-Sessions auf (wird lazy beim nächsten Upload-Call aufgerufen)."""
    now = time.time()
    # 1. Abgelaufene aktive Sessions
    expired = [k for k, v in list(_zip_sessions.items()) if v.get("expires", 0) < now]
    for k in expired:
        sess = _zip_sessions.pop(k, {})
        temp_dir = sess.get("temp_dir")
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        log.info("suggest.session_expired", temp_id=k)

    # 2. Deferred-Cleanup: Temp-Dirs aus bereits angewendeten Sessions
    cleanup_before = now - _DEFERRED_CLEANUP_DELAY
    stale = [p for p, t in list(_deferred_cleanup_dirs.items()) if t < cleanup_before]
    for path in stale:
        shutil.rmtree(path, ignore_errors=True)
        del _deferred_cleanup_dirs[path]
        log.info("suggest.deferred_cleanup", path=path)

_ZIP_MAX_FILES = 500
_ZIP_MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024   # 512 MB
_ZIP_ALLOWED_SUFFIXES = {
    ".pdf", ".docx", ".xlsx", ".xlsm", ".txt", ".md",
    ".html", ".htm", ".pptx", ".odt", ".ods", ".rtf", ".csv",
}


# ---------------------------------------------------------------------------
# Request / Response Schemas
# ---------------------------------------------------------------------------

class SuggestFromDocsRequest(BaseModel):
    doc_ids: list[UUID]


class SuggestionItem(BaseModel):
    doc_id: str | None = None
    filename: str
    current_folder: str
    suggested_folder: str
    reason: str


class SuggestResponse(BaseModel):
    suggestions: list[SuggestionItem]
    temp_id: str | None = None      # Nur bei ZIP-Session gesetzt


class ApplyRequest(BaseModel):
    suggestions: list[SuggestionItem]


class ApplyZipRequest(BaseModel):
    temp_id: str
    suggestions: list[SuggestionItem]


# ---------------------------------------------------------------------------
# POST /api/suggest/from-docs
# ---------------------------------------------------------------------------

@router.post("/from-docs", response_model=SuggestResponse)
async def suggest_from_docs(
    payload: SuggestFromDocsRequest,
    ctx: AuthContext = Depends(require_any_auth),
):
    """Schlägt Ordner für bereits indexierte Dokumente vor (via LanceDB-Textschnipsel)."""
    if not payload.doc_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keine Dokument-IDs angegeben")
    if len(payload.doc_ids) > 20:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Max 20 Dokumente pro Anfrage")

    async with get_session() as s:
        result = await s.execute(
            select(Document).where(Document.id.in_(payload.doc_ids))
        )
        docs = result.scalars().all()

    if not docs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Keine Dokumente gefunden")

    doc_infos: list[DocInfo] = []
    for doc in docs:
        snippet = await _store_snippet(str(doc.id))
        doc_infos.append(DocInfo(
            doc_id=str(doc.id),
            filename=doc.file_name,
            current_folder=doc.folder_path or "/",
            text_snippet=snippet,
            tags=list(doc.tags or []),
        ))

    suggestions = await suggest_folders(doc_infos)
    return _to_response(suggestions)


# ---------------------------------------------------------------------------
# POST /api/suggest/from-zip
# ---------------------------------------------------------------------------

@router.post("/from-zip", response_model=SuggestResponse)
async def suggest_from_zip(
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(require_any_auth),
):
    """
    Nimmt ein ZIP, extrahiert die Dateien in ein Temp-Verzeichnis,
    liest einen Text-Schnipsel pro Datei und fragt die KI nach Ordnern.
    Gibt temp_id zurück — Ingest passiert erst nach Bestätigung via /apply-zip.
    """
    if not ctx.has_scope("write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing 'write' scope")

    _cleanup_sessions()   # abgelaufene Sessions und Temp-Dirs bereinigen

    content = await file.read()
    if not zipfile.is_zipfile(io.BytesIO(content)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keine gültige ZIP-Datei")

    # Track C3b: ins geteilte Staging-Volume (nicht container-lokales /tmp) —
    # /apply-zip reiht diese extrahierten Dateien in die Queue ein, die der
    # separate rag-ingest-Worker liest. In /tmp wären sie für ihn unsichtbar.
    settings().staging_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="rag_suggest_", dir=settings().staging_dir))
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]

            if len(members) > _ZIP_MAX_FILES:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Zu viele Dateien im ZIP (max {_ZIP_MAX_FILES})"
                )
            total_size = sum(m.file_size for m in members)
            if total_size > _ZIP_MAX_UNCOMPRESSED_BYTES:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "ZIP unkomprimiert zu groß (max 512 MB)")

            extracted: list[tuple[Path, str, str]] = []   # (path, filename, zip_internal_path)
            for member in members:
                p = Path(member.filename)
                if p.suffix.lower() not in _ZIP_ALLOWED_SUFFIXES:
                    continue
                # Sichere Extraktion: nur den Dateinamen, nicht den Pfad
                safe_name = p.name
                target = temp_dir / f"{len(extracted):05d}_{safe_name}"
                target.write_bytes(zf.read(member.filename))
                extracted.append((target, safe_name, member.filename))

        if not extracted:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keine verwertbaren Dateien im ZIP")

        # Text-Snippets lesen + DocInfos bauen
        doc_infos: list[DocInfo] = []
        for path, name, zip_path in extracted:
            snippet = await _parse_snippet(path)
            # ZIP-internen Pfad als "aktueller Ordner" nutzen
            parent = str(Path(zip_path).parent).replace("\\", "/").strip("/")
            current = f"/{parent}/" if parent else "/"
            doc_infos.append(DocInfo(
                doc_id=None,
                filename=name,
                current_folder=current,
                text_snippet=snippet,
                tags=[],
            ))

        suggestions = await suggest_folders(doc_infos)

        # Session speichern (temp_id → Session-Dict mit TTL)
        # temp_dir NICHT löschen — Dateien werden erst in apply-zip ingestiert.
        temp_id = str(uuid.uuid4())
        _zip_sessions[temp_id] = {
            "expires": time.time() + _ZIP_SESSION_TTL,
            "temp_dir": str(temp_dir),
            "files": [
                {
                    "path": str(path),
                    "filename": name,
                    "suggested_folder": sug.suggested_folder,
                }
                for (path, name, _), sug in zip(extracted, suggestions)
            ],
        }

        log.info("suggest.from_zip.done", temp_id=temp_id, files=len(extracted))
        return _to_response(suggestions, temp_id=temp_id)

    except HTTPException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        log.exception("suggest.from_zip.error", error=str(exc))
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc


# ---------------------------------------------------------------------------
# POST /api/suggest/apply  (bestehende Docs verschieben)
# ---------------------------------------------------------------------------

@router.post("/apply")
async def apply_suggestions(
    payload: ApplyRequest,
    ctx: AuthContext = Depends(require_any_auth),
):
    """
    Setzt bestätigte Ordner-Vorschläge für bestehende Dokumente um.

    Verschiebt über die atomare `move_document()` (SQLite + DocumentChunk +
    LanceDB-Payload konsistent — kein Split-Brain). Pro Dokument wird die Ordner-ACL
    auf **Quell- UND Zielordner** geprüft (`can_access_folder`), damit ein
    eingeschränkter Write-Key keine fremden Docs verschieben kann.
    """
    if not ctx.has_scope("write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing 'write' scope")

    moved = 0
    errors: list[str] = []

    for item in payload.suggestions:
        if not item.doc_id:
            continue
        try:
            doc_id = UUID(item.doc_id)
            new_folder = _normalize_folder(item.suggested_folder)

            async with get_session() as s:
                doc = await s.scalar(select(Document).where(Document.id == doc_id))
            if not doc:
                continue
            # Per-Doc-ACL: Quell- UND Zielordner müssen erlaubt sein.
            if not ctx.can_access_folder(doc.folder_path) or not ctx.can_access_folder(new_folder):
                errors.append(f"{item.filename}: kein Zugriff auf Quell-/Zielordner")
                continue
            if doc.folder_path == new_folder:
                moved += 1
                continue
            # Atomarer Move (SQLite + DocumentChunk + LanceDB).
            await move_document(doc_id, new_folder)
            moved += 1
        except Exception as exc:
            errors.append(f"{item.filename}: {exc}")
            log.warning("suggest.apply.item_failed", filename=item.filename, error=str(exc))

    log.info("suggest.apply.done", moved=moved, errors=len(errors))
    return {"moved": moved, "errors": errors}


# ---------------------------------------------------------------------------
# POST /api/suggest/apply-zip  (ZIP ingestieren)
# ---------------------------------------------------------------------------

@router.post("/apply-zip", status_code=status.HTTP_202_ACCEPTED)
async def apply_zip_suggestions(
    payload: ApplyZipRequest,
    ctx: AuthContext = Depends(require_any_auth),
):
    """
    Ingestiert die vorher analysierten ZIP-Dateien mit den vom User
    bestätigten (ggf. editierten) Ordnern.
    """
    if not ctx.has_scope("write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing 'write' scope")

    session = _zip_sessions.pop(payload.temp_id, None)
    if not session:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Session nicht gefunden oder bereits abgelaufen — bitte ZIP erneut hochladen"
        )

    # Abgelaufene Session zurückweisen (TTL-Check)
    if session.get("expires", 0) < time.time():
        temp_dir_expired = session.get("temp_dir")
        if temp_dir_expired:
            shutil.rmtree(temp_dir_expired, ignore_errors=True)
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Session abgelaufen — bitte ZIP erneut hochladen"
        )

    # User-Bestätigungen übernehmen
    confirmed = {s.filename: _normalize_folder(s.suggested_folder) for s in payload.suggestions}

    job_id = uuid.uuid4()
    groups: dict[str, list[tuple[Path, str]]] = {}
    for entry in session["files"]:
        fname = entry["filename"]
        folder = confirmed.get(fname, _normalize_folder(entry["suggested_folder"]))
        groups.setdefault(folder, []).append((Path(entry["path"]), fname))

    for folder, files in groups.items():
        await enqueue_files(
            job_id=job_id,
            folder_path=folder,
            files=files,
            tags=[],
            uploaded_by=ctx.ui_user.id if ctx.ui_user else None,
        )

    total = sum(len(f) for f in groups.values())
    log.info("suggest.apply_zip.queued", job_id=str(job_id), total=total)

    # Temp-Verzeichnis NICHT sofort löschen — der Queue-Worker liest die Dateien
    # noch asynchron (ingest_file mit keep_source=False → shutil.move).
    # Stattdessen: verzögertes Cleanup nach _DEFERRED_CLEANUP_DELAY Sekunden.
    temp_dir_path = session.get("temp_dir")
    if temp_dir_path:
        _deferred_cleanup_dirs[temp_dir_path] = time.time()

    return {"job_id": str(job_id), "total": total}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_folder(path: str) -> str:
    p = path.strip().replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    if not p.endswith("/"):
        p += "/"
    while "//" in p:
        p = p.replace("//", "/")
    return p


async def _store_snippet(doc_id: str, max_chars: int = 300) -> str:
    """Erster Text-Chunk aus dem Store (LanceDB) für ein Dokument."""
    from pipelines import store
    try:
        chunks = await asyncio.to_thread(
            store.filter_by_meta,
            {
                "operator": "AND",
                "conditions": [
                    {"field": "meta.doc_id", "operator": "==", "value": doc_id}
                ],
            },
        )
        if chunks:
            chunks_sorted = sorted(
                chunks,
                key=lambda c: (c.meta or {}).get("page") or 0,
            )
            return (chunks_sorted[0].content or "")[:max_chars]
    except Exception as exc:
        log.warning("suggest.store_snippet_failed", doc_id=doc_id, error=str(exc))
    return ""


async def _parse_snippet(path: Path, max_chars: int = 300) -> str:
    """Liest erste Seite einer Datei für Text-Snippet (ohne Ingest)."""
    try:
        from ingest.parsers import parse_file   # lazy: fitz/magic (nur Writer)

        parsed = await asyncio.to_thread(parse_file, path)
        return (parsed.full_text or "")[:max_chars]
    except Exception as exc:
        log.warning("suggest.parse_snippet_failed", path=str(path), error=str(exc))
    return ""


def _to_response(
    suggestions: list[FolderSuggestion],
    temp_id: str | None = None,
) -> SuggestResponse:
    return SuggestResponse(
        suggestions=[
            SuggestionItem(
                doc_id=s.doc_id,
                filename=s.filename,
                current_folder=s.current_folder,
                suggested_folder=s.suggested_folder,
                reason=s.reason,
            )
            for s in suggestions
        ],
        temp_id=temp_id,
    )
