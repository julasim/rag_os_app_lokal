"""
MCP-Server via Streamable-HTTP.

Wird von FastAPI auf /mcp gemountet. Jeder Request muss einen gültigen
Bearer-API-Key im Authorization-Header mitbringen. Die allowed_folders-Liste
des Keys bestimmt, welche Ordner sichtbar sind (leere Liste = alles).

Lokale Variante: **Bearer-only** (kein OAuth), **read-only** — Upload/Löschen
laufen über die lokale Verwaltungs-UI bzw. den Überwachungsordner (Single-Writer),
nicht über MCP.

Tools (alle read-only):
  - rag_overview       kompakte Bestands-Karte (zuerst laden, dann drillen)
  - rag_retrieve       Hybrid-Suche → Chunks (der Such-Endpunkt)
  - norm_lookup        Norm exakt über norm_id finden
  - rag_list_documents Dokumentliste (optional Ordner)
  - rag_get_document   Metadaten + Volltext eines Dokuments
  - rag_stats          globale Zahlen
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select
from starlette.requests import Request
from starlette.responses import JSONResponse

from auth.folders import (
    accessible_folder_paths,
    key_allows_folder,
    user_accessible_folder_paths,
    user_allows_folder,
)
from auth.keys import verify_api_key
from db.models import ApiKey, Document, DocumentChunk, DocumentStatus
from db.session import get_session
from graph.canonical import canonical_norm_id
from mcp_server import audit
from pipelines.query import run_retrieve


# Aktiver API-Key des laufenden Requests (Middleware setzt ihn).
_current_key: ContextVar[ApiKey | None] = ContextVar("current_key", default=None)


def _key() -> ApiKey:
    k = _current_key.get()
    if not k:
        raise PermissionError("No authenticated API key in context")
    return k


def _require_folder(folder_path: str) -> None:
    k = _key()
    aa = getattr(k, "access_all", None)
    af = getattr(k, "allowed_folders", None)
    if aa is None:
        allowed = key_allows_folder(af, folder_path)          # Bearer (leer=alles)
    else:
        allowed = user_allows_folder(aa, af, folder_path)     # User/OAuth (fail-safe)
    if not allowed:
        raise PermissionError(f"not allowed for folder '{folder_path}'")


def _require_scope(scope: str) -> None:
    k = _key()
    if scope not in k.scopes:
        raise PermissionError(f"API key missing scope '{scope}'")


async def _accessible_paths(s, folder: str | None = None):
    """Erlaubte Ordnerpfade (inkl. Unterordner) des aktiven Keys. None = alles,
    [] = nichts. Bearer vs. User/OAuth über das `access_all`-Duck-Typing."""
    k = _key()
    aa = getattr(k, "access_all", None)
    af = getattr(k, "allowed_folders", None)
    if aa is None:
        return await accessible_folder_paths(af, folder, s)
    return await user_accessible_folder_paths(aa, af, folder, s)


# ---------------------------------------------------------------------------
# MCP-App bauen
# ---------------------------------------------------------------------------
def build_mcp_app() -> FastMCP:
    # stateless_http=True: jeder POST bekommt einen eigenen frischen Transport
    # und gibt eine synchrone JSON-Antwort zurück — kein SSE-Session-Handshake
    # nötig. Claude.ai und andere MCP-Clients unterstützen diesen Modus explizit.
    mcp = FastMCP("sima-rag", stateless_http=True, json_response=True)

    # --- rag_retrieve --------------------------------------------------------
    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    @audit.time_call
    async def rag_retrieve(
        query: str,
        folder: str | None = None,
        top_k: int | None = None,
        doc_type: str | None = None,
        language: str | None = None,
        only_current: bool = False,
    ) -> dict[str, Any]:
        """
        Liefert die Top-K relevanten Dokument-Chunks zu einer Frage —
        OHNE eigene LLM-Antwort. Du (der aufrufende Client-LLM) formulierst
        die Antwort selbst auf Basis dieser Chunks und ihrer Quellen.

        Der einzige Such-Endpunkt des Systems (Hybrid-Suche: semantisch +
        exaktes Keyword-Matching für Normnummern/§/Codes).

        WICHTIG — so nutzt du das Ergebnis:
          - Antworte AUSSCHLIESSLICH auf Basis der zurückgegebenen Chunks.
          - Steht die Antwort nicht in den Chunks, sage klar: "Das ist in der
            Dokumenten-Sammlung nicht enthalten." Nichts erfinden, keine
            Normnummern/Werte raten.
          - Zitiere JEDE Aussage mit dem mitgelieferten `citation`-Feld des
            Chunks (Datei, Seite, Abschnitt) — wörtlich übernehmen.
          - Ist ein Chunk `outdated: true`, weise darauf hin, dass es eine
            neuere/gültige Fassung geben kann, und nenne — falls vorhanden —
            `superseded_by`.

        Args:
            query: Die Frage in natürlicher Sprache.
            folder: Optional — nur in diesem Ordner suchen (z.B. "/Ausschreibungen/").
            top_k: Anzahl der Chunks (1–50, default 5).

        Returns:
            {chunks: [{doc_id, file_name, folder_path, page, section_title,
                       section_path, score, text, tags, citation, doc_type,
                       norm_id, doc_version, outdated, superseded_by}],
             latency_ms, query}
        """
        _require_scope("read")
        # Expliziten Ordner-Wunsch hart prüfen (klares 403 statt leerer Antwort);
        # ohne folder erzwingt run_retrieve die ACL über allowed_folders selbst.
        if folder:
            _require_folder(folder)
        k = _key()
        uid = getattr(k, "user_id", None)
        result = await run_retrieve(
            question=query,
            folder=folder,
            top_k=top_k,
            api_key_id=k.id,
            allowed_folders=k.allowed_folders,
            access_all=getattr(k, "access_all", None),
            user_id=UUID(uid) if uid else None,
            doc_type=doc_type,
            language=language,
            only_current=only_current,
        )
        return {
            "query": query,
            "chunks": [c.__dict__ for c in result.chunks],
            "latency_ms": result.latency_ms,
        }

    # --- rag_list_documents --------------------------------------------------
    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    async def rag_list_documents(
        folder: str | None = None,
    ) -> list[dict[str, Any]]:
        """Liste alle indexierten Dokumente (optional gefiltert nach Ordner)."""
        _require_scope("read")

        k = _key()
        aa = getattr(k, "access_all", None)
        af = getattr(k, "allowed_folders", None)

        async with get_session() as s:
            # Kanonische Ordner-ACL: erlaubte Ordner (inkl. Unterordner) auflösen.
            if aa is None:
                paths = await accessible_folder_paths(af, folder, s)      # Bearer
            else:
                paths = await user_accessible_folder_paths(aa, af, folder, s)  # User/OAuth
            stmt = select(Document)
            if paths is not None:
                if not paths:
                    return []  # nichts zugänglich → nicht ungefiltert listen
                stmt = stmt.where(Document.folder_path.in_(paths))
            stmt = stmt.order_by(Document.uploaded_at.desc()).limit(500)

            result = await s.execute(stmt)
            return [
                {
                    "id": str(d.id),
                    "file_name": d.file_name,
                    "folder_path": d.folder_path,
                    "status": d.status,
                    "chunk_count": d.chunk_count,
                    "tags": list(d.tags or []),
                    "uploaded_at": d.uploaded_at.isoformat(),
                    "indexed_at": d.indexed_at.isoformat() if d.indexed_at else None,
                }
                for d in result.scalars().all()
            ]

    # --- rag_get_document ----------------------------------------------------
    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    async def rag_get_document(doc_id: str, include_text: bool = True) -> dict[str, Any]:
        """Hole Metadaten — und optional den **Volltext** — eines Dokuments.

        Der Volltext wird aus den kanonischen Child-Chunks (in Ingest-Reihenfolge)
        rekonstruiert — verbatim, ohne LLM-Paraphrase. Setze `include_text=False`
        für nur Metadaten (z.B. große Dokumente)."""
        _require_scope("read")
        async with get_session() as s:
            d = (await s.execute(
                select(Document).where(Document.id == UUID(doc_id))
            )).scalar_one_or_none()
            if not d:
                return {"error": "not_found"}
            _require_folder(d.folder_path)
            full_text = None
            if include_text:
                rows = (await s.execute(
                    select(DocumentChunk.text)
                    .where(DocumentChunk.doc_id == d.id, DocumentChunk.level == "child")
                    .order_by(DocumentChunk.ordinal)
                )).scalars().all()
                full_text = "\n\n".join(t for t in rows if t and t.strip())
        return {
            "id": str(d.id),
            "folder_path": d.folder_path,
            "file_name": d.file_name,
            "mime_type": d.mime_type,
            "size_bytes": d.size_bytes,
            "tags": list(d.tags or []),
            "status": d.status,
            "chunk_count": d.chunk_count,
            "doc_type": d.doc_type,
            "norm_id": d.norm_id,
            "doc_version": d.doc_version,
            "valid_status": d.valid_status,
            "uploaded_at": d.uploaded_at.isoformat(),
            "indexed_at": d.indexed_at.isoformat() if d.indexed_at else None,
            "full_text": full_text,
        }

    # --- rag_overview --------------------------------------------------------
    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    async def rag_overview() -> dict[str, Any]:
        """Kompakte Bestands-Karte der Dokumenten-Sammlung — **zuerst aufrufen**,
        um dich zu orientieren, dann gezielt mit `rag_retrieve` / `norm_lookup`
        drillen. Zeigt Ordner (mit Doc-Zahlen), Norm-Abdeckung (Top-norm_ids) und
        die häufigsten Tags — ACL-beschränkt auf die für deinen Key sichtbaren Ordner.
        """
        from collections import Counter

        _require_scope("read")
        async with get_session() as s:
            paths = await _accessible_paths(s)
            stmt = select(Document).where(Document.status == DocumentStatus.INDEXED.value)
            if paths is not None:
                if not paths:
                    return {"total_documents": 0, "total_chunks": 0,
                            "folders": [], "top_norms": [], "top_tags": []}
                stmt = stmt.where(Document.folder_path.in_(paths))
            docs = (await s.execute(stmt)).scalars().all()

        folders = Counter(d.folder_path for d in docs)
        norms = Counter(d.norm_id for d in docs if d.norm_id)
        tags = Counter(t for d in docs for t in (d.tags or []))
        return {
            "total_documents": len(docs),
            "total_chunks": sum(d.chunk_count or 0 for d in docs),
            "folders": [{"folder": f, "documents": n} for f, n in sorted(folders.items())],
            "top_norms": [{"norm_id": nm, "documents": n} for nm, n in norms.most_common(20)],
            "top_tags": [{"tag": t, "count": n} for t, n in tags.most_common(20)],
        }

    # --- norm_lookup ---------------------------------------------------------
    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    async def norm_lookup(norm_id: str, only_current: bool = False) -> dict[str, Any]:
        """Findet alle Dokumente zu einer Norm über die **kanonische** norm_id
        (z.B. "ÖNORM B 1801-1", "EN 1992", "DIN 276"). Kanonisierung trennt
        Geschwister-Normen sauber (…-1 ≠ …-2) und egalisiert Schreibvarianten.

        `only_current=True` blendet abgelöste Fassungen aus. Ergebnis nennt je
        Treffer `valid_status`/`superseded_by`, damit du auf die gültige Fassung
        verweisen kannst."""
        _require_scope("read")
        target, _version = canonical_norm_id(norm_id)
        if not target:
            return {"norm_id": norm_id, "canonical": None, "matches": []}
        async with get_session() as s:
            paths = await _accessible_paths(s)
            stmt = select(Document).where(Document.norm_id.isnot(None))
            if paths is not None:
                if not paths:
                    return {"norm_id": norm_id, "canonical": target, "matches": []}
                stmt = stmt.where(Document.folder_path.in_(paths))
            docs = (await s.execute(stmt)).scalars().all()

        matches = []
        for d in docs:
            key, _v = canonical_norm_id(d.norm_id or "")
            if key != target:
                continue
            if only_current and d.valid_status == "superseded":
                continue
            matches.append({
                "doc_id": str(d.id),
                "file_name": d.file_name,
                "folder_path": d.folder_path,
                "norm_id": d.norm_id,
                "doc_version": d.doc_version,
                "valid_status": d.valid_status,
                "superseded_by": str(d.superseded_by) if d.superseded_by else None,
            })
        return {"norm_id": norm_id, "canonical": target, "matches": matches}

    # Kein rag_upload/rag_delete über MCP: MCP ist read-only. Schreiben läuft
    # lokal (Verwaltungs-UI / Überwachungsordner, Single-Writer).

    # --- rag_stats -----------------------------------------------------------
    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    async def rag_stats() -> dict[str, Any]:
        """Globale Zahlen zur Sammlung: Dokumente, Chunks, Größe."""
        _require_scope("read")
        async with get_session() as s:
            doc_count = await s.scalar(
                select(func.count())
                .select_from(Document)
                .where(
                    Document.status == DocumentStatus.INDEXED.value,
                )
            )
            chunk_count = await s.scalar(
                select(func.coalesce(func.sum(Document.chunk_count), 0))
            )
            size_bytes = await s.scalar(
                select(func.coalesce(func.sum(Document.size_bytes), 0))
            )
        return {
            "document_count": int(doc_count or 0),
            "chunk_count": int(chunk_count or 0),
            "size_bytes": int(size_bytes or 0),
        }

    return mcp


# ---------------------------------------------------------------------------
# Pure-ASGI-Auth-Middleware — streaming-kompatibel (kein BaseHTTPMiddleware)
# ---------------------------------------------------------------------------
class MCPAuthMiddleware:
    """
    Pure-ASGI-Middleware für MCP-Auth.

    Kein BaseHTTPMiddleware — leitet `send` direkt durch, sodass FastMCP's
    SSE-Streaming ungepuffert zum Client fließt.

    Auth: **Bearer-API-Key** aus der DB (lokale Variante — kein OAuth).
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive)
        auth = request.headers.get("authorization", "")

        if not auth.lower().startswith("bearer "):
            resp = JSONResponse(
                {"error": "missing_bearer_token"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="rag-os"'},
            )
            await resp(scope, receive, send)
            return

        token = auth.split(" ", 1)[1].strip()

        # Statischer Bearer-API-Key
        key = await verify_api_key(token)
        if not key:
            resp = JSONResponse({"error": "invalid_api_key"}, status_code=401)
            await resp(scope, receive, send)
            return

        tok = _current_key.set(key)
        try:
            await self._app(scope, receive, send)
        finally:
            _current_key.reset(tok)
