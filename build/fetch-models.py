"""
Backt die QUERY-Modelle (Hybrid-Strategie — kommen IMMER in beide Installer):
  - Reranker bge-reranker-v2-m3  → build/models/reranker/   (INT8-ONNX)
  - Embedder bge-m3 (fastembed)  → build/models/fastembed/  (ONNX)

Docling-Layout/TableFormer + RapidOCR + bge-m3-Tokenizer werden BEWUSST NICHT
gebacken — die lädt der Schreiber beim ersten Ingest herunter (First-Run).

Aufruf (im Build):  python build/fetch-models.py
Benötigt Build-Deps: optimum[onnxruntime] + torch (nur für den Reranker-Export),
fastembed. Beides ist in der Writer-Build-Umgebung vorhanden.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = Path(__file__).resolve().parent / "models"


def export_reranker() -> None:
    out = MODELS / "reranker"
    out.mkdir(parents=True, exist_ok=True)
    script = ROOT / "app" / "scripts" / "onnx_export_reranker.py"
    print(f"==> Reranker-ONNX-Export -> {out}")
    subprocess.run([sys.executable, str(script), str(out)], check=True)


def fetch_embedder() -> None:
    out = MODELS / "fastembed"
    out.mkdir(parents=True, exist_ok=True)
    print(f"==> bge-m3 (fastembed) laden -> {out}")
    from fastembed import TextEmbedding

    emb = TextEmbedding(model_name="intfloat/multilingual-e5-large", cache_dir=str(out))
    # Ein Embed erzwingt den vollständigen Download der ONNX-Dateien.
    list(emb.embed(["warmup"]))
    print("    fertig.")


if __name__ == "__main__":
    export_reranker()
    fetch_embedder()
    print(f"\nQuery-Modelle gebacken unter {MODELS}")
