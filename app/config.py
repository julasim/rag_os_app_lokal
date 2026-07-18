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

from pydantic import BaseModel
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
    # Der Vault (Docs + LanceDB-Index) ist portabel; Default lokal, per Env auf
    # die NAS zeigbar (RAG_VAULT_PATH). appstate.sqlite bleibt IMMER lokal.
    vault_path: Path = _DEFAULT_VAULT

    # --- Postgres (entfällt in M2 → SQLite; Default nur fürs Booten) ---
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "rag"
    postgres_user: str = "rag"
    postgres_password: str = ""

    # --- Qdrant (entfällt in M3 → LanceDB; Default nur fürs Booten) ---
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    qdrant_api_key: str = ""

    # --- Embeddings (M4: fastembed/ONNX, kein Ollama mehr) ---
    ollama_host: str = "http://127.0.0.1:11434"   # entfällt mit M4/M5
    embed_model: str = "BAAI/bge-m3"              # fastembed-Modell-ID (ONNX)
    llm_model: str = "qwen2.5:3b-instruct"        # entfällt in M5 (kein LLM)

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
    # Prozess-Rolle: lokal immer "all" (ein Prozess). "ingest"/"api" waren der
    # Docker-Worker-Split (entfällt mit M6 → In-Process-Task).
    service_role: str = "all"
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
    def runs_ingest_worker(self) -> bool:
        return self.service_role == "all"

    # --- Lokale Speicher-Pfade (abgeleitet) ---
    @property
    def ragos_dir(self) -> Path:
        """Versteckter App-Ordner im Vault (wie .obsidian/)."""
        return self.vault_path / ".ragos"

    @property
    def lancedb_uri(self) -> str:
        """LanceDB-Dataset im Vault (der EINZIGE Wissensspeicher, M3)."""
        return str(self.ragos_dir / "index.lance")

    @property
    def reader_cache_uri(self) -> str:
        """Lokaler Leser-Cache des Vault-Datasets (M7). SMB ist nur Transport —
        Leser fragen NIE live über SMB, sondern gegen diese lokale Kopie."""
        return str(_APPDATA / "cache" / "index.lance")

    @property
    def appstate_db_path(self) -> Path:
        """Lokale App-DB (Keys/Users/Query-Log), NIE im Vault (M2)."""
        return _APPDATA / "appstate.sqlite"

    @property
    def appstate_db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.appstate_db_path.as_posix()}"

    @property
    def ragos_config_path(self) -> Path:
        """Rollen + Norm-Muster (Vault-lokal)."""
        return self.ragos_dir / "config.json"

    # --- Legacy-Abgeleitete (entfallen mit M2/M3, bis dahin fürs Booten da) ---
    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def qdrant_url(self) -> str:
        return f"http://{self.qdrant_host}:{self.qdrant_port}"


@lru_cache
def settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------------
# Konfig-Typen (stabile Typen für alle Module)
# ---------------------------------------------------------------------------
class LLMConfig(BaseModel):
    provider: str = "ollama"
    model: str


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
    llm: LLMConfig
    chunking: ChunkingConfig
    retrieval: RetrievalConfig
    limits: LimitsConfig


@lru_cache
def global_config() -> GlobalConfig:
    s = settings()
    return GlobalConfig(
        embed_model=s.embed_model,
        llm=LLMConfig(provider="ollama", model=s.llm_model),
        chunking=ChunkingConfig(size=700, overlap=80, strategy="structural"),
        retrieval=RetrievalConfig(top_k=5, hybrid=True, rerank=s.rerank_enabled),
        limits=LimitsConfig(max_file_mb=50, max_context_chunks=8),
    )
