import { apiGet, apiPost } from './client';

export interface MaintenanceLogEntry {
  id: string;
  created_at: string;
  expires_at: string;
  action_type: string;
  summary: string;
  undo_applied: boolean;
}

export interface DuplicateSuggestion {
  id: string;
  created_at: string;
  doc_id_keep: string;
  doc_id_remove: string;
  doc_hash: string;
  reason: string;
  status: string;
}

export interface FolderSuggestion {
  id: string;
  created_at: string;
  doc_id: string;
  current_folder: string;
  suggested_folder: string;
  community_id: number | null;
  reason: string;
  status: string;
}

export interface MaintenanceRunResult {
  started_at: string;
  tag_merges: number;
  new_duplicate_suggestions: number;
}

export const getMaintenanceLog = (): Promise<MaintenanceLogEntry[]> =>
  apiGet('/api/maintenance/log');

export const getDuplicateSuggestions = (): Promise<DuplicateSuggestion[]> =>
  apiGet('/api/maintenance/suggestions/duplicates');

export const acceptDuplicate = (id: string): Promise<{ accepted: boolean; removed_doc_id: string }> =>
  apiPost(`/api/maintenance/suggestions/duplicates/${id}/accept`);

export const rejectDuplicate = (id: string): Promise<{ rejected: boolean }> =>
  apiPost(`/api/maintenance/suggestions/duplicates/${id}/reject`);

export const undoMaintenanceAction = (id: string): Promise<{ undone: boolean }> =>
  apiPost(`/api/maintenance/log/${id}/undo`);

export const runMaintenance = (): Promise<MaintenanceRunResult> =>
  apiPost('/api/maintenance/run');

// --- Ordner-Reorg-Vorschläge (Track F / M4) ---
export const getFolderSuggestions = (): Promise<FolderSuggestion[]> =>
  apiGet('/api/maintenance/suggestions/folders');

export const acceptFolderSuggestion = (
  id: string,
): Promise<{ accepted: boolean; doc_id: string; moved_to: string }> =>
  apiPost(`/api/maintenance/suggestions/folders/${id}/accept`);

export const rejectFolderSuggestion = (id: string): Promise<{ rejected: boolean }> =>
  apiPost(`/api/maintenance/suggestions/folders/${id}/reject`);

export const rebuildFolderSuggestions = (): Promise<{
  communities_considered: number;
  suggestions: number;
}> => apiPost('/api/maintenance/reorg/rebuild');
