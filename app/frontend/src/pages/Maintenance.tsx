import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  getMaintenanceLog,
  getDuplicateSuggestions,
  acceptDuplicate,
  rejectDuplicate,
  undoMaintenanceAction,
  runMaintenance,
  getFolderSuggestions,
  acceptFolderSuggestion,
  rejectFolderSuggestion,
  rebuildFolderSuggestions,
} from '../api/maintenance';
import type {
  MaintenanceLogEntry,
  DuplicateSuggestion,
  MaintenanceRunResult,
  FolderSuggestion,
} from '../api/maintenance';

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString('de-AT', { dateStyle: 'short', timeStyle: 'short' });
}

export default function Maintenance() {
  const qc = useQueryClient();

  const { data: logEntries = [], isLoading: logLoading } = useQuery<MaintenanceLogEntry[]>({
    queryKey: ['maintenance-log'],
    queryFn: getMaintenanceLog,
  });

  const { data: duplicates = [], isLoading: dupLoading } = useQuery<DuplicateSuggestion[]>({
    queryKey: ['duplicate-suggestions'],
    queryFn: getDuplicateSuggestions,
  });

  const { data: folderSuggestions = [], isLoading: folderLoading } = useQuery<FolderSuggestion[]>({
    queryKey: ['folder-suggestions'],
    queryFn: getFolderSuggestions,
  });

  const runMut = useMutation<MaintenanceRunResult>({
    mutationFn: runMaintenance,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['maintenance-log'] });
      qc.invalidateQueries({ queryKey: ['duplicate-suggestions'] });
      qc.invalidateQueries({ queryKey: ['folder-suggestions'] });
    },
  });

  const rebuildFolderMut = useMutation({
    mutationFn: rebuildFolderSuggestions,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['folder-suggestions'] }),
  });

  const acceptFolderMut = useMutation({
    mutationFn: (id: string) => acceptFolderSuggestion(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['folder-suggestions'] });
      qc.invalidateQueries({ queryKey: ['maintenance-log'] });
    },
  });

  const rejectFolderMut = useMutation({
    mutationFn: (id: string) => rejectFolderSuggestion(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['folder-suggestions'] }),
  });

  const acceptMut = useMutation({
    mutationFn: (id: string) => acceptDuplicate(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['duplicate-suggestions'] }),
  });

  const rejectMut = useMutation({
    mutationFn: (id: string) => rejectDuplicate(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['duplicate-suggestions'] }),
  });

  const undoMut = useMutation({
    mutationFn: (id: string) => undoMaintenanceAction(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['maintenance-log'] }),
  });

  const pendingDuplicates = duplicates.filter((d) => d.status === 'pending');
  const pendingFolders = folderSuggestions.filter((f) => f.status === 'pending');

  return (
    <div
      style={{
        fontFamily: '"Helvetica Neue", Helvetica, Arial, sans-serif',
        background: '#FAFAFA',
        minHeight: '100vh',
        padding: '16px',
        maxWidth: 900,
        margin: '0 auto',
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
      }}
    >
      {/* ── Run-Button ── */}
      <div
        style={{
          background: '#fff',
          border: '1px solid #EDEDED',
          borderRadius: 8,
          padding: '14px 16px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
        }}
      >
        <div>
          <div style={{ fontSize: 13, fontWeight: 500, color: '#111' }}>Manueller Wartungs-Lauf</div>
          <div style={{ fontSize: 12, color: '#737373', marginTop: 2 }}>
            Tag-Synonyme zusammenführen · Duplikate erkennen
          </div>
          {runMut.isSuccess && runMut.data && (
            <div style={{ fontSize: 12, color: '#047857', marginTop: 6 }}>
              Fertig — {runMut.data.tag_merges} Tag-Merges · {runMut.data.new_duplicate_suggestions} neue Duplikat-Vorschläge
            </div>
          )}
          {runMut.isError && (
            <div style={{ fontSize: 12, color: '#991b1b', marginTop: 6 }}>
              {(runMut.error as Error).message}
            </div>
          )}
        </div>
        <button
          onClick={() => runMut.mutate()}
          disabled={runMut.isPending}
          className="px-3 py-1.5 bg-[#111] text-white text-xs font-medium rounded-md border border-[#111] hover:bg-[#262626]"
          style={{ opacity: runMut.isPending ? 0.5 : 1, cursor: runMut.isPending ? 'not-allowed' : 'pointer', whiteSpace: 'nowrap' }}
        >
          {runMut.isPending ? 'Läuft…' : '▶ Jetzt starten'}
        </button>
      </div>

      {/* ── Duplikat-Vorschläge ── */}
      <div style={{ background: '#fff', border: '1px solid #EDEDED', borderRadius: 8, overflow: 'hidden' }}>
        <div style={{ padding: '10px 16px', borderBottom: '1px solid #F5F5F5', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: '#111' }}>Duplikat-Vorschläge</span>
          {pendingDuplicates.length > 0 && (
            <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 10, background: '#fef3c7', color: '#92400e' }}>
              {pendingDuplicates.length} offen
            </span>
          )}
        </div>

        {dupLoading && (
          <div style={{ padding: '20px 16px', fontSize: 13, color: '#A3A3A3' }}>Lädt…</div>
        )}
        {!dupLoading && pendingDuplicates.length === 0 && (
          <div style={{ padding: '20px 16px', fontSize: 13, color: '#A3A3A3', textAlign: 'center' }}>
            Keine offenen Duplikat-Vorschläge.
          </div>
        )}

        {pendingDuplicates.map((dup) => (
          <div
            key={dup.id}
            style={{
              padding: '12px 16px',
              borderBottom: '1px solid #F5F5F5',
              display: 'flex',
              alignItems: 'flex-start',
              justifyContent: 'space-between',
              gap: 12,
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, color: '#111', marginBottom: 4 }}>
                <span style={{ fontWeight: 500 }}>Behalten:</span>{' '}
                <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11 }}>{dup.doc_id_keep}</span>
              </div>
              <div style={{ fontSize: 12, color: '#111', marginBottom: 4 }}>
                <span style={{ fontWeight: 500 }}>Löschen:</span>{' '}
                <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11 }}>{dup.doc_id_remove}</span>
              </div>
              <div style={{ fontSize: 11, color: '#737373' }}>
                Grund: {dup.reason} · Hash: {dup.doc_hash.slice(0, 12)}… · {formatDate(dup.created_at)}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
              <button
                onClick={() => {
                  if (confirm('Duplikat wirklich löschen? Diese Aktion ist nicht rückgängig machbar.')) {
                    acceptMut.mutate(dup.id);
                  }
                }}
                disabled={acceptMut.isPending}
                className="px-2.5 py-1 bg-[#fef2f2] text-[#991b1b] text-[11px] rounded border border-[#fecaca] hover:bg-[#fee2e2]"
              >
                Löschen
              </button>
              <button
                onClick={() => rejectMut.mutate(dup.id)}
                disabled={rejectMut.isPending}
                className="px-2.5 py-1 bg-white text-[#525252] text-[11px] rounded border border-[#ededed] hover:border-[#d4d4d4]"
              >
                Ablehnen
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* ── Ordner-Reorg-Vorschläge (Track F) ── */}
      <div style={{ background: '#fff', border: '1px solid #EDEDED', borderRadius: 8, overflow: 'hidden' }}>
        <div style={{ padding: '10px 16px', borderBottom: '1px solid #F5F5F5', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: '#111' }}>Ordner-Vorschläge</span>
          {pendingFolders.length > 0 && (
            <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 10, background: '#dbeafe', color: '#1e40af' }}>
              {pendingFolders.length} offen
            </span>
          )}
          <button
            onClick={() => rebuildFolderMut.mutate()}
            disabled={rebuildFolderMut.isPending}
            className="px-2.5 py-1 bg-white text-[#525252] text-[11px] rounded border border-[#ededed] hover:border-[#d4d4d4]"
            style={{ marginLeft: 'auto', cursor: rebuildFolderMut.isPending ? 'not-allowed' : 'pointer' }}
          >
            {rebuildFolderMut.isPending ? 'Baut…' : '↻ Neu vorschlagen'}
          </button>
        </div>

        {folderLoading && (
          <div style={{ padding: '20px 16px', fontSize: 13, color: '#A3A3A3' }}>Lädt…</div>
        )}
        {!folderLoading && pendingFolders.length === 0 && (
          <div style={{ padding: '20px 16px', fontSize: 13, color: '#A3A3A3', textAlign: 'center' }}>
            Keine offenen Ordner-Vorschläge. Werden im Nachtlauf aus den Wissensgraph-Communities gebaut.
          </div>
        )}

        {pendingFolders.map((f) => (
          <div
            key={f.id}
            style={{
              padding: '12px 16px',
              borderBottom: '1px solid #F5F5F5',
              display: 'flex',
              alignItems: 'flex-start',
              justifyContent: 'space-between',
              gap: 12,
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, color: '#111', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11, color: '#737373' }}>{f.current_folder}</span>
                <span style={{ color: '#A3A3A3' }}>→</span>
                <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11, fontWeight: 600, color: '#1e40af' }}>{f.suggested_folder}</span>
              </div>
              <div style={{ fontSize: 11, color: '#737373', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {f.reason}
                {f.community_id !== null && <span> · Community {f.community_id}</span>}
                {' · '}Doc <span style={{ fontFamily: 'ui-monospace, monospace' }}>{f.doc_id.slice(0, 8)}</span>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
              <button
                onClick={() => acceptFolderMut.mutate(f.id)}
                disabled={acceptFolderMut.isPending}
                className="px-2.5 py-1 bg-[#eff6ff] text-[#1e40af] text-[11px] rounded border border-[#bfdbfe] hover:bg-[#dbeafe]"
              >
                Übernehmen
              </button>
              <button
                onClick={() => rejectFolderMut.mutate(f.id)}
                disabled={rejectFolderMut.isPending}
                className="px-2.5 py-1 bg-white text-[#525252] text-[11px] rounded border border-[#ededed] hover:border-[#d4d4d4]"
              >
                Ablehnen
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* ── Maintenance-Log ── */}
      <div style={{ background: '#fff', border: '1px solid #EDEDED', borderRadius: 8, overflow: 'hidden' }}>
        <div style={{ padding: '10px 16px', borderBottom: '1px solid #F5F5F5' }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: '#111' }}>Wartungs-Log</span>
          <span style={{ fontSize: 11, color: '#A3A3A3', marginLeft: 8 }}>30-Tage-Fenster, rückgängig machbar</span>
        </div>

        {logLoading && (
          <div style={{ padding: '20px 16px', fontSize: 13, color: '#A3A3A3' }}>Lädt…</div>
        )}
        {!logLoading && logEntries.length === 0 && (
          <div style={{ padding: '20px 16px', fontSize: 13, color: '#A3A3A3', textAlign: 'center' }}>
            Noch keine Wartungs-Aktionen.
          </div>
        )}

        {/* header */}
        {logEntries.length > 0 && (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 80px 160px 80px',
              padding: '6px 16px',
              borderBottom: '1px solid #F5F5F5',
              fontSize: 11,
              fontWeight: 600,
              color: '#737373',
            }}
          >
            <span>Zusammenfassung</span>
            <span>Typ</span>
            <span>Datum</span>
            <span>Undo</span>
          </div>
        )}

        {logEntries.map((entry) => (
          <div
            key={entry.id}
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 80px 160px 80px',
              alignItems: 'center',
              padding: '8px 16px',
              borderBottom: '1px solid #F5F5F5',
              fontSize: 12,
              color: '#111',
              opacity: entry.undo_applied ? 0.45 : 1,
            }}
          >
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {entry.summary}
            </span>
            <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11, color: '#737373' }}>
              {entry.action_type}
            </span>
            <span style={{ color: '#737373' }}>{formatDate(entry.created_at)}</span>
            <span>
              {!entry.undo_applied ? (
                <button
                  onClick={() => {
                    if (confirm('Aktion rückgängig machen?')) undoMut.mutate(entry.id);
                  }}
                  disabled={undoMut.isPending}
                  className="px-2 py-0.5 bg-white text-[#525252] text-[11px] rounded border border-[#ededed] hover:border-[#d4d4d4]"
                >
                  Undo
                </button>
              ) : (
                <span style={{ fontSize: 11, color: '#A3A3A3' }}>rückgängig</span>
              )}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
