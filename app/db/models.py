"""
SQLAlchemy ORM-Models.

Spiegelt 1:1 das Schema aus docs/ARCHITECTURE.md §3.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


class DocumentStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class Scope(str, enum.Enum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    ADMIN = "admin"  # Admin-Operationen


# ---------------------------------------------------------------------------
# UI-Benutzer (Admin-Website)
# ---------------------------------------------------------------------------
class UiUser(Base):
    __tablename__ = "ui_users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UserRole.USER.value
    )

    # --- per-User-Ordner-ACL (Track E) ---
    # access_all=True  → Vollzugriff (unrestricted); allowed_folders ignoriert.
    # access_all=False → NUR die Ordner in allowed_folders; leere Liste = NICHTS.
    # Fail-safe: Default neuer User = sieht nichts (access_all=false, [] Ordner).
    # ACHTUNG: NICHT mit der Bearer-Key-Semantik (leer=alles) vermischen —
    # der User-Pfad läuft über den getrennten access_all-Codepfad in folders.py.
    access_all: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    allowed_folders: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )

    # --- TOTP (Track E5: MCP-Admin-Write) ---
    totp_secret: Mapped[str | None] = mapped_column(Text)
    totp_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="ck_user_role"),
    )


# ---------------------------------------------------------------------------
# API-Keys (MCP/REST-Clients)
# ---------------------------------------------------------------------------
class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    key_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    # M2 (Track A0): erste 16 Zeichen des Klartext-Keys — Lookup-Index, damit
    # verify_api_key nicht ALLE Keys per bcrypt probieren muss. Nullable für
    # Bestands-Keys (Prefix aus dem bcrypt-Hash nicht rekonstruierbar).
    key_prefix: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    allowed_folders: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    scopes: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=lambda: [Scope.READ.value]
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ui_users.id", ondelete="SET NULL")
    )


# ---------------------------------------------------------------------------
# Dokumente
# ---------------------------------------------------------------------------
class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    doc_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    folder_path: Mapped[str] = mapped_column(Text, nullable=False, default="/")
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    tags: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DocumentStatus.QUEUED.value
    )
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error_msg: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ui_users.id", ondelete="SET NULL")
    )

    # --- Strukturierte Metadaten (beim Ingest via LLM extrahiert, alle optional) ---
    # Für gefilterte Suche + vertrauenswürdige Zitate im Norm-/Standard-Kontext.
    doc_type: Mapped[str | None] = mapped_column(String(64))       # Norm|Richtlinie|Anleitung|Vertrag|Protokoll|…
    norm_id: Mapped[str | None] = mapped_column(String(128))       # z.B. "ÖNORM B 1801-1"
    doc_version: Mapped[str | None] = mapped_column(String(64))    # Ausgabe/Edition, z.B. "2022-05-01"
    issued_date: Mapped[str | None] = mapped_column(String(32))    # Freitext/Jahr (keine strikte Validierung)
    issuer: Mapped[str | None] = mapped_column(String(128))        # Herausgeber, z.B. "Austrian Standards"
    language: Mapped[str | None] = mapped_column(String(16))       # ISO-Code, z.B. "de"
    valid_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown"
    )  # current | superseded | unknown
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    jobs: Mapped[list["IngestJob"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Dedup pro ORDNER, nicht global: identischer Inhalt darf in mehreren
        # Ordnern liegen (z.B. dieselbe ÖNORM-PDF in mehreren Projektordnern).
        # Früher war der Constraint auf doc_hash allein → ein Zweitupment in
        # einen anderen Ordner wurde still verworfen. Migration in init_db.
        UniqueConstraint("folder_path", "doc_hash", name="uq_folder_doc_hash"),
        Index("idx_documents_folder", "folder_path"),
        Index("idx_documents_status", "status"),
        # GIN für Tags wird in init_db via SQL nachgezogen
    )


# ---------------------------------------------------------------------------
# Kanonische Chunk-Schicht (Track C2b)
#
# Postgres = Source-of-Truth für Chunks; Qdrant ist der ABGELEITETE Vektor-Index.
# `chunk_id` ist INHALTSBASIERT und meta-unabhängig → stabil bei Ordner-Moves
# (nur Payload ändert sich, ID bricht nicht). Quelle für die Graph-Kanten
# (Track D: references-Regex über `text`, section_path).
#
# **Composite-PK `(doc_id, chunk_id)`** (M0.1): der Docling-Adapter erzeugt einen
# rein inhaltsbasierten `chunk_id`; identischer Inhalt in ZWEI Ordnern/Dokumenten
# (per `uq_folder_doc_hash` bewusst erlaubt) ergäbe sonst denselben chunk_id →
# PK-Kollision → 2. Ingest scheiterte. Der doc_id-Anteil macht die Zeile eindeutig;
# `parent_id/prev_id/next_id` sind doc-intern und bleiben gültig (kein FK).
# ---------------------------------------------------------------------------
class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    level: Mapped[str] = mapped_column(String(8), nullable=False, default="child")  # child|parent
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_id: Mapped[str | None] = mapped_column(String(64))
    prev_id: Mapped[str | None] = mapped_column(String(64))
    next_id: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    section_path: Mapped[str | None] = mapped_column(Text)
    element_types: Mapped[list[str] | None] = mapped_column(JSON)
    table_html: Mapped[str | None] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    folder_path: Mapped[str] = mapped_column(Text, nullable=False, default="/")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_chunks_doc", "doc_id"),
        Index("idx_chunks_folder", "folder_path"),
    )


# ---------------------------------------------------------------------------
# Ingest-Jobs (Verarbeitungs-Log)
# ---------------------------------------------------------------------------
class IngestJob(Base):
    __tablename__ = "ingest_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_msg: Mapped[str | None] = mapped_column(Text)

    document: Mapped[Document] = relationship(back_populates="jobs")


# ---------------------------------------------------------------------------
# Ingest-Queue (Welle 3 — Bulk-Uploads + ZIP)
#
# Eine Row pro pending File. Bulk-Uploads (Multi-File + ZIP) gruppieren ihre
# Rows über `job_id`. Single-File-Uploads gehen weiterhin synchron und nutzen
# die Queue NICHT — sie tauchen hier daher nie auf.
# ---------------------------------------------------------------------------
class IngestQueueEntry(Base):
    __tablename__ = "ingest_queue"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    folder_path: Mapped[str] = mapped_column(Text, nullable=False, default="/")
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued"
    )  # queued | running | done | failed
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_msg: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ui_users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        Index("idx_ingest_queue_pickup", "status", "created_at"),
        Index("idx_ingest_queue_job", "job_id"),
    )


# ---------------------------------------------------------------------------
# Maintenance-Log (Welle 8 — Audit-Trail für automatische Wartungs-Aktionen)
# ---------------------------------------------------------------------------
class MaintenanceLog(Base):
    __tablename__ = "maintenance_log"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    undo_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    undo_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("idx_maintenance_log_created", "created_at"),
    )


# ---------------------------------------------------------------------------
# Duplikat-Vorschläge (Welle 8 — hochrisiko, manuell bestätigen)
# ---------------------------------------------------------------------------
class DuplicateSuggestion(Base):
    __tablename__ = "duplicate_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    doc_id_keep: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    doc_id_remove: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    doc_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(50), nullable=False, default="exact_hash")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_dup_suggestions_status", "status"),
        UniqueConstraint("doc_id_keep", "doc_id_remove", name="uq_dup_pair"),
    )


# ---------------------------------------------------------------------------
# Ordner-Reorg-Vorschläge (Track F / M4 — hochrisiko, manuell bestätigen)
#
# Gruppierung ist DETERMINISTISCH aus den D-Communities (`graph_nodes.community_id`);
# das LLM benennt nur den Zielordner. Jede Zeile = ein Dokument, das aus
# `current_folder` nach `suggested_folder` wandern soll. `current_folder` ist
# zugleich die Undo-Information (Accept verschiebt hin, Undo zurück). Bewegen
# ausschließlich über die atomare `move_document()` (M0.2) — kein Auto-Löschen.
# ---------------------------------------------------------------------------
class FolderSuggestion(Base):
    __tablename__ = "folder_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    current_folder: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_folder: Mapped[str] = mapped_column(Text, nullable=False)
    community_id: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_folder_suggestions_status", "status"),
        Index("idx_folder_suggestions_doc", "doc_id"),
    )


# ---------------------------------------------------------------------------
# Query-Log (Beobachtbarkeit)
# ---------------------------------------------------------------------------
class QueryLog(Base):
    __tablename__ = "query_log"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("api_keys.id", ondelete="SET NULL")
    )
    # OAuth-/UI-User-Attribution (Track E): bei OAuth-Principals ist api_key_id
    # None (id=None-Duck-Typing) → user_id trägt die Identität für Audit/Budget.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ui_users.id", ondelete="SET NULL")
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    # SQLite: als JSON-Liste von doc_id-STRINGS (query.py stringifiziert vor dem Write).
    retrieved_doc_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# OAuth (MCP-Connector) — Clients (DCR) + Refresh-Tokens
#
# Ersetzt die frühere SQLite-DB unter /data/mcp-oauth.db (lag auf einem NICHT
# gemounteten Pfad → nach jedem Redeploy weg). Jetzt in Postgres: überlebt
# Redeploys, im pg_dump-Backup. Auth-Codes bleiben kurzlebig in-memory.
# ---------------------------------------------------------------------------
class OAuthClient(Base):
    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    redirect_uris: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    token_endpoint_auth_method: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthRefreshToken(Base):
    __tablename__ = "oauth_refresh_tokens"

    token_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(64), nullable=False)  # UiUser-UUID als Text
    scope: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    replaced_by: Mapped[str | None] = mapped_column(String(96))

    __table_args__ = (
        Index("idx_oauth_refresh_subject", "subject"),
        Index("idx_oauth_refresh_expires", "expires_at"),
    )


# ---------------------------------------------------------------------------
# Wissensgraph (Track D / M3)
#
# Der Graph verbindet Dokumente mit den Entitäten, die sie referenzieren
# (Normen, Rechtsverweise, Herausgeber, Tags, Ordner). Node-Identität ist ein
# String-PK `node_key = f"{node_type}:{canonical_key}"` — macht den späteren
# In-RAM-Load (networkx-Knoten = Strings) und die Kanten-Referenzen trivial,
# ohne Surrogat-Join. `canonical_key` kommt AUSSCHLIESSLICH aus
# app/graph/canonical.py (identische Normalisierung überall, sonst Ghost Nodes).
#
# Schichten: L1 = deterministisch (Regex + kanonische Normalisierung, references/
# supersedes/issued_by/has_tag/in_folder). L2 (später) = Ähnlichkeit (kNN/near_dup).
#
# `doc_id` an einem document-Node trägt die ACL-Herkunft — die Graph-Sicherheit
# (near_dup-Sichtbarkeit über Schnittmenge mit Caller-ACL) hängt sich später
# genau daran. Deshalb schon jetzt im Schema, auch wenn erst später erzwungen.
# ---------------------------------------------------------------------------
class GraphNode(Base):
    __tablename__ = "graph_nodes"

    node_key: Mapped[str] = mapped_column(Text, primary_key=True)  # "{node_type}:{canonical_key}"
    node_type: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # document | norm | legal | tag | folder | issuer
    canonical_key: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Bei document-Nodes: der eine Ordner des Docs. Bei Entity-Nodes: alle Ordner,
    # in denen referenzierende Docs liegen (akkumuliert) → Basis der Graph-ACL.
    folder_paths: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    # Nur bei node_type='document' gesetzt (ACL-Herkunft, Track-D-Sicherheit).
    doc_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE")
    )
    pagerank: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    community_id: Mapped[int | None] = mapped_column(Integer)
    participation: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_graph_nodes_type", "node_type"),
        Index("idx_graph_nodes_canonical", "canonical_key"),
        Index("idx_graph_nodes_community", "community_id"),
        # GIN auf folder_paths wird in init_db via SQL nachgezogen (ACL-Containment).
    )


class GraphEdge(Base):
    __tablename__ = "graph_edges"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    src_key: Mapped[str] = mapped_column(Text, nullable=False)
    tgt_key: Mapped[str] = mapped_column(Text, nullable=False)
    relation: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # references | supersedes | issued_by | has_tag | in_folder
    layer: Mapped[str] = mapped_column(String(4), nullable=False, default="L1")  # L1 | L2
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    w_eff: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("src_key", "tgt_key", "relation", name="uq_graph_edge"),
        Index("idx_graph_edges_src", "src_key"),
        Index("idx_graph_edges_tgt", "tgt_key"),
        Index("idx_graph_edges_layer", "layer"),
    )


class GraphCommunity(Base):
    __tablename__ = "graph_communities"

    community_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str | None] = mapped_column(Text)
    conductance: Mapped[float | None] = mapped_column(Float)
    member_fingerprint: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
