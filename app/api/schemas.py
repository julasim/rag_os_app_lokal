"""
Pydantic-Schemas für REST-Requests/Responses.
Zentrale Definitionen — so weiß jeder Router, wie das Wire-Format aussieht.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    # Bewusst `str`, nicht `EmailStr`: der Login vergleicht die Adresse nur
    # gegen bestehende User. EmailStr lehnt reservierte TLDs (.local/.internal/
    # .test) ab — sonst kann ein mit solcher ADMIN_EMAIL angelegter Admin sich
    # nie einloggen (interne Deployments). Fehleingaben matchen einfach kein
    # Konto → 401.
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user: "UserResponse"


class UserResponse(BaseModel):
    id: UUID
    email: str
    role: str


# ---------------------------------------------------------------------------
# API-Keys
# ---------------------------------------------------------------------------
class CreateApiKeyRequest(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    allowed_folders: list[str] = Field(default_factory=list)  # leer = alle Ordner
    scopes: list[str] = Field(default_factory=lambda: ["read"])
    expires_at: datetime | None = None


class ApiKeyResponse(BaseModel):
    id: UUID
    label: str
    allowed_folders: list[str]
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None


class ApiKeyCreatedResponse(ApiKeyResponse):
    """Wird NUR direkt nach der Erstellung zurückgegeben — enthält Klartext-Key."""
    plain_key: str


# ---------------------------------------------------------------------------
# Dokumente
# ---------------------------------------------------------------------------
class DocumentResponse(BaseModel):
    id: UUID
    folder_path: str
    file_name: str
    mime_type: str | None
    size_bytes: int | None
    tags: list[str]
    status: str
    chunk_count: int | None = None
    error_msg: str | None
    uploaded_at: datetime
    indexed_at: datetime | None
    # Strukturierte Metadaten (Phase C/D)
    doc_type: str | None = None
    norm_id: str | None = None
    doc_version: str | None = None
    issued_date: str | None = None
    issuer: str | None = None
    language: str | None = None
    valid_status: str = "unknown"
    superseded_by: UUID | None = None


class DocumentPatchRequest(BaseModel):
    folder_path: str | None = None
    tags: list[str] | None = None


# ---------------------------------------------------------------------------
# Bulk-Upload (Welle 3 — Multi-File und ZIP)
# ---------------------------------------------------------------------------
class IngestJobResponse(BaseModel):
    """Status eines asynchronen Ingest-Jobs (Bulk- oder ZIP-Upload)."""
    job_id: UUID
    status: str         # queued | running | done | failed | partial
    folder_path: str
    total: int
    processed: int
    failed: int
    error_msg: str | None
    created_at: datetime
    finished_at: datetime | None


class BulkUploadResponse(BaseModel):
    """Antwort auf einen Multi-File-/ZIP-Upload — Job ist gequeued, polle via `job_id`."""
    job_id: UUID
    total: int
    skipped: list[str] = []      # "filename: Grund" — Dateien die VOR dem Queuing verworfen wurden
    message: str = "Job in die Ingest-Queue gestellt. Status via GET /api/ingest/jobs/{job_id}."


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    version: str
    services: dict[str, bool]
    role: str = "writer"       # writer | reader — Frontend blendet z.B. den Graph-Rebuild aus
    vault_path: str = ""       # aktiver Vault-Ordner (Firma)
    vault_label: str = ""      # lesbarer Name (Ordner-Basename)


# --- Wissensgraph (Track D) — Visualisierung ---
class GraphNodeDTO(BaseModel):
    id: str                    # = node_key ("{type}:{canonical}")
    type: str                  # document | norm | legal | tag | issuer | folder
    label: str                 # lesbar (Dateiname / Wort / "§16 MRG" / Issuer)
    community: int | None
    pagerank: float
    doc_id: str | None         # nur document-Nodes → UI-Deeplink


class GraphEdgeDTO(BaseModel):
    source: str                # src_key
    target: str                # tgt_key
    relation: str              # references | supersedes | issued_by | has_tag | in_folder | similar_to | near_dup
    weight: float              # w_eff


class GraphResponse(BaseModel):
    nodes: list[GraphNodeDTO]
    edges: list[GraphEdgeDTO]
    # Nach ACL-/Typ-Filter: nodes/edges/communities = GEZEIGT (nach `limit`-Kappung),
    # total_nodes/total_edges = gesamter erlaubter Umfang, truncated = 1 wenn gekappt.
    stats: dict[str, int]


LoginResponse.model_rebuild()
