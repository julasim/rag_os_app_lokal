# ==============================================================================
# API-Container: FastAPI (REST + MCP) + React-Frontend (statisch gebaut)
#
# Multi-Stage-Build:
#   Stage 1: Node.js baut das React/Vite-Frontend (frontend/ → /frontend/dist)
#   Stage 2: Python-App + statische Frontend-Dateien in einem Image
#
# Ein Prozess im Container, gesteuert von supervisord:
#   - uvicorn → FastAPI auf :8000 (REST + MCP-Route /mcp + Static-UI auf /)
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1 — Frontend-Build
# ------------------------------------------------------------------------------
FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY app/frontend/package*.json ./
RUN npm ci --silent
COPY app/frontend/ .
RUN npm run build
# Output landet in /frontend/dist

# ------------------------------------------------------------------------------
# Stage 2 — Reranker-Export (Wegwerf): bge-reranker-v2-m3 → INT8-ONNX
#
# torch + optimum leben NUR in dieser Stage. Das Ergebnis (model.onnx +
# model_quantized.onnx + Tokenizer, ~1 GB) wird per `COPY --from` ins
# Serving-Image übernommen — torch/sentence-transformers landen NICHT im
# Endimage (spart ~3 GB). Die schwere Torch-Last lebt sonst nur noch im
# rag-ingest-Worker (Docling, siehe Dockerfile.ingest).
# ------------------------------------------------------------------------------
FROM python:3.12-slim AS reranker-build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_DISABLE_TELEMETRY=1
RUN pip install --upgrade pip \
    && pip install --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install "optimum[onnxruntime]>=1.23" "transformers>=4.44"
COPY app/scripts/onnx_export_reranker.py /tmp/onnx_export_reranker.py
RUN python /tmp/onnx_export_reranker.py /export/reranker

# ------------------------------------------------------------------------------
# Stage 3 — Python-App (Serving, torch-frei)
# ------------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System-Abhängigkeiten: Parser + OCR (Tesseract DE/EN) + Health-Tools
#
# WICHTIG — postgresql-client-16 explizit (nicht das unversionierte Paket):
# Der DB-Server ist postgres:16. Das Debian-Standardpaket `postgresql-client`
# zieht auf aktuellen Basis-Images den Client 17, dessen pg_dump ein
# Archiv-Format (1.16) schreibt, das der postgres:16-Server NICHT restaurieren
# kann ("unsupported version"). Darum den Client passend zum Server pinnen —
# via offiziellem PGDG-Repo.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo $VERSION_CODENAME)-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ghostscript \
        libmagic1 \
        postgresql-client-16 \
        supervisor \
        tesseract-ocr \
        tesseract-ocr-deu \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python-Abhängigkeiten zuerst (Cache-freundlich)
COPY app/pyproject.toml ./
# KEIN torch mehr im Serving-Image (M1.3): der Reranker läuft jetzt über
# onnxruntime auf einem gebackenen INT8-Modell (siehe Stage `reranker-build`).
# Die App-Deps (onnxruntime/transformers/numpy) sind torch-frei. Das spart
# ~3 GB gegenüber dem sentence-transformers/torch-Stack.
RUN pip install --upgrade pip \
    && pip install -e .

# App-Code
COPY app/ /app

# React-Build-Output kopieren
COPY --from=frontend-build /frontend/dist /app/ui_static

# Gebackenes INT8-ONNX-Reranker-Modell + Tokenizer (aus der Wegwerf-Stage).
# NACH /opt/models (NICHT /models — dort liegt ein Bind-Mount, der die
# gebackenen Dateien überdecken würde).
# --chown=1000:1000 setzt den Owner SCHON beim COPY (uid 1000 = appuser, der
# erst später angelegt wird → numerisch statt per Name). Ein nachträgliches
# `chown -R /opt/models` würde die ~0,6 GB Modell-Layer sonst voll DUPLIZIEREN.
COPY --chown=1000:1000 --from=reranker-build /export/reranker /opt/models/reranker

# Supervisord-Konfig
COPY app/supervisord.conf /etc/supervisor/conf.d/rag.conf

# --------------------------------------------------------------------------
# Non-Root-Benutzer (uid 1000) — kein root im laufenden Container.
#
# uid 1000 passt zum Server-Playbook: die Bind-Mounts (/data/uploads,
# /data/backups) müssen auf dem Host uid 1000 gehören, sonst EACCES.
# Die hier gesetzten Rechte greifen für /app und als Fallback für frische
# /data-Volumes; bei Bind-Mounts entscheidet der Host-Ownership.
# --------------------------------------------------------------------------
# /opt/models bewusst NICHT hier chownen — die Reranker-Dateien sind schon per
# COPY --chown=1000:1000 dem appuser übereignet (sonst dupliziert `chown -R` die
# GB-Modell-Layer). Hier nur /app + /data.
RUN useradd -u 1000 -m -s /usr/sbin/nologin appuser \
    && mkdir -p /data/uploads /data/backups \
    && chown -R appuser:appuser /app /data

USER appuser

# Port: 8000 (FastAPI REST+MCP+Static-UI)
EXPOSE 8000

# Health-Check: /api/health muss grün sein
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/rag.conf", "-n"]
