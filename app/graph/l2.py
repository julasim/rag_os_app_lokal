"""L2-Ähnlichkeitsschicht des Wissensgraphs (Track D / M3c).

Zwei symmetrische Relationen auf Dokument-Ebene (als EINE ungeordnete Kante
gespeichert, `src_key` lexikografisch < `tgt_key`):

  similar_to  Semantische Nähe: Cosine der Doc-Zentroide (Mittel der dichten
              bge-m3-Chunk-Vektoren aus Qdrant), sparsifiziert über **mutual-kNN**
              (A in Top-k von B UND B in Top-k von A) + Schwelle τ.
  near_dup    Beinah-Duplikate: **eigene MinHash** (128 Permutationen) über
              Wort-Shingles des Doc-Texts, Kandidaten via **LSH (b,r)=16×8**,
              dann geschätzte Jaccard ≥ τ_dup.

MinHash ist deterministisch (fixer Seed) → reproduzierbare Signaturen →
idempotente Kanten. Voller L2-Rebuild (löscht nur `layer='L2'`; L1 unberührt).
Setzt voraus, dass **L1 die document-Nodes bereits angelegt** hat — der
Rebuild-Endpoint und der Nachtlauf rufen L1 vor L2.

Bewusste Abweichung vom Plan-Wortlaut „Qdrant-kNN": bei der (kleinen) Büro-
Brain-Korpusgröße werden die Zentroide **exakt** in numpy verglichen (besser als
ANN und deterministisch). Bei starkem Wachstum auf Qdrant-ANN umstellbar.
"""
from __future__ import annotations

import asyncio
import zlib
from collections import defaultdict
from typing import NamedTuple

import numpy as np
from sqlalchemy import select

from config import settings
from db.models import Document, DocumentChunk, DocumentStatus, GraphEdge
from db.session import get_session
from logger import log
from pipelines.factory import COLLECTION_NAME

__all__ = ["BuildStatsL2", "build_l2"]


# --- MinHash-Konstanten (deterministisch) ---------------------------------
_NUM_PERM = 128
_LSH_BANDS = 16
_LSH_ROWS = 8            # 16 * 8 = 128 = _NUM_PERM
_MERSENNE = np.uint64(4294967311)   # kleinste Primzahl > 2^32
# Fixer Seed → dieselben Permutationen bei jedem Lauf → idempotente Signaturen.
_rng = np.random.default_rng(1801)
_A = _rng.integers(1, 2**32, size=_NUM_PERM, dtype=np.uint64)
_B = _rng.integers(0, 2**32, size=_NUM_PERM, dtype=np.uint64)


class BuildStatsL2(NamedTuple):
    documents: int
    similar_to: int
    near_dup: int


def _shingle_hashes(text: str, w: int) -> np.ndarray:
    """Menge der Wort-Shingle-Hashes (uint64 < 2^32) eines Dokuments."""
    words = text.split()
    if not words:
        return np.empty(0, dtype=np.uint64)
    if len(words) < w:
        shingles = {" ".join(words)}
    else:
        shingles = {" ".join(words[i:i + w]) for i in range(len(words) - w + 1)}
    return np.fromiter(
        (zlib.crc32(s.encode("utf-8")) & 0xFFFFFFFF for s in shingles),
        dtype=np.uint64,
        count=len(shingles),
    )


def _minhash(hashes: np.ndarray) -> np.ndarray | None:
    """MinHash-Signatur (128,) uint64 — universelles Hashing (a·x+b) mod p.

    Kein Overflow: a,x,b < 2^32 → a·x+b < 2^64 (passt in uint64), dann mod p.
    """
    if hashes.size == 0:
        return None
    m = (_A[:, None] * hashes[None, :] + _B[:, None]) % _MERSENNE
    return m.min(axis=1)


def _neardup_pairs(doc_ids: list[str], texts: list[str], w: int, tau: float
                   ) -> list[tuple[str, str, float]]:
    """near_dup-Paare via MinHash + LSH-Banding, geschätzte Jaccard ≥ tau."""
    sigs: dict[str, np.ndarray] = {}
    for did, txt in zip(doc_ids, texts):
        s = _minhash(_shingle_hashes(txt, w))
        if s is not None:
            sigs[did] = s

    buckets: dict[tuple[int, bytes], list[str]] = defaultdict(list)
    for did, s in sigs.items():
        bands = s.reshape(_LSH_BANDS, _LSH_ROWS)
        for bi in range(_LSH_BANDS):
            buckets[(bi, bands[bi].tobytes())].append(did)

    candidates: set[tuple[str, str]] = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                candidates.add((a, b) if a < b else (b, a))

    out: list[tuple[str, str, float]] = []
    for a, b in candidates:
        est = float(np.mean(sigs[a] == sigs[b]))
        if est >= tau:
            out.append((a, b, est))
    return out


def _similar_pairs(doc_id_set: set[str], top_k: int, tau: float
                   ) -> list[tuple[str, str, float]]:
    """similar_to-Paare: Doc-Zentroide (dense Qdrant-Vektoren) → mutual-kNN ≥ tau."""
    from qdrant_client import QdrantClient

    client = QdrantClient(url=settings().qdrant_url, api_key=settings().qdrant_api_key)
    acc: dict[str, list] = {}  # doc_id -> [sum_vec, count]
    try:
        offset = None
        while True:
            points, offset = client.scroll(
                collection_name=COLLECTION_NAME,
                with_vectors=["text-dense"],
                with_payload=["meta"],
                limit=256,
                offset=offset,
            )
            for p in points:
                meta = (p.payload or {}).get("meta") or {}
                did = meta.get("doc_id")
                if not did or did not in doc_id_set:
                    continue
                vec = p.vector.get("text-dense") if isinstance(p.vector, dict) else p.vector
                if vec is None:
                    continue
                v = np.asarray(vec, dtype=np.float32)
                if did not in acc:
                    acc[did] = [v.copy(), 1]
                else:
                    acc[did][0] += v
                    acc[did][1] += 1
            if offset is None:
                break
    finally:
        client.close()

    ids = list(acc.keys())
    if len(ids) < 2:
        return []
    mat = np.vstack([acc[d][0] / acc[d][1] for d in ids])
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = mat / norms
    sim = unit @ unit.T
    np.fill_diagonal(sim, -1.0)

    topk: dict[int, set[int]] = {}
    for i in range(len(ids)):
        order = np.argsort(sim[i])[::-1][:top_k]
        topk[i] = {int(j) for j in order if sim[i][j] >= tau}

    out: list[tuple[str, str, float]] = []
    for i in range(len(ids)):
        for j in topk[i]:
            if i < j and i in topk[j]:  # mutual
                a, b = ids[i], ids[j]
                lo, hi = (a, b) if a < b else (b, a)
                out.append((lo, hi, float(sim[i][j])))
    return out


async def build_l2() -> BuildStatsL2:
    """Baut die L2-Schicht (similar_to + near_dup) neu nach graph_edges."""
    log.info("graph.build.l2.start")
    cfg = settings()

    async with get_session() as s:
        indexed = (
            await s.execute(
                select(Document.id).where(Document.status == DocumentStatus.INDEXED.value)
            )
        ).scalars().all()
    doc_id_set = {str(d) for d in indexed}

    async with get_session() as s:
        rows = (
            await s.execute(
                select(DocumentChunk.doc_id, DocumentChunk.text)
                .where(DocumentChunk.level == "child")
                .order_by(DocumentChunk.doc_id, DocumentChunk.ordinal)
            )
        ).all()
    texts_by_doc: dict[str, list[str]] = defaultdict(list)
    for did, txt in rows:
        if str(did) in doc_id_set:
            texts_by_doc[str(did)].append(txt or "")
    nd_ids = list(texts_by_doc.keys())
    nd_texts = [" ".join(texts_by_doc[d]) for d in nd_ids]

    near = await asyncio.to_thread(
        _neardup_pairs, nd_ids, nd_texts, cfg.graph_shingle_size, cfg.graph_neardup_threshold
    )
    sim = await asyncio.to_thread(
        _similar_pairs, doc_id_set, cfg.graph_sim_top_k, cfg.graph_sim_threshold
    )

    edge_rows: list[dict] = []
    for a, b, score in sim:
        edge_rows.append({
            "src_key": f"document:{a}", "tgt_key": f"document:{b}",
            "relation": "similar_to", "layer": "L2",
            "confidence": round(score, 4), "w_eff": round(score, 4),
        })
    for a, b, score in near:
        edge_rows.append({
            "src_key": f"document:{a}", "tgt_key": f"document:{b}",
            "relation": "near_dup", "layer": "L2",
            "confidence": round(score, 4), "w_eff": round(score, 4),
        })

    async with get_session() as s:
        await s.execute(GraphEdge.__table__.delete().where(GraphEdge.layer == "L2"))
        if edge_rows:
            await s.execute(GraphEdge.__table__.insert(), edge_rows)

    stats = BuildStatsL2(
        documents=len(doc_id_set), similar_to=len(sim), near_dup=len(near)
    )
    log.info("graph.build.l2.done", **stats._asdict())
    return stats
