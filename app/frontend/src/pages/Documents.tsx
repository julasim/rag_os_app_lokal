import React, { useState, useRef, useMemo, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listDocuments,
  listFolders,
  getDocument,
  deleteDocument,
  deleteFolder,
  downloadDocument,
  uploadFiles,
  uploadZip,
  patchDocument,
  reindexDocument,
  getDocumentChunks,
  getJobStatus,
} from '../api/documents';
import type { DocumentChunk } from '../api/documents';
import type { Document, FolderMap } from '../types';
import {
  suggestFromDocs,
  suggestFromZip,
  applySuggestions,
  applyZipSuggestions,
} from '../api/suggest';
import type { SuggestionItem, SuggestResponse } from '../api/suggest';

const PAGE_SIZE = 50;

// ─── helpers ──────────────────────────────────────────────────────────────────

function formatBytes(bytes: number | null): string {
  if (bytes === null) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function triggerDownload(res: Response, fallbackName: string) {
  const disposition = res.headers.get('content-disposition') ?? '';
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match?.[1] ?? fallbackName;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ─── folder tree ──────────────────────────────────────────────────────────────

interface FolderNode {
  name: string;
  path: string;
  count: number;
  children: FolderNode[];
}

function buildTree(folderMap: FolderMap): FolderNode[] {
  const nodes: Record<string, FolderNode> = {};
  const roots: FolderNode[] = [];
  const paths = Object.keys(folderMap)
    .filter((p) => p !== '/' && p !== '')
    .sort();
  for (const raw of paths) {
    const parts = raw.split('/').filter(Boolean);
    let cur = '';
    for (let i = 0; i < parts.length; i++) {
      const parent = cur;
      cur = `${cur}/${parts[i]}`;
      if (!nodes[cur]) {
        const node: FolderNode = { name: parts[i], path: cur, count: folderMap[cur] ?? 0, children: [] };
        nodes[cur] = node;
        if (parent === '') roots.push(node);
        else nodes[parent]?.children.push(node);
      }
    }
  }
  return roots;
}

// ─── FileTypeIcon ─────────────────────────────────────────────────────────────

function FileTypeIcon({ mimeType, fileName, size = 44 }: { mimeType: string | null; fileName: string; size?: number }) {
  const ext = fileName.split('.').pop()?.toLowerCase() ?? '';
  let bg = '#f5f5f5', color = '#737373', label = 'FILE';
  if (mimeType === 'application/pdf' || ext === 'pdf') { bg = '#fef2f2'; color = '#ef4444'; label = 'PDF'; }
  else if (mimeType?.includes('word') || ext === 'docx' || ext === 'doc') { bg = '#eff6ff'; color = '#3b82f6'; label = 'DOC'; }
  else if (mimeType?.includes('sheet') || ext === 'xlsx' || ext === 'xls' || ext === 'xlsm') { bg = '#f0fdf4'; color = '#22c55e'; label = 'XLS'; }
  else if (mimeType?.includes('presentation') || ext === 'pptx' || ext === 'ppt') { bg = '#fff7ed'; color = '#f97316'; label = 'PPT'; }
  else if (ext === 'md') { bg = '#fdf4ff'; color = '#a855f7'; label = 'MD'; }
  else if (ext === 'txt') { bg = '#f5f5f5'; color = '#737373'; label = 'TXT'; }
  else if (ext === 'html' || ext === 'htm') { bg = '#fef3c7'; color = '#d97706'; label = 'HTML'; }
  const fontSize = size >= 40 ? 11 : 9;
  return (
    <div style={{ width: size, height: size, borderRadius: size >= 40 ? 10 : 6, background: bg, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
      <span style={{ color, fontSize, fontWeight: 700, fontFamily: 'ui-monospace, monospace' }}>{label}</span>
    </div>
  );
}

// ─── StatusBadge ─────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: Document['status'] }) {
  const map: Record<Document['status'], string> = {
    indexed: 'bg-[#ecfdf5] text-[#047857]',
    processing: 'bg-[#fffbeb] text-[#92400e]',
    failed: 'bg-[#fef2f2] text-[#991b1b]',
    queued: 'bg-[#f5f5f5] text-[#737373]',
  };
  return (
    <span className={`px-1.5 py-0.5 rounded text-[11px] font-medium font-mono ${map[status]}`}>
      {status}
    </span>
  );
}

// ─── TABS ─────────────────────────────────────────────────────────────────────

const TABS = [
  { key: '', label: 'Alle' },
  { key: 'indexed', label: 'Indexiert' },
  { key: 'processing', label: 'In Arbeit' },
  { key: 'failed', label: 'Fehlerhaft' },
] as const;
type TabKey = typeof TABS[number]['key'];

// ─── SuggestModal ────────────────────────────────────────────────────────────

function SuggestModal({
  isLoading,
  suggestions: initial,
  isZip,
  isApplying,
  onConfirm,
  onCancel,
}: {
  isLoading: boolean;
  suggestions: SuggestionItem[];
  isZip: boolean;
  isApplying: boolean;
  onConfirm: (items: SuggestionItem[]) => void;
  onCancel: () => void;
}) {
  const [items, setItems] = useState<SuggestionItem[]>(initial);
  useEffect(() => { setItems(initial); }, [initial]);

  function updateFolder(i: number, val: string) {
    setItems((prev) => prev.map((item, idx) => idx === i ? { ...item, suggested_folder: val } : item));
  }

  const changedCount = items.filter((it, i) => it.suggested_folder !== initial[i]?.current_folder).length;

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.35)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 600 }}
      onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div style={{ background: '#fff', borderRadius: 12, width: 760, maxWidth: '95vw', maxHeight: '85vh', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 60px rgba(0,0,0,0.2)', overflow: 'hidden' }}>
        {/* header */}
        <div style={{ padding: '16px 20px', borderBottom: '1px solid #EDEDED', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 700, color: '#111' }}>🤖 KI-Ordnervorschlag</div>
            <div style={{ fontSize: 12, color: '#737373', marginTop: 2 }}>
              {isLoading ? 'Analysiere Dokumente…' : `${items.length} Datei${items.length !== 1 ? 'en' : ''} · Ordner bearbeitbar vor dem Übernehmen`}
            </div>
          </div>
          <button onClick={onCancel} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 20, color: '#A3A3A3', lineHeight: 1 }}>×</button>
        </div>

        {/* body */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {isLoading ? (
            <div style={{ padding: 48, textAlign: 'center' }}>
              <div style={{ fontSize: 32, marginBottom: 12 }}>🔍</div>
              <div style={{ fontSize: 14, color: '#737373' }}>KI analysiert Dateiinhalte…</div>
              <div style={{ fontSize: 12, color: '#A3A3A3', marginTop: 4 }}>Das kann einige Sekunden dauern.</div>
            </div>
          ) : (
            <>
              {/* table header */}
              <div style={{ display: 'grid', gridTemplateColumns: '2fr 2fr 1.5fr', gap: 8, padding: '8px 20px', borderBottom: '1px solid #F5F5F5', fontSize: 11, fontWeight: 600, color: '#A3A3A3', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                <span>Datei</span>
                <span>Vorgeschlagener Ordner</span>
                <span>Begründung</span>
              </div>
              {items.map((item, i) => {
                const changed = item.suggested_folder !== item.current_folder;
                return (
                  <div
                    key={item.filename + i}
                    style={{ display: 'grid', gridTemplateColumns: '2fr 2fr 1.5fr', gap: 8, padding: '9px 20px', borderBottom: '1px solid #F5F5F5', alignItems: 'center', background: changed ? '#F0FDF4' : 'transparent' }}
                  >
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 500, color: '#111', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.filename}</div>
                      <div style={{ fontSize: 11, color: '#A3A3A3', fontFamily: 'ui-monospace, monospace', marginTop: 1 }}>{item.current_folder}</div>
                    </div>
                    <input
                      value={item.suggested_folder}
                      onChange={(e) => updateFolder(i, e.target.value)}
                      style={{ padding: '5px 8px', border: `1.5px solid ${changed ? '#86efac' : '#EDEDED'}`, borderRadius: 6, fontSize: 12, fontFamily: 'ui-monospace, monospace', color: '#111', outline: 'none', background: changed ? '#fff' : '#FAFAFA' }}
                      onFocus={(e) => (e.currentTarget.style.borderColor = '#111')}
                      onBlur={(e) => (e.currentTarget.style.borderColor = changed ? '#86efac' : '#EDEDED')}
                    />
                    <span style={{ fontSize: 11, color: '#737373', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.reason}</span>
                  </div>
                );
              })}
            </>
          )}
        </div>

        {/* footer */}
        {!isLoading && (
          <div style={{ padding: '12px 20px', borderTop: '1px solid #EDEDED', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 12, color: '#737373' }}>
              {changedCount > 0 ? `${changedCount} Ordner${changedCount !== 1 ? '' : ''} werden geändert` : 'Keine Änderungen'}
            </span>
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                onClick={onCancel}
                className="px-3 py-1.5 bg-white text-[#262626] text-xs font-medium rounded-md border border-[#ededed] hover:border-[#d4d4d4]"
              >
                Abbrechen
              </button>
              <button
                onClick={() => onConfirm(items)}
                disabled={isApplying}
                className="px-3 py-1.5 bg-[#111] text-white text-xs font-medium rounded-md hover:bg-[#262626]"
                style={{ opacity: isApplying ? 0.7 : 1 }}
              >
                {isApplying ? 'Wird übernommen…' : `${isZip ? 'ZIP hochladen' : 'Ordner übernehmen'}`}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── DocumentCard (grid) ──────────────────────────────────────────────────────

function DocumentCard({ doc, isSelected, isChecked, onCheck, onClick, onContextMenu }: { doc: Document; isSelected: boolean; isChecked: boolean; onCheck: (e: React.MouseEvent) => void; onClick: () => void; onContextMenu: (e: React.MouseEvent) => void }) {
  return (
    <div
      onClick={onClick}
      onContextMenu={onContextMenu}
      style={{
        background: '#fff',
        border: `1.5px solid ${isChecked ? '#7c3aed' : isSelected ? '#3b82f6' : '#EDEDED'}`,
        borderRadius: 12,
        padding: '14px 14px 12px',
        cursor: 'pointer',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        boxShadow: isChecked ? '0 0 0 3px rgba(124,58,237,0.1)' : isSelected ? '0 0 0 3px rgba(59,130,246,0.12)' : 'none',
        transition: 'border-color 0.15s, box-shadow 0.15s',
        position: 'relative',
      }}
    >
      {/* checkbox */}
      <div
        onClick={onCheck}
        style={{ position: 'absolute', top: 10, right: 10, zIndex: 2, display: 'flex', alignItems: 'center', justifyContent: 'center', width: 18, height: 18, borderRadius: 4, border: `2px solid ${isChecked ? '#7c3aed' : '#D1D5DB'}`, background: isChecked ? '#7c3aed' : '#fff', flexShrink: 0, transition: 'all 0.1s', cursor: 'pointer' }}
      >
        {isChecked && <span style={{ color: '#fff', fontSize: 11, lineHeight: 1, fontWeight: 700 }}>✓</span>}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', paddingRight: 24 }}>
        <FileTypeIcon mimeType={doc.mime_type} fileName={doc.file_name} size={44} />
        <StatusBadge status={doc.status} />
      </div>
      <div>
        <div style={{ fontSize: 13, fontWeight: 500, color: '#111', lineHeight: 1.35, wordBreak: 'break-word', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
          {doc.file_name}
        </div>
        <div style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11, color: '#A3A3A3', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {doc.folder_path || '/'}
        </div>
      </div>
      {doc.tags.length > 0 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {doc.tags.slice(0, 3).map((t) => (
            <span key={t} style={{ fontSize: 10, padding: '1px 6px', borderRadius: 4, background: '#f5f5f5', color: '#525252' }}>{t}</span>
          ))}
          {doc.tags.length > 3 && <span style={{ fontSize: 10, color: '#A3A3A3' }}>+{doc.tags.length - 3}</span>}
        </div>
      )}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 11, color: '#A3A3A3', borderTop: '1px solid #F5F5F5', paddingTop: 8, marginTop: 'auto' }}>
        <span>{doc.chunk_count != null ? `${doc.chunk_count} Chunks` : '—'}</span>
        <span>{formatBytes(doc.size_bytes)}</span>
      </div>
    </div>
  );
}

// ─── DocumentRow (list) ───────────────────────────────────────────────────────

function DocumentRow({ doc, isSelected, isChecked, onCheck, onClick, onContextMenu }: { doc: Document; isSelected: boolean; isChecked: boolean; onCheck: (e: React.MouseEvent) => void; onClick: () => void; onContextMenu: (e: React.MouseEvent) => void }) {
  return (
    <div
      onClick={onClick}
      onContextMenu={onContextMenu}
      style={{
        display: 'grid',
        gridTemplateColumns: '36px 28px 2fr 1.2fr 60px 100px 80px',
        gap: 8,
        alignItems: 'center',
        padding: '8px 14px',
        borderBottom: '1px solid #F5F5F5',
        borderLeft: `3px solid ${isChecked ? '#7c3aed' : isSelected ? '#3b82f6' : 'transparent'}`,
        background: isChecked ? '#faf5ff' : isSelected ? '#eff6ff' : 'transparent',
        cursor: 'pointer',
        fontSize: 13,
        color: '#111',
      }}
    >
      {/* checkbox */}
      <div
        onClick={onCheck}
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 18, height: 18, borderRadius: 4, border: `2px solid ${isChecked ? '#7c3aed' : '#D1D5DB'}`, background: isChecked ? '#7c3aed' : '#fff', flexShrink: 0, cursor: 'pointer', transition: 'all 0.1s' }}
      >
        {isChecked && <span style={{ color: '#fff', fontSize: 11, lineHeight: 1, fontWeight: 700 }}>✓</span>}
      </div>
      <FileTypeIcon mimeType={doc.mime_type} fileName={doc.file_name} size={22} />
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: isSelected ? 500 : 400 }}>{doc.file_name}</span>
      <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11, color: '#737373', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{doc.folder_path || '/'}</span>
      <span style={{ color: '#525252', textAlign: 'center' }}>{doc.chunk_count ?? '—'}</span>
      <StatusBadge status={doc.status} />
      <span style={{ textAlign: 'right', color: '#A3A3A3', fontSize: 12 }}>{formatBytes(doc.size_bytes)}</span>
    </div>
  );
}

// ─── InspectorPanel ───────────────────────────────────────────────────────────

function InspectorPanel({ doc, onClose, onDocUpdated }: {
  doc: Document;
  onClose: () => void;
  onDocUpdated: (d: Document) => void;
}) {
  const qc = useQueryClient();
  const [editingTags, setEditingTags] = useState<string[]>(doc.tags);
  const [tagInput, setTagInput] = useState('');
  const [moveFolder, setMoveFolder] = useState(doc.folder_path);
  const [chunksOpen, setChunksOpen] = useState(false);
  const [chunks, setChunks] = useState<DocumentChunk[]>([]);
  const [chunksLoading, setChunksLoading] = useState(false);

  useEffect(() => {
    setEditingTags(doc.tags);
    setTagInput('');
    setMoveFolder(doc.folder_path);
    setChunksOpen(false);
    setChunks([]);
  }, [doc.id]);

  async function loadChunks() {
    if (chunksLoading) return;
    setChunksLoading(true);
    try {
      setChunks(await getDocumentChunks(doc.id));
    } finally {
      setChunksLoading(false);
    }
  }

  const deleteMut = useMutation({
    mutationFn: () => deleteDocument(doc.id),
    onSuccess: () => {
      onClose();
      qc.invalidateQueries({ queryKey: ['documents'] });
      qc.invalidateQueries({ queryKey: ['folders'] });
    },
  });

  const reindexMut = useMutation<Document, Error>({
    mutationFn: () => reindexDocument(doc.id),
    onSuccess: (updated) => onDocUpdated(updated),
  });

  const tagsMut = useMutation<Document, Error, string[]>({
    mutationFn: (tags: string[]) => patchDocument(doc.id, { tags }),
    onSuccess: (updated) => { onDocUpdated(updated); setEditingTags(updated.tags); },
  });

  const moveMut = useMutation<Document, Error, string>({
    mutationFn: (fp: string) => patchDocument(doc.id, { folder_path: fp }),
    onSuccess: (updated) => {
      onDocUpdated(updated);
      qc.invalidateQueries({ queryKey: ['folders'] });
    },
  });

  function handleTagKey(e: React.KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      const t = tagInput.trim().replace(/,/g, '');
      if (t && !editingTags.includes(t)) setEditingTags((prev) => [...prev, t]);
      setTagInput('');
    }
  }

  const tagsChanged =
    JSON.stringify([...editingTags].sort()) !== JSON.stringify([...doc.tags].sort());

  const row = (label: string, value: React.ReactNode) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingBottom: 6, fontSize: 12 }}>
      <span style={{ color: '#737373', flexShrink: 0 }}>{label}</span>
      <span style={{ color: '#111', textAlign: 'right', maxWidth: 170, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</span>
    </div>
  );

  return (
    <div style={{ width: 300, minWidth: 300, borderLeft: '1px solid #EDEDED', background: '#fff', display: 'flex', flexDirection: 'column', overflowY: 'auto' }}>
      {/* header */}
      <div style={{ padding: '12px 16px', borderBottom: '1px solid #EDEDED', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
          <FileTypeIcon mimeType={doc.mime_type} fileName={doc.file_name} size={30} />
          <span style={{ fontSize: 13, fontWeight: 600, color: '#111', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{doc.file_name}</span>
        </div>
        <button
          onClick={onClose}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#A3A3A3', fontSize: 18, lineHeight: 1, padding: 2, flexShrink: 0 }}
        >×</button>
      </div>

      {/* metadata */}
      <div style={{ padding: '12px 16px', borderBottom: '1px solid #F5F5F5' }}>
        {row('Status', <StatusBadge status={doc.status} />)}
        {row('Ordner', <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11 }}>{doc.folder_path || '/'}</span>)}
        {row('Format', doc.mime_type ?? '—')}
        {row('Größe', formatBytes(doc.size_bytes))}
        {row('Chunks', doc.chunk_count != null ? String(doc.chunk_count) : '—')}
        {row('Hochgeladen', doc.uploaded_at ? new Date(doc.uploaded_at).toLocaleDateString('de-AT') : '—')}
        {doc.error_msg && (
          <div style={{ fontSize: 11, color: '#991b1b', marginTop: 4, padding: '4px 8px', background: '#fef2f2', borderRadius: 4 }}>
            {doc.error_msg}
          </div>
        )}
      </div>

      {/* actions */}
      <div style={{ padding: '12px 16px', borderBottom: '1px solid #F5F5F5', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        <button
          className="px-2.5 py-1 bg-white text-[#262626] text-[11px] rounded border border-[#ededed] hover:border-[#d4d4d4]"
          onClick={async () => { const res = await downloadDocument(doc.id); await triggerDownload(res, doc.file_name); }}
        >
          ⬇ Original
        </button>
        {doc.mime_type !== 'application/pdf' && (
          <button
            className="px-2.5 py-1 bg-white text-[#262626] text-[11px] rounded border border-[#ededed] hover:border-[#d4d4d4]"
            onClick={async () => {
              const res = await downloadDocument(doc.id, 'pdf');
              await triggerDownload(res, doc.file_name.replace(/\.[^.]+$/, '.pdf'));
            }}
          >
            Als PDF
          </button>
        )}
        <button
          className="px-2.5 py-1 bg-white text-[#262626] text-[11px] rounded border border-[#ededed] hover:border-[#d4d4d4]"
          disabled={reindexMut.isPending}
          onClick={() => reindexMut.mutate()}
        >
          {reindexMut.isPending ? '…' : '↻ Reindex'}
        </button>
        <button
          className="px-2.5 py-1 bg-[#fef2f2] text-[#991b1b] text-[11px] rounded border border-[#fecaca] hover:bg-[#fee2e2]"
          disabled={deleteMut.isPending}
          onClick={() => { if (confirm(`"${doc.file_name}" wirklich löschen?`)) deleteMut.mutate(); }}
        >
          Löschen
        </button>
      </div>

      {/* tags */}
      <div style={{ padding: '12px 16px', borderBottom: '1px solid #F5F5F5' }}>
        <div style={{ fontSize: 10, fontWeight: 600, color: '#A3A3A3', letterSpacing: '0.06em', marginBottom: 8 }}>TAGS</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 8 }}>
          {editingTags.map((t) => (
            <span key={t} style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: 11, padding: '2px 6px', borderRadius: 4, background: '#f5f5f5', color: '#525252' }}>
              {t}
              <button
                onClick={() => setEditingTags((p) => p.filter((x) => x !== t))}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#A3A3A3', fontSize: 12, lineHeight: 1, padding: 0 }}
              >×</button>
            </span>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 4 }}>
          <input
            className="px-2 py-1 border border-[#ededed] rounded text-[12px] outline-none focus:border-[#111]"
            style={{ flex: 1 }}
            placeholder="Tag + Enter"
            value={tagInput}
            onChange={(e) => setTagInput(e.target.value)}
            onKeyDown={handleTagKey}
          />
          {tagsChanged && (
            <button
              className="px-2.5 py-1 bg-[#111] text-white text-[11px] rounded hover:bg-[#262626]"
              disabled={tagsMut.isPending}
              onClick={() => tagsMut.mutate(editingTags)}
            >
              {tagsMut.isPending ? '…' : 'OK'}
            </button>
          )}
        </div>
      </div>

      {/* move */}
      <div style={{ padding: '12px 16px', borderBottom: '1px solid #F5F5F5' }}>
        <div style={{ fontSize: 10, fontWeight: 600, color: '#A3A3A3', letterSpacing: '0.06em', marginBottom: 8 }}>VERSCHIEBEN</div>
        <div style={{ display: 'flex', gap: 4 }}>
          <input
            className="px-2 py-1 border border-[#ededed] rounded text-[12px] outline-none focus:border-[#111]"
            style={{ flex: 1, fontFamily: 'ui-monospace, monospace' }}
            value={moveFolder}
            onChange={(e) => setMoveFolder(e.target.value)}
          />
          <button
            className="px-2.5 py-1 bg-[#111] text-white text-[11px] rounded hover:bg-[#262626]"
            disabled={moveMut.isPending || moveFolder === doc.folder_path}
            onClick={() => moveMut.mutate(moveFolder)}
          >
            {moveMut.isPending ? '…' : 'OK'}
          </button>
        </div>
      </div>

      {/* chunks */}
      <div style={{ padding: '12px 16px' }}>
        <button
          style={{ fontSize: 10, fontWeight: 600, color: '#A3A3A3', letterSpacing: '0.06em', background: 'none', border: 'none', cursor: 'pointer', padding: 0, display: 'flex', alignItems: 'center', gap: 4 }}
          onClick={() => {
            const opening = !chunksOpen;
            setChunksOpen(opening);
            if (opening && chunks.length === 0) loadChunks();
          }}
        >
          {chunksOpen ? '▾' : '▸'} CHUNKS{chunks.length > 0 ? ` (${chunks.length})` : ''}
        </button>
        {chunksOpen && (
          <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {chunksLoading ? (
              <span style={{ fontSize: 12, color: '#A3A3A3' }}>Lädt…</span>
            ) : chunks.length === 0 ? (
              <span style={{ fontSize: 12, color: '#A3A3A3' }}>Keine Chunks.</span>
            ) : (
              chunks.map((c, i) => (
                <div key={c.id} style={{ fontSize: 11, padding: '6px 8px', background: '#FAFAFA', border: '1px solid #EDEDED', borderRadius: 6 }}>
                  <div style={{ color: '#737373', marginBottom: 3 }}>
                    #{i + 1}{c.page != null ? ` · S. ${c.page}` : ''}{c.section_title ? ` · ${c.section_title}` : ''}
                  </div>
                  <div style={{ color: '#262626', fontFamily: 'ui-monospace, monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.4 }}>
                    {c.content.slice(0, 300)}{c.content.length > 300 ? '…' : ''}
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── NewFolderInlineRow ───────────────────────────────────────────────────────

function NewFolderInlineRow({ depth, input, inputRef, onChange, onConfirm, onCancel }: {
  depth: number;
  input: string;
  inputRef: React.RefObject<HTMLInputElement>;
  onChange: (v: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6,
        paddingLeft: 6 + depth * 12, paddingRight: 8, paddingTop: 4, paddingBottom: 4,
        background: '#F5F3FF', borderLeft: '2px solid #7C3AED',
      }}>
        <span style={{ fontSize: 12, flexShrink: 0, color: '#7C3AED' }}>📁</span>
        <input
          ref={inputRef}
          style={{ flex: 1, border: 'none', borderBottom: '1.5px solid #7C3AED', outline: 'none', fontSize: 12, color: '#111', background: 'transparent', padding: '2px 0', fontFamily: 'inherit', minWidth: 0 }}
          placeholder="Ordnername"
          value={input}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') onConfirm(); if (e.key === 'Escape') onCancel(); }}
        />
      </div>
      <div style={{ paddingLeft: 6 + depth * 12 + 18, paddingBottom: 5, display: 'flex', gap: 10, fontSize: 10 }}>
        <span style={{ color: '#7C3AED', cursor: 'pointer', fontWeight: 500 }} onClick={onConfirm}>↵ Erstellen</span>
        <span style={{ color: '#9CA3AF', cursor: 'pointer' }} onClick={onCancel}>Esc</span>
      </div>
    </>
  );
}

// ─── ContextMenu ─────────────────────────────────────────────────────────────

interface CtxItem {
  label?: string;
  icon?: string;
  danger?: boolean;
  disabled?: boolean;
  separator?: boolean;
  onClick?: () => void;
}

function ContextMenu({ x, y, items, onClose }: {
  x: number; y: number;
  items: CtxItem[];
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = () => onClose();
    const closeOnKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('click', close);
    window.addEventListener('contextmenu', close);
    window.addEventListener('keydown', closeOnKey);
    return () => {
      window.removeEventListener('click', close);
      window.removeEventListener('contextmenu', close);
      window.removeEventListener('keydown', closeOnKey);
    };
  }, [onClose]);

  // Adjust to stay within viewport
  const estHeight = items.filter(i => !i.separator).length * 32 + items.filter(i => i.separator).length * 9 + 8;
  const adjX = Math.min(x, window.innerWidth - 210);
  const adjY = Math.min(y, window.innerHeight - estHeight - 8);

  return (
    <div
      ref={ref}
      onClick={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
      style={{
        position: 'fixed', left: adjX, top: adjY, zIndex: 500,
        background: '#fff', borderRadius: 8, border: '1px solid #E5E7EB',
        boxShadow: '0 4px 24px rgba(0,0,0,0.13), 0 1px 4px rgba(0,0,0,0.07)',
        padding: '4px 0', minWidth: 190,
      }}
    >
      {items.map((item, i) =>
        item.separator ? (
          <div key={i} style={{ height: 1, background: '#F3F4F6', margin: '3px 0' }} />
        ) : (
          <div
            key={i}
            onClick={() => { if (!item.disabled) { item.onClick?.(); onClose(); } }}
            style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '7px 14px', fontSize: 13, cursor: item.disabled ? 'default' : 'pointer',
              color: item.danger ? '#DC2626' : item.disabled ? '#D1D5DB' : '#111',
              userSelect: 'none',
            }}
            onMouseEnter={(e) => {
              if (!item.disabled) (e.currentTarget as HTMLDivElement).style.background = item.danger ? '#FEF2F2' : '#F9FAFB';
            }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = ''; }}
          >
            {item.icon && <span style={{ width: 16, textAlign: 'center', fontSize: 13, flexShrink: 0 }}>{item.icon}</span>}
            <span style={{ flex: 1 }}>{item.label}</span>
          </div>
        )
      )}
    </div>
  );
}

// ─── FolderTreeNode ───────────────────────────────────────────────────────────

interface NewFolderProps {
  parentPath: string;
  input: string;
  inputRef: React.RefObject<HTMLInputElement>;
  onChange: (v: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}

function FolderTreeNode({ node, activeFolder, onSelect, depth = 0, newFolder, onContextMenu }: {
  node: FolderNode;
  activeFolder: string;
  onSelect: (path: string) => void;
  depth?: number;
  newFolder?: NewFolderProps;
  onContextMenu?: (e: React.MouseEvent, node: FolderNode) => void;
}) {
  const [open, setOpen] = useState(activeFolder.startsWith(node.path));
  const isActive = activeFolder === node.path;
  const hasChildren = node.children.length > 0;
  const showInlineNew = newFolder && newFolder.parentPath === node.path;

  return (
    <div>
      <div
        onClick={() => { onSelect(node.path); if (hasChildren || showInlineNew) setOpen(true); else setOpen((p) => !p); }}
        onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); onContextMenu?.(e, node); }}
        style={{
          display: 'flex', alignItems: 'center', gap: 4,
          paddingLeft: 8 + depth * 12, paddingRight: 8,
          paddingTop: 5, paddingBottom: 5,
          cursor: 'pointer',
          background: isActive ? '#eff6ff' : 'transparent',
          borderLeft: `2px solid ${isActive ? '#3b82f6' : 'transparent'}`,
          fontSize: 13,
          color: isActive ? '#1d4ed8' : '#374151',
          fontWeight: isActive ? 500 : 400,
        }}
      >
        {hasChildren || showInlineNew ? (
          <span style={{ fontSize: 9, color: '#A3A3A3', width: 12, flexShrink: 0 }}>{open ? '▾' : '▸'}</span>
        ) : (
          <span style={{ width: 12, flexShrink: 0, color: '#D4D4D4', fontSize: 10 }}>·</span>
        )}
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{node.name}</span>
        {node.count > 0 && (
          <span style={{ fontSize: 11, color: '#A3A3A3', flexShrink: 0 }}>{node.count}</span>
        )}
      </div>
      {/* inline new-folder always visible when active, regardless of open state */}
      {showInlineNew && (
        <NewFolderInlineRow
          depth={depth + 1}
          input={newFolder!.input}
          inputRef={newFolder!.inputRef}
          onChange={newFolder!.onChange}
          onConfirm={newFolder!.onConfirm}
          onCancel={newFolder!.onCancel}
        />
      )}
      {open && hasChildren && node.children.map((child) => (
        <FolderTreeNode key={child.path} node={child} activeFolder={activeFolder} onSelect={onSelect} depth={depth + 1} newFolder={newFolder} onContextMenu={onContextMenu} />
      ))}
    </div>
  );
}

// ─── main component ───────────────────────────────────────────────────────────

export default function Documents() {
  const queryClient = useQueryClient();

  const [folderFilter, setFolderFilter] = useState('');
  const [activeTab, setActiveTab] = useState<TabKey>('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [selectedDoc, setSelectedDoc] = useState<Document | null>(null);
  const [layoutMode, setLayoutMode] = useState<'grid' | 'list'>('grid');

  // Deep-Link aus dem Graph: /documents?doc=<id> → Dokument direkt auswählen.
  const [searchParams] = useSearchParams();
  useEffect(() => {
    const id = searchParams.get('doc');
    if (!id) return;
    getDocument(id).then(setSelectedDoc).catch(() => {});
  }, [searchParams]);

  const [selectedDocIds, setSelectedDocIds] = useState<Set<string>>(new Set());
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [suggestResponse, setSuggestResponse] = useState<SuggestResponse | null>(null);
  const [suggestApplying, setSuggestApplying] = useState(false);
  const [suggestOpen, setSuggestOpen] = useState(false);
  const [pendingZipFile, setPendingZipFile] = useState<File | null>(null);
  const [aiZipMode, setAiZipMode] = useState(true);   // KI-Analyse für ZIP an/aus

  const [uploadOpen, setUploadOpen] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);
  const [uploadErr, setUploadErr] = useState<string | null>(null);
  // Track C3b: Uploads sind async → Job-ID pollen bis done/failed/partial.
  const [activeJob, setActiveJob] = useState<string | null>(null);

  const [showNewFolder, setShowNewFolder] = useState(false);
  const [newFolderInput, setNewFolderInput] = useState('');
  const [localFolders, setLocalFolders] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem('rag_local_folders') || '[]'); }
    catch { return []; }
  });

  const fileInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
  const newFolderRef = useRef<HTMLInputElement>(null);

  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; items: CtxItem[] } | null>(null);

  // ── quick mutations (used by context menu) ────────────────────────────────

  const ctxDeleteMut = useMutation({
    mutationFn: (id: string) => deleteDocument(id),
    onSuccess: () => {
      setSelectedDoc(null);
      queryClient.invalidateQueries({ queryKey: ['documents'] });
      queryClient.invalidateQueries({ queryKey: ['folders'] });
    },
  });

  const ctxReindexMut = useMutation({
    mutationFn: (id: string) => reindexDocument(id),
    onSuccess: (updated) => { if (selectedDoc?.id === updated.id) setSelectedDoc(updated); },
  });

  const ctxDeleteFolderMut = useMutation({
    mutationFn: (path: string) => deleteFolder(path),
    onSuccess: (_data, path) => {
      // Lokale (leere) Ordner aus dem State entfernen — sonst bleiben sie im Baum
      setLocalFolders((prev) => prev.filter((lf) => lf !== path && !lf.startsWith(path + '/')));
      if (selectedDoc && folderFilter && selectedDoc.folder_path.startsWith(folderFilter)) setSelectedDoc(null);
      setFolderFilter('');
      queryClient.invalidateQueries({ queryKey: ['documents'] });
      queryClient.invalidateQueries({ queryKey: ['folders'] });
    },
    onError: (err: unknown) => {
      alert(err instanceof Error ? err.message : 'Ordner konnte nicht gelöscht werden.');
    },
  });

  // ── context menu helpers ──────────────────────────────────────────────────

  function openCtxMenu(e: React.MouseEvent, items: CtxItem[]) {
    e.preventDefault();
    e.stopPropagation();
    setCtxMenu({ x: e.clientX, y: e.clientY, items });
  }

  function docCtxItems(doc: Document): CtxItem[] {
    return [
      { icon: '◉', label: 'Details öffnen', onClick: () => setSelectedDoc(doc) },
      { icon: '↓', label: 'Herunterladen', onClick: async () => { const r = await downloadDocument(doc.id); await triggerDownload(r, doc.file_name); } },
      { separator: true },
      { icon: '↺', label: 'Reindexieren', onClick: () => ctxReindexMut.mutate(doc.id) },
      { separator: true },
      { icon: '×', label: 'Löschen', danger: true, onClick: () => { if (confirm(`"${doc.file_name}" wirklich löschen?`)) ctxDeleteMut.mutate(doc.id); } },
    ];
  }

  function folderCtxItems(node: FolderNode): CtxItem[] {
    return [
      { icon: '📁', label: 'Neuer Unterordner', onClick: () => { navigateTo(node.path); setShowNewFolder(true); } },
      { icon: '↑', label: 'Hierher hochladen', onClick: () => { navigateTo(node.path); setUploadOpen(true); } },
      { separator: true },
      { icon: '🤖', label: 'Ordner analysieren', onClick: () => openSuggestForFolder(node.path) },
      { icon: '→', label: 'Alle Dokumente anzeigen', onClick: () => navigateTo(node.path) },
      { separator: true },
      {
        icon: '×', label: 'Ordner löschen', danger: true,
        onClick: () => {
          if (confirm(`Ordner "${node.name}" und alle enthaltenen Dokumente löschen?`))
            ctxDeleteFolderMut.mutate(node.path);
        },
      },
    ];
  }

  function bgCtxItems(): CtxItem[] {
    return [
      { icon: '↑', label: 'Dateien hochladen', onClick: () => { setUploadOpen(true); setTimeout(() => fileInputRef.current?.click(), 100); } },
      { icon: '📁', label: 'Neuer Ordner', onClick: () => setShowNewFolder(true) },
    ];
  }

  // ── queries ────────────────────────────────────────────────────────────────

  const { data: folderMap = {} } = useQuery({
    queryKey: ['folders'],
    queryFn: () => listFolders(),
  });

  const { data: docsResult, isLoading: docsLoading } = useQuery({
    queryKey: ['documents', folderFilter, activeTab, search, page],
    queryFn: () =>
      listDocuments({
        folder_prefix: folderFilter || undefined,
        status_filter: activeTab || undefined,
        search: search || undefined,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
  });

  const docs = docsResult?.docs ?? [];
  const total = docsResult?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // ── async ingest-job polling (Track C3b) ──────────────────────────────────
  // Uploads (Dateien/ZIP/Apply-ZIP) laufen jetzt asynchron im rag-ingest-Worker.
  // Solange ein Job aktiv ist, alle 1,5 s den aggregierten Status pollen; bei
  // done/failed/partial stoppen und die Listen invalidieren.
  const TERMINAL = ['done', 'failed', 'partial'];
  const { data: jobStatus } = useQuery({
    queryKey: ['ingest-job', activeJob],
    queryFn: () => getJobStatus(activeJob!),
    enabled: !!activeJob,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s && TERMINAL.includes(s) ? false : 1500;
    },
  });

  useEffect(() => {
    if (!jobStatus) return;
    if (TERMINAL.includes(jobStatus.status)) {
      const label =
        jobStatus.status === 'done' ? 'abgeschlossen'
        : jobStatus.status === 'partial' ? 'teilweise fehlgeschlagen'
        : 'fehlgeschlagen';
      setUploadMsg(
        `Verarbeitung ${label}: ${jobStatus.processed}/${jobStatus.total} indexiert` +
        (jobStatus.failed ? `, ${jobStatus.failed} fehlgeschlagen` : '') + '.',
      );
      if (jobStatus.error_msg && jobStatus.failed) setUploadErr(jobStatus.error_msg);
      queryClient.invalidateQueries({ queryKey: ['documents'] });
      queryClient.invalidateQueries({ queryKey: ['folders'] });
      setActiveJob(null);
    } else {
      setUploadMsg(`Verarbeitung läuft… ${jobStatus.processed}/${jobStatus.total}`);
    }
  }, [jobStatus]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── localFolders merge + auto-clean ───────────────────────────────────────

  const tree = useMemo(() => {
    const merged: FolderMap = { ...folderMap };
    for (const lf of localFolders) {
      if (!(lf in merged)) merged[lf] = 0;
    }
    return buildTree(merged);
  }, [folderMap, localFolders]);

  useEffect(() => {
    localStorage.setItem('rag_local_folders', JSON.stringify(localFolders));
  }, [localFolders]);

  useEffect(() => {
    if (localFolders.length === 0) return;
    setLocalFolders((prev) => prev.filter((lf) => !(lf in folderMap)));
  }, [folderMap]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── upload ─────────────────────────────────────────────────────────────────

  async function handleFileUpload(files: FileList | null) {
    if (!files || files.length === 0) return;
    const arr = Array.from(files);
    setUploadMsg(null); setUploadErr(null); setIsUploading(true);
    if (fileInputRef.current) fileInputRef.current.value = '';
    try {
      // Immer async (1..n Dateien → Queue): BulkUploadResponse mit job_id.
      const result = await uploadFiles(arr, folderFilter || '/', []);
      setUploadMsg(`${result.total} Datei(en) in Verarbeitung…`);
      if (result.skipped && result.skipped.length > 0) {
        setUploadErr(`${result.skipped.length} Datei(en) übersprungen (Format nicht unterstützt): ${result.skipped.map(s => s.split(':')[0]).join(', ')}`);
      }
      setActiveJob(result.job_id);   // Fortschritt via Polling
    } catch (e: unknown) {
      setUploadErr(e instanceof Error ? e.message : 'Upload fehlgeschlagen.');
    } finally {
      setIsUploading(false);
    }
  }

  async function handleZipUpload(files: FileList | null) {
    if (!files || files.length === 0) return;
    const zipFile = files[0];
    if (zipInputRef.current) zipInputRef.current.value = '';

    if (aiZipMode) {
      // KI-Analyse-Flow: erst analysieren, dann Vorschau anzeigen
      setPendingZipFile(zipFile);
      setSuggestResponse(null);
      setSuggestLoading(true);
      setSuggestOpen(true);
      try {
        const res = await suggestFromZip(zipFile);
        setSuggestResponse(res);
      } catch (e: unknown) {
        setSuggestOpen(false);
        setUploadErr(e instanceof Error ? e.message : 'KI-Analyse fehlgeschlagen.');
      } finally {
        setSuggestLoading(false);
      }
      return;
    }

    // Direkter Upload ohne KI
    setUploadMsg(null); setUploadErr(null); setIsUploading(true);
    try {
      const result = await uploadZip(zipFile, folderFilter || '/', []);
      setUploadMsg(`ZIP wird verarbeitet (${result.total} Datei(en))…`);
      if (result.skipped && result.skipped.length > 0) {
        setUploadErr(`${result.skipped.length} Datei(en) übersprungen (Format nicht unterstützt): ${result.skipped.map(s => s.split(':')[0]).join(', ')}`);
      }
      setActiveJob(result.job_id);   // Fortschritt via Polling
    } catch (e: unknown) {
      setUploadErr(e instanceof Error ? e.message : 'ZIP-Upload fehlgeschlagen.');
    } finally {
      setIsUploading(false);
    }
  }

  // ── Suggest: bestehende Docs ──────────────────────────────────────────────

  async function openSuggestForSelection() {
    if (selectedDocIds.size === 0 || suggestLoading) return;
    setSuggestResponse(null);
    setSuggestLoading(true);
    setSuggestOpen(true);
    setPendingZipFile(null);
    try {
      const res = await suggestFromDocs(Array.from(selectedDocIds).slice(0, 20));
      setSuggestResponse(res);
    } catch (e: unknown) {
      setSuggestOpen(false);
      alert(e instanceof Error ? e.message : 'Analyse fehlgeschlagen.');
    } finally {
      setSuggestLoading(false);
    }
  }

  async function openSuggestForFolder(folderPath: string) {
    if (suggestLoading) return;
    setSuggestResponse(null);
    setSuggestLoading(true);
    setSuggestOpen(true);
    setPendingZipFile(null);
    try {
      const { docs: folderDocs } = await listDocuments({ folder_prefix: folderPath, limit: 20 });
      if (folderDocs.length === 0) {
        setSuggestOpen(false);
        return;
      }
      const res = await suggestFromDocs(folderDocs.map((d) => d.id));
      setSuggestResponse(res);
    } catch (e: unknown) {
      setSuggestOpen(false);
      alert(e instanceof Error ? e.message : 'Analyse fehlgeschlagen.');
    } finally {
      setSuggestLoading(false);
    }
  }

  async function confirmSuggestions(confirmed: SuggestionItem[]) {
    setSuggestApplying(true);
    try {
      if (pendingZipFile && suggestResponse?.temp_id) {
        // ZIP-Flow
        const result = await applyZipSuggestions(suggestResponse.temp_id, confirmed);
        setSuggestOpen(false);
        setPendingZipFile(null);
        setUploadMsg(`ZIP wird verarbeitet (${result.total} Dateien)…`);
        setActiveJob(result.job_id);   // Fortschritt via Polling
      } else {
        // Bestehende Docs verschieben
        const result = await applySuggestions(confirmed);
        setSuggestOpen(false);
        setSelectedDocIds(new Set());
        setUploadMsg(`${result.moved} Dokument(e) verschoben.`);
        if (result.errors.length > 0) setUploadErr(result.errors.join('; '));
        queryClient.invalidateQueries({ queryKey: ['documents'] });
        queryClient.invalidateQueries({ queryKey: ['folders'] });
      }
    } catch (e: unknown) {
      setUploadErr(e instanceof Error ? e.message : 'Fehler beim Übernehmen.');
    } finally {
      setSuggestApplying(false);
    }
  }

  function toggleDocCheck(e: React.MouseEvent, docId: string) {
    e.stopPropagation();
    setSelectedDocIds((prev) => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId);
      else next.add(docId);
      return next;
    });
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    const files = e.dataTransfer.files;
    if (!files?.[0]) return;
    files[0].name.endsWith('.zip') ? handleZipUpload(files) : handleFileUpload(files);
  }

  function navigateTo(path: string) {
    setFolderFilter(path);
    setPage(1);
    setSelectedDoc(null);
  }

  function confirmNewFolder() {
    const name = newFolderInput.trim().replace(/\//g, '');
    if (!name) return;
    const newPath = (folderFilter || '') + '/' + name;
    setLocalFolders((prev) => (prev.includes(newPath) ? prev : [...prev, newPath]));
    setShowNewFolder(false);
    setNewFolderInput('');
    navigateTo(newPath);
    setUploadOpen(true);
  }

  useEffect(() => {
    if (showNewFolder) setTimeout(() => newFolderRef.current?.focus(), 50);
  }, [showNewFolder]);

  // ── render ─────────────────────────────────────────────────────────────────

  return (
    <div
      style={{ display: 'flex', height: 'calc(100vh - 57px)', fontFamily: '-apple-system, "Inter", sans-serif', background: '#FAFAFA', overflow: 'hidden' }}
    >
      {/* ──── LEFT: Folder sidebar ──── */}
      <div style={{ width: 220, minWidth: 220, background: '#fff', borderRight: '1px solid #EDEDED', display: 'flex', flexDirection: 'column', overflowY: 'auto' }}>
        {/* sidebar header */}
        <div style={{ padding: '10px 12px 6px', borderBottom: '1px solid #F5F5F5', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 10, fontWeight: 600, color: '#A3A3A3', letterSpacing: '0.06em', textTransform: 'uppercase' }}>Explorer</span>
          <button
            onClick={() => { setShowNewFolder((p) => !p); setNewFolderInput(''); }}
            style={{ background: 'none', border: '1px solid #EDEDED', borderRadius: 4, cursor: 'pointer', color: '#737373', width: 22, height: 22, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16 }}
            title="Neuer Ordner"
          >
            +
          </button>
        </div>


        {/* "Alle" */}
        <div
          onClick={() => navigateTo('')}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '7px 12px', cursor: 'pointer', fontSize: 13,
            background: folderFilter === '' ? '#eff6ff' : 'transparent',
            borderLeft: `2px solid ${folderFilter === '' ? '#3b82f6' : 'transparent'}`,
            color: folderFilter === '' ? '#1d4ed8' : '#374151',
            fontWeight: folderFilter === '' ? 500 : 400,
          }}
        >
          <span>Alle Dokumente</span>
          <span style={{ fontSize: 11, color: '#A3A3A3' }}>{total}</span>
        </div>

        {/* tree */}
        <div style={{ flex: 1 }}>
          {tree.map((node) => (
            <FolderTreeNode
              key={node.path}
              node={node}
              activeFolder={folderFilter}
              onSelect={navigateTo}
              onContextMenu={(e, n) => openCtxMenu(e, folderCtxItems(n))}
              newFolder={showNewFolder ? {
                parentPath: folderFilter,
                input: newFolderInput,
                inputRef: newFolderRef,
                onChange: setNewFolderInput,
                onConfirm: confirmNewFolder,
                onCancel: () => { setShowNewFolder(false); setNewFolderInput(''); },
              } : undefined}
            />
          ))}
          {/* root-level new folder (when no folder is selected) */}
          {showNewFolder && folderFilter === '' && (
            <NewFolderInlineRow
              depth={0}
              input={newFolderInput}
              inputRef={newFolderRef}
              onChange={setNewFolderInput}
              onConfirm={confirmNewFolder}
              onCancel={() => { setShowNewFolder(false); setNewFolderInput(''); }}
            />
          )}
        </div>
      </div>

      {/* ──── CENTER: content ──── */}
      <div
        style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
        onContextMenu={(e) => { if ((e.target as HTMLElement).closest('[data-no-ctx]')) return; e.preventDefault(); openCtxMenu(e, bgCtxItems()); }}
      >
        {/* toolbar */}
        <div style={{ padding: '8px 14px', borderBottom: '1px solid #EDEDED', background: '#fff', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11, color: '#737373', fontFamily: 'ui-monospace, monospace', flexShrink: 0 }}>
            {folderFilter || '/'}
          </span>

          <input
            className="px-2.5 py-1.5 border border-[#ededed] rounded-md bg-white text-sm text-[#111] outline-none focus:border-[#111]"
            style={{ flex: 1, minWidth: 120, fontSize: 12 }}
            placeholder="Dateiname suchen…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          />

          {/* layout toggle */}
          <div style={{ display: 'flex', border: '1px solid #EDEDED', borderRadius: 6, overflow: 'hidden', flexShrink: 0 }}>
            <button
              onClick={() => setLayoutMode('grid')}
              style={{ padding: '4px 8px', background: layoutMode === 'grid' ? '#111' : '#fff', color: layoutMode === 'grid' ? '#fff' : '#737373', border: 'none', cursor: 'pointer', fontSize: 14 }}
              title="Kacheln"
            >⊞</button>
            <button
              onClick={() => setLayoutMode('list')}
              style={{ padding: '4px 8px', background: layoutMode === 'list' ? '#111' : '#fff', color: layoutMode === 'list' ? '#fff' : '#737373', border: 'none', cursor: 'pointer', fontSize: 14 }}
              title="Liste"
            >≡</button>
          </div>

          <a
            href={`/search${folderFilter ? `?folder=${encodeURIComponent(folderFilter)}` : ''}`}
            className="px-2.5 py-1 bg-white text-[#525252] text-[11px] rounded border border-[#ededed] hover:border-[#d4d4d4] no-underline"
            style={{ whiteSpace: 'nowrap', flexShrink: 0 }}
          >
            ⌕ Suchen
          </a>

          <button
            onClick={() => folderFilter ? openSuggestForFolder(folderFilter) : setUploadOpen(true)}
            disabled={suggestLoading}
            title={folderFilter ? `Ordner "${folderFilter}" mit KI analysieren` : 'Ordner auswählen oder ZIP hochladen'}
            className="px-2.5 py-1 text-[11px] rounded border"
            style={{ flexShrink: 0, background: '#f5f3ff', color: '#6d28d9', borderColor: '#ddd6fe', cursor: suggestLoading ? 'wait' : 'pointer', fontWeight: 500 }}
          >
            🤖 KI-Analyse
          </button>

          <button
            onClick={() => setUploadOpen((p) => !p)}
            className="px-2.5 py-1 bg-[#111] text-white text-[11px] rounded hover:bg-[#262626]"
            style={{ flexShrink: 0 }}
          >
            ↑ Hochladen
          </button>
        </div>

        {/* upload panel */}
        {uploadOpen && (
          <div style={{ padding: '8px 14px', borderBottom: '1px solid #EDEDED', background: '#FAFAFA', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: '#737373' }}>Upload nach:</span>
            <span style={{ fontSize: 12, fontFamily: 'ui-monospace, monospace', color: '#111', background: '#F5F5F5', border: '1px solid #EDEDED', borderRadius: 4, padding: '2px 8px' }}>
              {folderFilter || '/'}
            </span>
            {/* KI-Toggle für ZIP */}
            <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: '#525252', cursor: 'pointer', userSelect: 'none' }}>
              <input
                type="checkbox"
                checked={aiZipMode}
                onChange={(e) => setAiZipMode(e.target.checked)}
                style={{ accentColor: '#7c3aed' }}
              />
              🤖 KI-Ordner
            </label>
            <button
              className="px-2.5 py-1 bg-white text-[#262626] text-[11px] rounded border border-[#ededed] hover:border-[#d4d4d4]"
              onClick={() => zipInputRef.current?.click()}
              disabled={isUploading}
            >ZIP</button>
            <button
              className="px-2.5 py-1 bg-[#111] text-white text-[11px] rounded hover:bg-[#262626]"
              onClick={() => fileInputRef.current?.click()}
              disabled={isUploading}
            >{isUploading ? 'Lädt…' : 'Dateien'}</button>
            {uploadMsg && <span style={{ fontSize: 12, color: '#047857' }}>{uploadMsg}</span>}
            {uploadErr && <span style={{ fontSize: 12, color: '#991B1B' }}>{uploadErr}</span>}
            <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }} onChange={(e) => handleFileUpload(e.target.files)} />
            <input ref={zipInputRef} type="file" accept=".zip" style={{ display: 'none' }} onChange={(e) => handleZipUpload(e.target.files)} />
          </div>
        )}

        {/* filter tabs */}
        <div style={{ display: 'flex', borderBottom: '1px solid #EDEDED', background: '#fff', paddingLeft: 14 }}>
          {TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => { setActiveTab(tab.key); setPage(1); }}
              style={{
                padding: '7px 12px', fontSize: 12,
                fontWeight: activeTab === tab.key ? 500 : 400,
                color: activeTab === tab.key ? '#111' : '#737373',
                background: 'none', border: 'none',
                borderBottom: `2px solid ${activeTab === tab.key ? '#111' : 'transparent'}`,
                cursor: 'pointer',
              }}
            >{tab.label}</button>
          ))}
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 11, color: '#A3A3A3', alignSelf: 'center', paddingRight: 14 }}>
            {total} Dok.{totalPages > 1 ? ` · ${page}/${totalPages}` : ''}
          </span>
        </div>

        {/* content */}
        <div style={{ flex: 1, overflowY: 'auto', padding: layoutMode === 'grid' ? 14 : 0 }}>
          {docsLoading && (
            <div style={{ padding: 32, textAlign: 'center', fontSize: 13, color: '#A3A3A3' }}>Lädt…</div>
          )}
          {!docsLoading && docs.length === 0 && (
            <div style={{ padding: 32, textAlign: 'center', fontSize: 13, color: '#A3A3A3' }}>
              Keine Dokumente gefunden.
            </div>
          )}

          {/* grid */}
          {layoutMode === 'grid' && docs.length > 0 && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(195px, 1fr))', gap: 12 }}>
              {docs.map((doc) => (
                <DocumentCard
                  key={doc.id}
                  doc={doc}
                  isSelected={selectedDoc?.id === doc.id}
                  isChecked={selectedDocIds.has(doc.id)}
                  onCheck={(e) => toggleDocCheck(e, doc.id)}
                  onClick={() => setSelectedDoc(selectedDoc?.id === doc.id ? null : doc)}
                  onContextMenu={(e) => openCtxMenu(e, docCtxItems(doc))}
                />
              ))}
            </div>
          )}

          {/* list */}
          {layoutMode === 'list' && docs.length > 0 && (
            <div style={{ background: '#fff' }}>
              <div style={{
                display: 'grid', gridTemplateColumns: '36px 28px 2fr 1.2fr 60px 100px 80px',
                gap: 8, padding: '6px 14px', borderBottom: '1px solid #F5F5F5',
                fontSize: 11, fontWeight: 600, color: '#737373',
              }}>
                <span /><span /><span>Datei</span><span>Ordner</span><span>Chunks</span><span>Status</span>
                <span style={{ textAlign: 'right' }}>Größe</span>
              </div>
              {docs.map((doc) => (
                <DocumentRow
                  key={doc.id}
                  doc={doc}
                  isSelected={selectedDoc?.id === doc.id}
                  isChecked={selectedDocIds.has(doc.id)}
                  onCheck={(e) => toggleDocCheck(e, doc.id)}
                  onClick={() => setSelectedDoc(selectedDoc?.id === doc.id ? null : doc)}
                  onContextMenu={(e) => openCtxMenu(e, docCtxItems(doc))}
                />
              ))}
            </div>
          )}

          {/* pagination */}
          {totalPages > 1 && (
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', padding: layoutMode === 'list' ? '12px 14px' : '12px 0' }}>
              <button
                className="px-3 py-1.5 bg-white text-[#262626] text-xs rounded border border-[#ededed] hover:border-[#d4d4d4]"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >←</button>
              <span style={{ fontSize: 12, color: '#737373', alignSelf: 'center' }}>Seite {page} / {totalPages}</span>
              <button
                className="px-3 py-1.5 bg-white text-[#262626] text-xs rounded border border-[#ededed] hover:border-[#d4d4d4]"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >→</button>
            </div>
          )}
        </div>
      </div>

      {/* ──── RIGHT: Inspector ──── */}
      {selectedDoc && (
        <InspectorPanel
          doc={selectedDoc}
          onClose={() => setSelectedDoc(null)}
          onDocUpdated={setSelectedDoc}
        />
      )}

      {/* ──── Context Menu ──── */}
      {ctxMenu && (
        <ContextMenu
          x={ctxMenu.x}
          y={ctxMenu.y}
          items={ctxMenu.items}
          onClose={() => setCtxMenu(null)}
        />
      )}

      {/* ──── Floating Selection Bar ──── */}
      {selectedDocIds.size > 0 && (
        <div
          data-no-ctx
          style={{
            position: 'fixed', bottom: 24, left: '50%', transform: 'translateX(-50%)',
            background: '#111', color: '#fff', borderRadius: 10, padding: '10px 16px',
            display: 'flex', alignItems: 'center', gap: 12,
            boxShadow: '0 8px 32px rgba(0,0,0,0.25)', zIndex: 400,
            fontSize: 13,
          }}
        >
          <span style={{ color: '#a3a3a3' }}>{selectedDocIds.size} ausgewählt</span>
          <button
            onClick={openSuggestForSelection}
            style={{ background: '#7c3aed', border: 'none', borderRadius: 6, color: '#fff', fontSize: 12, fontWeight: 500, padding: '5px 12px', cursor: 'pointer' }}
          >
            🤖 Ordner vorschlagen
          </button>
          <button
            onClick={() => setSelectedDocIds(new Set())}
            style={{ background: 'none', border: '1px solid #525252', borderRadius: 6, color: '#a3a3a3', fontSize: 12, padding: '4px 10px', cursor: 'pointer' }}
          >
            Auswahl aufheben
          </button>
        </div>
      )}

      {/* ──── Suggest Modal ──── */}
      {suggestOpen && (
        <SuggestModal
          isLoading={suggestLoading}
          suggestions={suggestResponse?.suggestions ?? []}
          isZip={pendingZipFile !== null}
          isApplying={suggestApplying}
          onConfirm={confirmSuggestions}
          onCancel={() => { setSuggestOpen(false); setPendingZipFile(null); setSuggestResponse(null); }}
        />
      )}

    </div>
  );
}
