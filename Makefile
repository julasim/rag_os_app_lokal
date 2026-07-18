# ==============================================================================
# SIMA RAG — Makefile
# Alle täglichen Operationen als ein Befehl.
# Auf dem VPS ausführen. Braucht: docker, docker compose, jq.
# ==============================================================================

.DEFAULT_GOAL := help

# .env-Datei prüfen, bevor wir irgendwas starten
ENV_FILE := .env
ifneq ("$(wildcard $(ENV_FILE))","")
  include $(ENV_FILE)
  export
endif

.PHONY: help
help:
	@echo ""
	@echo "SIMA RAG — verfügbare Kommandos"
	@echo ""
	@echo "  make up              Alle Dienste im Hintergrund starten"
	@echo "  make down            Alle Dienste stoppen"
	@echo "  make restart         Kompletter Neustart"
	@echo "  make logs            Live-Logs aller Dienste"
	@echo "  make logs-api        Nur API-Logs"
	@echo ""
	@echo "  make build           API-Image bauen"
	@echo "  make rebuild         API-Image neu bauen (ohne Cache)"
	@echo ""
	@echo "  make models          BGE-M3 + Qwen 2.5 3B pullen (einmalig)"
	@echo "  make models-status   Zeigt geladene Modelle"
	@echo ""
	@echo "  make health          Health-Check (öffentlich)"
	@echo "  make shell-api       Shell im API-Container"
	@echo "  make shell-db        psql im Postgres"
	@echo "  make shell-ollama    Shell im Ollama-Container"
	@echo ""
	@echo "  make backup          Snapshot Qdrant + pg_dump in ./backups/"
	@echo "  make clean-logs      Docker-Log-Rotation manuell"
	@echo ""

# ---------- Lifecycle ----------
.PHONY: up down restart
up:
	docker compose up -d

down:
	docker compose down

restart: down up

# ---------- Logs ----------
.PHONY: logs logs-api logs-caddy
logs:
	docker compose logs -f --tail=200

logs-api:
	docker compose logs -f --tail=200 api

logs-caddy:
	docker compose logs -f --tail=200 caddy

# ---------- Build ----------
.PHONY: build rebuild
build:
	docker compose build api

rebuild:
	docker compose build --no-cache api
	docker compose up -d api

# ---------- Modelle ----------
.PHONY: models models-status
models:
	@echo "→ Pulle Embedding-Modell (BGE-M3, ~1.2 GB)…"
	docker compose exec -T ollama ollama pull bge-m3
	@echo "→ Pulle LLM (Qwen 2.5 3B, ~2 GB)…"
	docker compose exec -T ollama ollama pull qwen2.5:3b-instruct
	@echo "✓ Modelle bereit."

models-status:
	docker compose exec -T ollama ollama list

# ---------- Shells ----------
.PHONY: shell-api shell-db shell-ollama
shell-api:
	docker compose exec api /bin/bash

shell-db:
	docker compose exec postgres psql -U $(POSTGRES_USER) $(POSTGRES_DB)

shell-ollama:
	docker compose exec ollama /bin/bash

# ---------- Health ----------
.PHONY: health
health:
	@if [ -z "$(RAG_DOMAIN)" ] || [ "$(RAG_DOMAIN)" = "localhost" ]; then \
	  URL="http://localhost:8000/api/health"; \
	  echo "Local-Only-Modus erkannt → $$URL"; \
	else \
	  URL="https://$(RAG_DOMAIN)/api/health"; \
	  echo "Public-Modus → $$URL"; \
	fi; \
	curl -fsS $$URL | jq . || \
	  (echo "Health-Check fehlgeschlagen — Logs prüfen: make logs-api"; exit 1)

# ---------- Backup ----------
.PHONY: backup
backup:
	@mkdir -p backups
	@DATE=$$(date +%Y%m%d-%H%M%S); \
	echo "→ pg_dump…"; \
	docker compose exec -T postgres pg_dump -U $(POSTGRES_USER) $(POSTGRES_DB) \
	  | gzip > backups/postgres-$$DATE.sql.gz; \
	echo "→ Qdrant-Snapshot…"; \
	docker compose exec -T qdrant sh -c 'cd /qdrant/storage && tar czf - .' \
	  > backups/qdrant-$$DATE.tar.gz; \
	echo "✓ Backup nach ./backups/*-$$DATE.*"

# ---------- Wartung ----------
.PHONY: clean-logs
clean-logs:
	docker compose logs --no-color > /dev/null
	@echo "Tipp: dauerhaft begrenzen via /etc/docker/daemon.json (log-driver: json-file, max-size: 100m, max-file: 3)"
