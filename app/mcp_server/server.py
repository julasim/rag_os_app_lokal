"""
MCP-Server via Streamable-HTTP.

Wird von FastAPI auf /mcp gemountet. Jeder Request muss einen gültigen
API-Key im Authorization-Header mitbringen. Die allowed_folders-Liste des
Keys bestimmt, welche Ordner sichtbar sind (leere Liste = alles).

Tools:
  - rag_retrieve
  - rag_list_documents
  - rag_get_document
  - rag_upload        (nur MCP-Admin; Write-Härtung/TOTP siehe Track E5)
  - rag_stats

Löschen gibt es bewusst NICHT über MCP (Track E) — nur über die Web-UI (Admin).
"""
from __future__ import annotations

import hashlib
import shutil
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select
from starlette.requests import Request
from starlette.responses import JSONResponse

from auth import totp
from auth.folders import (
    accessible_folder_paths,
    key_allows_folder,
    user_accessible_folder_paths,
    user_allows_folder,
)
from auth.keys import verify_api_key
from config import settings
from db.models import ApiKey, Document, DocumentStatus, UiUser, UserRole
from db.session import get_session
from ingest.queue import enqueue_files
from logger import log
from mcp_server import audit, oauth
from pipelines.query import run_retrieve


# Aktiver API-Key des laufenden Requests (Middleware setzt ihn).
_current_key: ContextVar[ApiKey | None] = ContextVar("current_key", default=None)


# ---------------------------------------------------------------------------
# OAuth-Key-Stellvertreter (Modulebene, damit MCPAuthMiddleware darauf zugreifen kann)
# ---------------------------------------------------------------------------
class _OAuthPrincipal:
    """
    Synthetischer „Key" für OAuth-authentifizierte Requests, gebaut aus dem
    echten UiUser hinter dem Token. Duck-typed das ApiKey-Interface, das die
    MCP-Tools nutzen (`id`/`scopes`/`allowed_folders`), trägt zusätzlich die
    **echte Rolle + per-User-ACL** (Track E).

    Semantik (fail-safe):
      - `role` + `access_all` + `allowed_folders` stammen 1:1 aus dem UiUser.
      - `scopes` aus der Rolle abgeleitet: Admin → read+write (Löschen NIE über
        MCP), normaler User → nur read. Kein Principal bekommt hier `delete`.
      - `access_all` markiert diesen Principal als User-Pfad (vs. Bearer-Key,
        der kein `access_all`-Attribut trägt) — die MCP-Tools verzweigen darüber.
      - `id = None` → `QueryLog.api_key_id` bleibt None; die Identität trägt
        `user_id` (kein FK-Bruch, ist kein API-Key).
    """
    def __init__(
        self,
        user_id: str,
        email: str,
        role: str,
        access_all: bool,
        allowed_folders: list[str],
    ) -> None:
        self.id = None
        self.user_id = user_id
        self.email = email
        self.role = role
        self.access_all = access_all
        self.allowed_folders = list(allowed_folders or [])
        self.scopes = (
            ["read", "write"] if role == UserRole.ADMIN.value else ["read"]
        )


async def _resolve_oauth_principal(claims: dict) -> "_OAuthPrincipal | None":
    """Löst den Token-`sub` frisch zum lebenden UiUser auf (De-facto-Revocation).

    Rolle + ACL werden bei JEDEM Request frisch geladen → eine Rechte-Änderung
    (oder Löschung) des Users greift sofort.
    """
    sub = claims.get("sub")
    try:
        uid = UUID(str(sub))
    except (ValueError, TypeError):
        return None
    async with get_session() as s:
        user = (await s.execute(select(UiUser).where(UiUser.id == uid))).scalar_one_or_none()
    if not user:
        return None
    return _OAuthPrincipal(
        user_id=str(user.id),
        email=user.email,
        role=user.role,
        access_all=user.access_all,
        allowed_folders=list(user.allowed_folders or []),
    )


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


async def _require_mcp_admin_totp(
    totp_code: str | None, file_path: str, folder_path: str, tags: list[str]
) -> None:
    """
    Track E5 — MCP-Write nur für den EINEN designierten MCP-Admin MIT gültigem,
    single-use TOTP. `write`-Scope allein genügt NICHT (sonst Bearer-Bypass):

      1. Principal muss der OAuth-User `settings().resolved_mcp_admin_email`
         (Rolle admin) sein — ein Bearer-Key hat weder `email` noch `user_id`
         und fällt hier immer durch.
      2. Für ihn muss TOTP eingerichtet (`totp_enabled`) sein.
      3. Der Code muss gültig, nicht verbraucht und das Konto nicht gesperrt sein
         (harter Lockout nach 5 Fehlversuchen).

    Der Erfolg wird an die konkrete Aktion (file/folder/tags) audit-gebunden.
    """
    k = _key()
    email = getattr(k, "email", None)
    uid = getattr(k, "user_id", None)
    role = getattr(k, "role", None)
    if (
        email is None
        or uid is None
        or role != UserRole.ADMIN.value
        or email.lower() != settings().resolved_mcp_admin_email.lower()
    ):
        raise PermissionError("rag_upload ist nur dem MCP-Admin erlaubt")
    if not totp_code:
        raise PermissionError("TOTP-Code erforderlich (zweiter Faktor für MCP-Write)")
    if totp.is_locked(uid):
        raise PermissionError("Konto wegen zu vieler TOTP-Fehlversuche gesperrt")

    async with get_session() as s:
        row = (
            await s.execute(
                select(UiUser.totp_secret, UiUser.totp_enabled).where(
                    UiUser.id == UUID(uid)
                )
            )
        ).first()
    if not row or not row.totp_enabled or not row.totp_secret:
        raise PermissionError("Für den MCP-Admin ist kein TOTP eingerichtet")
    if not totp.check_and_consume(uid, row.totp_secret, str(totp_code)):
        raise PermissionError("TOTP ungültig, bereits verbraucht oder Konto gesperrt")

    action_hash = hashlib.sha256(
        f"{file_path}|{folder_path}|{','.join(sorted(tags))}".encode()
    ).hexdigest()[:16]
    log.info("mcp.upload.totp_ok", user=email, action_hash=action_hash)


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
    async def rag_get_document(doc_id: str) -> dict[str, Any]:
        """Hole Metadaten eines einzelnen Dokuments."""
        _require_scope("read")
        async with get_session() as s:
            result = await s.execute(
                select(Document).where(Document.id == UUID(doc_id))
            )
            d = result.scalar_one_or_none()
        if not d:
            return {"error": "not_found"}
        _require_folder(d.folder_path)
        return {
            "id": str(d.id),
            "folder_path": d.folder_path,
            "file_name": d.file_name,
            "mime_type": d.mime_type,
            "size_bytes": d.size_bytes,
            "tags": list(d.tags or []),
            "status": d.status,
            "chunk_count": d.chunk_count,
            "uploaded_at": d.uploaded_at.isoformat(),
            "indexed_at": d.indexed_at.isoformat() if d.indexed_at else None,
        }

    # --- rag_upload ---------------------------------------------------------
    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False})
    async def rag_upload(
        file_path: str,
        folder_path: str = "/",
        tags: list[str] | None = None,
        totp_code: str | None = None,
    ) -> dict[str, Any]:
        """
        Reiht eine Datei vom lokalen Dateisystem des Servers zur Indexierung ein.
        Achtung: Pfad muss im Container erreichbar sein (typ. /data/uploads/…).

        Schreibende Aktion — nur der designierte MCP-Admin darf sie ausführen und
        MUSS als zweiten Faktor `totp_code` (6-stelliger Code aus seiner
        Authenticator-App) mitgeben. Der Code ist einmalig verwendbar; nach
        mehreren Fehlversuchen wird das Konto vorübergehend gesperrt.

        Der Ingest läuft ASYNCHRON: die Datei wird ins geteilte Staging-Volume
        kopiert (Original bleibt erhalten) und in die Ingest-Queue gestellt; der
        separate rag-ingest-Worker verarbeitet sie. Rückgabe ist die `job_id` —
        der Fortschritt lässt sich (REST) über GET /api/documents/jobs/{job_id}
        verfolgen.

        Args:
            file_path: Serverpfad der Datei (z.B. /data/uploads/x.pdf).
            folder_path: Zielordner in der Sammlung.
            tags: optionale Tags.
            totp_code: aktueller 6-stelliger TOTP-Code des MCP-Admins (Pflicht).
        """
        _require_scope("write")
        _require_folder(folder_path)
        await _require_mcp_admin_totp(totp_code, file_path, folder_path, tags or [])
        p = Path(file_path)
        if not p.exists():
            return {"error": "file_not_found", "path": str(p)}

        # Track C3b: kein synchroner Ingest mehr im api-Prozess. Quelle worker-
        # lesbar ins geteilte Staging-Volume KOPIEREN (shutil.copy2 → Original
        # bleibt, entspricht dem bisherigen keep_source=True) und asynchron
        # einreihen. Der Worker räumt die Staging-Kopie nach dem Ingest weg.
        staging = settings().staging_dir
        staging.mkdir(parents=True, exist_ok=True)
        staged = staging / f"{uuid4().hex}_{p.name}"
        shutil.copy2(p, staged)

        job_id = uuid4()
        user_id = getattr(_key(), "user_id", None)
        await enqueue_files(
            job_id=job_id,
            folder_path=folder_path,
            files=[(staged, p.name)],
            tags=tags or [],
            uploaded_by=UUID(user_id) if user_id else None,
        )
        return {"job_id": str(job_id), "status": "queued"}

    # rag_delete_document wurde ENTFERNT (Track E, Sicherheitsmodell):
    # Löschen ist ausschließlich über die Web-UI möglich (`require_ui_admin`),
    # NIE über MCP — auch nicht für Admins oder Bearer-Keys mit delete-Scope.
    # So kann ein kompromittierter/über-berechtigter MCP-Client keine Dokumente
    # (DSGVO-relevant) löschen.

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

    Dual-Auth-Reihenfolge:
      1. OAuth-JWT (wenn OAUTH_JWT_SECRET gesetzt)
      2. Bearer-API-Key aus der DB
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

        # 1. Versuch: OAuth-JWT → frisch zum UiUser auflösen
        if oauth.is_enabled():
            claims = oauth.verify_access_token(token)
            if claims:
                principal = await _resolve_oauth_principal(claims)
                if principal is None:
                    # Gültiger Token, aber User existiert nicht mehr → 401.
                    resp = JSONResponse({"error": "invalid_token"}, status_code=401)
                    await resp(scope, receive, send)
                    return
                tok = _current_key.set(principal)  # type: ignore[arg-type]
                try:
                    await self._app(scope, receive, send)
                finally:
                    _current_key.reset(tok)
                return

        # 2. Versuch: statischer Bearer-API-Key
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
