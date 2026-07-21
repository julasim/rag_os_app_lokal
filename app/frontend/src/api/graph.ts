import { apiGet, apiPost } from './client';

export interface GNode {
  id: string;
  type: string;            // document | norm | legal | tag | issuer | folder
  label: string;
  community: number | null;
  pagerank: number;
  doc_id: string | null;   // nur document-Nodes
}

export interface GEdge {
  source: string;
  target: string;
  relation: string;        // references | supersedes | issued_by | has_tag | in_folder | similar_to | near_dup
  weight: number;
}

export interface GraphData {
  nodes: GNode[];
  edges: GEdge[];
  stats: Record<string, number>;   // { nodes, edges, communities }
}

/** Wissensgraph als Knoten + Kanten (Admin). `types` = CSV-Filter auf node_type. */
export const getGraph = (types?: string) =>
  apiGet<GraphData>('/api/graph', types ? { types } : undefined);

/** Baut den Graphen neu (L1 -> L2 -> Analyse). Liefert Statistiken. */
export const rebuildGraph = () =>
  apiPost<Record<string, unknown>>('/api/graph/rebuild');
