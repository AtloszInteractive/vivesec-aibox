import { z } from "zod";

// UI data layer over the ViVeSec AIBox adapter's agentic UI channel
// (spec §2.4: /api/v1/ui/ask + /api/v1/ui/poll bounded long-poll).
//
// These are plain async functions, not server functions: the embedded UI is
// served from ViVeSec's resource server and reaches the AIBox through their
// tunnel, which only forwards the versioned /api/v1/... contract. A framework
// route like /_serverFn/<build-hash> would pin every UI build to one exact
// AIBox build and break their fallback-to-latest scheme.
//
// The { data } call shape is kept so callers read the same either way.

import {
  adapterAsk,
  adapterCancelJob,
  adapterChildren,
  adapterDemoConfig,
  adapterSession,
  adapterDownloadGenerated,
  adapterDriveFiles,
  adapterFeedback,
  adapterGeneratedFiles,
  adapterGetJob,
  adapterListJobs,
  adapterMarkJobSeen,
  adapterSave,
  adapterSpeak,
  adapterStatus,
  adapterStatusRaw,
  adapterSubmitJob,
  adapterTranscribe,
  AdapterJobContinuesError,
  defaultDrive as adapterDefaultDrive,
  driveRoot,
  type AdapterAction,
  type AdapterAnswer,
  type AdapterJobStatus,
  type AdapterJobSummary,
  type AdapterVoice,
} from "../rag/adapter-client";

const ACTIONS = [
  "search",
  "summary",
  "report",
  "tracking",
  "presentation",
  "memo",
  "analyze",
] as const;

// The UI sends i18n codes (en/hu/da/de); the adapter's generator expects a
// language NAME (matches adapter/llm.py _REFUSALS). Unknown -> let the adapter
// default (English) by sending undefined.
const LANG_NAME: Record<string, string> = {
  en: "English",
  hu: "Hungarian",
  da: "Danish",
  de: "German",
};
function langName(code?: string): string | undefined {
  if (!code) return undefined;
  return LANG_NAME[code.toLowerCase()] ?? undefined;
}

function baseName(path?: string | null): string {
  if (!path) return "document";
  const trimmed = path.replace(/\/+$/, "");
  const idx = trimmed.lastIndexOf("/");
  return idx >= 0 ? trimmed.slice(idx + 1) || trimmed : trimmed;
}

export type RagCitation = {
  rank: number;
  source: string;
  fileId?: string;
  chunk: number;
  score: number;
  label: string;
  snippet: string;
};

export type RagAskResult = {
  ok: boolean;
  answer: string;
  mode: string;
  backend: string;
  command?: string;
  label?: string;
  /** The quick action the adapter actually ran (echoed back), if any. */
  action?: string;
  /** Filename-mode (#search files:) result list. */
  files?: { path: string; mtime: number | null; size: number | null }[];
  sources: string[];
  citations: RagCitation[];
  confidence?: { score: number; band: "green" | "amber" | "red"; auditId?: string };
  refused?: boolean;
  error?: string;
  backgroundJobId?: string;
};

export type RagHealth = {
  ok: boolean;
  ollama_running?: boolean;
  llm_available?: boolean;
  gen_model?: string;
  embed_model?: string;
  embed_backend?: string;
  index_ready?: boolean;
  mode?: string;
  /** On-box speech engines; absent/false means the box has no voice backend. */
  voice?: AdapterVoice;
  error?: string;
};

export async function ragHealth(): Promise<RagHealth> {
  try {
    const status = await adapterStatus();
    if (!status.ok) {
      return { ok: false, error: status.error ?? "adapter unreachable" };
    }
    return {
      ok: true,
      ollama_running: status.uiReady,
      llm_available: status.uiReady,
      index_ready: status.fsReady,
      embed_backend: status.features.join(", ") || "basic",
      voice: status.voice,
      mode: status.uiReady
        ? "aibox-adapter (/api/v1/ui)"
        : status.locked
          ? "locked"
          : "adapter-not-ready",
    };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "adapter unreachable" };
  }
}

const askInput = z.object({
  query: z.string().min(1),
  lang: z.string().optional(),
  drive: z.string().optional(),
  // Quick actions (function specification v2 F1-F7). The UI's slash command
  // maps to one of these; the adapter then runs the structured task instead
  // of a plain grounded question.
  action: z.enum(ACTIONS).optional(),
  mode: z.enum(["files", "text"]).optional(),
  audience: z.string().optional(),
  purpose: z.string().optional(),
  // Source files picked in the F1/F3/F5 dialog (adapter-side filter).
  files: z.array(z.string()).optional(),
});

export async function askRag({ data }: { data: z.input<typeof askInput> }): Promise<RagAskResult> {
  const empty: RagAskResult = {
    ok: false,
    answer: "",
    mode: "",
    backend: "",
    sources: [],
    citations: [],
  };
  try {
    const result = await adapterAsk({
      query: data.query,
      lang: langName(data.lang),
      drive: data.drive,
      action: data.action as AdapterAction | undefined,
      mode: data.mode,
      audience: data.audience,
      purpose: data.purpose,
      files: data.files,
      topK: data.action ? undefined : 5,
    });

    return ragResultFromAdapter(result);
  } catch (err) {
    if (err instanceof AdapterJobContinuesError) {
      return { ...empty, backgroundJobId: err.jobId };
    }
    return { ...empty, error: err instanceof Error ? err.message : "bridge error" };
  }
}

function ragResultFromAdapter(result: AdapterAnswer): RagAskResult {
  const empty: RagAskResult = {
    ok: false,
    answer: "",
    mode: "",
    backend: "",
    sources: [],
    citations: [],
  };
  if (!result.ok) return { ...empty, error: result.error ?? "adapter error" };
  const citations: RagCitation[] = result.citations.map((c) => ({
      rank: c.ref,
      source: c.path ?? c.chunk_id ?? "document",
      fileId: c.path ?? undefined,
      chunk: typeof c.page_number === "number" ? c.page_number : 0,
      score: Math.round((c.score ?? 0) * 100),
      label: baseName(c.path),
      snippet: c.snippet,
    }));
  const sources = Array.from(new Set(citations.map((c) => c.label)));
  const answer = result.answer?.trim() ? result.answer : (result.hits[0]?.snippet ?? "");
  const refused = !answer;
  return {
    ok: true,
    answer,
    mode: refused ? "refused" : "aibox",
    backend: result.backend ?? "aibox-adapter",
    label: result.corpus_id,
    action: result.action ?? undefined,
    files: result.files,
    sources,
    citations,
    confidence: result.confidence
      ? {
          score: result.confidence.score,
          band: result.confidence.band,
          auditId: result.confidence.audit_id,
        }
      : undefined,
    refused,
  };
}

export type BackgroundJob = AdapterJobSummary;

function adapterInput(data: z.input<typeof askInput>) {
  return {
    query: data.query,
    lang: langName(data.lang),
    drive: data.drive,
    action: data.action as AdapterAction | undefined,
    mode: data.mode,
    audience: data.audience,
    purpose: data.purpose,
    files: data.files,
    topK: data.action ? undefined : 5,
  };
}

export async function submitRagJob({ data }: { data: z.input<typeof askInput> }) {
  return adapterSubmitJob({ ...adapterInput(data), origin: "background" });
}

export function listRagJobs(drive?: string): Promise<BackgroundJob[] | null> {
  return adapterListJobs({ drive });
}

export async function getRagJob(jobId: string, drive?: string): Promise<{
  job: BackgroundJob;
  result?: RagAskResult;
}> {
  const restored = await adapterGetJob(jobId, { drive });
  return {
    job: restored.summary,
    result: restored.answer ? ragResultFromAdapter(restored.answer) : undefined,
  };
}

export async function markRagJobSeen(jobId: string, drive?: string): Promise<void> {
  await adapterMarkJobSeen(jobId, { drive });
}

export async function cancelRagJob(jobId: string, drive?: string): Promise<Record<string, unknown>> {
  return adapterCancelJob(jobId, { drive });
}

export type { AdapterJobStatus };

// ---------------------------------------------------------------------------
// C7 — Save steps: persist a generated answer to the ViVeSecBox drive over the
// adapter's ws-fs channel (B6). When the box rejects the write (permission) or
// no channel is connected, the file stays in the AIBox session store and the
// UI offers a download instead.
// ---------------------------------------------------------------------------

export type SaveFormat = "md" | "txt" | "pdf" | "pptx";

export type SaveResult = {
  ok: boolean;
  transferred: boolean;
  name: string;
  path?: string;
  format?: string;
  size?: number;
  reason?: string;
  canDownload: boolean;
  error?: string;
};

const saveInput = z.object({
  name: z.string().min(1),
  text: z.string().min(1),
  format: z.enum(["md", "txt", "pdf", "pptx"]).optional(),
  title: z.string().optional(),
  drive: z.string().optional(),
});

export async function saveToDrive({
  data,
}: {
  data: z.input<typeof saveInput>;
}): Promise<SaveResult> {
  try {
    const res = await adapterSave({
      name: data.name,
      text: data.text,
      format: data.format,
      title: data.title,
      drive: data.drive,
    });
    return {
      ok: res.ok,
      transferred: res.transferred,
      name: res.name,
      path: res.path,
      format: res.format,
      size: res.size,
      reason: res.reason,
      canDownload: Boolean(res.download),
      error: res.error,
    };
  } catch (err) {
    return {
      ok: false,
      transferred: false,
      name: data.name,
      canDownload: false,
      error: err instanceof Error ? err.message : "save failed",
    };
  }
}

export type GeneratedFile = { name: string; size: number; mtime: number };

// ---------------------------------------------------------------------------
// Answer feedback — one rating per displayed answer. The adapter stores the
// rating joined with the full answer trace (audit id = join key) as JSONL on
// the box; rated exchanges become gold-set candidates for the customer corpus.
// ---------------------------------------------------------------------------

const feedbackInput = z.object({
  auditId: z.string().min(1),
  rating: z.enum(["up", "down"]),
  reason: z.string().optional(),
  comment: z.string().optional(),
  question: z.string().optional(),
  answer: z.string().optional(),
  drive: z.string().optional(),
});

export async function sendFeedback({
  data,
}: {
  data: z.input<typeof feedbackInput>;
}): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await adapterFeedback({
      auditId: data.auditId,
      rating: data.rating,
      reason: data.reason,
      comment: data.comment,
      question: data.question,
      answer: data.answer,
      drive: data.drive,
    });
    return { ok: res.ok, error: res.error };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "feedback failed" };
  }
}

/** Files still held in the AIBox session store: everything the box has not
 *  taken over the ws-fs channel (permission denied / box offline). */
export async function listGeneratedFiles({
  data,
}: {
  data: { drive?: string };
}): Promise<{ ok: boolean; files: GeneratedFile[]; error?: string }> {
  try {
    return { ok: true, files: await adapterGeneratedFiles({ drive: data.drive }) };
  } catch (err) {
    return { ok: false, files: [], error: err instanceof Error ? err.message : "listing failed" };
  }
}

// ---------------------------------------------------------------------------
// Drives — the ViVeSecBox drive is the hard ACL boundary (one drive = one
// corpus). In production the drive comes from the box session; the picker below
// exists only so the demo/test build can switch between the drives on the box.
// ---------------------------------------------------------------------------

export type DriveInfo = { name: string; path: string };
export type DriveList = {
  ok: boolean;
  drives: DriveInfo[];
  current: string;
  picker: boolean;
  error?: string;
};

export async function listDrives(): Promise<DriveList> {
  // Embedded there is no demo-config endpoint, so the picker stays off and the
  // drive comes from the ViVeSecBox session.
  const cfg = await adapterDemoConfig();
  const current = cfg?.drive || adapterDefaultDrive() || (await adapterSession()).drive;
  const picker = cfg?.picker ?? false;
  if (!picker) return { ok: true, drives: [], current, picker: false };
  try {
    const children = await adapterChildren(cfg?.driveRoot ?? driveRoot());
    const drives = children
      .filter((c) => !c.file)
      .map((c) => ({ name: c.path.split("/").filter(Boolean).pop() ?? c.path, path: `${c.path}/` }))
      .sort((a, b) => a.name.localeCompare(b.name));
    return { ok: true, drives, current, picker: true };
  } catch (err) {
    return {
      ok: false,
      drives: [],
      current,
      picker: true,
      error: err instanceof Error ? err.message : "drive listing failed",
    };
  }
}

/** Signed-in identity from the ViVeSecBox session; empty when not embedded. */
export async function sessionUser(): Promise<string> {
  return (await adapterSession()).user;
}

export type DriveFileInfo = {
  path: string;
  name: string;
  size: number | null;
  mtime: number | null;
};
export type DriveEntryInfo = DriveFileInfo & { file: boolean };

/** Reject anything outside the drive: /index/get/children itself is the box
 *  sync endpoint and carries no ACL, so the scope check has to happen here. */
function withinDrive(path: string, drive: string): boolean {
  const root = drive.replace(/\/+$/, "");
  const p = path.replace(/\/+$/, "");
  if (p.includes("..") || !p.startsWith("/")) return false;
  return p === root || p.startsWith(root + "/");
}

export async function listDriveChildren({
  data,
}: {
  data: { drive?: string; path?: string };
}): Promise<{ ok: boolean; entries: DriveEntryInfo[]; error?: string }> {
  const drive = data.drive ?? adapterDefaultDrive();
  const path = (data.path || drive).replace(/\/+$/, "");
  if (!withinDrive(path, drive)) {
    return { ok: false, entries: [], error: "path outside the drive" };
  }
  try {
    const entries = await adapterChildren(path);
    return {
      ok: true,
      entries: entries.map((e) => ({
        path: e.path,
        name: e.path.split("/").pop() ?? e.path,
        size: e.size,
        mtime: e.mtime,
        file: Boolean(e.file),
      })),
    };
  } catch (err) {
    return { ok: false, entries: [], error: err instanceof Error ? err.message : "listing failed" };
  }
}

export async function listDriveFiles({
  data,
}: {
  data: { drive?: string; pattern?: string };
}): Promise<{ ok: boolean; files: DriveFileInfo[]; truncated: boolean; error?: string }> {
  try {
    const res = await adapterDriveFiles(data.drive ?? adapterDefaultDrive(), data.pattern ?? "");
    return {
      ok: true,
      truncated: res.truncated,
      files: res.entries
        .filter((e) => e.file)
        .map((e) => ({
          path: e.path,
          name: e.path.split("/").pop() ?? e.path,
          size: e.size,
          mtime: e.mtime,
        })),
    };
  } catch (err) {
    return {
      ok: false,
      files: [],
      truncated: false,
      error: err instanceof Error ? err.message : "file listing failed",
    };
  }
}

// ---------------------------------------------------------------------------
// Corporate Data Insight (/data): platform telemetry aggregated on the box.
// Only values the box actually reports are filled in — everything else stays
// null and the card says so, rather than showing a plausible-looking number.
// ---------------------------------------------------------------------------

export type InsightValue = number | string | null;

export type PlatformInsight = {
  ok: boolean;
  error?: string;
  /** ViVeSecBox side: what the drive sync has handed over. */
  box: {
    drives: InsightValue;
    files: InsightValue;
    folders: InsightValue;
    dataSizeBytes: InsightValue;
    storageMode: InsightValue;
    wsFsConnected: boolean | null;
    users: InsightValue;
    messages: InsightValue;
    lastBackupTime: InsightValue;
    lastBackupSize: InsightValue;
    perDrive: { name: string; files: number; bytes: number }[];
    types: { ext: string; files: number; bytes: number }[];
  };
  /** AI Box side: index + runtime. */
  ai: {
    documents: InsightValue;
    pages: InsightValue;
    chunks: InsightValue;
    corpora: InsightValue;
    activeSessions: InsightValue;
    generatedFiles: InsightValue;
    features: InsightValue;
    watchdogSeconds: InsightValue;
    llmModel: InsightValue;
    tokensPerSec: InsightValue;
    gpuPartitions: InsightValue;
    powerWatts: InsightValue;
    totalRequests: InsightValue;
  };
};

export async function platformInsight(): Promise<PlatformInsight> {
  const empty: PlatformInsight = {
    ok: false,
    box: {
      drives: null,
      files: null,
      folders: null,
      dataSizeBytes: null,
      storageMode: null,
      wsFsConnected: null,
      users: null,
      messages: null,
      lastBackupTime: null,
      lastBackupSize: null,
      perDrive: [],
      types: [],
    },
    ai: {
      documents: null,
      pages: null,
      chunks: null,
      corpora: null,
      activeSessions: null,
      generatedFiles: null,
      features: null,
      watchdogSeconds: null,
      llmModel: null,
      tokensPerSec: null,
      gpuPartitions: null,
      powerWatts: null,
      totalRequests: null,
    },
  };
  try {
    const st = await adapterStatusRaw();

    // Per-drive rollup from the metadata mirror (no model, no content read).
    const perDrive: { name: string; files: number; bytes: number }[] = [];
    const byExt = new Map<string, { files: number; bytes: number }>();
    let totalBytes = 0;
    let drives = 0;
    try {
      const roots = (await adapterChildren(driveRoot())).filter((c) => !c.file);
      drives = roots.length;
      for (const root of roots) {
        const name = root.path.split("/").filter(Boolean).pop() ?? root.path;
        const entries = (await adapterDriveFiles(`${root.path}/`)).entries.filter((e) => e.file);
        let bytes = 0;
        for (const e of entries) {
          const size = e.size ?? 0;
          bytes += size;
          const ext = (e.path.split("/").pop() ?? "").split(".").pop()?.toLowerCase() ?? "";
          const key = ext && ext.length <= 5 ? ext : "other";
          const acc = byExt.get(key) ?? { files: 0, bytes: 0 };
          acc.files += 1;
          acc.bytes += size;
          byExt.set(key, acc);
        }
        totalBytes += bytes;
        perDrive.push({ name, files: entries.length, bytes });
      }
    } catch {
      // Drive rollup is best-effort: the status counters below still stand.
    }

    const idx = st.index ?? {};
    return {
      ok: true,
      box: {
        drives: drives || null,
        files: st.mirror?.files ?? null,
        folders: st.mirror?.directories ?? null,
        dataSizeBytes: totalBytes || null,
        storageMode: st.storage?.mode ?? null,
        wsFsConnected: st.ws_fs?.connected ?? null,
        // Not reported by the box today — the card must say so.
        users: null,
        messages: null,
        lastBackupTime: null,
        lastBackupSize: null,
        perDrive: perDrive.sort((a, b) => b.files - a.files),
        types: Array.from(byExt.entries())
          .map(([ext, v]) => ({ ext, ...v }))
          .sort((a, b) => b.files - a.files),
      },
      ai: {
        documents: idx.documents ?? null,
        pages: idx.pages ?? null,
        chunks: idx.chunks ?? null,
        corpora: idx.corpora ?? null,
        activeSessions: st.sessions?.active ?? null,
        generatedFiles: st.files?.files ?? null,
        features: st.features?.length ? st.features.join(", ") : null,
        watchdogSeconds: st.watchdog_seconds ?? null,
        // No telemetry endpoint for these yet.
        llmModel: null,
        tokensPerSec: null,
        gpuPartitions: null,
        powerWatts: null,
        totalRequests: null,
      },
    };
  } catch (err) {
    return { ...empty, error: err instanceof Error ? err.message : "insight failed" };
  }
}

export async function downloadGenerated({
  data,
}: {
  data: { name: string; drive?: string };
}): Promise<{
  ok: boolean;
  name: string;
  contentB64?: string;
  contentType?: string;
  error?: string;
}> {
  try {
    return await adapterDownloadGenerated({ name: data.name, drive: data.drive });
  } catch (err) {
    return {
      ok: false,
      name: data.name,
      error: err instanceof Error ? err.message : "download failed",
    };
  }
}

/* ------------------------------- Voice I/O ------------------------------- */

export type TranscriptResult = { ok: boolean; text: string; error?: string };

/** Recorded utterance -> text, transcribed on the box. The text is then sent
 *  through askRag unchanged, so a spoken question is grounded and cited the
 *  same way a typed one is. */
export async function transcribeSpeech({
  data,
}: {
  data: { audio: Blob; lang?: string; drive?: string };
}): Promise<TranscriptResult> {
  try {
    return await adapterTranscribe({
      audio: data.audio,
      lang: langName(data.lang),
      drive: data.drive,
    });
  } catch (err) {
    return { ok: false, text: "", error: err instanceof Error ? err.message : "stt failed" };
  }
}

export type SpeechResult = { ok: boolean; audio?: Blob; truncated?: boolean; error?: string };

/** Answer text -> audio, synthesised on the box. */
export async function synthesizeSpeech({
  data,
}: {
  data: { text: string; lang?: string; drive?: string };
}): Promise<SpeechResult> {
  try {
    return await adapterSpeak({ text: data.text, lang: langName(data.lang), drive: data.drive });
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "tts failed" };
  }
}
