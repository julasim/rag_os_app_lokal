import { useEffect, useState } from 'react';
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
      {/* Aktiver Vault (Firma) + In-App-Firmenwechsel */}
      <VaultCard health={health} />

      {/* MCP-Anbindung (Claude Desktop) */}
      <McpConnectCard vaultLabel={health?.vault_label} />

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

type PyVault = { path: string; label?: string };

function VaultCard({ health }: { health?: HealthResponse }) {
  const [recent, setRecent] = useState<PyVault[]>([]);
  const [current, setCurrent] = useState('');
  const [restarting, setRestarting] = useState(false);
  const [busy, setBusy] = useState(false);
  // pywebview-Brücke gibt es nur in der Desktop-Shell (nicht im Browser/Dev).
  const api: any = (window as any).pywebview?.api; // eslint-disable-line @typescript-eslint/no-explicit-any
  const hasBridge = !!(api && api.switch_vault);

  useEffect(() => {
    if (!api?.list_vaults) return;
    api.list_vaults()
      .then((r: { current?: string; recent?: PyVault[] }) => {
        if (r) { setRecent(r.recent ?? []); setCurrent(r.current ?? ''); }
      })
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const labelOf = (path: string) =>
    recent.find((v) => v.path === path)?.label ||
    path.split(/[\\/]/).filter(Boolean).pop() || path;

  const doSwitch = async (path?: string) => {
    if (!hasBridge || busy) return;
    if (path && !window.confirm(`Zur Firma „${labelOf(path)}" wechseln?\nDie App startet dann neu.`)) return;
    setBusy(true);
    try {
      const res = path ? await api.switch_vault(path) : await api.switch_vault();
      if (res?.ok) setRestarting(true);
      else setBusy(false); // abgebrochen / gleiche Firma → wieder freigeben
    } catch {
      setBusy(false);
    }
  };

  const others = recent.filter((v) => v.path !== (current || health?.vault_path));

  return (
    <div className="bg-white border border-[#ededed] rounded-lg" style={{ padding: '14px 16px' }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: '#111', marginBottom: 8 }}>Aktiver Vault (Firma)</div>
      {!health ? (
        <p style={{ fontSize: 13, color: '#a3a3a3' }}>Lädt…</p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 15, fontWeight: 600, color: '#111' }}>{health.vault_label || '—'}</span>
            <span style={{ fontSize: 11, fontWeight: 600, color: '#fff', background: health.role === 'reader' ? '#6b7280' : '#059669', padding: '2px 7px', borderRadius: 999 }}>
              {health.role === 'reader' ? 'Leser' : 'Schreiber'}
            </span>
          </div>
          <div style={{ fontSize: 12, color: '#a3a3a3', fontFamily: 'ui-monospace, monospace', wordBreak: 'break-all' }}>{health.vault_path}</div>

          {restarting ? (
            <div style={{ fontSize: 13, color: '#059669', marginTop: 8, fontWeight: 500 }}>Firma gewechselt — App startet neu…</div>
          ) : hasBridge ? (
            <div style={{ marginTop: 10, borderTop: '1px solid #f0f0f0', paddingTop: 10 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#111', marginBottom: 6 }}>Firma wechseln</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {others.map((v) => (
                  <button
                    key={v.path}
                    onClick={() => doSwitch(v.path)}
                    disabled={busy}
                    title={v.path}
                    className="px-3 py-1.5 bg-white text-[#262626] text-xs font-medium rounded-md border border-[#ededed] cursor-pointer hover:border-[#d4d4d4] disabled:opacity-50"
                  >
                    {v.label || v.path}
                  </button>
                ))}
                <button
                  onClick={() => doSwitch()}
                  disabled={busy}
                  className="px-3 py-1.5 bg-[#111] text-white text-xs font-medium rounded-md cursor-pointer disabled:opacity-50"
                >
                  Anderen Ordner wählen…
                </button>
              </div>
              <div style={{ fontSize: 11, color: '#a3a3a3', marginTop: 6 }}>
                Beim Wechsel startet die App automatisch neu. Keys/Nutzer bleiben (maschinenweit),
                Dokumente/Graph sind pro Firma getrennt.
              </div>
            </div>
          ) : (
            <div style={{ fontSize: 12, color: '#a3a3a3', marginTop: 4 }}>
              Firma wechseln: über das RAG-OS-Symbol in der Taskleiste (→ „Vault (Firma)") —
              die App startet dann neu.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function McpConnectCard({ vaultLabel }: { vaultLabel?: string }) {
  const [copied, setCopied] = useState(false);
  const mono = { fontFamily: 'ui-monospace, monospace' } as const;
  const mcpUrl = window.location.origin + '/mcp';
  const cfg = {
    mcpServers: {
      'SAZTG RAG_OS': {
        command: 'cmd',
        args: ['/c', 'npx', '-y', 'mcp-remote', mcpUrl, '--header', 'Authorization:${AUTH}'],
        env: { AUTH: 'Bearer DEIN_RAG_SK_KEY' },
      },
    },
  };
  const cfgText = JSON.stringify(cfg, null, 2);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(cfgText);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* Clipboard evtl. blockiert — Nutzer kann den Block markieren/kopieren */
    }
  };

  return (
    <div className="bg-white border border-[#ededed] rounded-lg" style={{ padding: '14px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: '#111' }}>MCP-Anbindung (Claude Desktop)</span>
        <button
          onClick={copy}
          className="px-3 py-1.5 bg-white text-[#262626] text-xs font-medium rounded-md border border-[#ededed] cursor-pointer hover:border-[#d4d4d4] transition-colors"
        >
          {copied ? 'Kopiert ✓' : 'Config kopieren'}
        </button>
      </div>
      <p style={{ fontSize: 12.5, color: '#525252', margin: '0 0 8px', lineHeight: 1.5 }}>
        Über MCP durchsucht <b>Claude Desktop</b> diese Wissensdatenbank direkt (read-only).
        Config eintragen unter <b>Einstellungen → Entwickler → „Edit Config"</b>,{' '}
        <code style={mono}>DEIN_RAG_SK_KEY</code> durch einen Key von der Seite <b>„API-Keys"</b>{' '}
        ersetzen, dann Claude Desktop neu starten. Diese App muss laufen;{' '}
        <code style={mono}>npx</code> (Node) wird benötigt.
      </p>
      <div style={{ fontSize: 12, color: '#525252', margin: '0 0 8px', lineHeight: 1.5, background: '#fafafa', border: '1px solid #ededed', borderRadius: 6, padding: '8px 10px' }}>
        <b>Mehrere Firmen:</b> Der Connector durchsucht immer die <b>gerade aktive Firma</b>
        {vaultLabel ? <> (aktuell: <code style={mono}>{vaultLabel}</code>)</> : null}. Ein Key gilt{' '}
        <b>maschinenweit für alle Firmen</b> — nicht pro Firma neu eintragen. Wechselst du im Tray die
        Firma, startet die App neu; danach durchsucht Claude automatisch die neue Firma (URL/Port bleiben
        gleich, solange nur <b>eine</b> Instanz läuft).
      </div>
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
        }}
      >
        {cfgText}
      </pre>
    </div>
  );
}
