"""
Factory: Embeddings (INT8-ONNX via onnxruntime) + Store-Zugriff.

Lokale Variante: **kein Haystack, kein Qdrant, kein Ollama, kein fastembed.**
Dense-Embeddings laufen über ein **INT8-quantisiertes e5-large-ONNX**
(`models_dir/embedder`, vom Build gebacken — analog zum Reranker) direkt über
onnxruntime: Tokenize → Modell → Mean-Pooling → L2-Normalisierung. INT8 ist
~3,2× schneller auf CPU als fp32 und 4× kleiner (561 MB statt 2,2 GB), bei
praktisch identischer Retrieval-Qualität (Vektor-Treue 0,99). Der Vektor-Store
ist LanceDB (`pipelines/store.py`); die lexikalische Seite (BM25) ist die FTS.

Die `get_*`-Shims unten halten noch-nicht-umverdrahtete Call-Sites importierbar.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from config import settings
from logger import log

# Kompat-Konstante (graph/l2.py, backup/engine.py importieren sie noch).
COLLECTION_NAME = "chunks"

# INT8 bevorzugen (kleiner + schneller), sonst fp32-Fallback.
_ONNX_CANDIDATES = ("model_quantized.onnx", "model.onnx")
_MAX_LENGTH = 512
_BATCH = 32


# ---------------------------------------------------------------------------
# Dense-Embeddings (INT8-ONNX, onnxruntime — kein torch/fastembed)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _embed_model():
    """Lädt Tokenizer + ONNX-Session einmalig (prozessweit gecached).
    Rückgabe: (tokenizer, session, input_names)."""
    import os

    import onnxruntime as ort
    from transformers import AutoTokenizer

    model_dir = str(settings().models_dir / "embedder")
    onnx_path = next(
        (os.path.join(model_dir, n) for n in _ONNX_CANDIDATES
         if os.path.exists(os.path.join(model_dir, n))),
        None,
    )
    if onnx_path is None:
        raise FileNotFoundError(
            f"Kein ONNX-Embedder in {model_dir} ({'/'.join(_ONNX_CANDIDATES)})"
        )
    log.info("embedder.load", model_dir=model_dir, onnx=os.path.basename(onnx_path))
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(onnx_path, sess_options=so, providers=["CPUExecutionProvider"])
    input_names = {i.name for i in session.get_inputs()}
    log.info("embedder.ready", inputs=sorted(input_names))
    return tokenizer, session, input_names


def _embed(payloads: list[str]) -> list[list[float]]:
    """Batched Mean-Pooling-Embedding (L2-normalisiert) über die INT8-Session."""
    if not payloads:
        return []
    tok, session, names = _embed_model()
    out: list[list[float]] = []
    for i in range(0, len(payloads), _BATCH):
        batch = payloads[i:i + _BATCH]
        enc = tok(batch, padding=True, truncation=True,
                  max_length=_MAX_LENGTH, return_tensors="np")
        feeds = {"input_ids": enc["input_ids"].astype(np.int64),
                 "attention_mask": enc["attention_mask"].astype(np.int64)}
        if "token_type_ids" in names:
            feeds["token_type_ids"] = np.zeros_like(enc["input_ids"], dtype=np.int64)
        last = session.run(None, feeds)[0]                     # (B, T, H)
        mask = enc["attention_mask"][:, :, None].astype(np.float32)
        pooled = (last * mask).sum(1) / np.clip(mask.sum(1), 1e-9, None)
        pooled = pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)
        out.extend(pooled.astype(np.float32).tolist())
    return out


def embed_query(text: str, model: str | None = None) -> list[float]:
    """e5-Query-Präfix (asymmetrisch zu 'passage:' beim Ingest)."""
    return _embed([f"query: {text}"])[0]


def embed_texts(texts: list[str], model: str | None = None) -> list[list[float]]:
    """e5-Passage-Präfix für Dokument-Chunks."""
    return _embed([f"passage: {t}" for t in texts])


def warmup_embedder() -> None:
    try:
        embed_query("warmup")
        log.info("embedder.warmup_done")
    except Exception as e:  # noqa: BLE001 — Warmup darf den Start nie kippen
        log.warning("embedder.warmup_failed", error=str(e))


# ---------------------------------------------------------------------------
# Store-Zugriff
# ---------------------------------------------------------------------------
def get_store():
    from pipelines import store
    return store


def ensure_collection() -> None:
    """No-Op: LanceDB legt die Tabelle beim ersten Write an."""
    return None


def reset_collection() -> None:
    from pipelines import store
    store.reset()


# ---------------------------------------------------------------------------
# Rückwärtskompat-Shims (werfen beim Aufruf; Call-Sites werden ersetzt)
# ---------------------------------------------------------------------------
def get_vector_store():
    raise RuntimeError("get_vector_store entfernt — pipelines.store nutzen (M3)")


def get_embedder(model: str | None = None):
    raise RuntimeError("get_embedder(Ollama) entfernt — factory.embed_texts nutzen (M3/M4)")


def get_text_embedder(model: str | None = None):
    raise RuntimeError("get_text_embedder(Ollama) entfernt — factory.embed_query nutzen (M3/M4)")


def get_sparse_doc_embedder():
    raise RuntimeError("Sparse-Embedder entfällt — LanceDB-FTS (M3)")


def get_sparse_text_embedder():
    raise RuntimeError("Sparse-Embedder entfällt — LanceDB-FTS (M3)")
