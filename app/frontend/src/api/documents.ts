import { apiGet, apiGetWithHeaders, apiGetRaw, apiPost, apiPatch, apiPostForm, apiDelete } from './client';
import type { Document, BulkUploadResponse, IngestJob, FolderMap } from '../types';

export interface DocumentChunk {
  id: string;
  content: string;
  page: number | null;
  section_title: string | null;
}

export interface DocumentListParams {
  folder_prefix?: string;
  status_filter?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

export async function listDocuments(
  params: DocumentListParams,
): Promise<{ docs: Document[]; total: number }> {
  const { data, headers } = await apiGetWithHeaders<Document[]>(
    '/api/documents',
    params as Record<string, string | number>,
  );
  const total = parseInt(headers.get('X-Total-Count') || String(data.length), 10);
  return { docs: data, total };
}

export async function listFolders(): Promise<FolderMap> {
  return apiGet('/api/documents/folders');
}

export async function getDocument(id: string): Promise<Document> {
  return apiGet(`/api/documents/${id}`);
}

export async function deleteDocument(id: string): Promise<void> {
  return apiDelete(`/api/documents/${id}`);
}

export async function deleteFolder(folderPath: string): Promise<void> {
  return apiDelete(`/api/documents/folder?folder_path=${encodeURIComponent(folderPath)}`);
}

export async function downloadDocument(id: string, format?: 'pdf'): Promise<Response> {
  return apiGetRaw(`/api/documents/${id}/download`, format ? { format } : undefined);
}

export async function exportDocuments(
  ids: string[],
  format: 'original' | 'pdf' = 'original',
): Promise<Response> {
  const token = (await import('./client')).getToken();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers['X-UI-Token'] = token;
  return fetch('/api/documents/export', {
    method: 'POST',
    headers,
    body: JSON.stringify({ ids, format }),
  });
}

// Track C3b: Upload ist immer async (1..n Dateien → Queue). Der Server
// antwortet mit 202 + BulkUploadResponse (job_id); der frühere synchrone
// Single-File-Pfad (DocumentResponse) existiert nicht mehr.
export async function uploadFiles(
  files: File[],
  folderPath: string,
  tags: string[],
): Promise<BulkUploadResponse> {
  const form = new FormData();
  files.forEach((f) => form.append('files', f));
  form.append('folder_path', folderPath);
  form.append('tags', tags.join(','));
  return apiPostForm('/api/documents', form);
}

export async function uploadZip(
  file: File,
  folderPath: string,
  tags: string[],
): Promise<BulkUploadResponse> {
  const form = new FormData();
  form.append('file', file);
  form.append('folder_path', folderPath);
  form.append('tags', tags.join(','));
  return apiPostForm('/api/documents/zip', form);
}

export async function getJobStatus(jobId: string): Promise<IngestJob> {
  return apiGet(`/api/documents/jobs/${jobId}`);
}

export async function patchDocument(
  id: string,
  data: { tags?: string[]; folder_path?: string },
): Promise<Document> {
  return apiPatch(`/api/documents/${id}`, data);
}

export async function reindexDocument(id: string): Promise<Document> {
  return apiPost(`/api/documents/${id}/reindex`);
}

export async function getDocumentChunks(id: string): Promise<DocumentChunk[]> {
  return apiGet(`/api/documents/${id}/chunks`);
}
