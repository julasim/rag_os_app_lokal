import { useQuery } from '@tanstack/react-query';
import { apiGet } from '../api/client';
import type { HealthResponse } from '../types';

export default function System() {
  const { data: health, isLoading, error, refetch } = useQuery<HealthResponse>({
    queryKey: ['health'],
    queryFn: () => apiGet('/api/health'),
    staleTime: 10_000,
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Aktiver Vault (Firma) */}
      <div className="bg-white border border-[#ededed] rounded-lg" style={{ padding: '14px 16px' }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#111', marginBottom: 8 }}>
          Aktiver Vault (Firma)
        </div>
        {health ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 15, fontWeight: 600, color: '#111' }}>{health.vault_label || '—'}</span>
              <span style={{ fontSize: 11, fontWeight: 600, color: '#fff', background: health.role === 'reader' ? '#6b7280' : '#059669', padding: '2px 7px', borderRadius: 999 }}>
                {health.role === 'reader' ? 'Leser' : 'Schreiber'}
              </span>
            </div>
            <div style={{ fontSize: 12, color: '#a3a3a3', fontFamily: 'ui-monospace, monospace', wordBreak: 'break-all' }}>
              {health.vault_path}
            </div>
            <div style={{ fontSize: 12, color: '#a3a3a3', marginTop: 4 }}>
              Vault wechseln bzw. eine andere Firma öffnen: über das RAG-OS-Symbol in der
              Taskleiste (Rechtsklick → „Vault wechseln") — die App startet dann neu.
            </div>
          </div>
        ) : (
          <p style={{ fontSize: 13, color: '#a3a3a3' }}>Lädt…</p>
        )}
      </div>

      <div className="bg-white border border-[#ededed] rounded-lg" style={{ overflow: 'hidden' }}>
        <div
          style={{
            padding: '14px 16px',
            borderBottom: '1px solid #ededed',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <span style={{ fontSize: 13, fontWeight: 600, color: '#111' }}>Health-Status</span>
          <button
            onClick={() => refetch()}
            className="px-3 py-1.5 bg-white text-[#262626] text-xs font-medium rounded-md border border-[#ededed] cursor-pointer hover:border-[#d4d4d4] transition-colors"
          >
            Aktualisieren
          </button>
        </div>

        <div style={{ padding: '14px 16px' }}>
          {isLoading && (
            <p style={{ fontSize: 13, color: '#a3a3a3' }}>Lädt…</p>
          )}
          {error && (
            <p style={{ fontSize: 13, color: '#991b1b' }}>
              {(error as Error).message}
            </p>
          )}
          {health && (
            <pre
              style={{
                margin: 0,
                padding: '12px 14px',
                background: '#fafafa',
                border: '1px solid #ededed',
                borderRadius: 6,
                fontSize: 12,
                fontFamily: 'ui-monospace, "SF Mono", "JetBrains Mono", monospace',
                color: '#525252',
                overflowX: 'auto',
                whiteSpace: 'pre-wrap',
              }}
            >
              {JSON.stringify(health, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}
