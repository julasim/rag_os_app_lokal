"""
Retrieval-Pipeline.

Ein öffentlicher Einstiegspunkt:

  - `run_retrieve(...)`:
        Liefert nur die Chunks + Quellen-Metadaten — kein LLM-Call. Der
        konsumierende Client (Claude / GPT / Langdock, via MCP) formuliert die
        Antwort selbst. Die Ordner-ACL wird hier serverseitig erzwungen.

Bewusst als Code (nicht YAML) umgesetzt, weil Haystack-YAMLs mit
hybridem Qdrant + benutzerdefinierten Filtern noch nicht 100% glatt
serialisierbar sind, und lesbarer Python-Code hier mehr wert ist.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select

from auth.folders import (
    accessible_folder_paths,
    normalize_folder,
    user_accessible_folder_paths,
)
from config import global_config, settings
from db.models import Document, QueryLog
from db.session import get_session
from graph.store import GraphSnapshot, get_snapshot
from ingest.graph_refs import extract_refs
from logger import log
from pipelines import store
from pipelines.doc import RetrievedDoc as HayDoc
from pipelines.factory import embed_query


# ---------------------------------------------------------------------------
# Datenklassen für Responses
# ---------------------------------------------------------------------------
@dataclass
class RetrieveChunk:
    """
    Chunk mit **vollem** Text (nicht nur Snippet), damit der Client-LLM direkt
    damit arbeiten kann. `citation` ist eine fertig formatierte Quellenangabe,
    die der Client wörtlich übernehmen soll (Datei, Seite, Abschnitt).
    """
    doc_id: str
    file_name: str
    folder_path: str
    page: int | None
    section_title: str | None
    score: float
    text: str
    tags: list[str] = field(default_factory=list)
    citation: str = ""
    section_path: str | None = None
    doc_type: str | None = None
    norm_id: str | None = None
    doc_version: str | None = None
    outdated: bool = False           # aus Postgres angereichert (Ablöse-Status)
    superseded_by: str | None = None


@dataclass
class RetrieveResult:
    chunks: list[RetrieveChunk]
    latency_ms: int


# ---------------------------------------------------------------------------
# Filter-Builder
# ---------------------------------------------------------------------------
def _build_access_filter(
    folder_paths: list[str] | None,
    doc_type: str | None = None,
    language: str | None = None,
) -> dict[str, Any] | None:
    """
    Baut den Qdrant-Filter aus der ACL-Ordnerliste + optionalen Metadaten-Filtern.

      - folder_paths None → kein Ordner-Filter (unrestringierter Key ohne Wunsch).
      - folder_paths [..] → MatchAny auf `meta.folder`.
      - doc_type / language → exakter Match auf die Payload-Felder.

    Der Fall `folder_paths == []` (nichts zugänglich) wird VOM AUFRUFER
    abgefangen — hier darf er nie ankommen (sonst ließe ein leerer `in`-Filter
    alles durch).
    """
    conditions: list[dict[str, Any]] = []
    if folder_paths is not None:
        conditions.append({"field": "meta.folder", "operator": "in", "value": folder_paths})
    if doc_type:
        conditions.append({"field": "meta.doc_type", "operator": "==", "value": doc_type})
    if language:
        conditions.append({"field": "meta.language", "operator": "==", "value": language})

    if not conditions:
        return None
    return {"operator": "AND", "conditions": conditions}


# ---------------------------------------------------------------------------
# Graph-Augmentierung (Track D / M3e+f)
# ---------------------------------------------------------------------------
def _fastpath_doc_ids(question: str, snap: GraphSnapshot, visible: set[str]) -> list[str]:
    """Fastpath: exakte Norm-/Rechtsverweise in der Frage → referenzierende Docs.

    Regex→canonical_key (identisch zur L1-Extraktion via `extract_refs`) →
    Entity-Node → Docs mit `references`-Kante. **Nur ACL-sichtbare** Docs (die
    `visible`-Menge ist die Schnittmenge mit der Caller-ACL, M3f). Reihenfolge
    stabil (erstes Vorkommen), dedupliziert.
    """
    ids: list[str] = []
    seen: set[str] = set()
    for ref in extract_refs(question):
        node_key = f"{ref.kind}:{ref.canonical_key}"
        for doc_node in snap.referencing_docs.get(node_key, ()):  # document:<uuid>
            if doc_node in visible and doc_node not in seen:
                seen.add(doc_node)
                ids.append(doc_node.split(":", 1)[1])
    return ids


def _retrieve_docs_inner(
    question: str,
    doc_ids: list[str],
    top_k: int,
    embed_model: str,
    hybrid: bool,
) -> list[HayDoc]:
    """Wie `_retrieve_only_inner`, aber auf eine explizite `doc_id`-Menge gefiltert.

    Die `doc_ids` sind vom Aufrufer bereits ACL-geprüft (visible-Set) — diese
    Fetch-Funktion holt nur die frage-relevantesten Chunks *innerhalb* dieser
    Docs und erweitert die ACL NICHT.
    """
    filters = {
        "operator": "AND",
        "conditions": [{"field": "meta.doc_id", "operator": "in", "value": doc_ids}],
    }
    return _retrieve_only_inner(question, top_k, filters, embed_model, hybrid)


def _sanitize_chunks(
    chunks: list[RetrieveChunk], folder_paths: list[str] | None
) -> list[RetrieveChunk]:
    """Sanitize-on-Serialize (M3f): jeder serialisierte Chunk MUSS ACL-sichtbar sein.

    Für den reinen Hybrid-Pfad redundant (Qdrant-Filter greift schon), für die
    Graph-Kandidaten (Fastpath/PPR) die **letzte, autoritative** Schranke: ein
    Chunk, dessen Ordner nicht in der aufgelösten ACL liegt, wird verworfen —
    unabhängig davon, wie er in die Kandidatenmenge kam. `folder_paths is None`
    = Vollzugriff (Bearer ohne Einschränkung) → keine Filterung.
    """
    if folder_paths is None:
        return chunks
    allowed = {normalize_folder(fp) for fp in folder_paths}
    return [c for c in chunks if normalize_folder(c.folder_path) in allowed]


async def _scrub_cross_refs(
    chunks: list[RetrieveChunk], folder_paths: list[str] | None
) -> None:
    """Nullt `superseded_by`, wenn das Ziel-Doc außerhalb der Caller-ACL liegt (M3f).

    `superseded_by` ist das einzige Feld, das auf ein FREMDES Dokument zeigt.
    Ein eingeschränkter Caller darf darüber keine doc_id eines für ihn
    unsichtbaren Docs erhalten. `outdated` bleibt gesetzt (die Info „es gibt eine
    neuere Fassung" ist unkritisch), nur die fremde ID verschwindet.
    `folder_paths is None` = Vollzugriff → nichts zu tun.
    """
    if folder_paths is None:
        return
    targets = {c.superseded_by for c in chunks if c.superseded_by}
    if not targets:
        return
    try:
        target_uuids = [uuid.UUID(t) for t in targets]
    except ValueError:
        return
    allowed = {normalize_folder(fp) for fp in folder_paths}
    async with get_session() as s:
        rows = (await s.execute(
            select(Document.id, Document.folder_path).where(Document.id.in_(target_uuids))
        )).all()
    visible_targets = {
        str(r.id) for r in rows if normalize_folder(r.folder_path or "/") in allowed
    }
    for c in chunks:
        if c.superseded_by and c.superseded_by not in visible_targets:
            c.superseded_by = None


def _apply_content_budget(chunks: list[RetrieveChunk], budget: int) -> list[RetrieveChunk]:
    """Content-Budget (M3f): deckelt die Zahl **distinct doc_ids** pro Antwort.

    Verhindert, dass die Graph-Augmentierung eine Antwort mit beliebig vielen
    Dokumenten aufbläht. Alle Chunks bereits zugelassener Docs bleiben; sobald
    das Budget an *unterschiedlichen* Docs erreicht ist, werden weitere Docs
    übersprungen. `budget <= 0` = deaktiviert.
    """
    if budget <= 0:
        return chunks
    seen: set[str] = set()
    out: list[RetrieveChunk] = []
    for c in chunks:
        if c.doc_id not in seen:
            if len(seen) >= budget:
                continue
            seen.add(c.doc_id)
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# Store-interne Helfer (synchron, müssen in to_thread laufen)
# ---------------------------------------------------------------------------
def _retrieve_only_inner(
    question: str,
    top_k: int,
    filters: dict | None,
    embed_model: str,
    hybrid: bool,
) -> list[HayDoc]:
    """Dense-Embedding (fastembed/ONNX) + LanceDB-Store.

    hybrid=True: dense + FTS/BM25 mit RRF-Fusion. hybrid=False: reiner dense-Pfad.
    """
    qvec = embed_query(question, embed_model)
    return store.search_hybrid(question, qvec, top_k=top_k, filters=filters, hybrid=hybrid)


# ---------------------------------------------------------------------------
# Doc → Domain-Objekt-Mapper
# ---------------------------------------------------------------------------
def _build_citation(meta: dict) -> str:
    """Fertige Quellenangabe: 'Datei.pdf, S. 12, 6.2 Kostenschätzung'.

    Nutzt bevorzugt den hierarchischen `section_path` (Phase E), fällt auf
    `section_title` zurück. Leere Teile werden weggelassen.
    """
    parts: list[str] = []
    fn = meta.get("file_name")
    if fn:
        parts.append(str(fn))
    page = meta.get("page")
    if page is not None:
        parts.append(f"S. {page}")
    section = meta.get("section_path") or meta.get("section_title")
    if section:
        parts.append(str(section))
    return ", ".join(parts)


def _doc_to_chunk(d: HayDoc) -> RetrieveChunk:
    return RetrieveChunk(
        doc_id=str(d.meta.get("doc_id", "")),
        file_name=d.meta.get("file_name", ""),
        folder_path=d.meta.get("folder_path", "/"),
        page=d.meta.get("page"),
        section_title=d.meta.get("section_title"),
        score=float(d.score) if d.score is not None else 0.0,
        text=d.content or "",
        tags=list(d.meta.get("tags") or []),
        citation=_build_citation(d.meta),
        section_path=d.meta.get("section_path"),
        doc_type=d.meta.get("doc_type"),
        norm_id=d.meta.get("norm_id"),
        doc_version=d.meta.get("doc_version"),
    )


async def _annotate_status(chunks: list[RetrieveChunk]) -> None:
    """
    Reichert `outdated`/`superseded_by` aus Postgres an (Single-Source-of-Truth).

    Der Qdrant-Payload trägt den Ablöse-Status NICHT verlässlich (eine ältere
    Fassung wird erst nachträglich als 'superseded' markiert, ihre Chunks bleiben
    aber unverändert). Darum den Status hier frisch aus der DB holen.
    """
    doc_ids = {c.doc_id for c in chunks if c.doc_id}
    if not doc_ids:
        return
    try:
        uuids = [uuid.UUID(x) for x in doc_ids]
    except ValueError:
        return
    async with get_session() as s:
        rows = (await s.execute(
            select(Document.id, Document.valid_status, Document.superseded_by)
            .where(Document.id.in_(uuids))
        )).all()
    status = {str(r.id): (r.valid_status, r.superseded_by) for r in rows}
    for c in chunks:
        st = status.get(c.doc_id)
        if st:
            c.outdated = st[0] == "superseded"
            c.superseded_by = str(st[1]) if st[1] else None


# ---------------------------------------------------------------------------
# Öffentliche Async-API
# ---------------------------------------------------------------------------
async def run_retrieve(
    question: str,
    folder: str | None = None,
    top_k: int | None = None,
    api_key_id: UUID | None = None,
    allowed_folders: list[str] | None = None,
    access_all: bool | None = None,
    user_id: UUID | None = None,
    doc_type: str | None = None,
    language: str | None = None,
    only_current: bool = False,
) -> RetrieveResult:
    """
    Primärer Pfad: nur Chunks, kein LLM-Call. Der konsumierende Client
    (Claude/GPT/Langdock) formuliert die Antwort selbst.

    Ordner-ACL wird HIER serverseitig erzwungen (nicht dem `folder`-Parameter
    des Clients vertrauen). Zwei getrennte ACL-Semantiken (Track E):
      - `access_all is None` → **Bearer-Key**-Pfad (`accessible_folder_paths`,
        leere `allowed_folders` = Vollzugriff).
      - `access_all` ist bool → **User/OAuth**-Pfad (`user_accessible_folder_paths`,
        fail-safe: `access_all=False` + leere Liste = NICHTS).

    Optionale Filter: `doc_type`, `language` (Qdrant-Payload) sowie
    `only_current` (blendet 'superseded'-Fassungen aus, frischer Postgres-Status).
    """
    t0 = time.perf_counter()
    cfg = global_config()

    async with get_session() as s:
        if access_all is None:
            folder_paths = await accessible_folder_paths(allowed_folders, folder, s)
        else:
            folder_paths = await user_accessible_folder_paths(
                access_all, allowed_folders, folder, s
            )

    # Nichts zugänglich → leer antworten, NICHT ungefiltert suchen.
    if folder_paths is not None and not folder_paths:
        return RetrieveResult(chunks=[], latency_ms=int((time.perf_counter() - t0) * 1000))

    filters = _build_access_filter(folder_paths, doc_type=doc_type, language=language)
    effective_top_k = top_k or cfg.retrieval.top_k

    # Bei aktivem Reranker zuerst mehr Kandidaten holen, dann filtern
    retrieve_k = effective_top_k * 3 if cfg.retrieval.rerank else effective_top_k
    docs = await asyncio.to_thread(
        _retrieve_only_inner,
        question,
        retrieve_k,
        filters,
        cfg.embed_model,
        cfg.retrieval.hybrid,
    )

    # --- Graph-Augmentierung (M3e): Fastpath + PPR-Multi-Hop ---
    # Erweitert die Kandidatenmenge über den Wissensgraph — streng ACL-gefiltert
    # über die visible-Menge (Schnittmenge mit der Caller-ACL, M3f). Der Reranker
    # fusioniert die zusätzlichen Kandidaten (bei aktivem Reranker die eigentliche
    # RRF-Ersetzung). Fehler fallen sauber auf den reinen Hybrid-Pfad zurück
    # (kein Crash, laut geloggt).
    gcfg = settings()
    if gcfg.graph_retrieval_enabled:
        try:
            snap = await get_snapshot()
            if not snap.is_empty():
                visible = snap.visible_doc_nodes(folder_paths)
                present = {str(d.meta.get("doc_id", "")) for d in docs}

                # Fastpath: exakt genannte Norm/§ → referenzierende Docs (Recall
                # für Identifier-Fragen). Vorne einreihen (führen ohne Reranker).
                if gcfg.graph_fastpath_enabled:
                    fp_new = [
                        i for i in _fastpath_doc_ids(question, snap, visible)
                        if i not in present
                    ]
                    if fp_new:
                        extra = await asyncio.to_thread(
                            _retrieve_docs_inner, question, fp_new,
                            max(len(fp_new), retrieve_k),
                            cfg.embed_model, cfg.retrieval.hybrid,
                        )
                        docs = extra + docs
                        present |= {str(d.meta.get("doc_id", "")) for d in extra}
                        log.info("graph.fastpath.hit", added=len(extra))

                # PPR-Multi-Hop: Seeds = Top-Hybrid-Docs → verwandte sichtbare
                # Docs über den ACL-Subgraph. Weicher als Fastpath → angehängt,
                # der Reranker entscheidet.
                if gcfg.graph_ppr_enabled:
                    seed_keys = [
                        f"document:{sid}" for sid in
                        [str(d.meta.get("doc_id", "")) for d in docs[:gcfg.graph_ppr_seed_top_k]]
                        if f"document:{sid}" in visible
                    ]
                    ppr_ids = snap.ppr_candidate_docs(
                        seed_keys, visible, gcfg.graph_ppr_alpha,
                        gcfg.graph_ppr_iters, gcfg.graph_ppr_top_docs,
                    )
                    ppr_new = [i for i in ppr_ids if i not in present]
                    if ppr_new:
                        extra2 = await asyncio.to_thread(
                            _retrieve_docs_inner, question, ppr_new,
                            max(len(ppr_new), retrieve_k),
                            cfg.embed_model, cfg.retrieval.hybrid,
                        )
                        docs = docs + extra2
                        log.info("graph.ppr.hit", seeds=len(seed_keys), added=len(extra2))
        except Exception as e:
            log.warning("graph.augment.failed", error=str(e))

    if cfg.retrieval.rerank and len(docs) > 1:
        from pipelines.reranker import rerank
        docs = await asyncio.to_thread(rerank, question, docs, effective_top_k)
    else:
        docs = docs[:effective_top_k]

    chunks = [_doc_to_chunk(d) for d in docs]
    # Sicherheit (M3f): Sanitize-on-Serialize + Content-Budget — letzte Schranke
    # für die Graph-Kandidaten, bevor irgendetwas den Prozess verlässt.
    chunks = _sanitize_chunks(chunks, folder_paths)
    chunks = _apply_content_budget(chunks, gcfg.graph_content_budget)
    await _annotate_status(chunks)
    # Cross-Ref-Scrub: `superseded_by` ist das einzige Feld, das auf ein ANDERES
    # Doc zeigt — zeigt es aus der Caller-ACL hinaus, den Verweis nullen (Flag
    # `outdated` bleibt; nur die fremde doc_id verschwindet). Teil von M3f.
    await _scrub_cross_refs(chunks, folder_paths)
    if only_current:
        chunks = [c for c in chunks if not c.outdated]

    latency_ms = int((time.perf_counter() - t0) * 1000)

    # M2 (Track A0): Query-Log fire-and-forget — der DB-Write gehört nicht in den
    # Hot-Path. _log_query fängt eigene Fehler ab; die Referenz in _log_tasks
    # verhindert, dass der Task vor dem Lauf vom GC eingesammelt wird.
    log_task = asyncio.create_task(
        _log_query(
            api_key_id=api_key_id,
            user_id=user_id,
            question=question,
            doc_ids=[c.doc_id for c in chunks if c.doc_id],
            latency_ms=latency_ms,
            model="(retrieve-only)",
        )
    )
    _log_tasks.add(log_task)
    log_task.add_done_callback(_log_tasks.discard)

    return RetrieveResult(chunks=chunks, latency_ms=latency_ms)


# Fire-and-forget-Log-Tasks referenzhalten (asyncio.create_task hält allein keine
# starke Referenz → sonst könnte der GC den Task vor dem Lauf verwerfen).
_log_tasks: set[asyncio.Task] = set()


async def _log_query(
    api_key_id: UUID | None,
    question: str,
    doc_ids: list[str],
    latency_ms: int,
    model: str,
    user_id: UUID | None = None,
) -> None:
    try:
        # SQLite: retrieved_doc_ids ist eine JSON-Spalte → doc_id-STRINGS speichern
        # (keine UUID-Objekte; die sind nicht JSON-serialisierbar). Subagent-C-Fund C.
        retrieved_doc_ids = [str(d) for d in doc_ids]
        async with get_session() as s:
            s.add(
                QueryLog(
                    api_key_id=api_key_id,
                    user_id=user_id,
                    query_text=question[:4000],
                    retrieved_doc_ids=retrieved_doc_ids,
                    latency_ms=latency_ms,
                    model=model,
                )
            )
    except Exception as e:
        log.warning("query.log_failed", error=str(e))
