import { useQuery } from '@tanstack/react-query';
import { apiGet } from '../api/client';
import { useAuth } from '../hooks/useAuth';
import type { HealthResponse, MetricsResponse, Document } from '../types';


function formatDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('de-AT', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });
}

function ServiceCard({
  name,
  online,
}: {
  name: string;
  online: boolean | undefined;
}) {
  return (
    <div
      className="bg-white border border-[#ededed] rounded-lg p-3.5"
      style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
    >
      <div
        style={{
          fontSize: 10,
          fontWeight: 600,
          color: '#a3a3a3',
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
        }}
      >
        {name}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: online === undefined ? '#f59e0b' : online ? '#10b981' : '#ef4444',
            flexShrink: 0,
          }}
        />
        <span
          style={{
            fontSize: 13,
            fontWeight: 500,
            color: online === undefined ? '#92400e' : online ? '#047857' : '#991b1b',
          }}
        >
          {online === undefined ? 'Unbekannt' : online ? 'Online' : 'Degraded'}
        </span>
      </div>
    </div>
  );
}

export default function Dashboard() {
  const { isAdmin } = useAuth();

  const { data: health, isLoading: healthLoading } = useQuery<HealthResponse>({
    queryKey: ['health'],
    queryFn: () => apiGet('/api/health'),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

  const { data: metrics } = useQuery<MetricsResponse>({
    queryKey: ['metrics'],
    queryFn: () => apiGet('/api/metrics'),
    enabled: isAdmin,
    staleTime: 60_000,
  });

  const { data: recentDocs } = useQuery<Document[]>({
    queryKey: ['documents-recent'],
    queryFn: () => apiGet('/api/documents', { limit: 5, sort: 'indexed_at_desc' }),
    staleTime: 30_000,
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Service Health Cards */}
      <div>
        <div style={{ fontSize: 12, fontWeight: 600, color: '#737373', marginBottom: 10 }}>
          Service-Health
        </div>
        {healthLoading ? (
          <div style={{ fontSize: 13, color: '#a3a3a3' }}>Lädt…</div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
            <ServiceCard name="API" online={health?.status === 'ok' ? true : false} />
            <ServiceCard name="SQLite" online={health?.services.sqlite} />
            <ServiceCard name="LanceDB" online={health?.services.lancedb} />
          </div>
        )}
      </div>

      {/* Metrics Card (admin only) */}
      {isAdmin && (
        <div
          className="bg-white border border-[#ededed] rounded-lg"
          style={{ overflow: 'hidden' }}
        >
          <div
            style={{
              padding: '14px 16px',
              borderBottom: '1px solid #ededed',
            }}
          >
            <span style={{ fontSize: 13, fontWeight: 600, color: '#111' }}>
              Aktivität · 24h
            </span>
          </div>
          <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
            {metrics ? (
              <>
                <MetricRow label="Queries (24h)" value={String(metrics.queries_last_24h)} />
                <MetricRow label="Queries (7d)" value={String(metrics.queries_last_7d)} />
                <MetricRow
                  label="Ø Latenz"
                  value={`${Math.round(metrics.avg_latency_ms_7d)} ms`}
                />
                <MetricRow
                  label="Erfolgsrate"
                  value={`${metrics.ingest_success_rate.toFixed(1)} %`}
                />
                <div style={{ borderTop: '1px solid #f5f5f5', paddingTop: 10, marginTop: 2 }}>
                  <MetricRow label="Indexiert" value={String(metrics.documents_indexed)} />
                  <MetricRow
                    label="Fehlgeschlagen"
                    value={String(metrics.documents_failed)}
                    danger={metrics.documents_failed > 0}
                  />
                  <MetricRow label="Gesamt" value={String(metrics.documents_total)} />
                </div>
              </>
            ) : (
              <div style={{ fontSize: 13, color: '#a3a3a3' }}>Lädt…</div>
            )}
          </div>
        </div>
      )}

      {/* Recent Events */}
      <div className="bg-white border border-[#ededed] rounded-lg" style={{ overflow: 'hidden' }}>
        <div style={{ padding: '14px 16px', borderBottom: '1px solid #ededed' }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: '#111' }}>Letzte Ereignisse</span>
        </div>
        {!recentDocs?.length ? (
          <div style={{ padding: 16, fontSize: 13, color: '#a3a3a3' }}>
            Noch keine Dokumente indexiert.
          </div>
        ) : (
          <div>
            {recentDocs.map((doc) => (
              <div
                key={doc.id}
                style={{
                  padding: '10px 16px',
                  borderBottom: '1px solid #f5f5f5',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                }}
              >
                <StatusDot status={doc.status} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: 13,
                      fontWeight: 500,
                      color: '#111',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {doc.file_name}
                  </div>
                  <div style={{ fontSize: 11, color: '#a3a3a3', marginTop: 1 }}>
                    {doc.folder_path || '/'}
                  </div>
                </div>
                <div style={{ fontSize: 11, color: '#a3a3a3', flexShrink: 0 }}>
                  {formatDate(doc.indexed_at ?? doc.uploaded_at)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function MetricRow({
  label,
  value,
  danger,
}: {
  label: string;
  value: string;
  danger?: boolean;
}) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
      <span style={{ fontSize: 12, color: '#737373' }}>{label}</span>
      <span
        style={{ fontSize: 13, fontWeight: 500, color: danger ? '#991b1b' : '#111' }}
      >
        {value}
      </span>
    </div>
  );
}

function StatusDot({ status }: { status: Document['status'] }) {
  const colors: Record<Document['status'], string> = {
    indexed: '#10b981',
    processing: '#f59e0b',
    queued: '#a3a3a3',
    failed: '#ef4444',
  };
  return (
    <span
      style={{
        width: 7,
        height: 7,
        borderRadius: '50%',
        background: colors[status],
        flexShrink: 0,
      }}
    />
  );
}
