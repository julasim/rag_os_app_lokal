# RAG-OS Lokal — Verbindliche Spezifikation (v1)

> **Zweck:** Bauanleitung für die native, Docker-freie Windows-Variante von RAG-OS.
> Ersetzt die VPS-Instanz. Diese Datei ist die Quelle der Wahrheit für das *Was* und
> *Warum*; das *Wie* (Meilensteine) steht in Abschnitt 14.
>
> Stand: 2026-07-16 · Sprache: Deutsch · Autor der Entscheidungen: Julius Sima
>
> ⚠️ **Implementierungs-Abweichungen (as-built, seit M4/M5/M8g).** Dieser Spec hält das
> *Was/Warum* fest; die Umsetzung ist an einigen Stellen bewusst anders gelaufen. **Es gilt
> der as-built-Stand in [CLAUDE.md](CLAUDE.md) + [BUILD-PLAN.md](BUILD-PLAN.md):**
> - **Embeddings: `intfloat/multilingual-e5-large` (INT8-ONNX), nicht bge-m3** (fastembed
>   kennt bge-m3 nicht; M4). Dense **+ LanceDB-FTS/BM25** — kein Modell-Sparse-Vektor.
> - **Tagging/Metadaten/Graph sind deterministisch, LLM-FREI** (M5). **Kein Ollama, kein
>   qwen2.5** — der „Tagging-LLM" unten entfällt ersatzlos; der Schreiber braucht kein LLM.
> - Reranker `bge-reranker-v2-m3` als **INT8-ONNX** (kein torch zur Laufzeit).
> - Neu (Post-M8): Wissensgraph-**Visualisierung** `GET /api/graph`, per-User-ACL-gefiltert.
>
> Unverändert gültig: nativ/kein Docker, Vault + LanceDB, Schreiber/Leser-Rollen,
> versionierte Index-Stände, MCP Bearer-only/read-only, keine OAuth/2FA.

---

## 1. Was das Programm ist (Vision)

Ein **lokaler Dokument-Wissensspeicher**, mit dem **jeder MCP-fähige KI-Client**
(Claude Desktop u.a.) als Informationsträger spricht. Der **MCP-Server ist der Kern**,
nicht die Oberfläche. Die Web-/App-Oberfläche dient der **Verwaltung** (Import, prüfen,
Tags, Wartung). Alle Dokumente **und** der daraus zerlegte Index leben in **einem
portablen Ordner** (dem *Vault*), der auf einer NAS liegt — Prinzip wie **Obsidian**.
Ersetzt die VPS vollständig, aber **frisch** (keine Datenmigration).

---

## 2. Architektur-Überblick

Kernprinzip: **Ingest (schwer, selten, ein Schreiber) und Abfrage (leicht, oft, viele
Leser) sind getrennt.** Sicherheit bei geteiltem Speicher entsteht durch **unveränderliche,
versionierte Index-Stände** (Git-Prinzip) — nicht durch einen Live-Server.

```
 SCHREIBER-Rechner (Julius, stark: Ryzen/RTX)
 ├─ Ingest: OCR → Docling → Chunking → Embeddings → LLM-Tagging → Graph
 ├─ arbeitet auf LOKALER SSD (schnell, sicher)
 └─ VERÖFFENTLICHT eine neue, unveränderliche Index-Version → NAS
                      │
                      ▼
        ┌───────────────────────────────┐
        │   NAS-Vault  (Verteil-Hub)     │  Docs + versionierter Index
        │   = Wahrheit + Backup-Ziel     │  (unveränderliche Stände)
        └───────────────────────────────┘
                      │  (jeder zieht die aktuelle Version)
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   LESER-Rechner  LESER-Rechner  LESER-Rechner   (eigene Geräte + Kollegen)
   ├─ lokaler Cache der Version (schnelle Suche)
   ├─ eigener MCP-Server (127.0.0.1)  → eigener KI-Client dockt an
   └─ nur lesen (Query-Einbettung + Suche + Reranking)
```

- **Kein Always-on-Host, kein Mini-PC, kein zentraler Server.** Jeder Rechner hat die App
  installiert und seinen **eigenen lokalen MCP**, der läuft, wenn der Rechner an ist.
- **Nur EINE Maschine schreibt** den Index (der Schreiber) → keine SMB-Schreibkonflikte.
- **Leser lesen unveränderliche Stände** (idealerweise aus lokalem Cache) → keine Korruption,
  volle Geschwindigkeit.
- **Kurze Verzögerung akzeptiert:** Leser sehen neue Dokumente, sobald der Schreiber eine
  neue Version veröffentlicht hat (Sekunden–Minuten).

---

## 3. Verbindliche Entscheidungen

| Bereich | Entscheidung |
|---|---|
| **Zweck** | Dokument-DB als Informationsträger für KI-Clients; MCP = Kern, UI = Verwaltung. |
| **Speicher-Modell** | Vault-Prinzip (wie Obsidian): Docs + Index in einem portablen Ordner. |
| **Topologie** | App auf jedem Rechner, eigener lokaler MCP. Ein Schreiber, mehrere Leser. |
| **Nebenläufigkeit** | Gelöst über **unveränderliche, versionierte Index-Stände** (nicht über Datei-Sperren/Server). Git-artig: NAS = Remote, Rechner = lokaler Klon/Cache. |
| **Speicherort** | NAS = veröffentlichte Wahrheit + Backup-Hub. Schreiber arbeitet lokal & veröffentlicht. Leser cachen lokal. |
| **Erreichbarkeit** | Pro Rechner lokal (`127.0.0.1`). „Von unterwegs" = über den NAS-Zugriff des jeweiligen Rechners, **kein** app-eigener Fernzugriff/VPN nötig. |
| **Parsing** | **Docling inkl. OCR** — Tabellen-Treue ist Pflicht, gescannte PDFs müssen funktionieren. |
| **Formate** | PDF (inkl. gescannt/OCR), DOCX, TXT/MD. (Kein Excel/PPT in v1.) |
| **Vektor/Suche** | **Hybrid**: dense (bge-m3) + sparse/BM25 (exakte Normtreffer), Reranking (bge-reranker-v2-m3, ONNX). Store = Ergebnis aus **M0** (Richtung LanceDB, s. Abschnitt 13). |
| **Embeddings** | bge-m3 (mehrsprachig, stark für Deutsch; liefert dense **und** sparse). |
| **LLM (Tagging)** | Ollama nativ, **qwen2.5:7b** — nur fürs Auto-Tagging/Metadaten + Norm-Erkennung beim Ingest. **Kein In-App-Chat.** Nur auf dem Schreiber-Rechner nötig. |
| **Norm-Erkennung** | Automatisch beim Ingest: **Regex-Register** (ÖNORM, DIN/EN/ISO, OIB-Richtlinien, Gesetzes-§) + LLM-Tags. |
| **Graph** | **Behalten** (Dokument-Beziehungen, Near-Dup/Cluster). Wird vom Schreiber gebaut, versioniert im Vault. |
| **Re-Upload** | **Versionieren** — alte Fassung bleibt erhalten, neue kommt dazu. |
| **Nutzer/Rollen** | Schlanke Schicht: **Julius = Schreiber**, Rest (eigene Geräte + Kollegen) = **nur lesen**. Durchsetzung dreifach: Leser-Installer (kein Ingest), NAS-Leserechte, App-Key/Nutzer. **Keine** OAuth-/2FA-Kette. |
| **App-Form** | **Native Windows-App**: Tray + Autostart + Toast + Drag&Drop. Fenster (WebView2) für Verwaltung. Installer via Inno Setup. |
| **Backup** | NAS-eigene Snapshots + **Rebuild-Garantie** (Index jederzeit aus den Roh-Docs neu baubar → Docs sind die Wahrheit). |
| **Datenübernahme VPS** | **Keine.** Frisch anfangen, Dokumente neu einlesen (häppchenweise bis ~20.000). |

---

## 4. Datenfluss

**Ingest (nur Schreiber-Rechner):**
1. Dokument kommt rein (UI-Upload **oder** Überwachungsordner auf der NAS **oder** Drag&Drop).
2. OCR (falls Scan) → Docling-Layout-Parsing (Tabellen-treu) → Chunking.
3. Embeddings (bge-m3, dense+sparse), GPU-beschleunigt.
4. Norm-/§-Erkennung (Regex-Register) + LLM-Auto-Tagging (qwen2.5:7b).
5. Graph aktualisieren (Beziehungen/Near-Dup).
6. Schreiben in die **lokale Arbeitskopie** des Index.
7. **Veröffentlichen:** neue unveränderliche Version atomar auf die NAS.

**Abfrage (jeder Leser-Rechner, eigener MCP):**
1. KI-Client ruft ein MCP-Tool auf (127.0.0.1, Bearer-Key).
2. Query-Einbettung (bge-m3) → Hybrid-Suche (dense + sparse/BM25) im lokalen Cache der
   aktuellen Version → RRF-Fusion.
3. Reranking (bge-reranker-v2-m3, ONNX).
4. Ergebnis (Chunks + Quellen/Metadaten) zurück an den Client.

---

## 5. Der Vault (Ordnerstruktur)

```
MeinWissen/                    ← der Vault (veröffentlichte Version auf NAS)
├── Dokumente/                 ← Rohdateien, frei organisiert (PDF/DOCX/MD)
│   ├── Normen/…
│   └── Projekte/…
└── .ragos/                    ← versteckter App-Ordner (wie .obsidian/)
    ├── index/                 ← versionierter Vektor-/Volltext-Store (unveränderliche Stände)
    ├── meta.sqlite            ← veröffentlichter, NUR-LESE-Snapshot (Dokumente/Tags/Norm-Register/Graph)
    ├── graph/                 ← Wissens-Graph (versioniert)
    ├── versions.json          ← Zeiger auf die „aktuelle" Version
    └── config.json            ← Vault-Config, Rollen, Norm-Muster
```

- Der **Schreiber** hat zusätzlich eine **lokale Arbeitskopie** (read-write) auf seiner SSD;
  Veröffentlichen = atomarer Swap der Version + Aktualisieren von `versions.json`.
- **Leser** halten einen **lokalen Cache** der aktuellen Version (schnell); `meta.sqlite`
  wird als **unveränderlicher Snapshot** nur gelesen — dadurch auch über SMB unkritisch.
- Per-Rechner-App-Zustand (eigener Bearer-Key, lokale Logs, UI-Einstellungen) liegt **lokal**
  (`%LOCALAPPDATA%\RAG-OS\`), **nicht** im Vault.

---

## 6. Rollen & Zugriff

- **Schreiber (Julius):** Voll-Installer, Ingest aktiv, Schreibrecht auf den NAS-Vault.
- **Leser (eigene Geräte + Kollegen):** Leser-Installer (kein Ingest), **NAS-Leserecht**,
  eigener lokaler MCP nur mit Lese-Tools.
- **Durchsetzung dreifach & sich verstärkend:** (1) Installer-Typ, (2) NAS-Dateirechte,
  (3) App-Key/Nutzer-Flag.
- **Auth:** statischer Bearer-Key pro Rechner/Nutzer für den lokalen MCP. Keine OAuth/2FA.

---

## 7. MCP-Schnittstelle (die 4 Werkzeuge)

Standard-konformer MCP-Server (HTTP, `127.0.0.1`), damit **jeder** MCP-Client andocken kann.

1. **`rag_retrieve`** — relevante Chunks zu einer Frage (Hybrid + Rerank), inkl. Quelle.
2. **`norm_lookup`** — exakte Suche nach Normnummer/§ (nutzt das Norm-Register).
3. **`list_documents`** — Dokumente/Ordner/Tags auflisten & durchblättern.
4. **`get_document`** — ganzes Dokument (oder großen Abschnitt) abrufen.

Alle vier sind **Lese**-Tools → für Leser wie Schreiber identisch nutzbar.

---

## 8. Komponenten & Modelle

| Komponente | Wahl | Läuft wo |
|---|---|---|
| Parsing | Docling **inkl. OCR** (torch CPU/GPU) | nur Schreiber |
| Embeddings | bge-m3 (dense + sparse) | Schreiber (Ingest) + Leser (Query) |
| Reranker | bge-reranker-v2-m3 als ONNX | jeder (Abfrage) |
| Tagging-LLM | qwen2.5:7b via Ollama nativ | nur Schreiber (Ingest) |
| Graph | networkx/numpy | nur Schreiber (Ingest) |
| Vektor-/Volltext-Store | **M0-Entscheidung** (Abschnitt 13) | jeder |
| Relationale Metadaten | SQLite (als unveränderlicher Snapshot verteilt) | jeder (nur lesen), Schreiber (read-write Arbeitskopie) |

---

## 9. Norm-/§-Erkennung (Regex-Register)

Muster für automatische, exakte Erkennung beim Ingest:

- **ÖNORM** — z.B. `ÖNORM B 1801-1`, `ÖNORM EN 1990`.
- **DIN / EN / ISO** — z.B. `DIN 276`, `EN 1992-1-1`, `ISO 9001`.
- **OIB-Richtlinien** — z.B. `OIB-Richtlinie 6`.
- **Gesetzes-§** — z.B. `§ 3 Abs 2 BauO`, Bauordnungen/Gesetze.

Treffer werden als durchsuchbare Metadaten (Norm-Register) gespeichert → `norm_lookup`
liefert exakt. Zusätzlich vergibt das LLM Themen-Tags.

---

## 10. Installations-Profile

- **Voll-Installer (Schreiber):** App + Docling/OCR-Modelle + Ollama + qwen2.5:7b + bge-m3 +
  Reranker + Store. Groß (mehrere GB wegen torch/Docling/OCR + LLM).
- **Leser-Installer (schlank):** App + bge-m3 (Query) + Reranker + Store-Reader + MCP.
  **Ohne** Docling/OCR/Ollama → deutlich kleiner & schneller installiert.

Beide via Inno Setup. Erst-Start zieht ggf. Modelle nach.

---

## 11. Bewusst nicht in v1

- In-App-Chat / Chat-LLM als Hot-Path (KI-Clients übernehmen das Denken).
- Excel/PPT-Parser.
- Windows Hello / at-rest-Verschlüsselung.
- OAuth / 2FA / Multi-User-Vollstack (nur schlanke Lese-/Schreib-Rollen).
- App-eigener Internet-Fernzugriff/VPN (NAS-Zugriff des Rechners genügt).
- VPS-Datenmigration.
- Live-Konsistenz über Rechner hinweg (kurze Verzögerung ist akzeptiert).

---

## 12. Offene technische Risiken

1. **M0-Gate (hart):** Läuft die **Hybrid-Suche (dense+sparse)** in einem **embedded,
   shared-storage-tauglichen** Store, der **unveränderliche Versionen** unterstützt, auf
   Windows — und liefert er bei „ÖNORM B 1801-1" den norm-tragenden Treffer? Zusätzlich
   testen: **einprozessiges Lesen über SMB/NAS** stabil? (Fallback ist ohnehin der lokale
   Cache → Risiko gering.)
2. **Ingest-Tempo:** OCR + Docling + qwen2.5:7b für 20.000 Docs ist rechenintensiv. Läuft
   auf dem starken Schreiber-Rechner im Hintergrund, häppchenweise → vertretbar, aber ein
   großer Erst-Schwung dauert. GPU (RTX 4060) beschleunigt OCR/Embeddings.
3. **7B auf 4 GB VRAM:** qwen2.5:7b lagert teils auf CPU/RAM aus (61 GB vorhanden) →
   langsamer, aber funktionsfähig; da Ingest im Hintergrund läuft, akzeptiert.

---

## 13. DB-Auswahlkriterien (für die laufende Recherche)

Das Nebenläufigkeits-Modell (unveränderliche Versionen, ein Schreiber, viele Leser auf
geteiltem Speicher) **schränkt die DB-Wahl stark ein.** Kriterien:

**Muss:**
1. **Embedded / in-process** (kein separater DB-Server-Prozess).
2. **Datei-basiert, ein Ordner** (passt in `.ragos/`, per Datei-Copy portabel/backupbar).
3. **Unveränderliche, versionierte Stände** (Voraussetzung für sicheres Multi-Leser-Lesen
   über geteilten Speicher) — bzw. gut für „shared storage / decoupled compute" gebaut.
4. **Hybrid**: dense-Vektorsuche **+** sparse/BM25 (Volltext) in-process — für exakte Normtreffer.
5. **Metadaten-Filter** (Norm-Register, Tags, Ordner).
6. **Windows + Python**, ANN-Index, skaliert auf ~20k Docs (→ mehrere 100k Chunks),
   Query < ~1 s (lokal-gecacht).

**Kandidaten zum Vergleich:**
- **LanceDB** — Paradefall: Ordner-basiert, **versionierte/unveränderliche** Lance-Dateien,
  natives Hybrid (Vektor + Volltext/BM25), für geteilten Speicher gebaut, Python/Windows.
  **Stärkster Kandidat**, deckt Punkt 3+4 direkt ab.
- **Qdrant local mode** — Ordner-basiert, aber Sparse-Support im local mode historisch
  unvollständig **und** nicht auf „shared storage / immutable" ausgelegt → riskant für dieses Modell.
- **sqlite-vec + FTS5** — „alles in einer .sqlite": elegant für Einzelplatz, aber ein
  **veränderliches** Single-File über SMB mit mehreren Lesern ist genau der falsche Fall
  → nur tauglich, wenn strikt als unveränderlicher Snapshot + lokaler Cache verteilt.
- **Chroma** — dense-only nativ (BM25 extern), nicht auf immutable/shared ausgelegt.
- **Milvus Lite** — Windows-Support prüfen (evtl. WSL-Zwang → widerspricht „ohne WSL").

> Fazit für die Recherche: Punkt **3 (unveränderlich/versioniert)** + **4 (Hybrid)** sind die
> Filter, die die meisten Kandidaten aussortieren. LanceDB gezielt gegen die Alternativen
> prüfen; M0 entscheidet endgültig.

---

## 14. Meilensteine (revidiert)

- **M0 — Gate (zuerst!):** Machbarkeits-Spike Hybrid-Suche in embedded, versioniertem,
  shared-storage-tauglichem Store (Kandidat: LanceDB). Akzeptanz: In-process Hybrid-Query
  gibt bei „ÖNORM B 1801-1" das norm-tragende Doc zurück; Multi-Leser-Lesen aus lokalem
  Cache stabil. **Ohne M0-Grün wird nichts weiter gebaut.**
- **M1 — Konfig & Vault-Layout:** Pfade Vault-relativ (`.ragos/`), lokaler App-Zustand in
  `%LOCALAPPDATA%`, Rollen/Norm-Muster in `config.json`.
- **M2 — Metadaten-Schicht:** Postgres → SQLite, als **veröffentlichbarer, unveränderlicher
  Snapshot** modelliert (Schreiber read-write lokal, Leser read-only Snapshot/Cache).
- **M3 — Store einbetten & Versionierung:** M0-Ergebnis umsetzen; Veröffentlichen/atomarer
  Version-Swap; lokaler Leser-Cache (Pull der aktuellen Version).
- **M4 — Ollama nativ:** qwen2.5:7b, nur Ingest-Tagging + Norm-Erkennung (nur Schreiber).
- **M5 — Docling + OCR** als Ingest-Backend.
- **M6 — Import & Norm-Register:** Überwachungsordner (NAS) + UI-Upload + Drag&Drop;
  Regex-Register (ÖNORM/DIN/EN/ISO/OIB/§); Versionierung bei Re-Upload; Graph-Bau.
- **M7 — Native Windows-App:** Tray + Autostart + Toast + Drag&Drop, WebView2-Verwaltungs-UI;
  MCP als lokaler Connector (Claude Desktop). **Zwei Installer** (Voll/Leser) via Inno Setup.

---

## 15. Verifikation (durchgängig)

Kein `tests/`-Setup → Verifikation manuell gegen die laufende App + `ruff check app`, pro
Meilenstein ein konkreter End-to-End-Check:

- **M0:** Hybrid-Query trifft „ÖNORM B 1801-1"; zweiter Rechner liest denselben Cache-Stand.
- **M2:** Frischer Start legt Schema an, Admin-Bootstrap läuft, Upload → `documents`-Row.
- **M3:** Schreiber veröffentlicht Version → Leser-Rechner sieht sie nach Refresh.
- **M5:** Gescanntes PDF mit Tabelle → korrekt erkannter Text + erhaltene Tabelle.
- **M6:** Datei in Überwachungsordner → automatisch ingestet; `norm_lookup` findet Norm exakt.
- **M7:** Zwei Installer auf sauberem Profil; Claude Desktop ruft `rag_retrieve` → Chunks.

---

## Kritische Dateien (aus der RAG-OS-Quelle, für den Umbau)

`app/config.py` · `app/db/models.py` · `app/db/session.py` · `app/pipelines/factory.py` ·
`app/pipelines/query.py` · `app/ingest/pipeline.py` · `app/pipelines/vector_ops.py` ·
`app/graph/l2.py` · `app/graph/refresh.py` · `app/maintenance/tag_consolidation.py` ·
`app/maintenance/duplicate_detection.py` · `app/main.py` (Entrypoint/Shell) ·
**neu:** Versionier-/Veröffentlichungs-Schicht, Regex-Norm-Register, Packaging
(PyInstaller-Spec + Inno-Setup, zwei Profile).
