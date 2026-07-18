"""IngestConfig — Konfiguration des layout-aware Ingest (Docling-basiert).

Die Defaults sind auf den **air-gapped / offline** Betrieb ausgelegt (siehe
C0-Spike-Ergebnis im Masterplan):

  * `artifacts_path` zeigt auf den vorab gezogenen Docling-Modell-Cache
    (`docling-tools models download`), sonst laedt Docling Layout/TableFormer
    zur Laufzeit vom HF-Hub -> im air-gapped Betrieb Absturz.
  * `offline=True` setzt `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`, damit
    huggingface_hub ausschliesslich den lokalen Cache nutzt.
  * `ocr="off"` fuer born-digital PDFs (Norm-PDFs mit Text-Layer) — vermeidet
    den RapidOCR-Runtime-Download. `auto`/`force` brauchen vorgebackene
    OCR-Modelle (Track C1b, Scan-Erkennung).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Env-Vorgabe fuer den Modell-Cache; vom Deploy gesetzt (Track C3).
_DEFAULT_ARTIFACTS = os.environ.get("DOCLING_ARTIFACTS_PATH") or None


@dataclass(slots=True)
class IngestConfig:
    ocr: str = "off"                       # "auto" | "force" | "off"
    child_tokens: int = 256                # Retrieval-Praezision
    parent_tokens: int = 1024              # LLM-Kontext
    tokenizer: str = "BAAI/bge-m3"         # auf Ziel-Embedding-Modell ausgerichtet
    artifacts_path: str | None = _DEFAULT_ARTIFACTS
    offline: bool = True
    lang_detect: bool = True

    def apply_offline_env(self) -> None:
        """Setzt die Offline-Env-Variablen idempotent (nur wenn offline)."""
        if self.offline:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
