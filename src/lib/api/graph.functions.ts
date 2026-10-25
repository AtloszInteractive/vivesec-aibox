// UI data layer for relationship analysis (E26) over /api/v1/ui/graph/*.
// Plain async functions like rag.functions.ts: the embedded UI reaches the
// adapter through the ViVeSecBox tunnel, never through framework routes.

import {
  adapterGraph,
  AdapterGraphError,
  adapterSkipped,
  type AdapterSkipped,
} from "../rag/adapter-client";

export type IndexSkipped = AdapterSkipped;

export type GraphEntityType =
  | "person"
  | "organization"
  | "place"
  | "identifier"
  | "event"
  | "object";

export type GraphAttribute = { name: string; value: string; doc_id: string; page?: number | null };

export type GraphNode = {
  id: string;
  type: GraphEntityType;
  subtype: string;
  label: string;
  aliases: string[];
  mentions: number;
  documents: string[];
  attributes: GraphAttribute[];
};

export type GraphEvidence = {
  doc_id: string;
  page?: number | null;
  quote: string;
  method: string;
  confidence: number;
};

export type GraphEdge = {
  id: string;
  type: string;
  a: string;
  b: string;
  start: string | null;
  end: string | null;
  raw_label: string;
  weak: boolean;
  confidence: number;
  evidence: GraphEvidence[];
};

export type GraphDocument = { corpus_id: string; source_path: string; title: string; kind: string };
export type GraphDocuments = Record<string, GraphDocument>;

export type GraphTimelineItem = {
  date: string;
  end: string | null;
  type: string;
  other: GraphNode;
  evidence: GraphEvidence[];
};

export type GraphLabel = Partial<Record<"en" | "hu" | "da" | "de", string>>;
export type GraphProfile = {
  id: string;
  version: number;
  name: GraphLabel;
  description?: string;
  entity_subtypes: { id: string; type: GraphEntityType; label: GraphLabel }[];
  relation_types: {
    id: string;
    label: GraphLabel;
    from: GraphEntityType[];
    to: GraphEntityType[];
    synonyms: string[];
  }[];
} & Record<string, unknown>;

export type GraphReviewItem = {
  a: string;
  b: string;
  score: number;
  reason: string;
  doc_id: string;
  names: string[];
  a_label?: string;
  b_label?: string;
};

export type GraphStatus = {
  queued: number;
  busy: boolean;
  running: { source_path: string; started: number } | null;
  totals: Record<string, number>;
  recent: {
    doc_id: string;
    source_path: string;
    status: string;
    error?: string;
    stats?: Record<string, unknown>;
  }[];
  forward: { pending: number; counts: Record<string, number> };
  gpu?: { foreground_active: number; background_active: boolean; background_waiting: number };
};

/** Why a graph call failed, as the UI shows it. */
export type GraphFailure =
  | "disabled"
  | "extraction_off"
  | "forbidden"
  | "syncing"
  | "purpose"
  | "error";

export function graphFailure(err: unknown): GraphFailure {
  if (err instanceof AdapterGraphError) {
    if (err.status === 428) return "purpose";
    if (err.status === 403) return "forbidden";
    if (err.status === 503 && /synchronis/i.test(err.message)) return "syncing";
    if (err.status === 503 && /extraction/i.test(err.message)) return "extraction_off";
    if (err.status === 503) return "disabled";
  }
  return "error";
}

type WithDocs<T> = T & { documents: GraphDocuments };

function query<T>(drive: string, name: string, params: Record<string, unknown>) {
  return adapterGraph<WithDocs<T>>("query", { query: name, params }, drive);
}

export const graphApi = {
  find: (drive: string, text: string) =>
    query<{ entities: GraphNode[] }>(drive, "find", { text, limit: 30 }),
  entity: (drive: string, id: string) =>
    query<{ entity: GraphNode; edges: GraphEdge[] }>(drive, "entity", { id }),
  neighborhood: (drive: string, id: string, hops: number) =>
    query<{ nodes: GraphNode[]; edges: GraphEdge[]; truncated: boolean }>(drive, "neighborhood", {
      id,
      hops,
      limit: 150,
    }),
  path: (drive: string, a: string, b: string) =>
    query<{ path: { nodes: GraphNode[]; edges: GraphEdge[]; hops: number } | null }>(
      drive,
      "path",
      { a, b, max_hops: 6 },
    ),
  timeline: (drive: string, id: string) =>
    query<{ timeline: GraphTimelineItem[] }>(drive, "timeline", { id }),
  status: (drive: string) => adapterGraph<GraphStatus>("status", {}, drive),
  /** E10b: what the search cannot read in this scope (not a graph call). */
  skipped: (drive: string) => adapterSkipped(drive),
  reviewList: (drive: string) =>
    adapterGraph<{ review: GraphReviewItem[]; total: number }>(
      "review/list",
      { limit: 200 },
      drive,
    ),
  reviewDecide: (drive: string, a: string, b: string, decision: "merge" | "separate") =>
    adapterGraph<{ decided: string }>("review/decide", { a, b, decision }, drive),
  profile: (drive: string) =>
    adapterGraph<{ profile: GraphProfile; templates: unknown[]; can_edit: boolean }>(
      "profile/get",
      {},
      drive,
    ),
  saveProfile: (drive: string, profile: unknown) =>
    adapterGraph<{ profile: GraphProfile }>("profile/set", { profile }, drive),
  dossier: (drive: string, id: string, format: "docx" | "pdf", lang: string) =>
    adapterGraph<GraphDossier>("dossier", { id, format, lang }, drive),
  reprocessStatus: (drive: string) =>
    adapterGraph<GraphReprocessStatus>("reprocess/status", {}, drive),
  reprocessStart: (drive: string, pathPrefix: string) =>
    adapterGraph<{ run: GraphReprocessRun }>(
      "reprocess/start",
      { path_prefix: pathPrefix || undefined },
      drive,
    ),
  reprocessCancel: (drive: string) =>
    adapterGraph<{ run: GraphReprocessRun }>("reprocess/cancel", {}, drive),
};

/** A reprocessing run after a profile change (adapter/reprocess.py). */
export type GraphReprocessRun = {
  state: "idle" | "running" | "done" | "cancelled" | "failed";
  started_by?: string;
  profile?: string;
  path_prefix?: string | null;
  total?: number;
  done?: number;
  sent?: number;
  missing?: number;
  failed?: number;
  current?: string;
  error?: string | null;
};

/** Which profile the documents of this scope were extracted with. */
export type GraphReprocessStatus = {
  current: string;
  counts: Record<string, number>;
  stale_total: number;
  can_start: boolean;
  source: "local" | "ws-fs";
  source_ready: boolean;
  run: GraphReprocessRun;
};

/** A rendered entity dossier: the file (base64) and its markdown source. */
export type GraphDossier = {
  name: string;
  format: string;
  content_type: string;
  size: number;
  text: string;
  content_b64: string;
};
