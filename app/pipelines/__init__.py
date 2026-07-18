"""Pipelines: Store (LanceDB), Embeddings (fastembed/ONNX), Retrieval, Reranker.

Bewusst KEINE Eager-Imports (früher zog das Haystack + erzeugte Import-Zyklen).
Module direkt importieren: `from pipelines import store`, `from pipelines.query
import run_retrieve`, `from pipelines.factory import embed_query`.
"""
