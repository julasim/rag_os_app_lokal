"""
Graph-Qualitäts-Demonstration: was bringt der Graph WIRKLICH?

Vergleicht auf einem kleinen Normen-Korpus (Dokumente zitieren sich gegenseitig
in UNTERSCHIEDLICHER Schreibweise) drei Modi:
  1. hybrid_only  — reine LanceDB-Hybrid-Suche (dense + FTS/BM25 + RRF)
  2. graph_lite   — hybrid + Norm-Referenz-Register (kanonisierte Zitat-Kanten)
  3. (full graph) — PPR/Communities/Near-Dup: konzeptionell erklärt, nicht gemessen

Zeigt, WO sich die Modi unterscheiden (Querverweis-Fragen) und wo NICHT (direkte Fragen).
Ausführen mit dem M0-venv.
"""
from __future__ import annotations

import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import lancedb
import pyarrow as pa
from fastembed import TextEmbedding

# ---------------------------------------------------------------------------
# Korpus — Normen + Dokumente, die Normen in VERSCHIEDENER Schreibweise zitieren
# ---------------------------------------------------------------------------
DOCS = [
    dict(doc_id="d1_norm_1801", is_norm="ÖNORM B 1801-1", folder="/Normen/",
         text="ÖNORM B 1801-1 – Kosten im Hochbau, Objekterrichtung. Regelt die "
              "Gliederung der Errichtungskosten in Kostenbereiche und Kostengruppen."),
    dict(doc_id="d2_lv_beton", is_norm=None, folder="/Projekte/A/",
         text="Leistungsverzeichnis Stahlbeton: Bewehrung, Schalung, Betongüte "
              "C25/30, Fundamentplatte. Die Kostengliederung erfolgt nach B1801-1."),   # Variante ohne Leerzeichen
    dict(doc_id="d3_gutachten", is_norm=None, folder="/Projekte/B/",
         text="Wirtschaftlichkeitsgutachten Bürogebäude: Barwert, Lebenszykluskosten, "
              "Amortisation. Die Kostenstruktur folgt ÖNORM B 1801 Teil 1."),            # Variante 'Teil 1'
    dict(doc_id="d4_din276", is_norm="DIN 276", folder="/Normen/",
         text="DIN 276 – Kosten im Bauwesen. Kostengruppen 100 bis 700, "
              "Kostenermittlung und Kostenkontrolle."),
    dict(doc_id="d5_oib6", is_norm="OIB-Richtlinie 6", folder="/Normen/",
         text="OIB-Richtlinie 6 – Energieeinsparung und Wärmeschutz. Heizwärmebedarf, "
              "U-Wert von Bauteilen, Referenzklima."),
    dict(doc_id="d6_protokoll", is_norm=None, folder="/Projekte/A/",
         text="Baubesprechungsprotokoll KW12: Terminplan, offene Mängel, Freigaben, "
              "nächste Schritte der ausführenden Firmen."),
]

# ---------------------------------------------------------------------------
# Kanonisierung + Norm-Extraktion (das Herz von "graph_lite" / RAG-OS canonical.py)
# ---------------------------------------------------------------------------
NORM_RE = re.compile(
    r"(?:ÖNORM\s+)?B\s?\d{3,5}(?:\s*-\s*\d+|\s+TEIL\s+\d+)|DIN\s+\d{2,4}|OIB[-\s]?RICHTLINIE\s+\d+",
    re.IGNORECASE,
)

def canon(s: str) -> str:
    """Normalisiert Schreibvarianten auf einen kanonischen Schlüssel.
    'ÖNORM B 1801-1' / 'B1801-1' / 'ÖNORM B 1801 Teil 1'  ->  'B1801-1'."""
    s = s.upper().replace("ÖNORM", "")
    s = re.sub(r"\bTEIL\s*(\d+)", r"-\1", s)   # 'TEIL 1' -> '-1'
    s = re.sub(r"OIB[-\s]?RICHTLINIE", "OIB-RL", s)
    s = re.sub(r"\s+", "", s)                    # Leerzeichen raus -> 'B1801-1'
    s = s.replace("--", "-")
    return s

def extract_norms(text: str) -> set[str]:
    return {canon(m.group(0)) for m in NORM_RE.finditer(text)}


def main() -> int:
    emb = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")

    # --- Norm-Register aufbauen (kanonisierte Zitat-Kanten) ---
    ref_register: dict[str, set[str]] = {}   # canon(norm) -> {doc_ids, die ihn zitieren}
    norm_owner: dict[str, str] = {}          # canon(norm) -> doc_id, der die Norm IST
    for d in DOCS:
        own = canon(d["is_norm"]) if d["is_norm"] else None
        if own:
            norm_owner[own] = d["doc_id"]
        for c in extract_norms(d["text"]):
            if c != own:
                ref_register.setdefault(c, set()).add(d["doc_id"])

    print("Norm-Register (kanonisiert):")
    for c, docs in sorted(ref_register.items()):
        print(f"  {c}  ← zitiert von: {sorted(docs)}")
    print()

    # --- LanceDB-Tabelle + FTS ---
    vecs = [v.tolist() for v in emb.embed([d["text"] for d in DOCS])]
    dim = len(vecs[0])
    rows = [dict(doc_id=d["doc_id"], folder=d["folder"], text=d["text"], vector=vecs[i])
            for i, d in enumerate(DOCS)]
    schema = pa.schema([
        pa.field("doc_id", pa.string()), pa.field("folder", pa.string()),
        pa.field("text", pa.string()), pa.field("vector", pa.list_(pa.float32(), dim)),
    ])
    db = lancedb.connect("C:/Users/juliu/AppData/Local/Temp/graph_quality_db")
    tbl = db.create_table("docs", data=rows, schema=schema, mode="overwrite")
    tbl.create_fts_index("text", use_tantivy=False, replace=True)
    from lancedb.rerankers import RRFReranker

    def hybrid(q: str, k: int) -> list[str]:
        qv = list(emb.embed([q]))[0].tolist()
        hits = (tbl.search(query_type="hybrid").vector(qv).text(q)
                .rerank(RRFReranker()).limit(k).to_list())
        return [h["doc_id"] for h in hits]

    def graph_lite(q: str, k: int) -> tuple[list[str], list[str]]:
        """Fastpath: Normen aus der Frage -> zitierende Docs (kanonisiert), vorne;
        dann mit Hybrid auffüllen. Gibt (ergebnis, fastpath_zusätzlich) zurück."""
        base = hybrid(q, k)
        fast: list[str] = []
        for c in extract_norms(q):
            for doc in sorted(ref_register.get(c, ())):
                if doc not in fast:
                    fast.append(doc)
        merged = fast + [d for d in base if d not in fast]
        added = [d for d in fast if d not in base]   # was Hybrid (bei k) NICHT hatte
        return merged[:max(k, len(fast))], added

    # --- Zwei Fragen: direkt vs. Querverweis ---
    # k=2 als Proxy für "top_k << Korpusgröße" (bei 20k Docs fällt ein semantisch
    # fernes, zitierendes Doc regelmäßig aus den Top-k der reinen Hybrid-Suche).
    K = 2
    queries = [
        ("DIREKT (Sachfrage)",     "Wie werden Baukosten in Kostengruppen gegliedert?"),
        ("QUERVERWEIS (Zitat)",    "Welche Unterlagen wurden nach ÖNORM B 1801-1 erstellt?"),
    ]
    for label, q in queries:
        h = hybrid(q, K)
        g, added = graph_lite(q, K)
        print("=" * 70)
        print(f"{label}: {q!r}")
        print(f"  hybrid_only : {h}")
        print(f"  graph_lite  : {g}")
        if added:
            print(f"  >> graph_lite findet zusätzlich (Hybrid verpasst bei k={K}): {added}")
            for a in added:
                txt = next(d['text'] for d in DOCS if d['doc_id'] == a)
                print(f"       {a}: „…{txt[-70:].strip()}\"")
        else:
            print(f"  >> kein Unterschied — Graph bringt hier nichts.")
        print()

    print("Aufräumen: rm -rf C:/Users/juliu/AppData/Local/Temp/graph_quality_db")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
