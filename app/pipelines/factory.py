"""
Factory: Embeddings (fastembed/ONNX) + Store-Zugriff.

Lokale Variante: **kein Haystack, kein Qdrant, kein Ollama.** Dense-Embeddings
laufen über fastembed (ONNX) mit `bge-m3`; der Vektor-Store ist LanceDB
(`pipelines/store.py`); die lexikalische Seite (BM25) ist LanceDBs FTS.

Die `get_*`-Shims unten halten noch-nicht-umverdrahtete Call-Sites importierbar
(sie werfen erst beim AUFRUF) — werden in M3-Rest/M5 an den Stellen ersetzt.
"""
from __future__ import annotations

from functools import lru_cache

from config import settings
from logger import log

# Kompat-Konstante (graph/l2.py, backup/engine.py importieren sie noch).
COLLECTION_NAME = "chunks"


# ---------------------------------------------------------------------------
# Dense-Embeddings (fastembed/ONNX)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=2)
def _embedder(model: str):
    from fastembed import TextEmbedding
    log.info("embedder.load", model=model)
    return TextEmbedding(model_name=model)


def embed_query(text: str, model: str | None = None) -> list[float]:
    m = _embedder(model or settings().embed_model)
    return list(m.embed([text]))[0].tolist()


def embed_texts(texts: list[str], model: str | None = None) -> list[list[float]]:
    m = _embedder(model or settings().embed_model)
    return [[float(x) for x in v] for v in m.embed(texts)]


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


def enable_quantization(enable: bool = True) -> dict:
    """No-Op: LanceDB braucht keine Qdrant-Named-Vector-Quantisierung."""
    return {"enabled": False, "note": "LanceDB: keine Quantisierung nötig"}


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


def get_generator(*args, **kwargs):
    raise RuntimeError("LLM/Ollama entfernt — deterministisches Tagging (M5)")
