"""
Rate-Limiter für den MCP-Endpunkt (Token-Bucket, pro Client-IP).

Übernommen und angepasst aus julasim/MCP-Template.
Konfiguration via MCP_RATE_LIMIT_PER_MIN (0 = deaktiviert).

Wichtig: Pure-ASGI-Middleware (kein BaseHTTPMiddleware) — damit SSE-Streaming
von FastMCP nicht gepuffert/blockiert wird.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

import hashlib

_LIMIT = int(os.environ.get("MCP_RATE_LIMIT_PER_MIN", "60"))
_WINDOW = 60.0  # Sekunden

# {key: (tokens_remaining, window_start)}
_buckets: dict[str, tuple[float, float]] = {}
_lock = threading.Lock()

# Pfade die vom Rate-Limit ausgenommen sind
_EXEMPT_PREFIXES = ("/health", "/.well-known/", "/oauth/")


def _bucket_key(request: Request) -> str:
    """
    Schlüssel für den Rate-Limit-Bucket.

    Sicherheitskritisch: NICHT den `X-Forwarded-For`-Header als Schlüssel
    verwenden — der ist client-kontrolliert. Ein Angreifer rotiert ihn und
    umgeht so das Limit komplett (nachgewiesen im Audit).

    Stattdessen:
      1. Wenn ein Bearer-API-Key vorliegt → pro IDENTITÄT limitieren
         (unspoofbar, und fairer als pro-IP hinter dem gemeinsamen Edge-Proxy).
      2. Sonst (unauthentifiziert) → echter Socket-Peer (request.client),
         nicht der XFF-Header.
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return "key:" + hashlib.sha256(token.encode()).hexdigest()[:32]
    peer = request.client.host if request.client else "unknown"
    return "ip:" + peer


# Ab dieser Größe werden abgelaufene Buckets opportunistisch entfernt, damit
# viele unterschiedliche Keys/IPs den Speicher nicht unbegrenzt wachsen lassen.
_PRUNE_THRESHOLD = 10_000


def _check(ip: str) -> bool:
    """True = erlaubt, False = abgelehnt."""
    if _LIMIT <= 0:
        return True
    now = time.monotonic()
    with _lock:
        if len(_buckets) > _PRUNE_THRESHOLD:
            for k in [k for k, (_, ws) in _buckets.items() if now - ws >= _WINDOW]:
                del _buckets[k]
        remaining, window_start = _buckets.get(ip, (_LIMIT, now))
        if now - window_start >= _WINDOW:
            remaining = _LIMIT
            window_start = now
        if remaining < 1:
            _buckets[ip] = (remaining, window_start)
            return False
        _buckets[ip] = (remaining - 1, window_start)
        return True


class MCPRateLimitMiddleware:
    """
    Pure-ASGI Rate-Limiter — kein BaseHTTPMiddleware.

    BaseHTTPMiddleware puffert den Response-Body und ist inkompatibel mit
    FastMCP's SSE-Streaming. Diese Klasse ist ein direkter ASGI-Callable
    und leitet den send-Callable unverändert durch.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            request = Request(scope)  # kein receive nötig für Header-Zugriff
            path = request.url.path
            if not any(path.startswith(p) for p in _EXEMPT_PREFIXES):
                key = _bucket_key(request)
                if not _check(key):
                    resp = JSONResponse(
                        {"error": "rate_limit_exceeded"},
                        status_code=429,
                        headers={"Retry-After": str(int(_WINDOW))},
                    )
                    await resp(scope, receive, send)
                    return
        await self._app(scope, receive, send)
