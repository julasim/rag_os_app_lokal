export function getToken(): string | null {
  return localStorage.getItem('rag_token');
}
export function setToken(token: string): void {
  localStorage.setItem('rag_token', token);
}
export function clearToken(): void {
  localStorage.removeItem('rag_token');
}

function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { 'X-UI-Token': t } : {};
}
function jsonHeaders(): Record<string, string> {
  return { 'Content-Type': 'application/json', ...authHeaders() };
}
function handleUnauth(res: Response) {
  if (res.status === 401) {
    clearToken();
    window.location.href = '/login';
  }
}

export async function apiGet<T>(
  path: string,
  params?: Record<string, string | number | boolean | undefined>
): Promise<T> {
  const url = new URL(path, window.location.origin);
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v != null && v !== '') url.searchParams.set(k, String(v));
    });
  }
  const res = await fetch(url.toString(), { headers: authHeaders() });
  handleUnauth(res);
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

export async function apiGetWithHeaders<T>(
  path: string,
  params?: Record<string, string | number | undefined>
): Promise<{ data: T; headers: Headers }> {
  const url = new URL(path, window.location.origin);
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v != null && v !== '') url.searchParams.set(k, String(v));
    });
  }
  const res = await fetch(url.toString(), { headers: authHeaders() });
  handleUnauth(res);
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return { data: await res.json(), headers: res.headers };
}

export async function apiGetRaw(
  path: string,
  params?: Record<string, string>
): Promise<Response> {
  const url = new URL(path, window.location.origin);
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  }
  return fetch(url.toString(), { headers: authHeaders() });
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: jsonHeaders(),
    body: body != null ? JSON.stringify(body) : undefined,
  });
  handleUnauth(res);
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

export async function apiPostForm<T>(path: string, formData: FormData): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: authHeaders(),
    body: formData,
  });
  handleUnauth(res);
  if (!res.ok) {
    const text = await res.text();
    // FastAPI-Fehler lesbar machen: {"detail":"..."} oder {"detail":[{"msg":"..."}]}
    let msg = `${res.status}: ${text}`;
    try {
      const json = JSON.parse(text);
      if (Array.isArray(json?.detail)) {
        msg = json.detail
          .map((d: { msg?: string; loc?: string[] }) =>
            d.loc ? `${d.loc.slice(-1)[0]}: ${d.msg}` : (d.msg ?? ''),
          )
          .join(', ');
      } else if (typeof json?.detail === 'string') {
        msg = json.detail;
      }
    } catch {
      // Kein JSON (z.B. plain-text 500 von uvicorn/proxy) — rohen Text anzeigen
    }
    throw new Error(msg);
  }
  return res.json();
}

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'PATCH',
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  handleUnauth(res);
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

export async function apiDelete(path: string): Promise<void> {
  const res = await fetch(path, { method: 'DELETE', headers: authHeaders() });
  handleUnauth(res);
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
}
