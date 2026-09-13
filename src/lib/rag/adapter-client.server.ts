// Server-only client for the ViVeSec AIBox adapter's agentic UI channel
// (spec §2.4: POST /api/v1/ui/ask + POST /api/v1/ui/poll bounded long-poll).
//
// This is the PRODUCTION path: the demo UI talks to the same adapter the
// ViVeSecBox uses, instead of the old direct rag-engine bridge. The adapter
// fronts the RAG service + generation (adapter/llm.py, qwen2.5:14b) and enforces
// the VVS-Drive hard filter (one drive = one corpus).
//
// The VVS-Drive header is urlsafe base64 of the UTF-8 drive path (matches
// adapter/corpus.py decode_vvs_drive); VVS-User is plain. For the single-tenant
// demo the drive/user default from env.
//
// This file must never reach the client bundle (it reads process.env) — keep
// imports server-side (e.g. used from createServerFn handlers).
//
// Env (server-only):
//   ADAPTER_URL         adapter base url (default http://127.0.0.1:8088)
//   ADAPTER_DEMO_DRIVE  default VVS-Drive path (default /storage/drives/finance/)
//   ADAPTER_DEMO_USER   default VVS-User id (default demo)

import { Buffer } from "node:buffer";
import process from "node:process";

export type AdapterCitation = {
  ref: number;
  path: string | null;
  page_number: number | null;
  chunk_id: string | null;
  score: number | null;
  snippet: string;
};

export type AdapterHit = {
  rank: number;
  path: string | null;
  chunk_id: string | null;
  page_number: number | null;
  score: number | null;
  snippet: string;
};

export type AdapterConfidence = {
  score: number;
  band: "green" | "amber" | "red";
  components?: Record<string, { points: number; max: number }>;
};

export type AdapterAnswer = {
  ok: boolean;
  drive?: string;
  user?: string;
  corpus_id?: string;
  answer: string;
  backend?: string;
  action?: string | null;
  profile?: "grounded" | "hybrid";
  audit_id?: string;
  confidence?: AdapterConfidence;
  files?: { path: string; mtime: number | null; size: number | null }[];
  citations: AdapterCitation[];
  hits: AdapterHit[];
  error?: string;
};

export type AdapterStatus = {
  ok: boolean;
  uiReady: boolean;
  fsReady: boolean;
  features: string[];
  locked: boolean;
  error?: string;
};

/** Quick actions the adapter routes to a structured task (service._ACTIONS). */
export type AdapterAction =
  | "search"
  | "summary"
  | "report"
  | "tracking"
  | "presentation"
  | "memo"
  /** Whole-document analysis: the adapter reads the named file end to end. */
  | "analyze";

export type AdapterAskInput = {
  query: string;
  profile?: "grounded" | "hybrid";
  topK?: number;
  lang?: string; // adapter expects a language NAME (English/Hungarian/Danish/German)
  drive?: string;
  user?: string;
  /** Explicit quick action (wins over a typed "#action" prefix in the query). */
  action?: AdapterAction;
  /** Only meaningful for the search action: filename lookup vs. grounded text. */
  mode?: "files" | "text";
  /** Quick-action dialog answers forwarded as PARAMETERS to the generator. */
  audience?: string;
  purpose?: string;
  coverage?: string;
  report_type?: string;
  aspect?: string;
  keywords?: string;
  outcome?: string;
  situation?: string;
  extra?: string;
  /** Source files picked in the drive dialog (adapter _answer files filter). */
  files?: string[];
  /** Search scope selection. May only NARROW what the box already granted. */
  drives?: string[];
};

// This module runs in both worlds: in the browser (embedded UI + demo UI) every
// call is a relative /api/v1/... request, and on the server (SSR, demo proxy)
// it talks to the adapter directly with the VVS headers filled in from env.
const IS_BROWSER = typeof window !== "undefined";

function env(name: string): string | undefined {
  return IS_BROWSER ? undefined : process.env[name];
}

function adapterUrl(): string {
  // Relative in the browser: the ViVeSecBox tunnel (embedded) or the demo proxy
  // in src/server.ts is what forwards the request to the adapter.
  return IS_BROWSER ? "" : (env("ADAPTER_URL") ?? "http://127.0.0.1:80");
}

function demoDrive(): string {
  // Empty in the browser so no drive hint is sent unless the picker chose one —
  // the ViVeSecBox session decides, and the demo proxy fills in its own default.
  return env("ADAPTER_DEMO_DRIVE") ?? (IS_BROWSER ? "" : "/storage/drives/finance/");
}

/** The drive used when the caller does not pin one (box session default). */
export function defaultDrive(): string {
  return demoDrive();
}

function demoUser(): string {
  return env("ADAPTER_DEMO_USER") ?? (IS_BROWSER ? "" : "demo");
}

/** Root the ViVeSecBox mounts its drives under (adapter/corpus.py DRIVE_PREFIX). */
export function driveRoot(): string {
  return env("ADAPTER_DRIVE_PREFIX") ?? "/storage/drives";
}

/**
 * Demo/test only: lets the operator switch between the drives on the box. On a
 * real ViVeSecBox the drive comes from the session (VVS-Drive) and this must
 * stay off — hence opt-in, never a default. In the browser the demo proxy
 * answers /api/v1/ui/demo-config; embedded there is no such endpoint, so the
 * picker stays off.
 */
export function drivePickerEnabled(): boolean {
  return (env("ADAPTER_DEMO_DRIVE_PICKER") ?? "") === "1";
}

export type DemoConfig = { drive: string; driveRoot: string; picker: boolean };

/** Demo-proxy config; absent (404) when the UI runs embedded in the ViVeSecBox. */
export async function adapterDemoConfig(): Promise<DemoConfig | null> {
  if (!IS_BROWSER) {
    return { drive: demoDrive(), driveRoot: driveRoot(), picker: drivePickerEnabled() };
  }
  try {
    const res = await fetch("/api/v1/ui/demo-config", {
      signal: AbortSignal.timeout(4000),
    });
    if (!res.ok) return null;
    return (await res.json()) as DemoConfig;
  } catch {
    return null;
  }
}

function encodeDrive(drive: string): string {
  // urlsafe base64 of the UTF-8 path (matches corpus.decode_vvs_drive).
  return Buffer.from(drive, "utf-8").toString("base64url");
}

function vvsHeaders(drive: string, user: string): Record<string, string> {
  if (IS_BROWSER) {
    // The authoritative VVS-* headers are injected by the ViVeSecBox (embedded)
    // or by the demo proxy; only the picker's choice travels, as a hint.
    return {
      "Content-Type": "application/json",
      ...(drive ? { "X-Demo-Drive": drive } : {}),
    };
  }
  return {
    "Content-Type": "application/json",
    "VVS-Drive": encodeDrive(drive),
    "VVS-User": user,
  };
}

const ASK_TIMEOUT_MS = 10_000;
const POLL_TIMEOUT_S = 25; // adapter bounds this to LONGPOLL_MAX (55s)
const POLL_HTTP_TIMEOUT_MS = (POLL_TIMEOUT_S + 10) * 1000;
const STATUS_TIMEOUT_MS = 4_000;
const FILES_TIMEOUT_MS = 10_000; // mirror lookup, no model involved
const MAX_POLLS = 30; // durable result remains available after this convenience ceiling

class AdapterError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "AdapterError";
  }
}

async function safeJson(res: Response): Promise<Record<string, unknown>> {
  try {
    return (await res.json()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

/** POST /api/v1/status — readiness probe (ui-ready / fs-ready / features). */
export async function adapterStatus(): Promise<AdapterStatus> {
  const offline: AdapterStatus = {
    ok: false,
    uiReady: false,
    fsReady: false,
    features: [],
    locked: true,
  };
  try {
    const res = await fetch(`${adapterUrl()}/api/v1/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      signal: AbortSignal.timeout(STATUS_TIMEOUT_MS),
    });
    if (!res.ok) return { ...offline, error: `status HTTP ${res.status}` };
    const j = await safeJson(res);
    return {
      ok: Boolean(j.ok),
      // snake_case is canonical (aibox_more3 §2); dash keys read as fallback
      // for older adapter builds during the transition.
      uiReady: Boolean(j.ui_ready ?? j["ui-ready"]),
      fsReady: Boolean(j.fs_ready ?? j["fs-ready"]),
      features: Array.isArray(j.features) ? (j.features as string[]) : [],
      locked: Boolean(j.storage_locked ?? j.locked),
    };
  } catch (err) {
    return { ...offline, error: err instanceof Error ? err.message : "adapter unreachable" };
  }
}

/**
 * Agentic query over the async long-poll channel: submit the job (/ui/ask),
 * then bounded long-poll (/ui/poll) until it completes. The VVS-Drive header is
 * the mandatory corpus hard filter.
 */
export async function adapterAsk(input: AdapterAskInput): Promise<AdapterAnswer> {
  const drive = input.drive ?? demoDrive();
  const user = input.user ?? demoUser();
  const headers = vvsHeaders(drive, user);

  // 1) submit the job. `action`/`mode` select the spec quick actions (F1-F7);
  // top_k is left to the adapter for actions (synthesis needs a wider window,
  // service._DEFAULT_TOP_K) and only pinned for plain grounded questions.
  const askRes = await fetch(`${adapterUrl()}/api/v1/ui/ask`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      query: input.query,
      profile: input.profile,
      top_k: input.topK ?? (input.action ? undefined : 5),
      lang: input.lang,
      action: input.action,
      mode: input.mode,
      audience: input.audience,
      purpose: input.purpose,
      coverage: input.coverage,
      report_type: input.report_type,
      aspect: input.aspect,
      keywords: input.keywords,
      outcome: input.outcome,
      situation: input.situation,
      extra: input.extra,
      files: input.files?.length ? input.files : undefined,
      drives: input.drives?.length ? input.drives : undefined,
    }),
    signal: AbortSignal.timeout(ASK_TIMEOUT_MS),
  });
  if (askRes.status !== 202) {
    const body = await safeJson(askRes);
    throw new AdapterError((body.error as string) ?? `ui/ask HTTP ${askRes.status}`, askRes.status);
  }
  const ask = await safeJson(askRes);
  const jobId = ask.job_id as string | undefined;
  if (!jobId) throw new AdapterError("ui/ask returned no job_id", 502);

  // 2) bounded long-poll until done
  for (let i = 0; i < MAX_POLLS; i++) {
    const pollRes = await fetch(`${adapterUrl()}/api/v1/ui/poll`, {
      method: "POST",
      headers,
      body: JSON.stringify({ job_id: jobId, timeout: POLL_TIMEOUT_S }),
      signal: AbortSignal.timeout(POLL_HTTP_TIMEOUT_MS),
    });
    if (pollRes.status === 404) throw new AdapterError("job expired or unknown", 404);
    const j = await safeJson(pollRes);
    if (pollRes.status >= 400) {
      throw new AdapterError((j.error as string) ?? `ui/poll HTTP ${pollRes.status}`, pollRes.status);
    }
    if (j.status === "pending") continue;
    // done / error -> map the result verbatim
    return {
      ok: Boolean(j.ok),
      drive: j.drive as string | undefined,
      user: j.user as string | undefined,
      corpus_id: j.corpus_id as string | undefined,
      answer: (j.answer as string) ?? "",
      backend: j.backend as string | undefined,
      action: (j.action as string | undefined) ?? null,
      // C6: the confidence block (score/band/components) drives the UI badge —
      // it must be forwarded, otherwise the live path renders without a score.
      confidence: (j.confidence as AdapterConfidence | undefined) ?? undefined,
      profile: j.profile === "hybrid" ? "hybrid" : "grounded",
      audit_id: j.audit_id as string | undefined,
      files: Array.isArray(j.files)
        ? (j.files as { path: string; mtime: number | null; size: number | null }[])
        : undefined,
      citations: Array.isArray(j.citations) ? (j.citations as AdapterCitation[]) : [],
      hits: Array.isArray(j.hits) ? (j.hits as AdapterHit[]) : [],
      error: j.error as string | undefined,
    };
  }
  throw new AdapterError("long-poll exceeded max attempts", 504);
}

/** POST /api/v1/index/get/children — mirror listing for one directory. */
export type MirrorEntry = {
  path: string;
  file: boolean;
  mtime: number | null;
  size: number | null;
};

export async function adapterChildren(path: string): Promise<MirrorEntry[]> {
  const res = await fetch(`${adapterUrl()}/api/v1/index/get/children`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
    signal: AbortSignal.timeout(STATUS_TIMEOUT_MS),
  });
  if (!res.ok) throw new AdapterError(`index/get/children HTTP ${res.status}`, res.status);
  const j = (await safeJson(res)) as unknown;
  if (Array.isArray(j)) return j as MirrorEntry[];
  const legacy = (j as { children?: unknown }).children;
  return Array.isArray(legacy) ? (legacy as MirrorEntry[]) : [];
}

/**
 * Filename lookup under a drive, straight from the adapter's metadata mirror.
 * This is the `#search files:` quick action (spec F7 UX 2.A): deterministic, no
 * model involved, and scoped to the VVS-Drive header. An empty pattern lists
 * everything, capped by the adapter's ADAPTER_FILE_SEARCH_LIMIT.
 */
export async function adapterDriveFiles(
  drive: string,
  pattern = "",
): Promise<{ entries: MirrorEntry[]; truncated: boolean }> {
  const res = await fetch(`${adapterUrl()}/api/v1/ui/query`, {
    method: "POST",
    headers: vvsHeaders(drive, demoUser()),
    body: JSON.stringify({ action: "search", mode: "files", query: `files:${pattern}` }),
    signal: AbortSignal.timeout(FILES_TIMEOUT_MS),
  });
  const j = await safeJson(res);
  if (!res.ok) {
    throw new AdapterError((j.error as string) ?? `ui/query HTTP ${res.status}`, res.status);
  }
  return {
    entries: Array.isArray(j.files) ? (j.files as MirrorEntry[]) : [],
    truncated: Boolean(j.truncated),
  };
}

/**
 * The full status document (adapter/service.py status_payload). `adapterStatus`
 * reduces this to a readiness flag; the platform insight view needs the real
 * counters (index/mirror/sessions/storage), so it reads the raw payload.
 */
export type AdapterStatusRaw = {
  ok?: boolean;
  features?: string[];
  storage_locked?: boolean;
  presence_lost?: boolean;
  watchdog_seconds?: number;
  storage?: { mode?: string; locked?: boolean; mounted?: boolean };
  sessions?: { active?: number; idle_seconds?: number; max_turns?: number };
  ws_fs?: { connected?: boolean };
  files?: { enabled?: boolean; sessions?: number; files?: number };
  mirror?: { documents?: number; files?: number; directories?: number };
  index?: { documents?: number; pages?: number; chunks?: number; corpora?: number; error?: string };
};

export async function adapterStatusRaw(): Promise<AdapterStatusRaw> {
  const res = await fetch(`${adapterUrl()}/api/v1/status`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
    signal: AbortSignal.timeout(STATUS_TIMEOUT_MS),
  });
  if (!res.ok) throw new AdapterError(`status HTTP ${res.status}`, res.status);
  return (await safeJson(res)) as AdapterStatusRaw;
}

// ---------------------------------------------------------------------------
// C7 — Save steps over the B6 ws-fs channel (aibox_more3 §1).
// POST /api/v1/ui/save stores the generated file in the session store first,
// then transfers it to the ViVeSecBox drive over the ws-fs channel. When the
// transfer is rejected (permission) or no channel is up, the copy stays stored
// and is offered for download over the tunnel instead.
// ---------------------------------------------------------------------------

export type AdapterSaveInput = {
  name: string;
  text: string;
  /** Rendered by the adapter (docgen.py): md | txt | pdf | docx | pptx. */
  format?: string;
  title?: string;
  drive?: string;
  user?: string;
};

export type AdapterSaveResult = {
  ok: boolean;
  transferred: boolean;
  name: string;
  /** Drive-relative path when the transfer succeeded. */
  path?: string;
  /** The format the adapter actually rendered. */
  format?: string;
  size?: number;
  /** Why the file stayed on the AIBox: permission | no-channel | ... */
  reason?: string;
  /** Adapter download endpoint (tunnel fallback) when stored. */
  download?: string;
  error?: string;
};

const SAVE_TIMEOUT_MS = 45_000; // save waits for the ws put-file ack (+retries)

/** POST /api/v1/ui/save — session-store + ws-fs transfer of a generated file. */
export async function adapterSave(input: AdapterSaveInput): Promise<AdapterSaveResult> {
  const drive = input.drive ?? demoDrive();
  const user = input.user ?? demoUser();
  const res = await fetch(`${adapterUrl()}/api/v1/ui/save`, {
    method: "POST",
    headers: vvsHeaders(drive, user),
    body: JSON.stringify({
      name: input.name,
      text: input.text,
      format: input.format,
      title: input.title,
    }),
    signal: AbortSignal.timeout(SAVE_TIMEOUT_MS),
  });
  const j = await safeJson(res);
  if (!res.ok) {
    throw new AdapterError((j.error as string) ?? `ui/save HTTP ${res.status}`, res.status);
  }
  return {
    ok: Boolean(j.ok),
    transferred: Boolean(j.transferred),
    name: (j.name as string) ?? input.name,
    path: j.path as string | undefined,
    format: j.format as string | undefined,
    size: typeof j.size === "number" ? j.size : undefined,
    reason: j.reason as string | undefined,
    download: j.download as string | undefined,
    error: j.error as string | undefined,
  };
}

export type AdapterGeneratedFile = { name: string; size: number; mtime: number };

/** GET /api/v1/ui/files — what is still sitting in this session's store
 *  (i.e. everything the box has NOT taken over the ws-fs channel). */
export async function adapterGeneratedFiles(input?: {
  drive?: string;
  user?: string;
}): Promise<AdapterGeneratedFile[]> {
  const drive = input?.drive ?? demoDrive();
  const user = input?.user ?? demoUser();
  const res = await fetch(`${adapterUrl()}/api/v1/ui/files`, {
    headers: vvsHeaders(drive, user),
    signal: AbortSignal.timeout(STATUS_TIMEOUT_MS),
  });
  const j = await safeJson(res);
  if (!res.ok) {
    throw new AdapterError((j.error as string) ?? `ui/files HTTP ${res.status}`, res.status);
  }
  const files = Array.isArray(j.files) ? j.files : [];
  return files.map((f: Record<string, unknown>) => ({
    name: String(f.name ?? ""),
    size: typeof f.size === "number" ? f.size : 0,
    mtime: typeof f.mtime === "number" ? f.mtime : 0,
  }));
}

/** GET /api/v1/ui/files/download — fetch a session-stored generated file
 * (the tunnel download fallback). Returns the bytes base64-encoded so the
 * server function can hand them to the browser. */
export async function adapterDownloadGenerated(input: {
  name: string;
  drive?: string;
  user?: string;
}): Promise<{ ok: boolean; name: string; contentB64?: string; contentType?: string; error?: string }> {
  const drive = input.drive ?? demoDrive();
  const user = input.user ?? demoUser();
  const qs = new URLSearchParams({ name: input.name }).toString();
  const res = await fetch(`${adapterUrl()}/api/v1/ui/files/download?${qs}`, {
    method: "GET",
    headers: vvsHeaders(drive, user),
    signal: AbortSignal.timeout(STATUS_TIMEOUT_MS),
  });
  if (!res.ok) {
    const j = await safeJson(res);
    return { ok: false, name: input.name, error: (j.error as string) ?? `HTTP ${res.status}` };
  }
  const buf = Buffer.from(await res.arrayBuffer());
  return {
    ok: true,
    name: input.name,
    contentB64: buf.toString("base64"),
    contentType: res.headers.get("content-type") ?? "application/octet-stream",
  };
}

export { AdapterError };
