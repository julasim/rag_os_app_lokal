import { apiGet, apiPost, apiPatch, apiDelete } from './client';

export interface AdminUser {
  id: string;
  email: string;
  role: string; // "admin" | "user"
  access_all: boolean;
  allowed_folders: string[];
  totp_enabled: boolean;
  created_at: string;
  last_login: string | null;
}

export interface CreateUserRequest {
  email: string;
  password: string;
  role: string;
  access_all: boolean;
  allowed_folders: string[];
}

export interface UpdateUserRequest {
  password?: string;
  role?: string;
  access_all?: boolean;
  allowed_folders?: string[];
}

export async function listUsers(): Promise<AdminUser[]> {
  return apiGet('/api/users');
}

export async function createUser(data: CreateUserRequest): Promise<AdminUser> {
  return apiPost('/api/users', data);
}

export async function updateUser(id: string, data: UpdateUserRequest): Promise<AdminUser> {
  return apiPatch(`/api/users/${id}`, data);
}

export async function deleteUser(id: string): Promise<void> {
  return apiDelete(`/api/users/${id}`);
}
