"""
Backt ALLE lokalen KI-Modelle in build/models/ (der Installer legt sie nach
%LOCALAPPDATA%\\RAG-OS\\models):

  Query (IMMER, beide Installer):
  - Reranker bge-reranker-v2-m3  -> build/models/reranker/    (INT8-ONNX)
  - Embedder e5-large (fastembed) -> build/models/fastembed/   (ONNX)

  Ingest (NUR Schreiber; Reader-Installer excludet sie):
  - Docling Layout + TableFormer -> build/models/docling/      (artifacts_path)
  - e5-large-Tokenizer           -> build/models/e5-tokenizer/ (HybridChunker)

Warum gebündelt statt First-Run-Download (M8f): der erste Ingest einer frischen
Installation rannte mit dem HF-Runtime-Download um die Wette -> "Missing safe
tensors file". Gebündelt + artifacts_path -> kein Netz, kein Race, air-gapped.

Aufruf (im Build):  python build/fetch-models.py
Benoetigt Build-Deps: optimum[onnxruntime] + torch (Reranker-Export), fastembed,
docling, transformers. Alle in der Writer-Build-Umgebung (app[writer,dev]) vorhanden.
"""
from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = Path(__file__).resolve().parent / "models"

_EMBED_MODEL = "intfloat/multilingual-e5-large"


def export_reranker() -> None:
    out = MODELS / "reranker"
    out.mkdir(parents=True, exist_ok=True)
    script = ROOT / "app" / "scripts" / "onnx_export_reranker.py"
    print(f"==> Reranker-ONNX-Export -> {out}")
    subprocess.run([sys.executable, str(script), str(out)], check=True)


def fetch_embedder() -> None:
    out = MODELS / "fastembed"
    out.mkdir(parents=True, exist_ok=True)
    print(f"==> e5-large (fastembed) laden -> {out}")
    from fastembed import TextEmbedding

    emb = TextEmbedding(model_name=_EMBED_MODEL, cache_dir=str(out))
    # Ein Embed erzwingt den vollstaendigen Download der ONNX-Dateien.
    list(emb.embed(["warmup"]))
    print("    fertig.")


def fetch_docling() -> None:
    """Docling Layout + TableFormer nach build/models/docling (artifacts_path-Layout).
    Nur die fuer born-digital PDFs noetigen Modelle; OCR/Code/Picture bewusst aus."""
    out = MODELS / "docling"
    out.mkdir(parents=True, exist_ok=True)
    print(f"==> Docling Layout + TableFormer laden -> {out}")
    from docling.utils.model_downloader import download_models

    # Nur die von der installierten Docling-Version tatsaechlich unterstuetzten
    # with_*-Flags uebergeben (variiert je Release). Alles ausser Layout/TableFormer aus.
    want = {
        "with_layout": True,
        "with_tableformer": True,
        "with_easyocr": False,
        "with_code_formula": False,
        "with_picture_classifier": False,
        "with_smolvlm": False,
        "with_smoldocling": False,
        "with_granite_vision": False,
    }
    params = inspect.signature(download_models).parameters
    kwargs = {k: v for k, v in want.items() if k in params}
    if "progress" in params:
        kwargs["progress"] = True
    download_models(output_dir=out, **kwargs)
    print("    fertig.")


def fetch_chunk_tokenizer() -> None:
    """e5-large-Tokenizer nach build/models/e5-tokenizer (HybridChunker, offline)."""
    out = MODELS / "e5-tokenizer"
    out.mkdir(parents=True, exist_ok=True)
    print(f"==> e5-large-Tokenizer laden -> {out}")
    from transformers import AutoTokenizer

    AutoTokenizer.from_pretrained(_EMBED_MODEL).save_pretrained(str(out))
    print("    fertig.")


if __name__ == "__main__":
    export_reranker()
    fetch_embedder()
    fetch_docling()
    fetch_chunk_tokenizer()
    print(f"\nAlle Modelle gebacken unter {MODELS}")
