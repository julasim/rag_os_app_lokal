import { apiGet, apiPost, apiDelete } from './client';
import type { ApiKey } from '../types';

export interface CreateKeyRequest {
  label: string;
  scopes: string[]; // "read" | "write" | "delete" | "admin"
  allowed_folders: string[]; // leer = alle Ordner erlaubt
}

export interface CreateKeyResponse extends ApiKey {
  plain_key: string; // plaintext — nur einmalig direkt nach Erstellung
}

export async function listKeys(): Promise<ApiKey[]> {
  return apiGet('/api/keys');
}

export async function createKey(data: CreateKeyRequest): Promise<CreateKeyResponse> {
  return apiPost('/api/keys', data);
}

export async function revokeKey(id: string): Promise<void> {
  return apiDelete(`/api/keys/${id}`);
}
