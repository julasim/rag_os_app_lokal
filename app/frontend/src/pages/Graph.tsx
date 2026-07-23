import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore — Lib bringt Typen mit, die d3-Transitiv-Typen zicken aber unter tsc.
import ForceGraph2D from 'react-force-graph-2d';
import { getGraph, rebuildGraph, type GNode, type GraphData } from '../api/graph';
import { apiGet } from '../api/client';
import type { HealthResponse } from '../types';

const TYPE_COLORS: Record<string, string> = {
  document: '#2563eb',
  norm: '#059669',
  legal: '#7c3aed',
  tag: '#d97706',
  issuer: '#dc2626',
  folder: '#9ca3af',
};
const TYPE_LABELS: Record<string, string> = {
  document: 'Dokument', norm: 'Norm', legal: 'Rechtsstelle', tag: 'Tag',
  issuer: 'Aussteller', folder: 'Ordner',
};
const RELATION_LABELS: Record<string, string> = {
  references: 'Verweist auf', supersedes: 'Löst ab', issued_by: 'Ausgestellt von',
  has_tag: 'Tag', in_folder: 'Ordner', similar_to: 'Ähnlich zu', near_dup: 'Fast-Duplikat',
};
const COMMUNITY_PALETTE = [
  '#2563eb', '#059669', '#d97706', '#dc2626', '#7c3aed', '#0891b2',
  '#ca8a04', '#be185d', '#4d7c0f', '#9333ea', '#0d9488', '#b45309',
];

function communityColor(c: number | null): string {
  if (c == null) return '#9ca3af';
  return COMMUNITY_PALETTE[Math.abs(c) % COMMUNITY_PALETTE.length];
}
function withAlpha(hex: string, a: number): string {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${a})`;
}
const endId = (e: unknown): string => (typeof e === 'object' && e !== null ? (e as { id: string }).id : (e as string));

export default function Graph() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const fgRef = useRef<any>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [colorMode, setColorMode] = useState<'type' | 'community'>('type');
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');
  const [nodeScale, setNodeScale] = useState(3);   // Knotengröße (nodeRelSize), per Slider

  const { data, isLoading, error } = useQuery<GraphData>({
    queryKey: ['graph'],
    queryFn: () => getGraph(),
    staleTime: 30_000,
  });

  // Rolle: auf dem Leser gibt es keinen Graph-Bau (query-only) → Buttons ausblenden.
  const { data: health } = useQuery<HealthResponse>({
    queryKey: ['health'],
    queryFn: () => apiGet('/api/health'),
    staleTime: 60_000,
  });
  const isReader = health?.role === 'reader';

  const rebuild = useMutation({
    mutationFn: rebuildGraph,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['graph'] }),
  });

  // Canvas-Größe an den Container koppeln.
  useEffect(() => {
    if (!wrapRef.current) return;
    const el = wrapRef.current;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, [data]);

  // Stabile Kopie für die Simulation (react-force-graph mutiert die Objekte).
  const graphData = useMemo(() => {
    if (!data) return { nodes: [], links: [] };
    return {
      nodes: data.nodes.map((n) => ({ ...n })),
      links: data.edges.map((e) => ({ ...e })),
    };
  }, [data]);

  const nodeById = useMemo(() => {
    const m = new Map<string, GNode>();
    (data?.nodes ?? []).forEach((n) => m.set(n.id, n));
    return m;
  }, [data]);

  // Nachbarn des ausgewählten Knotens (aus den Original-Kanten mit String-IDs).
  const selected = selectedId ? nodeById.get(selectedId) ?? null : null;
  const neighborIds = useMemo(() => {
    const s = new Set<string>();
    if (!selectedId || !data) return s;
    for (const e of data.edges) {
      if (e.source === selectedId) s.add(e.target);
      else if (e.target === selectedId) s.add(e.source);
    }
    return s;
  }, [selectedId, data]);

  // Nach Relation gruppierte Nachbarn fürs Detail-Panel.
  const grouped = useMemo(() => {
    const out: { relation: string; other: GNode; asDoc: boolean }[] = [];
    if (!selectedId || !data) return out;
    for (const e of data.edges) {
      let otherId: string | null = null;
      let otherIsSource = false;
      if (e.source === selectedId) { otherId = e.target; }
      else if (e.target === selectedId) { otherId = e.source; otherIsSource = true; }
      if (!otherId) continue;
      const other = nodeById.get(otherId);
      if (other) out.push({ relation: e.relation, other, asDoc: otherIsSource });
    }
    return out;
  }, [selectedId, data, nodeById]);

  function focus(id: string) {
    setSelectedId(id);
    const n: any = graphData.nodes.find((x: any) => x.id === id);
    if (n && fgRef.current && n.x != null) {
      fgRef.current.centerAt(n.x, n.y, 500);
      fgRef.current.zoom(4, 500);
    }
  }

  function doSearch() {
    const q = search.trim().toLowerCase();
    if (!q || !data) return;
    const hit = data.nodes.find((n) => n.label.toLowerCase().includes(q));
    if (hit) focus(hit.id);
  }

  const colorOf = (n: any): string => {
    const base = colorMode === 'community' ? communityColor(n.community) : (TYPE_COLORS[n.type] || '#9ca3af');
    if (!selectedId) return base;
    if (n.id === selectedId || neighborIds.has(n.id)) return base;
    return withAlpha(base, 0.1);
  };
  const linkColorOf = (l: any): string => {
    if (!selectedId) return 'rgba(160,160,160,0.35)';
    const s = endId(l.source), t = endId(l.target);
    return s === selectedId || t === selectedId ? 'rgba(60,60,60,0.9)' : 'rgba(200,200,200,0.06)';
  };

  const nodes = data?.nodes ?? [];
  const typesPresent = useMemo(() => Array.from(new Set(nodes.map((n) => n.type))), [nodes]);

  if (isLoading) return <p style={{ fontSize: 13, color: '#a3a3a3' }}>Lädt…</p>;
  if (error) return <p style={{ fontSize: 13, color: '#991b1b' }}>{(error as Error).message}</p>;

  const empty = nodes.length === 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: 'calc(100vh - 120px)' }}>
      {/* Steuerleiste */}
      <div className="bg-white border border-[#ededed] rounded-lg" style={{ padding: '10px 12px', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 6 }}>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && doSearch()}
            placeholder="Knoten suchen…"
            style={{ fontSize: 13, padding: '5px 9px', border: '1px solid #ededed', borderRadius: 6, width: 180 }}
          />
          <button onClick={doSearch} className="px-3 py-1.5 bg-white text-[#262626] text-xs font-medium rounded-md border border-[#ededed] cursor-pointer hover:border-[#d4d4d4]">Finden</button>
        </div>

        {/* Typ-Filter */}
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {typesPresent.map((t) => {
            const on = !hiddenTypes.has(t);
            return (
              <button
                key={t}
                onClick={() => setHiddenTypes((prev) => { const n = new Set(prev); n.has(t) ? n.delete(t) : n.add(t); return n; })}
                style={{
                  fontSize: 11, padding: '3px 8px', borderRadius: 999, cursor: 'pointer',
                  border: `1px solid ${on ? TYPE_COLORS[t] : '#ededed'}`,
                  background: on ? withAlpha(TYPE_COLORS[t] || '#9ca3af', 0.12) : '#fff',
                  color: on ? '#111' : '#a3a3a3',
                }}
              >
                <span style={{ display: 'inline-block', width: 7, height: 7, borderRadius: 999, background: TYPE_COLORS[t] || '#9ca3af', marginRight: 5 }} />
                {TYPE_LABELS[t] || t}
              </button>
            );
          })}
        </div>

        <button
          onClick={() => setColorMode((m) => (m === 'type' ? 'community' : 'type'))}
          className="px-3 py-1.5 bg-white text-[#262626] text-xs font-medium rounded-md border border-[#ededed] cursor-pointer hover:border-[#d4d4d4]"
        >
          Farbe: {colorMode === 'type' ? 'Typ' : 'Community'}
        </button>

        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#525252' }}>
          Knotengröße
          <input
            type="range" min={1} max={8} step={0.5} value={nodeScale}
            onChange={(e) => setNodeScale(Number(e.target.value))}
            style={{ width: 96 }}
          />
        </label>

        <div style={{ marginLeft: 'auto', display: 'flex', gap: 10, alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: '#a3a3a3' }}>
            {data?.stats.truncated
              ? `Top ${data.stats.nodes} von ${data.stats.total_nodes} Knoten`
              : `${data?.stats.nodes ?? 0} Knoten`} · {data?.stats.edges ?? 0} Kanten · {data?.stats.communities ?? 0} Communities
          </span>
          {!isReader && (
            <button
              onClick={() => rebuild.mutate()}
              disabled={rebuild.isPending}
              className="px-3 py-1.5 bg-[#111] text-white text-xs font-medium rounded-md cursor-pointer disabled:opacity-50"
            >
              {rebuild.isPending ? 'Baue…' : 'Graph neu bauen'}
            </button>
          )}
        </div>
      </div>
      {rebuild.isError && <p style={{ fontSize: 12, color: '#991b1b' }}>{(rebuild.error as Error).message}</p>}

      {/* Graph + Detail-Panel */}
      <div style={{ display: 'flex', gap: 12, flex: 1, minHeight: 0 }}>
        <div ref={wrapRef} className="bg-white border border-[#ededed] rounded-lg" style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
          {empty ? (
            <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'center', justifyContent: 'center', padding: 24, textAlign: 'center' }}>
              <p style={{ fontSize: 14, color: '#262626', fontWeight: 600 }}>Der Wissensgraph ist noch leer</p>
              <p style={{ fontSize: 13, color: '#a3a3a3', maxWidth: 420 }}>
                Er entsteht aus den indexierten Dokumenten (Norm-Verweise, Tags, Aussteller, Ähnlichkeiten).
                {isReader
                  ? ' Der Graph wird auf dem Schreiber gebaut; sobald das passiert ist, erscheint er hier.'
                  : ' Baue ihn jetzt, dann liegt er im Vault und der Leser sieht ihn ebenfalls.'}
              </p>
              {!isReader && (
                <button onClick={() => rebuild.mutate()} disabled={rebuild.isPending} className="px-3 py-1.5 bg-[#111] text-white text-xs font-medium rounded-md cursor-pointer disabled:opacity-50">
                  {rebuild.isPending ? 'Baue…' : 'Graph jetzt bauen'}
                </button>
              )}
            </div>
          ) : (
            <ForceGraph2D
              ref={fgRef}
              width={size.w}
              height={size.h}
              graphData={graphData}
              nodeId="id"
              nodeLabel={(n: any) => `${n.label} · ${TYPE_LABELS[n.type] || n.type}`}
              nodeVal={(n: any) => 0.4 + (n.pagerank || 0) * 30}
              nodeRelSize={nodeScale}
              nodeColor={colorOf}
              nodeVisibility={(n: any) => !hiddenTypes.has(n.type)}
              linkVisibility={(l: any) => {
                const s = nodeById.get(endId(l.source)); const t = nodeById.get(endId(l.target));
                return !!s && !!t && !hiddenTypes.has(s.type) && !hiddenTypes.has(t.type);
              }}
              linkColor={linkColorOf}
              linkWidth={(l: any) => (selectedId && (endId(l.source) === selectedId || endId(l.target) === selectedId) ? 1.6 : 0.4)}
              onNodeClick={(n: any) => focus(n.id)}
              onNodeHover={(n: any) => setHoverId(n ? n.id : null)}
              onBackgroundClick={() => setSelectedId(null)}
              nodeCanvasObjectMode={() => 'after'}
              nodeCanvasObject={(node: any, ctx: CanvasRenderingContext2D, scale: number) => {
                const show = node.id === selectedId || node.id === hoverId || neighborIds.has(node.id) || scale > 2.6;
                if (!show) return;
                const fs = Math.max(3, 11 / scale);
                ctx.font = `${fs}px Arimo, "Helvetica Neue", Helvetica, Arial, sans-serif`;
                ctx.textAlign = 'center';
                ctx.fillStyle = '#111';
                const t = node.label.length > 30 ? node.label.slice(0, 29) + '…' : node.label;
                ctx.fillText(t, node.x, node.y + 7 + fs);
              }}
              cooldownTicks={120}
              onEngineStop={() => fgRef.current?.zoomToFit?.(400, 40)}
            />
          )}
        </div>

        {/* Detail-Panel */}
        <div className="bg-white border border-[#ededed] rounded-lg" style={{ width: 320, flexShrink: 0, overflow: 'auto', padding: 14 }}>
          {!selected ? (
            <p style={{ fontSize: 13, color: '#a3a3a3' }}>Knoten anklicken, um die Verknüpfungen zu sehen.</p>
          ) : (
            <DetailPanel selected={selected} grouped={grouped} onFocus={focus} onOpenDoc={(id) => navigate('/documents?doc=' + id)} />
          )}
        </div>
      </div>
    </div>
  );
}

function DetailPanel({
  selected, grouped, onFocus, onOpenDoc,
}: {
  selected: GNode;
  grouped: { relation: string; other: GNode; asDoc: boolean }[];
  onFocus: (id: string) => void;
  onOpenDoc: (docId: string) => void;
}) {
  const isEntity = selected.type !== 'document';
  // Dokumente, in denen diese Entität vorkommt (Entität ist tgt der Kante -> asDoc=true).
  const docsWith = grouped.filter((g) => g.asDoc && g.other.type === 'document');
  // Relations-Gruppierung für die übrigen Nachbarn.
  const byRelation = new Map<string, GNode[]>();
  for (const g of grouped) {
    if (isEntity && g.asDoc && g.other.type === 'document') continue; // oben separat
    const arr = byRelation.get(g.relation) || [];
    if (!arr.find((x) => x.id === g.other.id)) arr.push(g.other);
    byRelation.set(g.relation, arr);
  }

  const NodeRow = ({ n }: { n: GNode }) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '3px 0' }}>
      <span style={{ width: 7, height: 7, borderRadius: 999, background: TYPE_COLORS[n.type] || '#9ca3af', flexShrink: 0 }} />
      <button onClick={() => onFocus(n.id)} title="Im Graph fokussieren"
        style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: '#262626', fontSize: 12.5, textAlign: 'left', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {n.label}
      </button>
      {n.type === 'document' && n.doc_id && (
        <button onClick={() => onOpenDoc(n.doc_id!)} title="Dokument öffnen"
          style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: '#2563eb', fontSize: 11 }}>→ öffnen</button>
      )}
    </div>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 11, fontWeight: 600, color: '#fff', background: TYPE_COLORS[selected.type] || '#9ca3af', padding: '2px 7px', borderRadius: 999 }}>
            {TYPE_LABELS[selected.type] || selected.type}
          </span>
        </div>
        <p style={{ fontSize: 14, fontWeight: 600, color: '#111', marginTop: 8, wordBreak: 'break-word' }}>{selected.label}</p>
        <p style={{ fontSize: 11, color: '#a3a3a3', marginTop: 2 }}>
          Community {selected.community ?? '–'} · PageRank {selected.pagerank.toFixed(4)}
        </p>
      </div>

      {isEntity && (
        <div>
          <p style={{ fontSize: 12, fontWeight: 600, color: '#111', marginBottom: 4 }}>
            Kommt in {docsWith.length} Dokument{docsWith.length === 1 ? '' : 'en'} vor
          </p>
          {docsWith.length === 0
            ? <p style={{ fontSize: 12, color: '#a3a3a3' }}>—</p>
            : docsWith.map((g) => <NodeRow key={g.other.id} n={g.other} />)}
        </div>
      )}

      {Array.from(byRelation.entries()).map(([rel, list]) => (
        <div key={rel}>
          <p style={{ fontSize: 12, fontWeight: 600, color: '#111', marginBottom: 4 }}>
            {RELATION_LABELS[rel] || rel} <span style={{ color: '#a3a3a3', fontWeight: 400 }}>({list.length})</span>
          </p>
          {list.map((n) => <NodeRow key={n.id} n={n} />)}
        </div>
      ))}
    </div>
  );
}
