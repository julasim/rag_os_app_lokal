"""
Strukturiertes JSON-Logging.

Alle Logs gehen als JSON nach stdout → Docker-Logs → `docker compose logs api`.
Einheitliches Format macht später Parsing/Monitoring trivial.
"""
from __future__ import annotations

import logging
import sys

import structlog

from config import settings


def setup_logging() -> None:
    level = getattr(logging, settings().log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
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
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger("rag")
