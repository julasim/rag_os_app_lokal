import { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listKeys, createKey, revokeKey } from '../api/keys';
import type { ApiKey } from '../types';

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

const ALL_SCOPES = ['read', 'write', 'delete', 'admin'] as const;
type Scope = (typeof ALL_SCOPES)[number];

// ─── Row Menu ────────────────────────────────────────────────────────────────

function RowMenu({ onRevoke }: { onRevoke: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    if (open) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          padding: '2px 6px',
          borderRadius: 4,
          fontSize: 16,
          color: '#737373',
          lineHeight: 1,
        }}
      >
        ⋯
      </button>
      {open && (
        <div
          style={{
            position: 'absolute',
            right: 0,
            top: '100%',
            marginTop: 4,
            background: '#fff',
            border: '1px solid #ededed',
            borderRadius: 6,
            minWidth: 140,
            zIndex: 20,
            boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
            overflow: 'hidden',
          }}
        >
          <button
            onClick={() => {
              setOpen(false);
              onRevoke();
            }}
            style={{
              display: 'block',
              width: '100%',
              textAlign: 'left',
              padding: '8px 12px',
              fontSize: 13,
              color: '#991b1b',
              background: 'none',
              border: 'none',
              cursor: 'pointer',
            }}
          >
            Widerrufen
          </button>
        </div>
      )}
    </div>
  );
}

// ─── Revoke Confirm ───────────────────────────────────────────────────────────

function RevokeConfirm({
  keyLabel,
  onConfirm,
  onCancel,
}: {
  keyLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
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
      <div
        style={{
          background: '#fff',
          border: '1px solid #ededed',
          borderRadius: 10,
          padding: 24,
          width: 380,
          maxWidth: '90vw',
        }}
      >
        <div style={{ fontSize: 15, fontWeight: 600, color: '#991b1b', marginBottom: 8 }}>
          Key widerrufen
        </div>
        <p style={{ fontSize: 13, color: '#525252', margin: '0 0 16px' }}>
          Soll der Key <strong style={{ color: '#111' }}>{keyLabel}</strong> wirklich
          unwiderruflich gesperrt werden?
        </p>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
            className="px-3 py-1.5 bg-white text-[#262626] text-xs font-medium rounded-md border border-[#ededed] hover:border-[#d4d4d4]"
          >
            Abbrechen
          </button>
          <button
            onClick={onConfirm}
            style={{
              padding: '6px 12px',
              background: '#991b1b',
              color: '#fff',
              fontSize: 12,
              fontWeight: 500,
              borderRadius: 6,
              border: 'none',
              cursor: 'pointer',
            }}
          >
            Widerrufen
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Create Form (inline card) ────────────────────────────────────────────────

interface CreateFormProps {
  onClose: () => void;
  onCreated: (key: string) => void;
}

function CreateForm({ onClose, onCreated }: CreateFormProps) {
  const [label, setLabel] = useState('');
  const [scopes, setScopes] = useState<Scope[]>(['read']);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: createKey,
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['keys'] });
      onCreated(res.plain_key);
    },
    onError: (err: Error) => setError(err.message),
  });

  function toggleScope(scope: Scope) {
    setScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]
    );
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!label.trim()) {
      setError('Label ist erforderlich.');
      return;
    }
    if (scopes.length === 0) {
      setError('Mindestens ein Scope ist erforderlich.');
      return;
    }
    setError(null);
    mutation.mutate({ label, scopes, allowed_folders: [] });
  }

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
        <div style={{ fontSize: 14, fontWeight: 600, color: '#111' }}>Neuer API-Key</div>
        <button
          onClick={onClose}
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            fontSize: 18,
            color: '#a3a3a3',
            lineHeight: 1,
          }}
        >
          ×
        </button>
      </div>

      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* Label */}
        <div>
          <label
            style={{
              display: 'block',
              fontSize: 12,
              fontWeight: 500,
              color: '#262626',
              marginBottom: 4,
            }}
          >
            Label <span style={{ color: '#991b1b' }}>*</span>
          </label>
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="z.B. Langdock Integration"
            className="w-full px-2.5 py-1.5 border border-[#ededed] rounded-md bg-white text-sm text-[#111] outline-none focus:border-[#111]"
            style={{ display: 'block', width: '100%', boxSizing: 'border-box' }}
          />
        </div>

        {/* Scopes */}
        <div>
          <label
            style={{
              display: 'block',
              fontSize: 12,
              fontWeight: 500,
              color: '#262626',
              marginBottom: 6,
            }}
          >
            Rechte
          </label>
          <div style={{ display: 'flex', gap: 12 }}>
            {ALL_SCOPES.map((scope) => (
              <label
                key={scope}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 5,
                  fontSize: 12,
                  color: '#525252',
                  cursor: 'pointer',
                }}
              >
                <input
                  type="checkbox"
                  checked={scopes.includes(scope)}
                  onChange={() => toggleScope(scope)}
                />
                {scope}
              </label>
            ))}
          </div>
        </div>

        {error && (
          <div
            className="text-[#991b1b] text-sm p-2"
            style={{ background: '#fef2f2', borderRadius: 6 }}
          >
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
            {mutation.isPending ? 'Erstelle…' : 'Key erstellen'}
          </button>
        </div>
      </form>
    </div>
  );
}

// ─── New Key Warning Card ─────────────────────────────────────────────────────

function NewKeyWarning({ keyValue, onClose }: { keyValue: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    navigator.clipboard.writeText(keyValue).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div
      style={{
        background: '#fffbeb',
        border: '1px solid #fde68a',
        borderRadius: 8,
        padding: 14,
        marginBottom: 16,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          marginBottom: 8,
        }}
      >
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#92400e' }}>
            Neuer Key erstellt
          </div>
          <div style={{ fontSize: 12, color: '#b45309', marginTop: 2 }}>
            Jetzt kopieren — wird nie wieder angezeigt.
          </div>
        </div>
        <button
          onClick={onClose}
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            fontSize: 18,
            color: '#b45309',
            lineHeight: 1,
            padding: 0,
          }}
        >
          ×
        </button>
      </div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          background: '#fff',
          border: '1px solid #fde68a',
          borderRadius: 6,
          padding: '8px 10px',
        }}
      >
        <code
          style={{
            fontFamily: 'monospace',
            fontSize: 12,
            color: '#111',
            flex: 1,
            wordBreak: 'break-all',
          }}
        >
          {keyValue}
        </code>
        <button
          onClick={handleCopy}
          style={{
            padding: '4px 10px',
            background: copied ? '#059669' : '#111',
            color: '#fff',
            fontSize: 11,
            fontWeight: 500,
            borderRadius: 4,
            border: 'none',
            cursor: 'pointer',
            flexShrink: 0,
            transition: 'background 0.2s',
          }}
        >
          {copied ? 'Kopiert!' : 'Kopieren'}
        </button>
      </div>
    </div>
  );
}

// ─── Scope Tag ────────────────────────────────────────────────────────────────

function ScopeTag({ scope }: { scope: string }) {
  const colors: Record<string, { bg: string; color: string; border: string }> = {
    read: { bg: '#f0fdf4', color: '#166534', border: '#bbf7d0' },
    write: { bg: '#eff6ff', color: '#1e40af', border: '#bfdbfe' },
    delete: { bg: '#fef2f2', color: '#991b1b', border: '#fecaca' },
    admin: { bg: '#faf5ff', color: '#6b21a8', border: '#e9d5ff' },
  };
  const c = colors[scope] ?? { bg: '#fafafa', color: '#525252', border: '#ededed' };
  return (
    <span
      style={{
        padding: '2px 6px',
        background: c.bg,
        border: `1px solid ${c.border}`,
        borderRadius: 4,
        fontSize: 11,
        color: c.color,
        fontWeight: 500,
      }}
    >
      {scope}
    </span>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function Keys() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [revokeConfirm, setRevokeConfirm] = useState<ApiKey | null>(null);

  const { data: keys = [], isLoading, error } = useQuery<ApiKey[]>({
    queryKey: ['keys'],
    queryFn: listKeys,
    staleTime: 30_000,
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => revokeKey(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['keys'] });
      setRevokeConfirm(null);
    },
  });

  const activeKeys = keys;
  const lastUsed = activeKeys
    .filter((k) => k.last_used_at)
    .sort((a, b) => new Date(b.last_used_at!).getTime() - new Date(a.last_used_at!).getTime())[0];

  return (
    <div>
      {/* Header */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 16,
        }}
      >
        <div style={{ fontSize: 13, color: '#737373' }}>
          {activeKeys.length} aktive Key{activeKeys.length !== 1 ? 's' : ''}
          {lastUsed && (
            <span>
              {' '}
              · zuletzt benutzt {formatRelative(lastUsed.last_used_at)}
            </span>
          )}
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 bg-[#111] text-white text-xs font-medium rounded-md border border-[#111] hover:bg-[#262626]"
        >
          + Neuer Key
        </button>
      </div>

      {/* New key warning */}
      {newKey && (
        <NewKeyWarning keyValue={newKey} onClose={() => setNewKey(null)} />
      )}

      {/* Create form */}
      {showCreate && (
        <CreateForm
          onClose={() => setShowCreate(false)}
          onCreated={(key) => {
            setShowCreate(false);
            setNewKey(key);
          }}
        />
      )}

      {/* Loading / Error */}
      {isLoading && <div style={{ color: '#a3a3a3', fontSize: 13 }}>Keys werden geladen…</div>}
      {error && (
        <div
          className="text-[#991b1b] text-sm p-2"
          style={{ background: '#fef2f2', borderRadius: 6 }}
        >
          {(error as Error).message}
        </div>
      )}

      {/* Table */}
      {!isLoading && !error && (
        <div
          style={{
            background: '#fff',
            border: '1px solid #ededed',
            borderRadius: 8,
            overflow: 'hidden',
          }}
        >
          {/* Table header */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '2fr 1.5fr 1.5fr 1fr 40px',
              padding: '8px 14px',
              borderBottom: '1px solid #ededed',
              background: '#fafafa',
            }}
          >
            {['Label', 'Ordner', 'Rechte', 'Zuletzt benutzt', ''].map((h) => (
              <div
                key={h}
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: '#a3a3a3',
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                }}
              >
                {h}
              </div>
            ))}
          </div>

          {/* Empty state */}
          {keys.length === 0 && (
            <div
              style={{
                padding: '32px',
                textAlign: 'center',
                fontSize: 13,
                color: '#a3a3a3',
              }}
            >
              Noch keine API-Keys. Erstelle deinen ersten Key.
            </div>
          )}

          {/* Rows */}
          {keys.map((k) => (
            <div
              key={k.id}
              style={{
                display: 'grid',
                gridTemplateColumns: '2fr 1.5fr 1.5fr 1fr 40px',
                padding: '10px 14px',
                borderBottom: '1px solid #ededed',
                alignItems: 'center',
                opacity: 1,
              }}
            >
              {/* Label + ID suffix */}
              <div>
                <div style={{ fontSize: 13, fontWeight: 500, color: '#111' }}>{k.label}</div>
                <div
                  style={{
                    fontFamily: 'monospace',
                    fontSize: 11,
                    color: '#a3a3a3',
                    marginTop: 1,
                  }}
                >
                  {k.id.slice(0, 8)}…
                </div>
              </div>

              {/* Zugängliche Ordner */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                {k.allowed_folders.length === 0 ? (
                  <span
                    style={{
                      padding: '2px 6px',
                      background: '#fafafa',
                      border: '1px solid #ededed',
                      borderRadius: 4,
                      fontSize: 11,
                      color: '#a3a3a3',
                    }}
                  >
                    alle
                  </span>
                ) : (
                  k.allowed_folders.map((p) => (
                    <span
                      key={p}
                      style={{
                        padding: '2px 6px',
                        background: '#fafafa',
                        border: '1px solid #ededed',
                        borderRadius: 4,
                        fontSize: 11,
                        color: '#525252',
                      }}
                    >
                      {p}
                    </span>
                  ))
                )}
              </div>

              {/* Scopes */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                {k.scopes.map((s) => (
                  <ScopeTag key={s} scope={s} />
                ))}
              </div>

              {/* Last used */}
              <div style={{ fontSize: 12, color: '#737373' }}>
                {formatRelative(k.last_used_at)}
              </div>

              {/* Actions */}
              <div>
                <RowMenu onRevoke={() => setRevokeConfirm(k)} />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Revoke confirm */}
      {revokeConfirm && (
        <RevokeConfirm
          keyLabel={revokeConfirm.label}
          onConfirm={() => revokeMutation.mutate(revokeConfirm.id)}
          onCancel={() => setRevokeConfirm(null)}
        />
      )}
    </div>
  );
}
