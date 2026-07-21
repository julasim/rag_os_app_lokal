"""
Zentraler FastAPI-Einstiegspunkt.

Verbindet alle Komponenten:
  - REST-API-Router (/api/*)
  - MCP-Server (/mcp/*) mit eigener Auth-Middleware
  - Startup: DB-Schema + Admin-User + MCP-Session-Manager + Folder-Watcher
  - Shutdown: Clean-up von Watcher + DB-Pool
"""
from __future__ import annotations

# Air-gapped (M8f): HF-Offline-Flags MÜSSEN gesetzt sein, BEVOR irgendein transitiver
# huggingface_hub-Import (via transformers/docling) das Flag import-zeitig
# cached. Deshalb ganz oben, vor allen App-Importen. Die KI-Modelle sind gebündelt
# (Installer → %LOCALAPPDATA%\RAG-OS\models); ohne diese Zeilen lädt Docling/HF beim
# ersten Ingest zur Laufzeit nach (Race → „Missing safe tensors file"). Dev kann mit
# HF_HUB_OFFLINE=0 übersteuern.
import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from starlette.types import ASGIApp, Receive, Scope, Send

from api import (
    auth_router,
    documents_router,
    keys_router,
    maintenance_router,
    suggest_router,
    system_router,
    users_router,
)
from config import settings
from db.session import dispose, init_db
from logger import log, setup_logging
# ingest.queue / ingest.watcher werden LAZY im Writer-Zweig des Lifespan importiert
# (ziehen die schwere Docling/torch/Legacy-Parser-Last) → der Leser bleibt schlank.
from mcp_server import build_mcp_app, MCPAuthMiddleware
from mcp_server.ratelimit import MCPRateLimitMiddleware
from pipelines.factory import ensure_collection


# ---------------------------------------------------------------------------
# DNS-Rebinding-Schutz im MCP-SDK deaktivieren (class-level Patch).
#
# TransportSecurityMiddleware lehnt standardmäßig alle Hosts außer
# "127.0.0.1:*" / "localhost:*" / "[::1]:*" ab — inklusive aller
# externen Hosts wie rag-os.sima.business.  Für einen öffentlichen
# HTTPS-Server hinter Caddy ist dieser Schutz bedeutungslos: TLS und
# der Edge-Proxy sind die eigentliche Sicherheitsschicht.
#
# Patch muss VOR build_mcp_app() stehen, da streamable_http_app()
# beim Aufruf eine TransportSecurityMiddleware-Instanz anlegt.
# ---------------------------------------------------------------------------
from mcp.server import transport_security as _mcp_ts


async def _ts_allow_all(self: object, *args: object, **kwargs: object) -> None:  # noqa: ANN001
    """Kein-Op-Ersatz für validate_request — lässt alle Hosts durch."""
    return None


_mcp_ts.TransportSecurityMiddleware.validate_request = _ts_allow_all  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# MCP-App wird VOR dem Lifespan gebaut, damit ihr Session-Manager im
# Lifespan-Kontext laufen kann.
# ---------------------------------------------------------------------------
_mcp = build_mcp_app()
_mcp_asgi = _mcp.streamable_http_app()


# ---------------------------------------------------------------------------
# Modell-Vorwärmung (Hintergrund) — lädt Reranker + Embedder einmalig, damit
# die erste echte Suche nicht den Lade-/Download-Preis zahlt.
# ---------------------------------------------------------------------------
async def _warmup_models() -> None:
    try:
        from config import global_config
        from pipelines.factory import warmup_embedder
        cfg = global_config()
        log.info("warmup.start")
        # Dense-Embedder (INT8-ONNX e5-large) einmalig laden. Die lexikalische
        # Seite ist LanceDBs FTS — kein Sparse-Embedder, kein Ollama mehr.
        await asyncio.to_thread(warmup_embedder)
        # ONNX-Reranker (INT8, gebacken) laden, falls aktiv
        if cfg.retrieval.rerank:
            from pipelines.reranker import warmup as rerank_warmup
            await asyncio.to_thread(rerank_warmup)
        log.info("warmup.done")
    except Exception as e:
        log.warning("warmup.failed", error=str(e))


async def _reader_refresh_loop(stop_event: asyncio.Event) -> None:
    """Reader (M8e): hält den lokalen Cache periodisch mit der veröffentlichten
    Vault-Version (`current`-Tag) synchron. Writer nutzt diesen Loop nicht."""
    from pipelines.publish import refresh_reader_cache
    interval = max(30, settings().reader_refresh_interval_sec)
    log.info("reader.refresh_loop_started", interval_sec=interval)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass
        try:
            await asyncio.to_thread(refresh_reader_cache)
            log.info("reader.cache_refreshed")
        except Exception as e:  # noqa: BLE001 — Refresh darf den Reader nie kippen
            log.warning("reader.cache_refresh_failed", error=str(e))
    log.info("reader.refresh_loop_stopped")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    log.info("app.boot", upload_dir=str(settings().upload_dir))

    # Upload-Verzeichnis sicherstellen
    settings().upload_dir.mkdir(parents=True, exist_ok=True)

    # DB-Schema + Admin-User
    await init_db()

    # Store-Collection sicherstellen (No-Op bei LanceDB — legt die Tabelle beim ersten Write an)
    try:
        ensure_collection()
        log.info("store.collection_ready")
    except Exception as e:
        log.warning("store.collection_failed", error=str(e))

    # Modelle im HINTERGRUND vorwärmen (Reranker ~2,4 GB + Embedder), damit die
    # erste echte Suche nicht den Lade-/Download-Preis zahlt. Blockiert den
    # App-Start bewusst NICHT (sonst 502, solange der Container 'starting' ist).
    # Referenz halten — sonst kann der GC den Task vor Abschluss einsammeln.
    warmup_task = asyncio.create_task(_warmup_models(), name="model-warmup")

    # --- Rollen-abhängige Hintergrund-Tasks (M8e) ---
    # Writer: Überwachungsordner + Ingest-Queue + Nachtlauf (Maintenance/Backup+Publish).
    # Reader: nur den lokalen Cache mit der veröffentlichten Vault-Version synchron halten
    # (kein Ingest, kein Docling/torch, keine Wartung/Backup).
    watcher = None
    queue_stop: asyncio.Event | None = None
    queue_task: asyncio.Task | None = None
    maint_stop: asyncio.Event | None = None
    maint_task: asyncio.Task | None = None
    backup_stop: asyncio.Event | None = None
    backup_task: asyncio.Task | None = None
    reader_stop: asyncio.Event | None = None
    reader_task: asyncio.Task | None = None

    if settings().runs_ingest_worker:
        # Lazy-Importe: ziehen die schwere Writer-Last (Docling/torch/Legacy-Parser).
        from backup.engine import nightly_backup_loop
        from ingest.queue import queue_worker_loop
        from ingest.watcher import FolderWatcher
        from maintenance.engine import nightly_maintenance_loop

        watcher = FolderWatcher()
        try:
            watcher.start()
        except Exception as e:
            log.warning("watcher.start_failed", error=str(e))
        queue_stop = asyncio.Event()
        queue_task = asyncio.create_task(
            queue_worker_loop(queue_stop), name="ingest-queue-worker"
        )
        maint_stop = asyncio.Event()
        maint_task = asyncio.create_task(
            nightly_maintenance_loop(maint_stop), name="maintenance-nightly"
        )
        settings().backup_dir.mkdir(parents=True, exist_ok=True)
        backup_stop = asyncio.Event()
        backup_task = asyncio.create_task(
            nightly_backup_loop(backup_stop), name="backup-nightly"
        )
    else:
        # Reader: einmal jetzt synchronisieren, dann periodisch (M8e).
        from pipelines.publish import refresh_reader_cache
        try:
            await asyncio.to_thread(refresh_reader_cache)
            log.info("reader.cache_synced")
        except Exception as e:
            log.warning("reader.cache_sync_failed", error=str(e))
        reader_stop = asyncio.Event()
        reader_task = asyncio.create_task(
            _reader_refresh_loop(reader_stop), name="reader-cache-refresh"
        )

    # Wichtig: MCP-Session-Manager muss explizit laufen, da die gemountete
    # Sub-App sonst keinen eigenen Lifespan bekommt.
    async with _mcp.session_manager.run():
        log.info("mcp.session_manager.started")
        yield

    # Shutdown
    warmup_task.cancel()  # i.d.R. längst fertig; Cancel eines fertigen Tasks ist No-op
    for ev in (queue_stop, maint_stop, backup_stop, reader_stop):
        if ev is not None:
            ev.set()
    for task in (queue_task, maint_task, backup_task, reader_task):
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=10)
            except asyncio.TimeoutError:
                task.cancel()
                log.warning("lifespan.task_force_stopped", task=task.get_name())
    if watcher is not None:
        watcher.stop()
    await dispose()
    log.info("app.shutdown")


# ---------------------------------------------------------------------------
# FastAPI-App
# ---------------------------------------------------------------------------
_OPENAPI_TAGS = [
    {"name": "documents", "description": "Dokumente hochladen, auflisten, löschen."},
    {"name": "keys", "description": "API-Keys erstellen und widerrufen."},
    {"name": "system", "description": "Health-Check."},
    {"name": "auth", "description": "UI-Login / Logout."},
    {"name": "ingest", "description": "Ingest-Job-Status für Bulk- und ZIP-Uploads."},
    {"name": "maintenance", "description": "Self-Maintenance: Tag-Konsolidierung, Duplikat-Erkennung, Undo-Log."},
]

# _fastapi ist die eigentliche FastAPI-Instanz. Am Ende wird `app` durch den
# _MCPRouter ersetzt, der /mcp-Requests direkt an FastMCP weitergibt.
# /docs, /redoc und /openapi.json nur ausliefern, wenn DOCS_ENABLED=true.
# Sonst legt die öffentlich erreichbare API ihre gesamte Oberfläche (alle
# Endpunkte + Schemas) anonym offen. Default: aus (secure by default).
_docs_on = settings().docs_enabled
_fastapi = FastAPI(
    title="RAG OS",
    version="0.1.0",
    description=(
        "Self-hosted Retrieval-as-a-Service. Suche läuft über den MCP-Endpunkt "
        "(`/mcp`, Tool `rag_retrieve`); die REST-API deckt Verwaltung ab "
        "(Dokumente, Keys, System, Wartung). Auth: `Authorization: Bearer <api_key>`."
    ),
    lifespan=lifespan,
    openapi_tags=_OPENAPI_TAGS,
    redirect_slashes=False,  # Verhindert 307-Redirects
    docs_url="/docs" if _docs_on else None,
    redoc_url="/redoc" if _docs_on else None,
    openapi_url="/openapi.json" if _docs_on else None,
)

# CORS: eigene Domain (für UI-Cookies mit allow_credentials=True).
# Wildcard + credentials wird vom Browser abgelehnt, daher explizit.
# Nur die echte Domain + der Vite-Dev-Server (localhost:5173). Der alte
# Streamlit-Origin (:8501) und das nackte http://localhost sind entfernt.
_allowed_origins = [
    f"https://{settings().rag_domain}",
    f"http://{settings().rag_domain}",
    "http://localhost:5173",   # Vite-Dev-Server (nur Entwicklung)
]
_fastapi.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST-Router
_fastapi.include_router(auth_router)
_fastapi.include_router(documents_router)
_fastapi.include_router(keys_router)
_fastapi.include_router(users_router)
_fastapi.include_router(maintenance_router)
_fastapi.include_router(suggest_router)
_fastapi.include_router(system_router)


# ---------------------------------------------------------------------------
# MCP-Middleware-Stack manuell verketten (kein add_middleware — pure ASGI,
# damit FastMCP's SSE-Streaming nicht von BaseHTTPMiddleware gepuffert wird)
#
#   [außen] MCPRateLimitMiddleware
#     └─ MCPAuthMiddleware
#          └─ _mcp_asgi  (FastMCP Starlette-App)
# ---------------------------------------------------------------------------
_mcp_stack = MCPRateLimitMiddleware(MCPAuthMiddleware(_mcp_asgi))


# ---------------------------------------------------------------------------
# React-Frontend (SPA) servieren
# ---------------------------------------------------------------------------
_FRONTEND_DIR = Path(__file__).parent / "ui_static"
if _FRONTEND_DIR.exists():
    _assets_dir = _FRONTEND_DIR / "assets"
    if _assets_dir.exists():
        _fastapi.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="frontend-assets")

    @_fastapi.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):  # noqa: ARG001
        return FileResponse(str(_FRONTEND_DIR / "index.html"))


# ---------------------------------------------------------------------------
# OpenAPI-Customization: Bearer-Auth-Scheme + Server-URL
# ---------------------------------------------------------------------------
def _custom_openapi() -> dict:
    if _fastapi.openapi_schema:
        return _fastapi.openapi_schema
    schema = get_openapi(
        title=_fastapi.title,
        version=_fastapi.version,
        description=_fastapi.description,
        routes=_fastapi.routes,
        tags=_OPENAPI_TAGS,
    )
    schema.setdefault("components", {})
    schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "RAG OS API Key",
            "description": "API-Key aus dem UI (Admin → API-Keys).",
        }
    }
    schema["security"] = [{"BearerAuth": []}]
    schema["servers"] = [
        {
            "url": f"https://{settings().rag_domain}",
            "description": "Production",
        }
    ]
    _fastapi.openapi_schema = schema
    return schema


_fastapi.openapi = _custom_openapi  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Top-Level ASGI-Router: /mcp → FastMCP, alles andere → FastAPI
#
# Warum kein app.mount("/mcp", ...)?
# Starlette-Mount strippt den Prefix (/mcp → /), FastMCP hat aber intern
# Route('/mcp', ...) und würde dann 404 sehen. Anstatt komplexe
# Pfad-Rewrite-Wrapper (die in der Praxis fragil sind), routet dieser
# Top-Level-Dispatcher /mcp-Requests direkt an FastMCP — ohne Stripping,
# FastMCP sieht exakt den Pfad '/mcp', den sein Router erwartet.
#
# Lifespan: scope["type"] == "lifespan" wird an _fastapi weitergegeben,
# sodass der FastAPI-Lifespan (DB-Init, Session-Manager etc.) normal läuft.
# ---------------------------------------------------------------------------
class _MCPRouter:
    """
    Top-Level-ASGI-Dispatcher.

    /mcp, /mcp/, /mcp/* → _mcp_asgi (FastMCP, mit Auth + Rate-Limit)
    alles andere          → _fastapi  (REST-API + Frontend)
    """

    def __init__(self, fastapi_app: ASGIApp, mcp_app: ASGIApp) -> None:
        self._fastapi = fastapi_app
        self._mcp = mcp_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            path: str = scope.get("path", "/")
            if path == "/mcp" or path == "/mcp/" or path.startswith("/mcp/"):
                # Scope kopieren — immer, weil wir mindestens den Host-Header
                # umschreiben müssen.
                #
                # Das MCP-SDK enthält einen DNS-Rebinding-Schutz
                # (mcp.server.transport_security), der nur "localhost" als
                # Host-Header akzeptiert. Für einen öffentlichen HTTPS-Server
                # hinter Caddy ist diese Prüfung bedeutungslos (TLS + Caddy
                # übernehmen die Absicherung), also setzen wir den Header auf
                # localhost, damit der SDK-Check durchkommt.
                # Header-Normalisierung für FastMCP-Kompatibilität:
                #
                # 1. host → "localhost"
                #    DNS-Rebinding-Schutz im MCP-SDK akzeptiert nur localhost.
                #
                # 2. accept → "application/json, text/event-stream"
                #    FastMCP verlangt beide Content-Types im Accept-Header.
                #    Claude.ai und andere Clients schicken oft nur
                #    "application/json" und erhalten sonst 406.
                _REQUIRED_ACCEPT = b"application/json, text/event-stream"
                new_headers = []
                for k, v in scope.get("headers", []):
                    if k == b"host":
                        new_headers.append((b"host", b"localhost"))
                    elif k == b"accept":
                        new_headers.append((b"accept", _REQUIRED_ACCEPT))
                    else:
                        new_headers.append((k, v))
                if not any(k == b"host" for k, _ in scope.get("headers", [])):
                    new_headers.append((b"host", b"localhost"))
                if not any(k == b"accept" for k, _ in scope.get("headers", [])):
                    new_headers.append((b"accept", _REQUIRED_ACCEPT))

                new_scope = {**scope, "headers": new_headers}

                # Normalisiere /mcp/ und /mcp/* → /mcp
                if path != "/mcp":
                    new_scope["path"] = "/mcp"
                    new_scope["raw_path"] = b"/mcp"

                await self._mcp(new_scope, receive, send)
                return
        # Lifespan + alle nicht-MCP-Requests → FastAPI
        await self._fastapi(scope, receive, send)


# `app` = Entry-Point für uvicorn (main:app)
app = _MCPRouter(_fastapi, _mcp_stack)
