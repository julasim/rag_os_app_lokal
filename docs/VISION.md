# RAG OS — Vision

> 🧭 **Der Vision-KERN gilt weiter; die INFRA-Details unten sind überholt.** Noch gültig:
> selbstgehosteter Such-Knoten, „das System antwortet nicht — es liefert" (retrieve-only,
> der Client formuliert), selbst-pflegende Ablage, kein Chat/DMS. **Überholt** (pre-native-
> Rebuild): „Projekt = Qdrant-Collection" (heute nur Ordner + Tags, EINE LanceDB-Tabelle),
> Ollama/lokaler-LLM-Antwortpfad + LLM-Tagging (heute LLM-frei, INT8-ONNX, deterministisch),
> `rag_search`/`rag_export_answer` (heute `rag_retrieve` MCP-only), VPS/Docker (heute native
> Windows-App), Phasen-Status (siehe **[../CLAUDE.md](../CLAUDE.md)** + **[../BUILD-PLAN.md](../BUILD-PLAN.md)**).

Diese Datei beschreibt, **was das System am Ende sein soll**. Sie ist die Nordsicht
für jede Architektur-Entscheidung. Wenn ein Vorschlag mit dieser Vision in Konflikt
steht, gewinnt die Vision — oder die Vision wird bewusst angepasst, *bevor* der
Code geschrieben wird.

---

## Was RAG OS ist

**RAG OS ist ein selbstgehosteter Such-Knoten über das gesamte Wissen von Julius.**

Projekte sind die oberste Ablage-Ebene mit harter Datentrennung — beliebig viele
dynamisch anlegbar (wie Top-Level-Ordner), jedes Projekt eine eigene
Qdrant-Collection mit eigener LLM- und Chunking-Konfiguration. Innerhalb der
Projekte: organische Sub-Ordner und Tags, alles dynamisch wachsend.

**Zielgröße am Endausbau:** ~10.000 Dokumente, manueller Upload via UI
(auch ganze Ordner als ZIP), keine automatische Quellen-Synchronisierung,
1–2 menschliche Admins, beliebig viele Programm-Clients via API-Keys.

---

## Das System antwortet nicht — es liefert

Auf jede Frage gibt es die relevantesten Text-Chunks mit vollständigen
Quellen-Metadaten (Dokument, Ordner, Seite, Score) zurück. Den eigentlichen
Antwort-Text formuliert immer der konsumierende Client:

- Claude Desktop via MCP
- ChatGPT via GPT-Actions
- Langdock via REST-Connector
- eigene n8n-Flows via Bearer-API

Das hebt das System aus der Klasse "lokales RAG-Spielzeug" heraus — es ist
die **Knowledge-Layer-Schnittstelle** unter Julius' KI-Toolbelt.

Der ursprüngliche `rag_search`-Pfad mit lokaler LLM-Antwort bleibt als
Compatibility-Modus erhalten, ist aber nicht mehr der Standardweg.

---

## Datensouveränität, ehrlich beschrieben

Dokumente liegen ausschließlich auf dem eigenen VPS, werden nie an Dritte
hochgeladen. Auf konkrete Anfragen verlassen die relevanten Chunks aber den
Server — sie kommen ja im Claude/GPT-Kontext an. Wer absolute Lokalität braucht,
nutzt den optionalen lokalen LLM-Antwort-Pfad. Das ist eine bewusste Abwägung:
Qualität (Claude/GPT) gegen Strikt-lokal (Ollama).

Was bleibt strikt lokal:
- die Dokumenten-Datei selbst (nie als File rausgereicht)
- der Embedding-Vektor und sämtliche Indexe
- das Auto-Tagging beim Ingest (lokaler Ollama-Call)

---

## Antwort-Konsolidierung als Service

Über das MCP-Tool `rag_export_answer` schickt der Client seine fertige Antwort
plus Quellen-Liste zurück, das System erzeugt daraus eine formatierte DOCX oder
PDF mit konsistentem Layout. Damit ist das System auch der Single-Point-of-Truth
für die *finalen* Recherche-Artefakte, nicht nur für die Rohdaten.

Der gleiche Endpunkt steht über REST und UI zur Verfügung.

---

## Selbst-pflegende Ablage

Das System bleibt nicht nur stabil, sondern verbessert sich mit der Zeit. Jede
Nacht läuft ein Maintenance-Pass, der das Wissen-Inventar reorganisiert:

- clustert Dokumente nach Inhalt und schlägt eine bessere Ordnerstruktur vor
  (Bestätigung per Klick)
- führt synonyme Tags zusammen (autonom, mit Audit-Log und 30-Tage-Undo)
- meldet semantische Duplikate zur Bereinigung (Bestätigung per Klick)

Niedrigrisiko-Aktionen (Tag-Synonyme) laufen autonom. Hochrisiko-Aktionen
(Ordner verschieben, Dokumente löschen) brauchen 1-Klick-Bestätigung. Damit
verfällt die Ablage nicht zur Halde, sondern wird mit dem Wachstum
*intelligenter* — auch wenn sie nur in 30-Sekunden-Häppchen gepflegt wird.

---

## Was RAG OS nicht ist

- **Keine Chat-Oberfläche.** Die Admin-UI dient der Pflege, nicht dem
  Antworten-Holen. Antworten kommen aus Claude Desktop, ChatGPT, Langdock & Co.
- **Kein DMS.** Die Quell-Dateien werden gespeichert, aber das System ist
  keine vollwertige Dokumentenverwaltung mit Versionierung, Workflows oder
  Rechte-Schichten unterhalb der Projekt-Ebene.
- **Keine Multi-Mandanten-Plattform.** Es bleibt ein Single-Tenant-System
  für 1–2 menschliche Admins.
- **Kein Ersatz für Cloud-LLMs.** Der lokale 3B-LLM ist Werkzeug für
  Auto-Tagging und Cluster-Naming, nicht für die finale Antwort.

---

## Phasen-Status

Aktueller Stand (Phase 3 abgeschlossen): Compose läuft, Ingest und Query
funktionieren, MCP-Server mit den Tools `rag_search`, `rag_list_documents`,
`rag_get_document`, `rag_upload`, `rag_delete_document`, `rag_list_projects`,
`rag_stats`. Streamlit-Admin mit Login, API-Keys und Projekt-Trennung.
Auto-Tagging via LLM beim Ingest.

Die nächsten Schritte sind in [docs/ROADMAP.md](ROADMAP.md) festgehalten (oder
in der jeweils aktuellen Plan-Datei unter `~/.claude/plans/`).
