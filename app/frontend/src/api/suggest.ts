import { apiPost, apiPostForm } from './client';

export interface SuggestionItem {
  doc_id: string | null;
  filename: string;
  current_folder: string;
  suggested_folder: string;
  reason: string;
}

export interface SuggestResponse {
  suggestions: SuggestionItem[];
  temp_id: string | null;
}

export interface ApplyResult {
  moved: number;
  errors: string[];
}

export interface ApplyZipResult {
  job_id: string;
  total: number;
}

/** Schlägt Ordner für bereits indexierte Dokumente vor. */
export function suggestFromDocs(doc_ids: string[]): Promise<SuggestResponse> {
  return apiPost('/api/suggest/from-docs', { doc_ids });
}

/** Analysiert ZIP vor dem Ingest und schlägt Ordnerstruktur vor. */
export async function suggestFromZip(file: File): Promise<SuggestResponse> {
  const form = new FormData();
  form.append('file', file);
  return apiPostForm('/api/suggest/from-zip', form);
}

/** Wendet bestätigte Vorschläge auf bestehende Dokumente an (Verschieben). */
export function applySuggestions(suggestions: SuggestionItem[]): Promise<ApplyResult> {
  return apiPost('/api/suggest/apply', { suggestions });
}

/** Ingestiert analysiertes ZIP mit bestätigten Ordnern. */
export function applyZipSuggestions(
  temp_id: string,
  suggestions: SuggestionItem[],
): Promise<ApplyZipResult> {
  return apiPost('/api/suggest/apply-zip', { temp_id, suggestions });
}
