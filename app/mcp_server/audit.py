"""
MCP Audit-Log.

Schreibt jede Tool-Anfrage und Auth-Aktion als JSONL in eine Datei.
Übernommen und angepasst aus julasim/MCP-Template.
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import time
from pathlib import Path
from typing import Any

from logger import log

_AUDIT_LOG = Path(os.environ.get("MCP_AUDIT_LOG", "/data/mcp-audit.log"))

# Argument-Keys, die geschwärzt werden
_SENSITIVE = frozenset({"token", "password", "secret", "api_key", "confirm_token"})
# Text-Args die bei >100 Zeichen abgekürzt werden
_LARGE_TEXT = frozenset({"body", "text", "content", "query"})


def _mask(key: str, val: Any) -> Any:
    if isinstance(val, str):
        if key in _SENSITIVE:
            return "***REDACTED***"
        if key in _LARGE_TEXT and len(val) > 100:
            return val[:100] + f"...<+{len(val) - 100}ch>"
    return val


def _write(record: dict[str, Any]) -> None:
    """Schreibt einen Audit-Record als JSONL (append). Blockierend — nur aus Thread aufrufen."""
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        log.warning("mcp.audit.write_failed", error=str(exc))


async def _awrite(record: dict[str, Any]) -> None:
    await asyncio.to_thread(_write, record)


def time_call(fn):
    """Dekorator: Misst Latenz und schreibt tool_call-Record."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        masked_args = {k: _mask(k, v) for k, v in kwargs.items()}
        error: str | None = None
        try:
            result = await fn(*args, **kwargs)
            return result
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            latency_ms = int((time.perf_counter() - start) * 1000)
            await _awrite({
                "ts": time.time(),
                "kind": "tool_call",
                "tool": fn.__name__,
                "args": masked_args,
                "latency_ms": latency_ms,
                "error": error,
            })
    return wrapper
