# Sicherheitskonzept — RAG OS

> 🗄️ **HISTORISCH (Audit-Record, Stand 2026-07-11).** Punkt-in-Zeit-Audit aus der
> **Docker/Qdrant/Postgres/OAuth-Ära**. Die Infrastruktur-Bezüge sind überholt (heute:
> native App, LanceDB, SQLite, MCP Bearer-only, kein OAuth/Docker). Die **Sicherheits-
> Prinzipien und behobenen Befunde** leben weiter in **[CLAUDE.md](../CLAUDE.md) §13** —
> nicht rückbauen. Dieses Dokument bleibt als Nachweis, was wann warum auditiert wurde.

> **Stand:** 2026-07-11 · **Autor:** Code-Review (statische Analyse)
> **Kontext:** Ergänzung zum Pre-Prod-Audit (Juli 2026, CLAUDE.md §13) vor der
> Mehrbenutzer-Einführung bei SIMA Architecture.
>
> **Wichtig:** Dieses Review war rein **statisch** (Code-Lesung, keine
> Live-Exploitation in dieser Session). Die mit *(neu)* markierten Befunde
> waren im Juli-Audit noch nicht erfasst. Vor Freigabe: jeden Fix live
> nachstellen (wie im Juli-Audit üblich).
>
> **STATUS 2026-07-11 — BEHOBEN.** S1–S8 sowie der Reindex-Qdrant-Bug umgesetzt.
> Kern: kanonische ACL [app/auth/folders.py](../app/auth/folders.py);
> Retrieval-ACL in [app/pipelines/query.py](../app/pipelines/query.py); Export-Guard
> + gemeinsame `delete_qdrant_chunks` ([app/pipelines/vector_ops.py](../app/pipelines/vector_ops.py));
> OAuth redirect_uri-Validierung + XSS-Escape in
> [app/mcp_server/oauth_routes.py](../app/mcp_server/oauth_routes.py). Suche ist
> jetzt **MCP-only** (REST-Suche entfernt → S1-Fläche kleiner).
>
> **LIVE-VERIFIZIERT (WSL-Audit-Umgebung, ALLE grün):** S1/S1b/S2/S3, Reindex
> und MCP-only → 12/12; S4 (nicht-registrierte `redirect_uri` → 400
> `invalid_redirect_uri`) und S5 (Login-`state` HTML-escaped + CSP-Header) → 8/8
> mit aktiviertem OAuth. Kern-Belege: Key `/Steuer/` ohne folder sieht nur
> `/Steuer/`; `/Steuer` (ohne Slash) matcht nicht `/Steuer2025-Neukunde/`;
> `/Steuer/`-Key exportiert fremdes `/Marketing/`-Doc nicht; Reindex verdoppelt
> keine Chunks; `/api/retrieve`+`/api/query` liefern 404, `rag_search` ist weg.

---

## 0. Management-Zusammenfassung

Das Juli-Audit hat die Einzel-Dokument-Endpunkte (IDOR) sauber abgesichert.
Bei der erneuten Prüfung sind jedoch **mehrere Stellen aufgefallen, die dieselbe
Absicherung noch nicht bekommen haben** — teils an genau den Endpunkten, über
die die Mandantentrennung (`allowed_folders`) im Mehrbenutzerbetrieb steht und
fällt.

| # | Befund | Schwere | Kern |
|---|--------|---------|------|
| S1 | `/api/retrieve` + `/api/query` ohne `folder` umgehen die Ordner-ACL | **Kritisch** | Eingeschränkter Key liest die **gesamte** Sammlung |
| S2 | `/api/documents/export` ohne Ordner-Prüfung | **Hoch** | IDOR: fremde Dokumente per ID exportierbar |
| S3 | MCP `_require_folder` nicht segmentgrenzbewusst | **Mittel** | `/Steuer` erlaubt `/Steuerberatung-Fremd/` |
| S4 | OAuth `redirect_uri` wird nicht gegen Client validiert | **Mittel** | Auth-Code-Diebstahl per Phishing |
| S5 | OAuth-Login-Seite: reflektiertes XSS | **Mittel** | `redirect_uri`/`state`/`scope` ungeescaped |
| S6 | CORS erlaubt `localhost` mit Credentials in Prod | **Niedrig** | Angriffsfläche über lokale Origins |
| S7 | Rate-Limit-/Session-Dicts wachsen unbegrenzt | **Niedrig** | Speicher-DoS über viele Keys/IPs |
| S8 | Login-Timing erlaubt User-Enumeration | **Niedrig** | Kein Dummy-Hash bei unbekannter Mail |

**Priorität:** S1 und S2 blockieren den Multi-User-Rollout — beide brechen die
Mandantentrennung, die laut §13 der zentrale Schutz ist. Zuerst fixen.

---

## 1. Kritische & hohe Befunde

### S1 — Retrieval umgeht die Ordner-ACL bei fehlendem `folder` *(neu)*
**Ort:** [app/api/query_router.py:38](../app/api/query_router.py) und
[app/api/query_router.py:85](../app/api/query_router.py)

```python
if payload.folder and not ctx.can_access_folder(payload.folder):
    raise HTTPException(403, ...)
```

Die Prüfung läuft **nur wenn `payload.folder` gesetzt ist**. Ruft ein API-Key
mit eingeschränkten `allowed_folders` `/api/retrieve` (oder `/api/query`) **ohne**
`folder` auf, wird:
1. die ACL-Prüfung übersprungen, und
2. in [query.py](../app/pipelines/query.py) `_build_folder_filter(None)` → `None`
   gebaut, d.h. **kein** Qdrant-Filter → Suche über die **komplette** Collection.

**Auswirkung:** Ein Mandant/Partner-Key, der nur `/Steuer/` sehen dürfte,
bekommt über den **Hauptendpunkt** (`/api/retrieve` ist laut §5 der dokumentierte
Primärweg) Chunk-Volltexte aus **allen** Ordnern zurück. Das ist der direkte
Bruch der Mandantentrennung — DSGVO-relevant.

**Zum Vergleich:** Der MCP-Pfad macht es richtig — `rag_retrieve` ruft
`_require_folder(folder or "/")`, was einen eingeschränkten Key bei `folder=None`
ablehnt (`"/"` liegt in keinem erlaubten Unterordner). Der REST-Pfad hat diese
Logik nicht.

**Fix (empfohlen):**
- `run_retrieve` / `run_query` die `allowed_folders` des Keys übergeben und den
  Qdrant-Filter serverseitig **erzwingen** (OR über die erlaubten Ordner), nicht
  dem Client-Parameter vertrauen.
- Solange der Filter auf `meta.folder` exakt-matcht: entweder Filter auf
  `folder_path`-Prefix umstellen oder bei `folder=None` + eingeschränktem Key
  hart **403** werfen (schneller Minimal-Fix, gleiche Semantik wie MCP).

---

### S2 — `/api/documents/export` ohne Ordner-Zugriffskontrolle *(neu)*
**Ort:** [app/api/documents.py:776](../app/api/documents.py) (`export_documents`)

Der Endpunkt lädt Dokumente per `Document.id.in_(payload.ids)` und packt deren
**Originaldateien/PDFs** in ein ZIP — **ohne** `_require_folder_access`. Genau
die IDOR-Klasse, die das Juli-Audit für `get/patch/delete/chunks/download`
geschlossen hat; `export` wurde übersehen.

**Auswirkung:** Ein Key mit eingeschränkten `allowed_folders` (oder ein
Angreifer mit erratbaren/geleakten doc_ids) exportiert Inhalte fremder Ordner im
Klartext.

**Fix:** Vor dem Zippen pro Dokument `_require_folder_access(ctx, doc.folder_path)`
prüfen und nicht-erlaubte IDs überspringen (oder 403). Zusätzlich `write`/`read`-
Scope-Check konsistent zu den anderen Endpunkten. Denselben Guard in
`ingest_job_status` erwägen (Job-Metadaten sind weniger sensibel, aber der
Konsistenz halber).

---

## 2. Mittlere Befunde

### S3 — MCP-Ordnerprüfung nicht segmentgrenzbewusst *(neu)*
**Ort:** [app/mcp_server/server.py:75](../app/mcp_server/server.py) (`_require_folder`),
analog [server.py:188](../app/mcp_server/server.py) (`rag_list_documents`)

```python
if not any(folder_path.startswith(f) for f in af):
```

Nacktes `startswith` — genau der Bug, den §13 in `can_access_folder` behoben hat
(dort mit Normalisierung auf abschließendes `/`). Ist ein erlaubter Ordner ohne
Trailing-Slash gespeichert (z.B. `/Steuer`), matcht der Key auch
`/Steuerberatung-Fremd/`.

**Fix:** MCP-Pfad auf `AuthContext.can_access_folder()` umstellen bzw. dieselbe
Segment-Normalisierung anwenden. **Eine** ACL-Implementierung, die REST und MCP
teilen — die Doppelung ist die eigentliche Ursache (siehe Sanierungskonzept R4).

---

### S4 — OAuth: `redirect_uri` nicht gegen Client validiert *(neu)*
**Ort:** [app/mcp_server/oauth_routes.py:150/182](../app/mcp_server/oauth_routes.py)
(`oauth_authorize_get`/`_post`)

Die Funktion `oauth._validate_redirect_uri()`
([oauth.py:133](../app/mcp_server/oauth.py)) existiert, wird aber **nirgends
aufgerufen** (toter Code). Der Authorize-Endpunkt stellt einen Auth-Code für eine
**beliebige** `redirect_uri` aus und leitet dorthin um — ohne zu prüfen, dass sie
zu den bei der Registrierung hinterlegten `redirect_uris` des Clients gehört.

**Auswirkung:** Klassischer Auth-Code-Diebstahl: Angreifer schickt dem Admin
einen Authorize-Link mit angreiferkontrollierter `redirect_uri` **und**
angreiferkontrolliertem `code_challenge`; nach Login landet der Code beim
Angreifer, der ihn mit seinem `code_verifier` einlöst (PKCE schützt hier nicht,
weil der Angreifer die Challenge selbst gesetzt hat). Single-User mildert das
(nur `OAUTH_USER_EMAIL` kann sich anmelden), macht es aber nicht harmlos.

**Fix:** `_validate_redirect_uri(client, redirect_uri)` in **beiden** Authorize-
Handlern erzwingen; bei Mismatch abbrechen (kein Redirect).

---

### S5 — OAuth-Login-Seite: reflektiertes XSS *(neu)*
**Ort:** [app/mcp_server/oauth_routes.py:171](../app/mcp_server/oauth_routes.py)
(`_LOGIN_HTML.format(...)`)

`client_id`, `redirect_uri`, `state`, `scope` kommen aus Query-Parametern und
werden **ohne HTML-Escaping** per `str.format()` in Attribut-Werte interpoliert.
Ein Wert wie `"><script>…` bricht aus dem Attribut aus.

**Auswirkung:** Reflektiertes XSS auf der Anmeldeseite → Diebstahl der
eingegebenen MCP-Credentials.

**Fix:** Alle interpolierten Werte durch `html.escape(..., quote=True)` schicken,
oder ein Template-Engine mit Auto-Escaping (Jinja2) verwenden. Zusätzlich eine
`Content-Security-Policy` auf der Login-Antwort setzen.

---

## 3. Niedrige Befunde / Härtung

- **S6 — CORS-Origins.** [main.py:207](../app/main.py) erlaubt
  `http://localhost` und `http://localhost:8501` mit `allow_credentials=True`
  auch in Produktion. `:8501` ist Streamlit-Altlast (entfernt). Origins auf die
  echte Domain reduzieren; localhost nur im Dev-Profil.
- **S7 — Unbegrenzt wachsende In-Memory-Maps.** `_buckets`
  ([ratelimit.py:26](../app/mcp_server/ratelimit.py)), `_login_buckets`
  ([auth_router.py:30](../app/api/auth_router.py)), `_zip_sessions`
  ([suggest.py:42](../app/api/suggest.py)) werden nur lazy/teilweise bereinigt.
  Viele verschiedene Keys/IPs → langsames Speicherwachstum. Periodische
  Eviction oder `cachetools.TTLCache`.
- **S8 — User-Enumeration per Timing.**
  [users.py:51](../app/auth/users.py) `authenticate_user` überspringt bcrypt bei
  unbekannter Mail → messbar schnellere Antwort. Dummy-`checkpw` gegen einen
  konstanten Hash ausführen. (Durch S-Rate-Limit pro Mail abgemildert.)
- **MCP DNS-Rebinding-Schutz global aus.** [main.py:63](../app/main.py) patcht
  `validate_request` auf No-Op und setzt den Host-Header hart auf `localhost`.
  Hinter Caddy/TLS vertretbar (so dokumentiert), aber: Der Schutz ist damit auch
  weg, falls der Container je **ohne** Edge-Proxy exponiert wird. Als bewusstes
  Restrisiko im Betriebshandbuch führen.
- **Prompt-Injection-Restrisiko.** `rag_retrieve` reicht rohen Chunk-Text an den
  Client — beim konsumierenden LLM abzusichern (bereits in §13 vermerkt).

---

## 4. Was solide ist (nicht anfassen)

- API-Keys: bcrypt-Hash, Klartext nur einmalig, Prefix-Gate vor dem Vergleich.
- UI-JWT: HS256 mit **explizit** gepinntem `algorithms=[...]` → keine
  alg-confusion. ([users.py:75](../app/auth/users.py))
- ZIP-Upload: Pfad-Traversal-, Symlink-, Zip-Bomb-, Datei-/Größen-Limits
  vorhanden und ordentlich. ([documents.py:269](../app/api/documents.py))
- Refresh-Token-Rotation mit Replay-Detection (revoke-all bei Wiederverwendung).
- Login-Rate-Limit pro Mail, MCP-Rate-Limit auf Identität statt spoofbarem XFF.
- Qdrant-Löschung per `meta.doc_id`-Filter (an den Lösch-Endpunkten korrekt).
- Keine Secrets im Repo (nur `.env.example`), keine `subprocess(shell=True)`,
  kein `eval`/`pickle`/`yaml.load`.

---

## 5. Sicherheits-Grundsätze (verbindlich für neue Endpunkte)

1. **Jeder Endpunkt, der Dokumente/Inhalte per ID oder Filter liefert, ruft die
   Ordner-ACL** — auch Sammel-/Export-/Such-Endpunkte, auch wenn kein `folder`
   übergeben wird. Default = deny.
2. **Eine ACL-Implementierung** für REST und MCP (siehe Sanierung R4). Nie
   nacktes `startswith`.
3. **Filter serverseitig erzwingen**, nie dem Client-`folder`-Parameter allein
   vertrauen.
4. **HTML immer escapen** (Auto-Escaping-Template), Redirect-Ziele immer gegen
   Whitelist prüfen.
5. Fixes **live nachstellen** (isolierte WSL/Docker-Umgebung, wie Juli-Audit),
   Testskripte als Grundlage einer echten Test-Suite ablegen.
