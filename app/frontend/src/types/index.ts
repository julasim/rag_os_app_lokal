export interface Document {
  id: string;
  folder_path: string;
  file_name: string;
  mime_type: string | null;
  size_bytes: number | null;
  tags: string[];
  status: 'queued' | 'processing' | 'indexed' | 'failed';
  chunk_count: number | null;
  error_msg: string | null;
  uploaded_at: string;
  indexed_at: string | null;
}

export interface ApiKey {
  id: string;
  label: string;
  scopes: string[];
  allowed_folders: string[];  // leer = Zugriff auf alle Ordner
  last_used_at: string | null;
  created_at: string;
  expires_at: string | null;
}

export interface User {
  id: string;
  email: string;
  role: string;
}

export interface HealthResponse {
  status: string;
  version: string;
  services: { postgres: boolean; qdrant: boolean; ollama: boolean };
}

export interface MetricsResponse {
  queries_last_24h: number;
  queries_last_7d: number;
  avg_latency_ms_7d: number;
  documents_indexed: number;
  documents_failed: number;
  documents_total: number;
  ingest_success_rate: number;
}

export interface BulkUploadResponse {
  job_id: string;
  total: number;
  skipped?: string[];   // "filename: Grund" — vor dem Queuing verworfene Dateien
}

export interface IngestJob {
  job_id: string;
  status: string;
  folder_path: string;
  total: number;
  processed: number;
  failed: number;
  error_msg: string | null;
  created_at: string;
  finished_at: string | null;
}

export type FolderMap = Record<string, number>;
