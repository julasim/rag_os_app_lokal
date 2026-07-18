import { apiPost } from './client';
import type { User } from '../types';

export async function login(
  email: string,
  password: string
): Promise<{ token: string; user: User }> {
  return apiPost('/api/auth/login', { email, password });
}

export async function logout(): Promise<void> {
  try {
    await apiPost('/api/auth/logout');
  } catch {
    /* ignore */
  }
}
