"""
Zentrale Konfiguration.

Settings: aus Umgebungsvariablen / .env, via pydantic-settings, gecached.
Andere Module importieren NUR aus hier, niemals direkt aus os.environ.

Lokale Variante (rag-os-app-lokal): keine Docker-Env-Pflicht. Alle früheren
Pflichtfelder haben lokale Defaults; Vault + lokale App-DB liegen unter
Windows-Userdata (%LOCALAPPDATA%\\RAG-OS) bzw. im Vault-Ordner.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Plattform-Pfade (KEINE App-Config — nur OS-Standardorte)
# ---------------------------------------------------------------------------
def _appdata_dir() -> Path:
    """Userdata-Basis: %LOCALAPPDATA%\\RAG-OS (Windows) bzw. Fallback ~/.rag-os."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    return (Path(base) / "RAG-OS") if base else (Path.home() / ".rag-os")


_APPDATA = _appdata_dir()
_DEFAULT_VAULT = _APPDATA / "vault"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Vault + lokale App-DB (lokale Variante) ---
    # Der Vault (Docs + LanceDB-Index) ist portabel; Default lokal, per Env
    # RAG_VAULT_PATH auf die NAS zeigbar (setzt die Desktop-Shell aus
    # app-settings.json). appstate.sqlite bleibt IMMER lokal.
    vault_path: Path = Field(default=_DEFAULT_VAULT, validation_alias="RAG_VAULT_PATH")

    # --- Embeddings (INT8-ONNX via onnxruntime, kein Ollama/fastembed) ---
    # multilingual-e5-large (1024-dim, mehrsprachig/DE), INT8-quantisiert und vom
    # Build gebacken (models_dir/embedder). e5 verlangt Query/Passage-Präfixe
    # (siehe factory). Feld nur noch informativ — factory lädt das gebündelte ONNX.
    embed_model: str = "intfloat/multilingual-e5-large"

    # --- App ---
    app_secret_key: str = "local-rag-os-secret"   # lokal; OAuth ist ohnehin aus
    admin_email: str = "admin@local"
    admin_password: str = "changeme"
    upload_dir: Path = _DEFAULT_VAULT / "Dokumente"
    staging_dir: Path = _APPDATA / "staging"
    backup_dir: Path = _APPDATA / "backups"
    backup_keep_days: int = 7
    # --- Publish/Versionierung (M7) ---
    # Getaggte Versionen ("current"/"prev") sind vor Cleanup HART geschützt;
    # ungetaggte Alt-Versionen werden best-effort nach dieser Frist geräumt.
    publish_cleanup_grace_days: int = 7
    # Reader (M8e): Intervall (Sek.), in dem der lokale Cache mit der
    # veröffentlichten Vault-Version resynchronisiert wird.
    reader_refresh_interval_sec: int = 300
    query_log_keep_days: int = 90           # DSGVO-Speicherbegrenzung; 0 = nie löschen
    docs_enabled: bool = False              # /docs + /openapi.json nur wenn true
    rerank_enabled: bool = True             # Post-Retrieval-Reranker (BGE) an/aus
    # Lokale Ein-Nutzer-Desktop-App: die UI-Endpunkte fallen ohne Token auf den
    # lokalen Admin zurück (kein Login-Wall). NUR vertretbar, weil der Server an
    # 127.0.0.1 gebunden ist (pywebview-Shell, M8). Für einen echten Mehrbenutzer-/
    # Netz-Betrieb auf false setzen → normaler Login.
    local_ui_autologin: bool = True
    # Parsing-Backend: "docling" (layout-aware, Tabellen-/OCR-treu) ist der Standard
    # der lokalen Variante; "legacy" nur als Notfall-Fallback.
    ingest_backend: str = "docling"
    # Prozess-/Installer-Rolle (M8e): "writer" = voller Knoten (Ingest+Query,
    # schreibt neue Vault-Versionen) · "reader" = schlanker Query-Knoten (liest
    # den lokalen Cache, kein Docling/torch, kein Watcher/Nachtlauf). Setzt die
    # Shell via RAG_SERVICE_ROLE aus app-settings.json / Installer-Default.
    service_role: str = Field(default="writer", validation_alias="RAG_SERVICE_ROLE")
    log_level: str = "INFO"
    rag_domain: str = "localhost"          # für CORS + Cookies

    # --- Wissensgraph L2 (Ähnlichkeits-Schwellen) ---
    graph_sim_threshold: float = 0.60
    graph_sim_top_k: int = 8
    graph_neardup_threshold: float = 0.85
    graph_shingle_size: int = 5

    # --- Wissensgraph-Retrieval (PPR-Teile entfallen mit M3) ---
    graph_retrieval_enabled: bool = True
    graph_fastpath_enabled: bool = True    # Norm-Referenz-Fastpath — BLEIBT
    graph_ppr_enabled: bool = False        # M3: PPR-Multi-Hop raus (marginal)
    graph_ppr_alpha: float = 0.15
    graph_ppr_iters: int = 50
    graph_ppr_seed_top_k: int = 8
    graph_ppr_top_docs: int = 8
    graph_content_budget: int = 20
    graph_cache_ttl: float = 30.0

    # --- Ordner-Reorg (LLM-Naming entfällt mit M5) ---
    reorg_enabled: bool = True
    reorg_min_community_docs: int = 3
    reorg_dominant_folder_ratio: float = 0.6

    @property
    def is_reader(self) -> bool:
        """Schlanker Leser-Knoten (query-only, liest lokalen Cache)."""
        return self.service_role == "reader"

    @property
    def runs_ingest_worker(self) -> bool:
        """Writer betreibt Ingest-Queue + Watcher; Reader nicht."""
        return not self.is_reader

    # --- Lokale Speicher-Pfade (abgeleitet) ---
    @property
    def app_settings_path(self) -> Path:
        """Lokale Shell-Einstellungen (Vault-Pfad + Rolle), pro Rechner, NICHT im Vault."""
        return _APPDATA / "app-settings.json"

    @property
    def ragos_dir(self) -> Path:
        """Versteckter App-Ordner im Vault (wie .obsidian/)."""
        return self.vault_path / ".ragos"

    @property
    def lancedb_uri(self) -> str:
        """LanceDB-Dataset im Vault (der EINZIGE Wissensspeicher, M3)."""
        return str(self.ragos_dir / "index.lance")

    @property
    def graph_json_path(self) -> Path:
        """Wissensgraph als flache JSON-Datei im Vault (`.ragos/graph.json`) — die
        EINZIGE Lesequelle für die Graph-Visualisierung. Der Schreiber schreibt sie
        beim (manuell ausgelösten) Rebuild; Leser lesen sie nur passiv. Bewusst im
        Vault (nicht in appstate), damit ein Leser dieselbe Datei sieht — kein Sync."""
        return self.ragos_dir / "graph.json"

    @property
    def models_dir(self) -> Path:
        """Gebackene/heruntergeladene KI-Modelle (M8d), pro Rechner."""
        return _APPDATA / "models"

    @property
    def docling_artifacts_dir(self) -> str | None:
        """Vorab gebündelte Docling-Modelle (Layout + TableFormer), vom Schreiber-
        Installer nach `models_dir/docling` gelegt. Ist der Ordner da, zeigt Docling
        über `artifacts_path` darauf → kein Runtime-Download vom HF-Hub (kein Race,
        air-gapped). Fehlt er (z.B. Reader/Dev ohne Bake), None → altes Verhalten."""
        d = self.models_dir / "docling"
        return str(d) if d.is_dir() else None

    @property
    def chunk_tokenizer_dir(self) -> str | None:
        """Vorab gebündelter e5-large-Tokenizer für den HybridChunker (token-aligned
        Chunk-Grenzen). Als lokaler Pfad an `HuggingFaceTokenizer.from_pretrained` →
        offline ohne Netz. Fehlt er, None → Chunker nutzt die Model-ID (First-Run)."""
        d = self.models_dir / "e5-tokenizer"
        return str(d) if d.is_dir() else None

    @property
    def reader_cache_uri(self) -> str:
        """Lokaler Leser-Cache des Vault-Datasets (M7). SMB ist nur Transport —
        Leser fragen NIE live über SMB, sondern gegen diese lokale Kopie."""
        return str(_APPDATA / "cache" / "index.lance")

    @property
    def appstate_db_path(self) -> Path:
        """LEGACY-DB (eine appstate mit ALLEN Tabellen). Seit dem Multi-Vault-Split
        nur noch **Migrationsquelle** (db/migrate.py) + Basis für den Log-Ordner.
        Aktiv sind stattdessen `credentials_db_path` (lokal) + `vault_db_path` (im Vault)."""
        return _APPDATA / "appstate.sqlite"

    # --- Multi-Vault: Credentials LOKAL, Content IM VAULT (pro Firma getrennt) ---
    @property
    def credentials_db_path(self) -> Path:
        """Keys + Nutzer — bleiben LOKAL pro Rechner (nie auf NAS/Vault), maschinenweit
        über alle Firmen-Vaults geteilt."""
        return _APPDATA / "credentials.sqlite"

    @property
    def credentials_db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.credentials_db_path.as_posix()}"

    @property
    def vault_db_path(self) -> Path:
        """Content (Dokumente/Chunks/Graph/Logs/Jobs) — liegt IM Vault, damit eine Firma
        = ein selbst-beschreibender, portabler Ordner ist. Der Leser liest die vom Sync
        mitgezogene Cache-Kopie (neben dem LanceDB-Cache), nie live über SMB."""
        if self.is_reader:
            return Path(self.reader_cache_uri).parent / "state.sqlite"
        return self.ragos_dir / "state.sqlite"

    @property
    def vault_db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.vault_db_path.as_posix()}"

    @property
    def ragos_config_path(self) -> Path:
        """Rollen + Norm-Muster (Vault-lokal)."""
        return self.ragos_dir / "config.json"


@lru_cache
def settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------------
# Konfig-Typen (stabile Typen für alle Module)
# ---------------------------------------------------------------------------
class ChunkingConfig(BaseModel):
    size: int = 700
    overlap: int = 80
    strategy: str = "structural"


class RetrievalConfig(BaseModel):
    top_k: int = 5
    hybrid: bool = True
    rerank: bool = False


class LimitsConfig(BaseModel):
    max_file_mb: int = 50
    max_context_chunks: int = 8


class GlobalConfig(BaseModel):
    """Globale Laufzeit-Konfiguration (aus Settings, gecached)."""
    embed_model: str
    chunking: ChunkingConfig
    retrieval: RetrievalConfig
    limits: LimitsConfig


@lru_cache
def global_config() -> GlobalConfig:
    s = settings()
    return GlobalConfig(
        embed_model=s.embed_model,
        chunking=ChunkingConfig(size=700, overlap=80, strategy="structural"),
        retrieval=RetrievalConfig(top_k=5, hybrid=True, rerank=s.rerank_enabled),
        limits=LimitsConfig(max_file_mb=50, max_context_chunks=8),
    )
