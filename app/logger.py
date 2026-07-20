"""
Strukturiertes JSON-Logging.

Logs gehen als JSON-Zeilen (a) nach stdout, wenn vorhanden, und (b) IMMER in eine
rotierende Datei unter %LOCALAPPDATA%\\RAG-OS\\logs\\ragos.log. Die native Shell
(pywebview, windowed) hat KEIN stdout — ohne Datei-Handler waeren die Logs weg
bzw. ein print wuerde crashen. Einheitliches JSON-Format macht Parsing trivial.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

import structlog

from config import settings


def setup_logging() -> None:
    level = getattr(logging, settings().log_level.upper(), logging.INFO)

    handlers: list[logging.Handler] = []

    # (a) stdout — nur wenn vorhanden (im Terminal/uvicorn-CLI); in der windowed
    #     Shell ist sys.stdout None und wird uebersprungen.
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(stream=sys.stdout))

    # (b) rotierende Log-Datei (immer). Verzeichnis defensiv anlegen.
    try:
        log_dir = settings().appstate_db_path.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "ragos.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handlers.append(file_handler)
    except Exception:  # noqa: BLE001 — Logging-Setup darf den Start nie kippen
        pass

    logging.basicConfig(
        format="%(message)s",
        level=level,
        handlers=handlers,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        # Über das stdlib-Logging routen (nicht PrintLogger), damit die Log-Datei
        # ebenfalls beschrieben wird.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger("rag")
