// UI data layer for relationship analysis (E26) over /api/v1/ui/graph/*.
// Plain async functions like rag.functions.ts: the embedded UI reaches the
// adapter through the ViVeSecBox tunnel, never through framework routes.

import {
  adapterGraph,
  AdapterGraphError,
  adapterQueryLog,
  adapterSkipped,
  type AdapterQueryLog,
  type AdapterQueryLogRecord,
  type AdapterSkipped,
} from "../rag/adapter-client";

export type IndexSkipped = AdapterSkipped;
export type QueryLog = AdapterQueryLog;
export type QueryLogRecord = AdapterQueryLogRecord;

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

/** Who exported a picture, for which case and when -- as the box knows it. */
export type GraphWatermark = {
  user: string;
  case_ref?: string | null;
  legal_basis?: string | null;
  at: string;
};

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
    symmetric?: boolean;
    presence?: boolean;
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

export type GraphReviewList = {
  review: GraphReviewItem[];
  /** After the name and reason filters. */
  total: number;
  /** Every open pair in the caller's scope. */
  open_total: number;
  reasons: Record<string, number>;
};

export type GraphReviewDecision = {
  a: string;
  b: string;
  decision: "merge" | "separate";
  by: string;
  at: number | null;
  score: number | null;
  reason: string;
  a_label: string;
  b_label: string;
  undoable: boolean;
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
  /** The query log: the caller's own records, everyone's for a leader (not a graph call). */
  queryLog: (
    drive: string,
    filter: {
      from?: string;
      to?: string;
      user?: string;
      caseRef?: string;
      offset?: number;
      before?: string;
    },
  ) => adapterQueryLog(drive, filter),
  reviewList: (drive: string, filter: { q?: string; reason?: string; offset?: number } = {}) =>
    adapterGraph<GraphReviewList>("review/list", { limit: 200, ...filter }, drive),
  reviewDecide: (drive: string, a: string, b: string, decision: "merge" | "separate") =>
    adapterGraph<{ decided: string }>("review/decide", { a, b, decision }, drive),
  reviewDecideMany: (
    drive: string,
    pairs: { a: string; b: string }[],
    decision: "merge" | "separate",
  ) =>
    adapterGraph<{
      decided: string;
      done: number;
      skipped: { a: string; b: string; error: string }[];
    }>("review/decide", { pairs, decision }, drive),
  reviewHistory: (drive: string, offset = 0) =>
    adapterGraph<{ history: GraphReviewDecision[]; total: number; can_undo: boolean }>(
      "review/history",
      { limit: 100, offset },
      drive,
    ),
  reviewUndo: (drive: string, a: string, b: string) =>
    adapterGraph<{ undone: string; documents: number; failed: unknown[] }>(
      "review/undo",
      { a, b },
      drive,
    ),
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
  /** Log a picture export (purpose-bound) and get the watermark it must carry. */
  imageLog: (
    drive: string,
    req: { format: "png" | "svg"; view: "2d" | "3d"; nodes: number; edges: number },
  ) => adapterGraph<GraphWatermark>("image/log", req, drive),
  /** One investigation table as XLSX, rebuilt on the box within the caller's
   *  current access: "download" returns the file, "drive" saves it like /ui/save. */
  investigationExport: (
    drive: string,
    req: {
      start: string;
      levels: number;
      drives?: string[];
      auditId?: string;
      lang: string;
      deliver: "download" | "drive";
      sharedContact?: boolean;
    },
  ) =>
    adapterGraph<GraphInvestigationExport>(
      "investigation/export",
      {
        start: req.start,
        levels: req.levels,
        drives: req.drives?.length ? req.drives : undefined,
        audit_id: req.auditId,
        lang: req.lang,
        deliver: req.deliver,
        shared_contact: req.sharedContact || undefined,
      },
      drive,
    ),
  /** The subgraph of one investigation table (BV 1.3 / 2.3), rebuilt on the box
   *  within the caller's current access, in the neighbourhood shape. */
  investigationGraph: (
    drive: string,
    req: {
      start: string;
      levels: number;
      drives?: string[];
      auditId?: string;
      sharedContact?: boolean;
    },
  ) =>
    adapterGraph<GraphInvestigationView>(
      "investigation/graph",
      {
        start: req.start,
        levels: req.levels,
        drives: req.drives?.length ? req.drives : undefined,
        audit_id: req.auditId,
        shared_contact: req.sharedContact || undefined,
      },
      drive,
    ),
  /** BV 1.4 / 2.4: what the documents say about each person of an investigation
   *  table, as a background task. The box rebuilds the table when the task runs;
   *  the result comes back as a finished background task (an answer card). */
  investigationSummaries: (
    drive: string,
    req: {
      start: string;
      levels: number;
      drives?: string[];
      auditId?: string;
      lang: string;
      sharedContact?: boolean;
    },
  ) =>
    adapterGraph<GraphInvestigationSummaries>(
      "investigation/summaries",
      {
        start: req.start,
        levels: req.levels,
        drives: req.drives?.length ? req.drives : undefined,
        audit_id: req.auditId,
        lang: req.lang,
        shared_contact: req.sharedContact || undefined,
      },
      drive,
    ),
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
  /** E28: the structured sources of the profile (graph administrators). */
  importSources: (drive: string) =>
    adapterGraph<{ sources: GraphImportSource[] }>("import/sources", {}, drive),
  /** E28: one periodic export into a folder; answers within `wait` seconds or
   *  with the job still running (then `importStatus`). */
  importRecords: (
    drive: string,
    req: {
      source: string;
      asOf: string;
      folder: string;
      filename: string;
      contentB64: string;
      mode?: "delta" | "full";
      force?: boolean;
    },
  ) =>
    adapterGraph<GraphImportAnswer>(
      "import",
      {
        source: req.source,
        as_of: req.asOf,
        folder: req.folder,
        filename: req.filename,
        content_b64: req.contentB64,
        mode: req.mode,
        force: req.force || undefined,
        wait: 20,
      },
      drive,
    ),
  importStatus: (drive: string, job: string) =>
    adapterGraph<GraphImportAnswer>("import/status", { job, wait: 15 }, drive),
  /** E29: how far the search copy of a folder's records got. */
  importRag: (drive: string, folder: string) =>
    adapterGraph<{ folder: string; rag: GraphImportRag }>("import/rag", { folder }, drive),
  /** Watchlist pilot: the caller's watched entities in this scope. */
  watchList: (drive: string) =>
    adapterGraph<{ items: GraphWatchItem[]; max_items: number; max_days: number }>(
      "watch/list",
      {},
      drive,
    ),
  watchAdd: (drive: string, entityId: string) =>
    adapterGraph<{ created: boolean; item: GraphWatchItem }>(
      "watch/add",
      { entity_id: entityId },
      drive,
    ),
  watchRemove: (drive: string, id: string) =>
    adapterGraph<{ removed: boolean }>("watch/remove", { id }, drive),
  /** What is new about each watched entity (purpose-bound, logged). */
  watchCheck: (drive: string) =>
    adapterGraph<{ items: GraphWatchCheck[]; documents: GraphDocuments }>("watch/check", {}, drive),
  /** Take over what the last check found. */
  watchSeen: (drive: string, id: string) =>
    adapterGraph<{ seen: boolean }>("watch/seen", { id }, drive),
};

/** A watched entity (adapter watch.py). */
export type GraphWatchItem = {
  id: string;
  entity_id: string;
  label: string;
  type: GraphEntityType;
  subtype: string;
  case_ref: string | null;
  created: number;
  expires: number;
  checked: number | null;
  seen: number | null;
  relations: number;
};

/** A relation of a watched entity that is new, or that more documents back. */
export type GraphWatchChange = {
  id: string;
  type: string;
  start: string | null;
  end: string | null;
  weak: boolean;
  documents: number;
  doc_ids: string[];
  /** Documents before (relations with new evidence only). */
  before?: number;
  other: { id: string | null; label: string | null; type: GraphEntityType | null };
};

export type GraphWatchCheck = GraphWatchItem & {
  new: GraphWatchChange[];
  more: GraphWatchChange[];
  /** Earlier relations the caller does not see now. */
  gone: number;
  truncated?: boolean;
  /** The entity is not visible in this scope now. */
  missing?: boolean;
};

/** A structured source of the profile (graph_service import/sources). */
export type GraphImportSource = {
  id: string;
  label: GraphLabel;
  title: string;
  mode: "delta" | "full";
  record_id: string[];
  columns: string[];
  acl: boolean;
  /** Its records' free text also goes into the search (E29). */
  rag?: boolean;
};

export type GraphImportStats = {
  source: string;
  as_of: string;
  mode: "delta" | "full";
  rows: number;
  records: number;
  created: number;
  updated: number;
  unchanged: number;
  older: number;
  removed: number;
  problems: number;
  problem_rows: { row: number; reason: string }[];
  duplicates: number;
  missing_columns: string[];
  unknown_codes: Record<string, string[]>;
  person_key: "on" | "off";
  person_keys: number;
  acl_nobody: number;
  protected: number;
  review_new?: number;
};

/** The search copy of a folder's records (adapter/record_rag.py). */
export type GraphImportRag = {
  state: "none" | "pending" | "running" | "done" | "failed";
  records?: number;
  written?: number;
  removed?: number;
  unchanged?: number;
  error?: string;
};

export type GraphImportAnswer = {
  import: {
    status: "queued" | "running" | "done" | "refused" | "failed";
    job?: string;
    stats?: GraphImportStats;
    error?: string;
    source_path?: string;
  };
  rag?: GraphImportRag;
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

/** Which profile the documents of this scope were extracted with, and how many
 *  synced files never reached the graph (a reprocessing run sends them first). */
export type GraphReprocessStatus = {
  current: string;
  counts: Record<string, number>;
  stale_total: number;
  /** Of stale_total: read without the language model during a long queue. */
  partial_total?: number;
  absent_total: number;
  can_start: boolean;
  source: "store" | "local" | "ws-fs";
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

/** An investigation spreadsheet: the file itself (download) or where it went
 *  (drive, the /ui/save reply), with the size of the rebuilt table. */
/** A queued person summaries task (BV 1.4 / 2.4): `summarised` of `people`. */
export type GraphInvestigationSummaries = {
  job_id: string;
  job_status: string;
  queue_position: number | null;
  people: number;
  summarised: number;
};

export type GraphInvestigationExport = {
  name: string;
  format: string;
  size: number;
  rows: number;
  truncated: boolean;
  levels: number;
  content_type?: string;
  content_b64?: string;
  transferred?: boolean;
  stored?: boolean;
  path?: string;
  reason?: string;
  download?: string;
};

/** One row of an investigation subgraph: its level (0: starting point) and path. */
export type GraphInvestigationRow = {
  id: string;
  level: number;
  name: string;
  role: string;
  path: string;
  also: string[];
};

/** An investigation as a subgraph, in the neighbourhood shape plus its rows. */
export type GraphInvestigationView = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  documents: GraphDocuments;
  truncated: boolean;
  rows: GraphInvestigationRow[];
  start: string;
  levels: number;
};
