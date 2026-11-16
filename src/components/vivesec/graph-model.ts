// Pure transformations of the relationship picture (E26): merging an added
// neighbourhood, grouping the leaves of crowded nodes, where a relation comes
// from, which data is an informant's (protected), and finding names.

import type { GraphDocuments, GraphEdge, GraphNode } from "@/lib/api/graph.functions";

export type ViewGraph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  documents: GraphDocuments;
  truncated?: boolean;
};

/** `b` added to `a`: one node per id (the newer copy), one edge per id. */
export function mergeGraphs<T extends ViewGraph>(a: T, b: ViewGraph): T {
  const nodes = new Map(a.nodes.filter(Boolean).map((n) => [n.id, n]));
  for (const n of b.nodes) if (n) nodes.set(n.id, n);
  const edges = new Map(a.edges.map((e) => [e.id, e]));
  for (const e of b.edges) edges.set(e.id, e);
  return {
    ...a,
    nodes: [...nodes.values()],
    edges: [...edges.values()],
    documents: { ...a.documents, ...(b.documents ?? {}) },
    truncated: Boolean(a.truncated || b.truncated),
  };
}

/** The protected part of a structured record (E29) lives under this folder. */
const PROTECTED_SEGMENT = "/~protected/";

export function isProtectedDoc(documents: GraphDocuments, docId: string): boolean {
  return (documents[docId]?.source_path ?? "").includes(PROTECTED_SEGMENT);
}

/** Relations shown only because of an informant's (protected) record. */
export function isProtectedEdge(documents: GraphDocuments, edge: GraphEdge): boolean {
  return edge.evidence.some((ev) => isProtectedDoc(documents, ev.doc_id));
}

/** A person or entry known only from protected records. */
export function isProtectedNode(documents: GraphDocuments, node: GraphNode): boolean {
  return node.documents.length > 0 && node.documents.every((d) => isProtectedDoc(documents, d));
}

/** Where the relations of one pair come from, the strongest first: a register
 *  (a structured export), the text of a document, or only being named together. */
export type EdgeSource = "register" | "text" | "comention";

export function edgeSource(list: GraphEdge[]): EdgeSource {
  const methods = new Set(list.flatMap((e) => e.evidence.map((ev) => ev.method)));
  if (methods.has("structured")) return "register";
  if (list.every((e) => e.weak)) return "comention";
  return "text";
}

export type Cluster = {
  id: string;
  hub: string;
  members: string[];
  type: GraphNode["type"];
  subtype: string;
};

/** The leaves of a crowded node (a cell with its forty inmates) as one bubble
 *  per kind: a node with at least `minDegree` neighbours whose neighbours of one
 *  type and subtype that link to nothing else number `minGroup` or more. `keep`
 *  stays apart (the focus, picked nodes), `open` hubs are not grouped. */
export function clusterLeaves(
  nodes: GraphNode[],
  edges: GraphEdge[],
  keep: Set<string>,
  open: Set<string>,
  minDegree = 8,
  minGroup = 4,
): { nodes: GraphNode[]; edges: GraphEdge[]; clusters: Map<string, Cluster> } {
  const adjacent = new Map<string, Set<string>>();
  for (const e of edges) {
    if (e.a === e.b) continue;
    adjacent.set(e.a, (adjacent.get(e.a) ?? new Set()).add(e.b));
    adjacent.set(e.b, (adjacent.get(e.b) ?? new Set()).add(e.a));
  }
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const clusters = new Map<string, Cluster>();
  const memberOf = new Map<string, string>();
  for (const [hub, around] of adjacent) {
    if (around.size < minDegree || open.has(hub) || keep.has(hub)) continue;
    const groups = new Map<string, string[]>();
    for (const leaf of around) {
      const node = byId.get(leaf);
      if (!node || keep.has(leaf) || adjacent.get(leaf)?.size !== 1) continue;
      const kind = `${node.type}:${node.subtype}`;
      groups.set(kind, [...(groups.get(kind) ?? []), leaf]);
    }
    for (const [kind, members] of groups) {
      if (members.length < minGroup) continue;
      const first = byId.get(members[0])!;
      const id = `cluster:${hub}:${kind}`;
      clusters.set(id, { id, hub, members, type: first.type, subtype: first.subtype });
      for (const m of members) memberOf.set(m, id);
    }
  }
  if (!clusters.size) return { nodes, edges, clusters };
  const outNodes = nodes.filter((n) => !memberOf.has(n.id));
  for (const c of clusters.values()) {
    outNodes.push({
      id: c.id,
      type: c.type,
      subtype: c.subtype,
      label: "",
      aliases: [],
      mentions: c.members.reduce((sum, m) => sum + (byId.get(m)?.mentions ?? 1), 0),
      documents: [],
      attributes: [],
    });
  }
  const outEdges: GraphEdge[] = [];
  const grouped = new Map<string, GraphEdge[]>();
  for (const e of edges) {
    const cluster = memberOf.get(e.a) ?? memberOf.get(e.b);
    if (cluster) grouped.set(cluster, [...(grouped.get(cluster) ?? []), e]);
    else outEdges.push(e);
  }
  for (const [id, list] of grouped) {
    const types = new Map<string, number>();
    for (const e of list) types.set(e.type, (types.get(e.type) ?? 0) + 1);
    const type = [...types.entries()].sort((x, y) => y[1] - x[1])[0][0];
    const first = list[0];
    const starts = list.map((e) => e.start).filter(Boolean) as string[];
    const ends = list.map((e) => e.end);
    outEdges.push({
      id: `edge:${id}`,
      type,
      a: memberOf.has(first.a) ? id : first.a,
      b: memberOf.has(first.b) ? id : first.b,
      start: starts.length ? starts.sort()[0] : null,
      end: ends.some((x) => !x) ? null : (ends as string[]).sort()[ends.length - 1],
      raw_label: "",
      weak: list.every((e) => e.weak),
      confidence: 1,
      evidence: list.flatMap((e) => e.evidence.slice(0, 1)),
    });
  }
  return { nodes: outNodes, edges: outEdges, clusters };
}

/** Case- and accent-insensitive form for finding names and numbers. */
export function fold(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

/** Nodes whose name, other names or attribute values contain the query. */
export function findNodes(nodes: GraphNode[], query: string): string[] {
  const q = fold(query);
  if (!q) return [];
  return nodes
    .filter((n) =>
      [n.label, ...n.aliases, ...n.attributes.map((a) => a.value)].some((s) =>
        fold(String(s ?? "")).includes(q),
      ),
    )
    .map((n) => n.id);
}
