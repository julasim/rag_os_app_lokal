"""
Post-Retrieval-Reranker via ONNX-Runtime (torch-frei).

Modell: BAAI/bge-reranker-v2-m3 — multilingual (gut für Deutsch), als
INT8-quantisiertes ONNX ins Serving-Image gebacken (siehe Dockerfile,
Stage `reranker-build`). Damit trägt das Serving-Image **kein torch/
sentence-transformers** mehr (~3 GB schlanker) — die schwere Torch-Last
lebt nur noch im rag-ingest-Worker (Docling).

Lazy geladen (erster Aufruf) und dann prozessweit gecached. `warmup()`
zieht das Laden bewusst nach vorne (Lifespan-Hintergrundtask), damit die
erste echte Suche nicht den Lade-Preis zahlt.

Ranking-Äquivalenz zu sentence-transformers: der CrossEncoder gibt für
bge-reranker EIN Logit je (query, passage)-Paar zurück. Ob darauf noch ein
Sigmoid liegt oder nicht, ändert die REIHENFOLGE nicht (streng monoton) —
und `rerank` nutzt ausschließlich die Reihenfolge. Wir nehmen daher das rohe
Logit als Score.
"""
from __future__ import annotations

import os
from functools import lru_cache

import numpy as np

from logger import log
from pipelines.doc import RetrievedDoc as HayDoc

# Verzeichnis mit dem gebackenen ONNX-Modell + Tokenizer (Dockerfile:
# COPY --from=reranker-build ... /opt/models/reranker). Fixer Deploy-Pfad —
# bewusst KEINE os.environ-Config (Konvention: nur settings()); Tests setzen
# dieses Modul-Attribut direkt.
_MODEL_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    "RAG-OS", "models", "reranker",
)
# INT8-quantisiertes Modell bevorzugen (kleiner + schneller auf CPU), sonst
# auf das fp32-Export zurückfallen.
_ONNX_CANDIDATES = ("model_quantized.onnx", "model.onnx")
_MAX_LENGTH = 512


@lru_cache(maxsize=1)
def _model():
    """Lädt Tokenizer + ONNX-Session einmalig. Rückgabe: (tokenizer, session, input_names)."""
    import onnxruntime as ort
    from transformers import AutoTokenizer

    onnx_path = next(
        (os.path.join(_MODEL_DIR, name)
         for name in _ONNX_CANDIDATES
         if os.path.exists(os.path.join(_MODEL_DIR, name))),
        None,
    )
    if onnx_path is None:
        raise FileNotFoundError(
            f"Kein ONNX-Reranker in {_MODEL_DIR} ({'/'.join(_ONNX_CANDIDATES)})"
        )

    log.info("reranker.load", model_dir=_MODEL_DIR, onnx=os.path.basename(onnx_path))
    # Tokenizer strikt lokal (offline) — die Dateien sind mitgebacken.
    tokenizer = AutoTokenizer.from_pretrained(_MODEL_DIR, local_files_only=True)

    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(onnx_path, sess_options=so, providers=["CPUExecutionProvider"])
    input_names = {i.name for i in session.get_inputs()}
    log.info("reranker.ready", inputs=sorted(input_names))
    return tokenizer, session, input_names


def warmup() -> None:
    """Lädt Modell + Tokenizer vorab (Lifespan-Hintergrundtask). Schluckt Fehler."""
    try:
        tokenizer, session, input_names = _model()
        enc = tokenizer(
            ["warmup"], ["warmup"],
            padding=True, truncation=True, max_length=8, return_tensors="np",
        )
        feeds = {k: v.astype(np.int64) for k, v in enc.items() if k in input_names}
        session.run(None, feeds)
        log.info("reranker.warmup_done")
    except Exception as e:  # noqa: BLE001 — Warmup darf den Start nie kippen
        log.warning("reranker.warmup_failed", error=str(e))


def rerank(query: str, docs: list[HayDoc], top_k: int) -> list[HayDoc]:
    """
    Re-rankt `docs` nach Relevanz zu `query`, gibt die besten `top_k` zurück.

    Fällt bei Fehler (Modell nicht verfügbar, OOM) auf die Original-Reihenfolge
    zurück — kein harter Absturz.
    """
    if len(docs) <= 1:
        return docs[:top_k]

    try:
        tokenizer, session, input_names = _model()
        enc = tokenizer(
            [query] * len(docs),
            [d.content or "" for d in docs],
            padding=True,
            truncation=True,
            max_length=_MAX_LENGTH,
            return_tensors="np",
        )
        feeds = {k: v.astype(np.int64) for k, v in enc.items() if k in input_names}
        logits = session.run(None, feeds)[0]          # (N, 1) bei seq-classification, num_labels=1
        scores = np.asarray(logits).reshape(-1)        # → (N,)
        order = np.argsort(-scores)                    # absteigend
        result = [docs[i] for i in order[:top_k]]
        log.info("reranker.done", input=len(docs), output=len(result))
        return result
    except Exception as e:  # noqa: BLE001 — Reranker-Fehler darf Retrieval nie kippen
        log.warning("reranker.failed", error=str(e))
        return docs[:top_k]
