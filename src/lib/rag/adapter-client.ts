// Client for the ViVeSec AIBox adapter's agentic UI channel
// (spec §2.4: POST /api/v1/ui/ask + POST /api/v1/ui/poll bounded long-poll).
//
// This is the PRODUCTION path: the UI talks to the same adapter the ViVeSecBox
// uses (the adapter fronts the RAG service + generation and enforces the
// VVS-Drive hard filter).
//
// Every request targets the versioned /api/v1/... contract — never a framework
// internal route — so any UI version works against any AIBox version, which is
// what the ViVeSecBox embedding requires.
//
// Env (server side only):
//   ADAPTER_URL         adapter base url (default http://127.0.0.1:80)
//   ADAPTER_DEMO_DRIVE  default VVS-Drive path (default /storage/drives/finance/)
//   ADAPTER_DEMO_USER   default VVS-User id (default demo)

export type ChatProfile = "grounded" | "hybrid";
export type ChatPolicy = {
  policy: "locked_grounded" | "locked_hybrid" | "selectable_grounded" | "selectable_hybrid";
  default_profile: ChatProfile;
  allow_switch: boolean;
};

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
  /** C6 audit id — also the join key for /ui/feedback ratings. */
  audit_id?: string;
  components?: Record<string, { points: number; max: number }>;
};

export type AdapterAnswer = {
  ok: boolean;
  profile?: ChatProfile;
  audit_id?: string;
  drive?: string;
  user?: string;
  corpus_id?: string;
  answer: string;
  backend?: string;
  action?: string | null;
  confidence?: AdapterConfidence;
  files?: { path: string; mtime: number | null; size: number | null }[];
  citations: AdapterCitation[];
  hits: AdapterHit[];
  error?: string;
};

/** On-box speech capabilities (adapter/voice.py). Both default to off. */
export type AdapterVoice = { stt: boolean; tts: boolean };

export type AdapterStatus = {
  ok: boolean;
  uiReady: boolean;
  fsReady: boolean;
  features: string[];
  locked: boolean;
  voice: AdapterVoice;
  chatPolicy?: ChatPolicy;
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
  profile?: ChatProfile;
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
  /** Search scope selection. May only NARROW what the box already granted;
   *  the adapter answers 403 for anything else. */
  drives?: string[];
  /** Where the request was started; only background runs join the job list. */
  origin?: "chat" | "background";
};

export type AdapterJobStatus =
  | "queued"
  | "running"
  | "done"
  | "error"
  | "cancelled"
  | "interrupted";

export type AdapterJobSummary = {
  job_id: string;
  status: AdapterJobStatus;
  action?: AdapterAction | null;
  query?: string;
  created?: number;
  started?: number | null;
  finished?: number | null;
  seen_ts?: number | null;
  error?: string | null;
  progress_chars?: number;
  progress_tokens?: number;
  cancel_requested?: boolean;
  queue_position?: number | null;
};

export type AdapterJobSubmission = {
  jobId: string;
  status: AdapterJobStatus;
  queuePosition: number | null;
};

// This module runs in both worlds: in the browser (embedded UI + demo UI) every
// call is a relative /api/v1/... request, and on the server (SSR, demo proxy)
// it talks to the adapter directly with the VVS headers filled in from env.
const IS_BROWSER = typeof window !== "undefined";

/** True when the ViVeSecBox embedding installed its frame-socket transport. */
export function isEmbedded(): boolean {
  return Boolean((globalThis as { frameSocketFetcher?: unknown }).frameSocketFetcher);
}

// Embedded, requests must travel through the ViVeSecBox frame socket rather than
// the browser network stack — every call in this module goes through here.
function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const tunnel = (globalThis as { frameSocketFetcher?: typeof fetch }).frameSocketFetcher;
  return (tunnel ?? fetch)(input, init);
}

function env(name: string): string | undefined {
  if (IS_BROWSER) return undefined;
  const proc = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process;
  return proc?.env?.[name];
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
  // Embedded there is no demo proxy, so skip the call rather than provoke a 404.
  if (isEmbedded()) return null;
  try {
    const res = await apiFetch("/api/v1/ui/demo-config", {
      signal: AbortSignal.timeout(4000),
    });
    if (!res.ok) return null;
    return (await res.json()) as DemoConfig;
  } catch {
    return null;
  }
}

function encodeDrive(drive: string): string {
  // urlsafe base64 of the UTF-8 path (matches corpus.decode_vvs_drive), without
  // depending on Buffer so the module stays browser-safe.
  const bytes = new TextEncoder().encode(drive);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
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
const MAX_POLLS = 30; // convenience ceiling only; durable jobs remain retrievable

class AdapterError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "AdapterError";
  }
}

export class AdapterJobContinuesError extends Error {
  constructor(readonly jobId: string) {
    super("The request continues in the background.");
    this.name = "AdapterJobContinuesError";
  }
}

async function safeJson(res: Response): Promise<Record<string, unknown>> {
  try {
    return (await res.json()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function readVoice(raw: unknown): AdapterVoice {
  const v = (raw ?? {}) as Record<string, unknown>;
  return { stt: Boolean(v.stt), tts: Boolean(v.tts) };
}

/** POST /api/v1/status — readiness probe (ui-ready / fs-ready / features). */
export async function adapterStatus(): Promise<AdapterStatus> {
  const offline: AdapterStatus = {
    ok: false,
    uiReady: false,
    fsReady: false,
    features: [],
    locked: true,
    voice: { stt: false, tts: false },
  };
  try {
    const res = await apiFetch(`${adapterUrl()}/api/v1/status`, {
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
      voice: readVoice(j.voice),
      chatPolicy: j.chat_policy as ChatPolicy | undefined,
    };
  } catch (err) {
    return { ...offline, error: err instanceof Error ? err.message : "adapter unreachable" };
  }
}

export type SessionInfo = { drive: string; user: string };

let sessionRequest: Promise<SessionInfo> | null = null;

async function fetchSession(): Promise<SessionInfo> {
  try {
    const res = await apiFetch(`${adapterUrl()}/api/v1/ui/init`, {
      signal: AbortSignal.timeout(STATUS_TIMEOUT_MS),
    });
    if (!res.ok) return { drive: "", user: "" };
    const j = await safeJson(res);
    return {
      drive: typeof j.drive === "string" ? j.drive : "",
      user: typeof j.user === "string" ? j.user : "",
    };
  } catch {
    return { drive: "", user: "" };
  }
}

/**
 * GET /api/v1/ui/init — VVS handshake. The browser never sees the VVS-Drive and
 * VVS-User headers the box injects, so this is the only way it learns which
 * drive it is bound to and who is signed in. Stable per page load, so cached.
 */
export function adapterSession(): Promise<SessionInfo> {
  sessionRequest ??= fetchSession();
  return sessionRequest;
}

export type AdapterScopeDrive = { path: string; name: string; active: boolean };
export type AdapterScope = {
  ok: boolean;
  activeDrive: string;
  source: string;
  drives: AdapterScopeDrive[];
};

/**
 * GET /api/v1/ui/scope — the drives this session may search. The entitlement
 * never passes through the browser, so the UI cannot derive it locally.
 */
export async function adapterScope(input?: {
  drive?: string;
  user?: string;
}): Promise<AdapterScope> {
  const drive = input?.drive ?? demoDrive();
  const user = input?.user ?? demoUser();
  const empty: AdapterScope = { ok: false, activeDrive: drive, source: "", drives: [] };
  try {
    const res = await apiFetch(`${adapterUrl()}/api/v1/ui/scope`, {
      headers: vvsHeaders(drive, user),
      signal: AbortSignal.timeout(STATUS_TIMEOUT_MS),
    });
    if (!res.ok) return empty;
    const j = await safeJson(res);
    const drives = Array.isArray(j.drives) ? j.drives : [];
    return {
      ok: true,
      activeDrive: typeof j.active_drive === "string" ? j.active_drive : drive,
      source: typeof j.source === "string" ? j.source : "",
      drives: drives.map((d: Record<string, unknown>) => ({
        path: String(d.path ?? ""),
        name: String(d.name ?? ""),
        active: Boolean(d.active),
      })),
    };
  } catch {
    return empty;
  }
}

/**
 * Agentic query over the async long-poll channel: submit the job (/ui/ask),
 * then bounded long-poll (/ui/poll) until it completes. The VVS-Drive header is
 * the mandatory corpus hard filter.
 */
function jobScope(input: Pick<AdapterAskInput, "drive" | "user">) {
  const drive = input.drive ?? demoDrive();
  const user = input.user ?? demoUser();
  return { drive, user, headers: vvsHeaders(drive, user) };
}

export async function adapterSubmitJob(input: AdapterAskInput): Promise<AdapterJobSubmission> {
  const { headers } = jobScope(input);
  const askRes = await apiFetch(`${adapterUrl()}/api/v1/ui/ask`, {
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
      origin: input.origin ?? "chat",
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
  return {
    jobId,
    status: ((ask.job_status as AdapterJobStatus | undefined) ?? "queued"),
    queuePosition: typeof ask.queue_position === "number" ? ask.queue_position : null,
  };
}

function answerFromJson(j: Record<string, unknown>): AdapterAnswer {
  return {
    ok: Boolean(j.ok),
    drive: j.drive as string | undefined,
    user: j.user as string | undefined,
    corpus_id: j.corpus_id as string | undefined,
    answer: (j.answer as string) ?? "",
    backend: j.backend as string | undefined,
    action: (j.action as string | undefined) ?? null,
    profile: j.profile === "hybrid" ? "hybrid" : "grounded",
    audit_id: j.audit_id as string | undefined,
    confidence: (j.confidence as AdapterConfidence | undefined) ?? undefined,
    files: Array.isArray(j.files)
      ? (j.files as { path: string; mtime: number | null; size: number | null }[])
      : undefined,
    citations: Array.isArray(j.citations) ? (j.citations as AdapterCitation[]) : [],
    hits: Array.isArray(j.hits) ? (j.hits as AdapterHit[]) : [],
    error: j.error as string | undefined,
  };
}

export async function adapterPollJob(
  jobId: string,
  scope: Pick<AdapterAskInput, "drive" | "user"> = {},
  timeout = 0,
): Promise<{ status: AdapterJobStatus; answer?: AdapterAnswer; queuePosition: number | null }> {
  const { headers } = jobScope(scope);
  const pollRes = await apiFetch(`${adapterUrl()}/api/v1/ui/poll`, {
    method: "POST",
    headers,
    body: JSON.stringify({ job_id: jobId, timeout }),
    signal: AbortSignal.timeout((Math.max(0, timeout) + 10) * 1000),
  });
  const j = await safeJson(pollRes);
  if (pollRes.status >= 400) {
    throw new AdapterError((j.error as string) ?? `ui/poll HTTP ${pollRes.status}`, pollRes.status);
  }
  if (j.status === "pending") {
    return {
      status: (j.job_status as AdapterJobStatus | undefined) ?? "running",
      queuePosition: typeof j.queue_position === "number" ? j.queue_position : null,
    };
  }
  return {
    status: j.status as AdapterJobStatus,
    answer: answerFromJson(j),
    queuePosition: null,
  };
}

export async function adapterListJobs(
  scope: Pick<AdapterAskInput, "drive" | "user"> = {},
): Promise<AdapterJobSummary[] | null> {
  const { headers } = jobScope(scope);
  const res = await apiFetch(`${adapterUrl()}/api/v1/ui/jobs`, { headers });
  const body = await safeJson(res);
  if (res.status === 404) return null;
  if (!res.ok) throw new AdapterError((body.error as string) ?? `ui/jobs HTTP ${res.status}`, res.status);
  return Array.isArray(body.jobs) ? (body.jobs as AdapterJobSummary[]) : [];
}

export async function adapterGetJob(
  jobId: string,
  scope: Pick<AdapterAskInput, "drive" | "user"> = {},
): Promise<{ summary: AdapterJobSummary; answer?: AdapterAnswer }> {
  const { headers } = jobScope(scope);
  const res = await apiFetch(`${adapterUrl()}/api/v1/ui/jobs/get`, {
    method: "POST",
    headers,
    body: JSON.stringify({ job_id: jobId }),
  });
  const body = await safeJson(res);
  if (!res.ok) throw new AdapterError((body.error as string) ?? `ui/jobs/get HTTP ${res.status}`, res.status);
  const job = (body.job ?? {}) as Record<string, unknown>;
  const request = (job.request ?? {}) as Record<string, unknown>;
  return {
    summary: {
      job_id: String(job.job_id ?? jobId),
      status: job.status as AdapterJobStatus,
      action: (request.action as AdapterAction | null | undefined) ?? null,
      query: request.query as string | undefined,
      created: job.created as number | undefined,
      started: job.started as number | null | undefined,
      finished: job.finished as number | null | undefined,
      seen_ts: job.seen_ts as number | null | undefined,
      error: job.error as string | null | undefined,
    },
    answer: job.result ? answerFromJson(job.result as Record<string, unknown>) : undefined,
  };
}

async function adapterJobMutation(
  operation: "seen" | "cancel",
  jobId: string,
  scope: Pick<AdapterAskInput, "drive" | "user"> = {},
): Promise<Record<string, unknown>> {
  const { headers } = jobScope(scope);
  const res = await apiFetch(`${adapterUrl()}/api/v1/ui/jobs/${operation}`, {
    method: "POST",
    headers,
    body: JSON.stringify({ job_id: jobId }),
  });
  const body = await safeJson(res);
  if (!res.ok) throw new AdapterError((body.error as string) ?? `ui/jobs/${operation} HTTP ${res.status}`, res.status);
  return body;
}

export function adapterMarkJobSeen(jobId: string, scope: Pick<AdapterAskInput, "drive" | "user"> = {}) {
  return adapterJobMutation("seen", jobId, scope);
}

export function adapterCancelJob(jobId: string, scope: Pick<AdapterAskInput, "drive" | "user"> = {}) {
  return adapterJobMutation("cancel", jobId, scope);
}

export async function adapterAsk(input: AdapterAskInput): Promise<AdapterAnswer> {
  const submitted = await adapterSubmitJob(input);

  for (let i = 0; i < MAX_POLLS; i++) {
    const polled = await adapterPollJob(submitted.jobId, input, POLL_TIMEOUT_S);
    if (polled.answer) return polled.answer;
  }
  throw new AdapterJobContinuesError(submitted.jobId);
}

/** POST /api/v1/index/get/children — mirror listing for one directory. */
export type MirrorEntry = {
  path: string;
  file: boolean;
  mtime: number | null;
  size: number | null;
};

export async function adapterChildren(path: string): Promise<MirrorEntry[]> {
  const res = await apiFetch(`${adapterUrl()}/api/v1/index/get/children`, {
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
  const res = await apiFetch(`${adapterUrl()}/api/v1/ui/query`, {
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
  const res = await apiFetch(`${adapterUrl()}/api/v1/status`, {
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
  /** Rendered by the adapter (docgen.py): md | txt | pdf | pptx. */
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
  const res = await apiFetch(`${adapterUrl()}/api/v1/ui/save`, {
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

// ---------------------------------------------------------------------------
// Answer feedback — POST /api/v1/ui/feedback. The adapter joins the rating
// with the full answer trace (retrieved hits, citations, confidence) by the
// audit id and appends it to JSONL on the box; rated exchanges are the seed
// of a per-customer gold set. question/answer only travel as a fallback echo
// for when the box no longer holds the trace (restart).
// ---------------------------------------------------------------------------

export type AdapterFeedbackInput = {
  auditId: string;
  rating: "up" | "down";
  reason?: string;
  comment?: string;
  question?: string;
  answer?: string;
  drive?: string;
  user?: string;
};

export type AdapterFeedbackResult = {
  ok: boolean;
  /** JSONL file the record went into. */
  stored?: string;
  /** Whether the box still held the full answer trace. */
  trace?: boolean;
  error?: string;
};

/** POST /api/v1/ui/feedback — rate one generated answer (up/down). */
export async function adapterFeedback(
  input: AdapterFeedbackInput,
): Promise<AdapterFeedbackResult> {
  const drive = input.drive ?? demoDrive();
  const user = input.user ?? demoUser();
  const res = await apiFetch(`${adapterUrl()}/api/v1/ui/feedback`, {
    method: "POST",
    headers: vvsHeaders(drive, user),
    body: JSON.stringify({
      audit_id: input.auditId,
      rating: input.rating,
      reason: input.reason,
      comment: input.comment,
      question: input.question,
      answer: input.answer,
    }),
    signal: AbortSignal.timeout(STATUS_TIMEOUT_MS),
  });
  const j = await safeJson(res);
  if (!res.ok) {
    throw new AdapterError((j.error as string) ?? `ui/feedback HTTP ${res.status}`, res.status);
  }
  return {
    ok: Boolean(j.ok),
    stored: j.stored as string | undefined,
    trace: typeof j.trace === "boolean" ? j.trace : undefined,
    error: j.error as string | undefined,
  };
}

/** GET /api/v1/ui/files — what is still sitting in this session's store
 *  (i.e. everything the box has NOT taken over the ws-fs channel). */
export async function adapterGeneratedFiles(input?: {
  drive?: string;
  user?: string;
}): Promise<AdapterGeneratedFile[]> {
  const drive = input?.drive ?? demoDrive();
  const user = input?.user ?? demoUser();
  const res = await apiFetch(`${adapterUrl()}/api/v1/ui/files`, {
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
}): Promise<{
  ok: boolean;
  name: string;
  contentB64?: string;
  contentType?: string;
  error?: string;
}> {
  const drive = input.drive ?? demoDrive();
  const user = input.user ?? demoUser();
  const qs = new URLSearchParams({ name: input.name }).toString();
  const res = await apiFetch(`${adapterUrl()}/api/v1/ui/files/download?${qs}`, {
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

/** How long the box may take to hand over a document (ws-fs round trip). */
const DRIVE_FILE_TIMEOUT_MS = 60_000;

/** POST /api/v1/ui/file — the ORIGINAL document, fetched from the ViVeSecBox.
 *
 * The AI Box only ever stored the passages it retrieved, so showing the real
 * document means asking the box for it. The adapter re-checks the read scope,
 * so a path outside this request's drives comes back as 403 rather than bytes.
 *
 * POST, not GET: embedded in the ViVeSecBox the tunnel drops the query string
 * of a GET, so `?path=` arrived empty (measured on the demo box: every embedded
 * call landed as a bare /ui/file -> 400). The path is sent BOTH in the body and
 * in the query, because which of the two their tunnel preserves is its own
 * implementation detail; the adapter reads the body first and falls back.
 */
export async function adapterDriveFile(input: {
  path: string;
  drive?: string;
  user?: string;
}): Promise<{ ok: boolean; blob?: Blob; contentType?: string; error?: string }> {
  const drive = input.drive ?? demoDrive();
  const user = input.user ?? demoUser();
  const encoded = isEmbedded();
  const payload = { path: input.path, ...(encoded ? { encode: "base64" } : {}) };
  const qs = new URLSearchParams(payload).toString();
  let res: Response;
  try {
    res = await apiFetch(`${adapterUrl()}/api/v1/ui/file?${qs}`, {
      method: "POST",
      headers: vvsHeaders(drive, user),
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(DRIVE_FILE_TIMEOUT_MS),
    });
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "request failed" };
  }
  if (!res.ok) {
    const j = await safeJson(res);
    return { ok: false, error: (j.error as string) ?? `HTTP ${res.status}` };
  }
  if (encoded) {
    const document = await safeJson(res);
    if (document.ok !== true || typeof document.content_b64 !== "string") {
      return { ok: false, error: "Invalid encoded document response" };
    }
    try {
      const binary = atob(document.content_b64);
      const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
      const contentType = typeof document.content_type === "string"
        ? document.content_type : "application/octet-stream";
      return { ok: true, blob: new Blob([bytes], { type: contentType }), contentType };
    } catch {
      return { ok: false, error: "Invalid base64 document content" };
    }
  }
  return {
    ok: true,
    blob: await res.blob(),
    contentType: res.headers.get("content-type") ?? "application/octet-stream",
  };
}

// ---------------------------------------------------------------------------
// Voice I/O. Speech is only an input and an output shell around the SAME
// grounded pipeline: /ui/stt returns text that is then sent through /ui/ask
// exactly as if it had been typed, and /ui/tts reads back an answer that was
// already produced, cited and scored. Nothing about retrieval changes.
// ---------------------------------------------------------------------------
const STT_TIMEOUT_MS = 120_000;
const TTS_TIMEOUT_MS = 120_000;

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  // Chunked: String.fromCharCode(...bigArray) blows the argument limit.
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode(...bytes.subarray(i, i + step));
  }
  return btoa(binary);
}

export type AdapterTranscript = { ok: boolean; text: string; error?: string };

/** POST /api/v1/ui/stt — recorded utterance -> text, transcribed on the box. */
export async function adapterTranscribe(input: {
  audio: Blob;
  lang?: string;
  drive?: string;
  user?: string;
}): Promise<AdapterTranscript> {
  const drive = input.drive ?? demoDrive();
  const user = input.user ?? demoUser();
  const bytes = new Uint8Array(await input.audio.arrayBuffer());
  try {
    const res = await apiFetch(`${adapterUrl()}/api/v1/ui/stt`, {
      method: "POST",
      headers: vvsHeaders(drive, user),
      body: JSON.stringify({
        audio_b64: bytesToBase64(bytes),
        content_type: input.audio.type || "audio/webm",
        lang: input.lang,
      }),
      signal: AbortSignal.timeout(STT_TIMEOUT_MS),
    });
    const j = await safeJson(res);
    if (!res.ok) {
      return { ok: false, text: "", error: (j.error as string) ?? `stt HTTP ${res.status}` };
    }
    return { ok: true, text: String(j.text ?? "").trim() };
  } catch (err) {
    return { ok: false, text: "", error: err instanceof Error ? err.message : "stt failed" };
  }
}

export type AdapterSpeech = { ok: boolean; audio?: Blob; truncated?: boolean; error?: string };

/** POST /api/v1/ui/tts — answer text -> spoken audio, synthesised on the box. */
export async function adapterSpeak(input: {
  text: string;
  lang?: string;
  drive?: string;
  user?: string;
}): Promise<AdapterSpeech> {
  const drive = input.drive ?? demoDrive();
  const user = input.user ?? demoUser();
  try {
    const res = await apiFetch(`${adapterUrl()}/api/v1/ui/tts`, {
      method: "POST",
      headers: vvsHeaders(drive, user),
      body: JSON.stringify({ text: input.text, lang: input.lang }),
      signal: AbortSignal.timeout(TTS_TIMEOUT_MS),
    });
    if (!res.ok) {
      const j = await safeJson(res);
      return { ok: false, error: (j.error as string) ?? `tts HTTP ${res.status}` };
    }
    return {
      ok: true,
      audio: await res.blob(),
      truncated: res.headers.get("X-Speech-Truncated") === "1",
    };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "tts failed" };
  }
}

export { AdapterError };
