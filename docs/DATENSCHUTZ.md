# Datenschutz (DSGVO) — technische Übersicht

> **Kein Rechtsdokument, keine Rechtsberatung.** Diese Datei beschreibt, welche
> personenbezogenen Daten die App wo verarbeitet und welche **technischen**
> Datenschutz-Maßnahmen (DSGVO Art. 25/32) umgesetzt sind. **Verantwortlicher** i.S.d.
> DSGVO ist der Betreiber (Julius Sima) — Rechtsgrundlage, Betroffenen-Information,
> Auftragsverarbeiter-Verträge und die Bewertung „konform" liegen bei ihm.
> Stand: 2026-07-22.

## 1. Welche Daten liegen wo

| Speicher | Ort | Personenbezogene Daten (potenziell) |
|---|---|---|
| `credentials.sqlite` | **lokal** `%LOCALAPPDATA%\RAG-OS` | Nutzer-E-Mails, **bcrypt-Passwort-Hashes**, API-Key-Hashes |
| `<vault>/.ragos/state.sqlite` | **im Vault** (Firma) | Dokument-Metadaten, **Chunk-Volltext**, `query_text` (Suchanfragen), Graph, Audit-UUIDs |
| LanceDB `index.lance` | **im Vault** | Chunk-Volltext + Vektoren + Metadaten |
| Rohdateien | **im Vault** (`Dokumente/`) | die Original-PDFs/DOCX (beliebiger Inhalt) |
| `ragos.log` | lokal | Nur Event-Namen, IDs, Fehler **+ E-Mail** bei Login/User-Anlage — **kein** Dokument-/Query-Inhalt, keine Passwörter |
| Backups | `backup_dir` | Kopien von credentials + state (Klartext), LanceDB-Snapshot |

## 2. Technische Maßnahmen (Art. 25/32)

- **Lokal, keine Netz-Exposition:** nur `127.0.0.1`, kein offener Port, kein VPS/Cloud-Dienst.
- **Keine externen Web-Ressourcen in der UI.** Die Oberfläche lädt **nichts** aus dem Netz.
  *Korrektur/Historie:* bis 2026-07-22 lud die UI **Inter von Google Fonts**
  (`fonts.googleapis.com`/`gstatic.com`) — Google sah damit bei jedem UI-Start die IP des
  Rechners (der bekannte Google-Fonts-Fall). **Behoben:** Links entfernt, Schrift auf einen
  **lokalen System-Stack** umgestellt (`"Helvetica Neue", Helvetica, Arial`); verifiziert,
  dass im gebauten Frontend keine `googleapis`/`gstatic`-Referenz mehr steckt.
  **Rest-Risiko:** es gibt (noch) **keine CSP**, die künftige externe Requests hart blockt.
- **Zugriffskontrolle:** kanonische, serverseitig erzwungene Ordner-ACL
  ([auth/folders.py](../app/auth/folders.py)), per-User, segmentgrenzbewusst, IDOR-fest;
  Löschen nur Web-UI-Admin. Wissensgraph-Anzeige ebenfalls per-User ACL-gefiltert (§13).
- **Geheimnisse gehasht:** Passwörter + API-Keys nur als bcrypt-Hash, nie Klartext.
- **Retrieve-only:** die App generiert keine Antworten, sie liefert Chunks + Quellen.
- **Mandanten-Trennung (Multi-Vault):** Credentials bleiben lokal (nie im Vault/NAS),
  Content pro Firma in getrenntem `state.sqlite`.
- **Logging minimiert:** kein Dokument-/Query-Inhalt, keine Passwörter im Log.

## 3. Betroffenenrechte (technisch abgedeckt)

- **Löschung (Art. 17):** `delete_document` ([api/documents.py](../app/api/documents.py))
  entfernt LanceDB-Chunks (`store.delete_by_doc_id`), die Rohdatei **und** die DB-Zeile
  (Cascade auf Chunks/Graph/Jobs). Siehe Einschränkung §5.
- **Auskunft/Portabilität (Art. 15/20):** Dokument-Export über die REST-API.
- **Berichtigung (Art. 16):** Dokumente lassen sich neu indexieren/verschieben.

## 4. Aufbewahrung (Art. 5 Abs. 1 e)

- **Query-Log:** `QUERY_LOG_KEEP_DAYS` (Default 90 Tage), nächtlicher Cleanup
  (`cleanup_old_query_logs`). `0` = nie löschen.
- **Backups:** `backup_keep_days`-Retention (Cleanup alter Snapshots).

## 5. Offene Punkte — Verantwortung des Betreibers

Diese Punkte kann/soll die Software allein nicht entscheiden:

1. **Datenfluss zum KI-Client (wichtigster Punkt).** `rag_retrieve` liefert Chunk-Text an
   den konsumierenden MCP-Client. Ist das ein **Cloud-LLM** (Claude Desktop, ChatGPT),
   verlassen personenbezogene Daten das System → **Auftragsverarbeitung/Drittland**
   (Art. 28, Art. 44 ff.). Erfordert Rechtsgrundlage + AV-Vertrag mit dem Anbieter —
   **oder** einen lokalen Client. Die App sendet nichts aktiv nach außen.
2. **Verschlüsselung at-rest.** `credentials.sqlite`, `state.sqlite`, LanceDB und die
   Rohdateien liegen als **Klartext** — wer Dateisystem-/NAS-Zugriff hat, liest alles
   (die App-ACL greift dort nicht). **Empfehlung:** Datenträger-Verschlüsselung
   (BitLocker) + strikte NAS-/Ordnerrechte.
3. **Löschung restlos.** LanceDB ist versioniert (MVCC): gelöschte Chunks bleiben in
   `prev`/alten Versionen bis zur Kompaktierung (`prune_versions`) und in Backups bis zum
   Retention-Ablauf. Für eine echte Betroffenen-Löschung ggf. Kompaktierung anstoßen +
   Backup-Ablauf beachten.
4. **Datenminimierung `query_text`.** Suchanfragen werden im Volltext gespeichert (90 Tage).
   Prüfen, ob nötig — sonst `QUERY_LOG_KEEP_DAYS` kürzen/0.

> Verankert im Sicherheits-Audit: [../CLAUDE.md](../CLAUDE.md) §13 (ACL/Löschung/Graph-ACL).
