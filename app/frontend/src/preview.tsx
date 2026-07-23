/**
 * Frontend-Preview-Harness (Design-/UI-Vorschau OHNE Backend).
 *
 * Rendert die ECHTEN Seiten-Komponenten (kein Mockup-Duplikat → kein Drift) mit
 * einem in-memory Mock-Backend, das `/api/*` abfängt. Anlegen/Ändern/Löschen
 * wirken live (react-query refetcht gegen den Mock-Store).
 *
 * Nutzung:  cd app/frontend && npm run dev  →  http://localhost:5173/preview.html
 *           ?page=users (default) | ?page=keys
 *
 * Neue Seite ergänzen: unten in PAGES eintragen + ggf. Mock-Route ergänzen.
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider } from './hooks/useAuth';
import Sidebar from './components/layout/Sidebar';
import Users from './pages/Users';
import Keys from './pages/Keys';
import './index.css';

// ─── Mock-Store ───────────────────────────────────────────────────────────────
type MockUser = {
  id: string;
  email: string;
  role: string;
  access_all: boolean;
  allowed_folders: string[];
  created_at: string;
  last_login: string | null;
};

const now = new Date().toISOString();
const users: MockUser[] = [
  { id: 'u-admin', email: 'julius@sima.or.at', role: 'admin', access_all: true, allowed_folders: [], created_at: now, last_login: now },
  { id: 'u-steuer', email: 'steuerberater@extern.at', role: 'user', access_all: false, allowed_folders: ['/Steuer/'], created_at: now, last_login: new Date(Date.now() - 3600_000).toISOString() },
  { id: 'u-bau', email: 'bauleiter@firma.at', role: 'user', access_all: false, allowed_folders: ['/Bau/', '/Angebote/'], created_at: now, last_login: null },
  { id: 'u-neu', email: 'neuer@firma.at', role: 'user', access_all: false, allowed_folders: [], created_at: now, last_login: null },
];

const folders: Record<string, number> = {
  '/Steuer/': 12, '/Bau/': 8, '/Angebote/': 5, '/Vertraege/': 3, '/Normen/': 21,
};

const keys = [
  { id: 'k-langdock-1234', label: 'Langdock Integration', allowed_folders: [], scopes: ['read'], created_at: now, last_used_at: now, expires_at: null },
];

// ─── Fetch-Interception ───────────────────────────────────────────────────────
const realFetch = window.fetch.bind(window);
const jsonRes = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
  const path = url.replace(window.location.origin, '');
  const method = (init?.method ?? 'GET').toUpperCase();
  const body = init?.body ? JSON.parse(init.body as string) : {};

  const canon = (fs: string[]) =>
    Array.from(new Set(fs.map((f) => `/${f}/`.replace(/\/+/g, '/')))).sort();

  // --- Users ---
  if (path.startsWith('/api/users')) {
    const id = path.split('/api/users/')[1];
    if (method === 'GET') return jsonRes(users);
    if (method === 'POST') {
      const u: MockUser = {
        id: `u-${Math.random().toString(36).slice(2, 8)}`,
        email: (body.email ?? '').toLowerCase(),
        role: body.role ?? 'user',
        access_all: !!body.access_all,
        allowed_folders: body.access_all ? [] : canon(body.allowed_folders ?? []),
        created_at: new Date().toISOString(),
        last_login: null,
      };
      users.push(u);
      return jsonRes(u, 201);
    }
    if (method === 'PATCH' && id) {
      const u = users.find((x) => x.id === id)!;
      if (body.role !== undefined) u.role = body.role;
      if (body.access_all !== undefined) u.access_all = body.access_all;
      if (body.allowed_folders !== undefined || body.access_all !== undefined) {
        u.allowed_folders = u.access_all ? [] : canon(body.allowed_folders ?? u.allowed_folders);
      }
      return jsonRes(u);
    }
    if (method === 'DELETE' && id) {
      const i = users.findIndex((x) => x.id === id);
      if (i >= 0) users.splice(i, 1);
      return new Response(null, { status: 204 });
    }
  }
  // --- Folders / Keys / Health ---
  if (path.startsWith('/api/documents/folders')) return jsonRes(folders);
  if (path.startsWith('/api/keys')) {
    if (method === 'GET') return jsonRes(keys);
    if (method === 'DELETE') return new Response(null, { status: 204 });
    if (method === 'POST') return jsonRes({ ...keys[0], id: 'k-neu', plain_key: 'rag_demo_key_xxxxxxxx' }, 201);
  }
  if (path.startsWith('/api/health')) return jsonRes({ status: 'ok', version: 'preview', services: {} });

  return realFetch(input, init);
};

// Als eingeloggter Admin präsentieren
localStorage.setItem('rag_token', 'preview');
localStorage.setItem('rag_user', JSON.stringify({ id: 'u-admin', email: 'julius@sima.or.at', role: 'admin' }));

// ─── Render ───────────────────────────────────────────────────────────────────
const PAGES: Record<string, { title: string; el: React.ReactElement }> = {
  users: { title: 'Nutzer', el: <Users /> },
  keys: { title: 'API-Keys', el: <Keys /> },
};
const which = new URLSearchParams(window.location.search).get('page') ?? 'users';
const page = PAGES[which] ?? PAGES.users;

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <BrowserRouter>
          <div style={{ display: 'flex', height: '100vh', background: '#fafafa', fontFamily: 'Arimo, "Helvetica Neue", Helvetica, Arial, sans-serif' }}>
            <Sidebar />
            <div style={{ flex: 1, overflow: 'auto' }}>
              <div style={{ borderBottom: '1px solid #ededed', background: '#fff', padding: '16px 28px' }}>
                <div style={{ fontSize: 16, fontWeight: 600, color: '#111' }}>{page.title}</div>
                <div style={{ fontSize: 12, color: '#a3a3a3', marginTop: 2 }}>
                  UI-Preview (Mock-Backend, kein Server) · ?page=users|keys
                </div>
              </div>
              <div style={{ padding: '24px 28px' }}>{page.el}</div>
            </div>
          </div>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
