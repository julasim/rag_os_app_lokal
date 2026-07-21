# Sanierungskonzept — RAG OS

> 🗄️ **HISTORISCH (Audit-Record, Stand 2026-07-11).** Rückbau-Plan aus der
> **Docker/Qdrant/Postgres-Ära**; der native Umbau (M1–M8) hat den Alt-Code ohnehin
> ersetzt. Infrastruktur-Bezüge überholt; die noch gültigen Prinzipien stehen in
> **[CLAUDE.md](../CLAUDE.md) §13**. Bleibt als Nachweis der damaligen Rückbau-Entscheidungen.

> **Stand:** 2026-07-11 · **Autor:** Code-Review (statische Analyse)
> **Zweck:** Toten Code und Alt-Konstrukte geordnet zurückbauen, damit die
> Codebasis vor dem Multi-User-Rollout schlank und eindeutig ist. Ergänzt das
> [Sicherheitskonzept](SICHERHEITSKONZEPT.md) (Befunde S1–S8).

Die Sicherheitsfixes (S1–S5) haben **Vorrang** vor der Sanierung. Sanierung ist
Risikoabbau zweiter Ordnung: weniger Code = weniger Angriffsfläche und weniger
Stellen, an denen die ACL vergessen wird.

> **STATUS 2026-07-11 — UMGESETZT:** D1–D9 (toter Code, Alt-Config,
> `streamlit`/`pandas`-Deps), R2 (vestigialer `project`-Param, Backend+Frontend),
> R4 (kanonische ACL [app/auth/folders.py](../app/auth/folders.py)) und der
> Reindex-Qdrant-Bug sind erledigt. Zusätzlich: MCP-only-Rückbau (REST-Suche +
> `run_query`/`rag_search` entfernt). Offen als bewusste Nacharbeit: R5
> (`X-UI-Token`-Pfad), R6 (`os.environ` in `oauth.py`), R7 (ARCHITECTURE/README
> vollständig nachziehen) — siehe unten.

---

## 1. Toter Code — Inventar

Alles hier ist **nachweislich unreferenziert** (kein Import-/Routing-Pfad) oder
laut [CLAUDE.md](../CLAUDE.md) §4 bewusst stillgelegt.

| # | Artefakt | Nachweis | Aktion |
|---|----------|----------|--------|
| D1 | `app/ui/` (Streamlit: `app.py`, `client.py`, `pages/*`) | Kein Import außerhalb `app/ui/` (grep) | **Löschen** |
| D2 | `app/api/projects.py` | Nicht in `api/__init__.py` / `main.py` gemountet | **Löschen** |
| D3 | Frontend `pages/Projects.tsx`, `pages/ProjectDetail.tsx`, `api/projects.ts` | Nicht in `App.tsx` geroutet/importiert | **Löschen** |
| D4 | `mcp_server.mcp_auth_middleware` (BaseHTTPMiddleware-Variante) | `main.py` nutzt `MCPAuthMiddleware` (pure ASGI) | **Löschen** + Export in `__init__` entfernen |
| D5 | `oauth._validate_redirect_uri` | Nirgends aufgerufen | **NICHT löschen — verdrahten** (Sicherheit S4) |
| D6 | `config/projects.yml`, `config/project_defaults.yml` | Von keinem Code gelesen (§4) | **Löschen** (nach Rücksprache, §4-Hinweis) |
| D7 | `scripts/migrate-projects-to-db.py` | Importiert entferntes Submodul → kaputt (§4) | **Löschen** |
| D8 | Deps `streamlit>=1.40`, `pandas>=2.2` in `app/pyproject.toml` | Nicht mehr importiert (§12) | **Entfernen** |
| D9 | `.streamlit/secrets.toml` in `.gitignore` | Streamlit weg | Zeile entfernen (kosmetisch) |

**Umfang:** ~10 Python-Dateien + 3 TS-Dateien + 3 Configs. Grober Schätzwert
**>1.200 Zeilen** entfernbar, plus zwei schwere Dependencies.

---

## 2. Vestigiales „Projekt"-Konzept — Rückbau

Das „Projekt"-Konzept ist laut §4 entfernt, aber die **API-Oberfläche trägt es
noch als Leichnam mit**:

- `project: str = Form(...)` in `upload_documents`, `upload_zip` und diverse
  Aufrufe, die es an `_check_upload_access(ctx, project)` durchreichen — dort mit
  `# noqa: ARG001` **ignoriert**.
  ([documents.py:92/157/323](../app/api/documents.py))
- `project=""` in `reindex_document_file(...)` und `_run_ingest_job` — reiner
  Ballast. ([documents.py:674](../app/api/documents.py), [pipeline.py:70](../app/ingest/pipeline.py))

**Risiko:** Verwirrt Leser und suggeriert eine Zugriffsdimension, die es nicht
mehr gibt. Ein Reviewer könnte glauben, `project` sei ein Filter/Scope.

**Aktion (R2):** `project`-Parameter aus Signaturen und Form-Feldern entfernen.
Das ist ein **API-Contract-Change** → Frontend-`documents.ts` gleichzeitig
anpassen. In einem Commit, klar als „remove vestigial project param" markiert.

---

## 3. Doppelte/riskante ACL-Logik — Konsolidierung

Es gibt **drei** Ordner-Zugriffs-Implementierungen mit **unterschiedlichem**
Verhalten:

1. `AuthContext.can_access_folder()` — segmentgrenzbewusst (**korrekt**).
   ([dependencies.py:37](../app/auth/dependencies.py))
2. `mcp_server._require_folder()` — nacktes `startswith` (**Bug S3**).
   ([server.py:70](../app/mcp_server/server.py))
3. Inline-`startswith`/`.in_()`-Filter in `documents.py:list_documents`,
   `list_folders`, `rag_list_documents`.

**Aktion (R4):** Eine kanonische ACL — `can_access_folder()` — als einzige
Quelle. MCP-Tools und Listen-Endpunkte darauf umstellen. Für DB-Filter eine
Helper-Funktion `folder_filter_clause(ctx)` bauen, die dieselbe Segmentlogik in
SQL abbildet. Beseitigt S1 und S3 an der Wurzel und verhindert die nächste
vergessene Prüfung.

---

## 4. Weitere Aufräumpunkte

| # | Punkt | Ort | Aktion |
|---|-------|-----|--------|
| R5 | `X-UI-Token`-Header-Auth (Streamlit-Backend-Relikt) | [dependencies.py:91/123](../app/auth/dependencies.py) | Prüfen ob React ihn nutzt (`client.ts` setzt ihn) — wenn Cookie reicht, Header-Pfad entfernen |
| R6 | `mcp_server/oauth.py` liest `os.environ` direkt | [oauth.py:30-38](../app/mcp_server/oauth.py) | In `settings()`-Schicht überführen (§13 „noch offen", verletzt harte Konvention §3) |
| R7 | Doku-Drift: `docs/ARCHITECTURE.md`/`README.md` beschreiben „Projekt"-3-Ebenen-Modell | (§4) | Auf Ordner+Tags-Modell aktualisieren |
| R8 | `_docs_on`/CORS-`:8501` Streamlit-Reste | [main.py:211](../app/main.py) | Mit D-Reihe zusammen bereinigen |

---

## 5. Empfohlene Reihenfolge

1. **Sicherheit zuerst:** S1, S2 (blockieren Rollout), dann S3–S5.
   → dabei **R4** (ACL-Konsolidierung) mitziehen, weil es S1/S3 an der Wurzel löst.
2. **Toter Code D1–D4, D7:** risikoarm, reine Löschung, sofort machbar.
   `ruff check app` als Netz; Container-Boot + Durchklicken zur Verifikation
   (kein Test-Setup, §6).
3. **Vestigiales `project` (R2) + Deps (D8):** API-/Build-Change, ein Commit,
   Frontend synchron.
4. **D6 (config/*.yml)** nur nach kurzer Rücksprache (§4 markiert sie als „nicht
   ohne Rücksprache löschen").
5. **R6/R7:** Nachzieharbeit, kein Blocker.

**Verifikation ohne Test-Suite** (§6): Nach jedem Schritt `ruff check app`,
Container-Boot (`make restart`), MCP + `/api/retrieve` + Upload + Löschen
manuell/curl durchspielen. Die im Juli-Audit erstellten Live-Prüfskripte (WSL,
außerhalb Repo) als Regressionsbasis wiederverwenden — idealerweise jetzt in ein
echtes `tests/` gegen lokales Postgres/Qdrant überführen (§6/§7 Anti-Goal: keine
Mocks).

---

## 6. Nicht sanieren (bewusste Entscheidungen)

- API-Key-Verifikation als O(n)-bcrypt-Schleife ([keys.py:61](../app/auth/keys.py))
  — bis ~1000 Keys ok, so dokumentiert. Erst bei realem Bedarf Prefix-Index.
- `run_query`/`rag_search` (lokaler Ollama-Pfad) ist deprecated, aber als
  Offline-Fallback bewusst erhalten (§5) — nicht entfernen.
- Zwei Compose-Varianten (Edge/Standalone) — Absicht (§10/§11), kein Wildwuchs.
