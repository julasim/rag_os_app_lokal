"""
M0 — Machbarkeits-Gate: LanceDB Hybrid + exakte Norm + Versionierung + Publish/Cache.

Prüft (rein lokal, kein Docker, kein Server) die Punkte, an denen "native App bei 20k
Docs" steht oder fällt. Läuft auf Python 3.14/Windows mit lancedb + fastembed.

Ausführen (aus dem Spike-venv):
    <scratchpad>/m0-spike/.venv/Scripts/python.exe spike/m0_lancedb.py

Akzeptanz (SPEC/Plan M0):
  A  FTS/BM25 findet "ÖNORM B 1801-1" — unterscheidet vom Geschwister "B 1801-2"?
  B  norm_id-WHERE trifft exakt genau ein Doc (deterministischer Primärpfad).
  C  Hybrid (dense + FTS + RRF) läuft in-process.
  D  Versionierung/Time-Travel: alte Version read-only öffnen, FTS weiter nutzbar.
  E  Publish/Cache: Tabellen-Verzeichnis kopieren -> read-only öffnen -> FTS ohne Rebuild.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import traceback
from pathlib import Path

# Windows-Konsole ist cp1252 -> Umlaute/Sonderzeichen crashen print(). UTF-8 erzwingen.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import lancedb
import pyarrow as pa

results: dict[str, tuple[bool, str]] = {}


def mark(key: str, ok: bool, detail: str = "") -> None:
    results[key] = (ok, detail)
    print(f"  [{'PASS' if ok else 'FAIL'}] {key}: {detail}")


# ---------------------------------------------------------------------------
# Testkorpus — Normen/Richtlinien/§ (Julius' realer Use-Case), inkl. Geschwister
# ---------------------------------------------------------------------------
DOCS = [
    dict(doc_id="d1", norm_id="ÖNORM B 1801-1", folder="/Normen/",
         text="ÖNORM B 1801-1 Kosten im Hochbau — Objekterrichtung. Regelt die Gliederung "
              "der Errichtungskosten eines Bauwerks in Kostenbereiche und Kostengruppen."),
    dict(doc_id="d2", norm_id="ÖNORM B 1801-2", folder="/Normen/",
         text="ÖNORM B 1801-2 Kosten im Hochbau — Objektfolgekosten. Behandelt Betriebs-, "
              "Instandhaltungs- und Rückbaukosten über den Lebenszyklus."),
    dict(doc_id="d3", norm_id="ÖNORM EN 1990", folder="/Normen/",
         text="ÖNORM EN 1990 Eurocode — Grundlagen der Tragwerksplanung. Definiert "
              "Bemessungssituationen und Zuverlässigkeit."),
    dict(doc_id="d4", norm_id="OIB-Richtlinie 6", folder="/OIB/",
         text="OIB-Richtlinie 6 Energieeinsparung und Wärmeschutz. Anforderungen an den "
              "Heizwärmebedarf und den U-Wert von Bauteilen."),
    dict(doc_id="d5", norm_id="DIN 276", folder="/Normen/",
         text="DIN 276 Kosten im Bauwesen. Deutsche Norm zur Kostengliederung, verwandt "
              "aber nicht identisch mit ÖNORM B 1801-1."),
    dict(doc_id="d6", norm_id="§ 3 BauO", folder="/Recht/",
         text="§ 3 Abs 2 Bauordnung. Bestimmungen zu Abständen und Bebauungsdichte."),
]


def embedder():
    """Dense-Embedder (fastembed/ONNX). Klein für den M0-Mechaniktest; Produktion = bge-m3."""
    from fastembed import TextEmbedding
    return TextEmbedding(model_name="BAAI/bge-small-en-v1.5")  # 384-dim, ~130 MB


def build_table(db, emb):
    texts = [d["text"] for d in DOCS]
    vecs = [v.tolist() for v in emb.embed(texts)]
    dim = len(vecs[0])
    rows = [dict(**d, vector=vecs[i]) for i, d in enumerate(DOCS)]
    schema = pa.schema([
        pa.field("doc_id", pa.string()),
        pa.field("norm_id", pa.string()),
        pa.field("folder", pa.string()),
        pa.field("text", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), dim)),
    ])
    tbl = db.create_table("norms", data=rows, schema=schema, mode="overwrite")
    return tbl, dim


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="m0_lancedb_"))
    db_path = work / "vault_index"        # spielt "Version vN auf der NAS"
    cache_path = work / "reader_cache"     # spielt "lokaler Leser-Cache"
    print(f"Arbeitsverzeichnis: {work}")
    print(f"lancedb {lancedb.__version__} · pyarrow {pa.__version__} · python {sys.version.split()[0]}\n")

    print("== Setup: Embedder + Tabelle ==")
    emb = embedder()
    db = lancedb.connect(str(db_path))
    tbl, dim = build_table(db, emb)
    print(f"  Tabelle 'norms' angelegt: {tbl.count_rows()} Zeilen, dense-dim={dim}, version={tbl.version}")

    print("\n== FTS-Index (native, kein Tantivy) ==")
    try:
        tbl.create_fts_index("text", use_tantivy=False, replace=True)
        mark("fts_index", True, f"angelegt, version={tbl.version}")
    except Exception as e:
        mark("fts_index", False, f"{type(e).__name__}: {e}")

    # --- A: FTS exakte Norm + Geschwister-Unterscheidung ---
    print("\n== A: FTS 'ÖNORM B 1801-1' (Geschwister-Test) ==")
    try:
        hits = tbl.search("ÖNORM B 1801-1", query_type="fts").limit(3).to_list()
        top = hits[0] if hits else {}
        order = [(h["norm_id"], round(h.get("_score", 0), 3)) for h in hits]
        print(f"    Reihenfolge: {order}")
        ok = bool(hits) and top.get("norm_id") == "ÖNORM B 1801-1"
        mark("A_fts_exact_norm", ok,
             f"Top={top.get('norm_id')!r} (erwartet 'ÖNORM B 1801-1'); "
             f"{'unterscheidet Geschwister' if ok else 'FTS-Tokenisierung schwach — norm_id-Pfad nötig'}")
    except Exception as e:
        mark("A_fts_exact_norm", False, f"{type(e).__name__}: {e}")

    # --- B: norm_id-WHERE = deterministischer Primärpfad ---
    print("\n== B: norm_id-WHERE (deterministisch) ==")
    try:
        rows = tbl.search().where("norm_id = 'ÖNORM B 1801-1'").limit(5).to_list()
        ok = len(rows) == 1 and rows[0]["doc_id"] == "d1"
        mark("B_norm_id_where", ok, f"{len(rows)} Treffer, exakt d1={ok}")
    except Exception as e:
        mark("B_norm_id_where", False, f"{type(e).__name__}: {e}")

    # --- C: Hybrid dense + FTS + RRF ---
    print("\n== C: Hybrid (dense + FTS + RRF) ==")
    try:
        try:
            from lancedb.rerankers import RRFReranker
        except ImportError:
            from lancedb.rerank import RRFReranker  # ältere Pfad-Variante
        q = "Gliederung der Baukosten im Hochbau"
        qvec = list(emb.embed([q]))[0].tolist()
        hits = (tbl.search(query_type="hybrid")
                .vector(qvec).text(q)
                .rerank(RRFReranker())
                .limit(3).to_list())
        order = [h["norm_id"] for h in hits]
        print(f"    Query {q!r} -> {order}")
        ok = bool(hits)
        mark("C_hybrid", ok, f"{len(hits)} Treffer, Fusion lief; Top={order[0] if order else None!r}")
    except Exception as e:
        traceback.print_exc()
        mark("C_hybrid", False, f"{type(e).__name__}: {e}")

    # --- D: Versionierung / Time-Travel, FTS auf alter Version ---
    print("\n== D: Versionierung / Time-Travel ==")
    try:
        v_now = tbl.version
        tbl.add([dict(doc_id="d7", norm_id="ÖNORM B 2110", folder="/Normen/",
                      text="ÖNORM B 2110 Werkvertragsnorm für Bauleistungen.",
                      vector=list(emb.embed(["ÖNORM B 2110 Werkvertragsnorm"]))[0].tolist())])
        v_after = tbl.version
        versions = [v["version"] for v in tbl.list_versions()]
        # Auf die Version MIT FTS-Index + ohne d7 zurückspringen (read-only Lesen)
        tbl.checkout(v_now)
        n_at_vnow = tbl.count_rows()
        fts_at_vnow = tbl.search("ÖNORM B 1801-1", query_type="fts").limit(1).to_list()
        tbl.checkout_latest()
        ok = v_after > v_now and n_at_vnow == 6 and bool(fts_at_vnow)
        mark("D_versioning", ok,
             f"versions={versions}, checkout(v{v_now}): rows={n_at_vnow}, "
             f"FTS-Treffer={'ja' if fts_at_vnow else 'NEIN (Index reist nicht mit!)'}")
    except Exception as e:
        traceback.print_exc()
        mark("D_versioning", False, f"{type(e).__name__}: {e}")

    # --- E: Publish/Cache — Verzeichnis kopieren, read-only öffnen, FTS ohne Rebuild ---
    print("\n== E: Publish/Cache (Verzeichnis-Kopie = NAS->Leser-Cache) ==")
    try:
        tbl.checkout_latest()
        shutil.copytree(db_path, cache_path)          # "Publish + Pull" = Datei-Kopie
        rdb = lancedb.connect(str(cache_path))
        rtbl = rdb.open_table("norms")
        fts_cache = rtbl.search("ÖNORM B 1801-1", query_type="fts").limit(1).to_list()
        where_cache = rtbl.search().where("norm_id = 'ÖNORM B 1801-1'").limit(1).to_list()
        ok = bool(fts_cache) and len(where_cache) == 1
        mark("E_publish_cache", ok,
             f"aus Kopie: FTS={'ja' if fts_cache else 'nein'}, norm_id-WHERE={'ja' if where_cache else 'nein'} "
             f"(Index ohne Rebuild nutzbar={ok})")
    except Exception as e:
        traceback.print_exc()
        mark("E_publish_cache", False, f"{type(e).__name__}: {e}")

    # --- Zusammenfassung ---
    print("\n" + "=" * 64)
    print("M0-ERGEBNIS")
    print("=" * 64)
    for k, (ok, det) in results.items():
        print(f"  {'PASS' if ok else 'FAIL':4}  {k:22}  {det}")
    core = ["fts_index", "B_norm_id_where", "C_hybrid", "D_versioning", "E_publish_cache"]
    core_ok = all(results.get(k, (False, ""))[0] for k in core)
    a_ok = results.get("A_fts_exact_norm", (False, ""))[0]
    print("\n  Kern-Mechanik (Store/Hybrid/Version/Cache):", "GRÜN" if core_ok else "ROT")
    print("  Exakte Norm via FTS:", "ja" if a_ok else "nein → norm_id-WHERE als Primärpfad (Plan sieht das vor)")
    print(f"\n  Aufräumen: rm -rf {work}")
    return 0 if core_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
