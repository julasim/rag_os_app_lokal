import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listUsers,
  createUser,
  updateUser,
  deleteUser,
  type AdminUser,
} from '../api/users';
import { listFolders } from '../api/documents';
import { useAuth } from '../hooks/useAuth';

// ─── helpers ────────────────────────────────────────────────────────────────

function formatRelative(iso: string | null): string {
  if (!iso) return 'nie';
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1) return 'gerade eben';
  if (min < 60) return `vor ${min} Min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `vor ${h} Std`;
  return `vor ${Math.floor(h / 24)} Tagen`;
}

function RoleTag({ role }: { role: string }) {
  const admin = role === 'admin';
  return (
    <span
      style={{
        padding: '2px 6px',
        background: admin ? '#faf5ff' : '#f0fdf4',
        border: `1px solid ${admin ? '#e9d5ff' : '#bbf7d0'}`,
        borderRadius: 4,
        fontSize: 11,
        color: admin ? '#6b21a8' : '#166534',
        fontWeight: 500,
      }}
    >
      {admin ? 'Admin' : 'Nutzer'}
    </span>
  );
}

// ─── User-Formular (Anlegen + Bearbeiten) ─────────────────────────────────────

interface UserFormProps {
  existing?: AdminUser;
  onClose: () => void;
  onSaved: () => void;
}

function UserForm({ existing, onClose, onSaved }: UserFormProps) {
  const isEdit = !!existing;
  const [email, setEmail] = useState(existing?.email ?? '');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState(existing?.role ?? 'user');
  const [accessAll, setAccessAll] = useState(existing?.access_all ?? false);
  const [folders, setFolders] = useState<string[]>(existing?.allowed_folders ?? []);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();

  const { data: folderMap = {} } = useQuery<Record<string, number>>({
    queryKey: ['folders'],
    queryFn: listFolders,
    staleTime: 30_000,
  });
  const allFolders = Object.keys(folderMap).sort();

  const mutation = useMutation({
    mutationFn: async () => {
      if (isEdit) {
        return updateUser(existing!.id, {
          role,
          access_all: accessAll,
          allowed_folders: accessAll ? [] : folders,
          ...(password ? { password } : {}),
        });
      }
      return createUser({
        email,
        password,
        role,
        access_all: accessAll,
        allowed_folders: accessAll ? [] : folders,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] });
      onSaved();
    },
    onError: (err: Error) => setError(err.message),
  });

  function toggleFolder(f: string) {
    setFolders((prev) => (prev.includes(f) ? prev.filter((x) => x !== f) : [...prev, f]));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!isEdit && !email.trim()) return setError('E-Mail ist erforderlich.');
    if (!isEdit && password.length < 8) return setError('Passwort muss mind. 8 Zeichen haben.');
    if (isEdit && password && password.length < 8)
      return setError('Neues Passwort muss mind. 8 Zeichen haben.');
    if (!accessAll && folders.length === 0)
      return setError('Bei „Bestimmte Ordner" mindestens einen Ordner wählen (sonst sieht der Nutzer nichts).');
    setError(null);
    mutation.mutate();
  }

  const labelStyle: React.CSSProperties = {
    display: 'block',
    fontSize: 12,
    fontWeight: 500,
    color: '#262626',
    marginBottom: 4,
  };

  return (
    <div
      style={{
        background: '#fff',
        border: '1px solid #ededed',
        borderRadius: 8,
        padding: 16,
        marginBottom: 16,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 14,
        }}
      >
        <div style={{ fontSize: 14, fontWeight: 600, color: '#111' }}>
          {isEdit ? `Nutzer bearbeiten – ${existing!.email}` : 'Neuer Nutzer'}
        </div>
        <button
          onClick={onClose}
          style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 18, color: '#a3a3a3', lineHeight: 1 }}
        >
          ×
        </button>
      </div>

      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {!isEdit && (
          <div>
            <label style={labelStyle}>
              E-Mail <span style={{ color: '#991b1b' }}>*</span>
            </label>
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="mitarbeiter@firma.at"
              className="w-full px-2.5 py-1.5 border border-[#ededed] rounded-md bg-white text-sm text-[#111] outline-none focus:border-[#111]"
              style={{ display: 'block', width: '100%', boxSizing: 'border-box' }}
            />
          </div>
        )}

        <div>
          <label style={labelStyle}>
            {isEdit ? 'Neues Passwort (leer = unverändert)' : 'Passwort *'}
          </label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={isEdit ? '••••••••' : 'mind. 8 Zeichen'}
            className="w-full px-2.5 py-1.5 border border-[#ededed] rounded-md bg-white text-sm text-[#111] outline-none focus:border-[#111]"
            style={{ display: 'block', width: '100%', boxSizing: 'border-box' }}
          />
        </div>

        {/* Rolle */}
        <div>
          <label style={labelStyle}>Rolle</label>
          <div style={{ display: 'flex', gap: 16 }}>
            {[
              { v: 'user', l: 'Nutzer (nur lesen, über MCP)' },
              { v: 'admin', l: 'Admin (Web-UI, volle Rechte)' },
            ].map((o) => (
              <label key={o.v} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#525252', cursor: 'pointer' }}>
                <input type="radio" name="role" checked={role === o.v} onChange={() => setRole(o.v)} />
                {o.l}
              </label>
            ))}
          </div>
        </div>

        {/* Ordner-Zugriff */}
        <div>
          <label style={labelStyle}>Ordner-Zugriff</label>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#525252', cursor: 'pointer' }}>
              <input type="radio" name="access" checked={accessAll} onChange={() => setAccessAll(true)} />
              Zugriff auf alles
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#525252', cursor: 'pointer' }}>
              <input type="radio" name="access" checked={!accessAll} onChange={() => setAccessAll(false)} />
              Bestimmte Ordner
            </label>
          </div>

          {!accessAll && (
            <div
              style={{
                marginTop: 8,
                border: '1px solid #ededed',
                borderRadius: 6,
                padding: 8,
                maxHeight: 180,
                overflowY: 'auto',
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
              }}
            >
              {allFolders.length === 0 && (
                <div style={{ fontSize: 12, color: '#a3a3a3' }}>Noch keine Ordner vorhanden.</div>
              )}
              {allFolders.map((f) => (
                <label key={f} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#525252', cursor: 'pointer' }}>
                  <input type="checkbox" checked={folders.includes(f)} onChange={() => toggleFolder(f)} />
                  {f}
                </label>
              ))}
            </div>
          )}
        </div>

        {error && (
          <div className="text-[#991b1b] text-sm p-2" style={{ background: '#fef2f2', borderRadius: 6 }}>
            {error}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 bg-white text-[#262626] text-xs font-medium rounded-md border border-[#ededed] hover:border-[#d4d4d4]"
          >
            Abbrechen
          </button>
          <button
            type="submit"
            disabled={mutation.isPending}
            className="px-3 py-1.5 bg-[#111] text-white text-xs font-medium rounded-md border border-[#111] hover:bg-[#262626]"
            style={{ opacity: mutation.isPending ? 0.7 : 1 }}
          >
            {mutation.isPending ? 'Speichere…' : isEdit ? 'Speichern' : 'Nutzer anlegen'}
          </button>
        </div>
      </form>
    </div>
  );
}

// ─── Lösch-Bestätigung ────────────────────────────────────────────────────────

function DeleteConfirm({ user, onConfirm, onCancel }: { user: AdminUser; onConfirm: () => void; onCancel: () => void }) {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.25)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 100,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div style={{ background: '#fff', border: '1px solid #ededed', borderRadius: 10, padding: 24, width: 380, maxWidth: '90vw' }}>
        <div style={{ fontSize: 15, fontWeight: 600, color: '#991b1b', marginBottom: 8 }}>Nutzer löschen</div>
        <p style={{ fontSize: 13, color: '#525252', margin: '0 0 16px' }}>
          Soll der Nutzer <strong style={{ color: '#111' }}>{user.email}</strong> wirklich gelöscht werden?
        </p>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onCancel} className="px-3 py-1.5 bg-white text-[#262626] text-xs font-medium rounded-md border border-[#ededed] hover:border-[#d4d4d4]">
            Abbrechen
          </button>
          <button
            onClick={onConfirm}
            style={{ padding: '6px 12px', background: '#991b1b', color: '#fff', fontSize: 12, fontWeight: 500, borderRadius: 6, border: 'none', cursor: 'pointer' }}
          >
            Löschen
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Hauptseite ───────────────────────────────────────────────────────────────

export default function Users() {
  const qc = useQueryClient();
  const { user: me } = useAuth();
  const [showCreate, setShowCreate] = useState(false);
  const [editUser, setEditUser] = useState<AdminUser | null>(null);
  const [delUser, setDelUser] = useState<AdminUser | null>(null);

  const { data: users = [], isLoading, error } = useQuery<AdminUser[]>({
    queryKey: ['users'],
    queryFn: listUsers,
    staleTime: 30_000,
  });

  const delMutation = useMutation({
    mutationFn: (id: string) => deleteUser(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] });
      setDelUser(null);
    },
    onError: (err: Error) => {
      alert(err.message);
      setDelUser(null);
    },
  });

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div style={{ fontSize: 13, color: '#737373' }}>
          {users.length} Nutzer{users.length !== 1 ? '' : ''}
        </div>
        <button
          onClick={() => {
            setEditUser(null);
            setShowCreate(true);
          }}
          className="px-3 py-1.5 bg-[#111] text-white text-xs font-medium rounded-md border border-[#111] hover:bg-[#262626]"
        >
          + Neuer Nutzer
        </button>
      </div>

      {(showCreate || editUser) && (
        <UserForm
          existing={editUser ?? undefined}
          onClose={() => {
            setShowCreate(false);
            setEditUser(null);
          }}
          onSaved={() => {
            setShowCreate(false);
            setEditUser(null);
          }}
        />
      )}

      {isLoading && <div style={{ color: '#a3a3a3', fontSize: 13 }}>Nutzer werden geladen…</div>}
      {error && (
        <div className="text-[#991b1b] text-sm p-2" style={{ background: '#fef2f2', borderRadius: 6 }}>
          {(error as Error).message}
        </div>
      )}

      {!isLoading && !error && (
        <div style={{ background: '#fff', border: '1px solid #ededed', borderRadius: 8, overflow: 'hidden' }}>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '2fr 1fr 2fr 1fr 1fr 40px',
              padding: '8px 14px',
              borderBottom: '1px solid #ededed',
              background: '#fafafa',
            }}
          >
            {['E-Mail', 'Rolle', 'Ordner-Zugriff', 'TOTP', 'Letzter Login', ''].map((h) => (
              <div key={h} style={{ fontSize: 11, fontWeight: 600, color: '#a3a3a3', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                {h}
              </div>
            ))}
          </div>

          {users.map((u) => (
            <div
              key={u.id}
              style={{
                display: 'grid',
                gridTemplateColumns: '2fr 1fr 2fr 1fr 1fr 40px',
                padding: '10px 14px',
                borderBottom: '1px solid #ededed',
                alignItems: 'center',
              }}
            >
              <div style={{ fontSize: 13, fontWeight: 500, color: '#111' }}>
                {u.email}
                {me?.email === u.email && (
                  <span style={{ fontSize: 11, color: '#a3a3a3', fontWeight: 400 }}> (du)</span>
                )}
              </div>
              <div>
                <RoleTag role={u.role} />
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                {u.access_all ? (
                  <span style={{ padding: '2px 6px', background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 4, fontSize: 11, color: '#1e40af' }}>
                    alles
                  </span>
                ) : u.allowed_folders.length === 0 ? (
                  <span style={{ padding: '2px 6px', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 4, fontSize: 11, color: '#991b1b' }}>
                    nichts
                  </span>
                ) : (
                  u.allowed_folders.map((f) => (
                    <span key={f} style={{ padding: '2px 6px', background: '#fafafa', border: '1px solid #ededed', borderRadius: 4, fontSize: 11, color: '#525252' }}>
                      {f}
                    </span>
                  ))
                )}
              </div>
              <div style={{ fontSize: 12, color: u.totp_enabled ? '#166534' : '#a3a3a3' }}>
                {u.totp_enabled ? 'aktiv' : '—'}
              </div>
              <div style={{ fontSize: 12, color: '#737373' }}>{formatRelative(u.last_login)}</div>
              <div style={{ display: 'flex', gap: 4, justifyContent: 'flex-end' }}>
                <button
                  onClick={() => {
                    setShowCreate(false);
                    setEditUser(u);
                  }}
                  title="Bearbeiten"
                  style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '2px 6px', fontSize: 13, color: '#525252' }}
                >
                  ✎
                </button>
                {me?.email !== u.email && (
                  <button
                    onClick={() => setDelUser(u)}
                    title="Löschen"
                    style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '2px 6px', fontSize: 13, color: '#991b1b' }}
                  >
                    🗑
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {delUser && (
        <DeleteConfirm user={delUser} onConfirm={() => delMutation.mutate(delUser.id)} onCancel={() => setDelUser(null)} />
      )}
    </div>
  );
}
