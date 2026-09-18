import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";
import { motion, AnimatePresence } from "framer-motion";
import { LanguageProvider, useLang, LANGS, type Lang } from "./i18n";
import { DataInsightCard } from "./DataInsightCard";
import { getDataInsightCopy } from "./data-insight";
import { DriveExplorerModal } from "./DriveExplorerModal";
import {
  parseMemoTitle,
  parseReport,
  parseSlides,
  parseSummary,
  parseTracking,
  splitAuditFooter,
} from "./live-parse";
import {
  askRag,
  cancelRagJob,
  getRagJob,
  listDriveChildren,
  listDriveFiles,
  listDrives,
  listScope,
  sessionUser,
  listGeneratedFiles,
  listRagJobs,
  markRagJobSeen,
  platformInsight,
  ragHealth,
  saveToDrive,
  sendFeedback,
  submitRagJob,
  downloadGenerated,
  openDriveDocument,
  type DriveInfo,
  type BackgroundJob,
  type GeneratedFile,
  type PlatformInsight,
  type SaveFormat,
  type ScopeDrive,
  type ChatProfile,
  type ChatPolicy,
} from "@/lib/api/rag.functions";
import { useVoice } from "./use-voice";
import {
  ArrowLeft,
  ArrowRight,
  MoreVertical,
  Send,
  Laptop,
  Search,
  RefreshCw,
  List,
  Lock,
  Sparkles,
  FileText,
  FileSpreadsheet,
  FileCode,
  FileBarChart,
  X,
  Crown,
  Check,
  Copy,
  ClipboardList,
  BarChart3,
  StickyNote,
  Search as SearchIcon,
  ChevronRight,
  CornerLeftUp,
  Folder,
  Zap,
  CircleDot,
  Bot,
  Workflow,
  Database,
  Mail,
  Presentation,
  ChevronLeft,
  ChevronDown,
  LayoutTemplate,
  Pencil,
  Link2,
  ShieldCheck,
  CheckSquare,
  AlertTriangle,
  TrendingUp,
  Scale,
  Coins,
  Users,
  Target,
  Home,
  Save,
  Mic,
  Square,
  Volume2,
  VolumeX,
  Loader2,
  Download,
  ThumbsUp,
  ThumbsDown,
} from "lucide-react";

/* ---------- Types ---------- */
// One command per function specification v2 quick action (F1-F7), plus /data.
type SlashCmd =
  | "/summary"
  | "/report"
  | "/tracking"
  | "/memo"
  | "/search"
  | "/presentation"
  | "/data";

type DriveFileType = "pdf" | "docx" | "xlsx" | "csv" | "md" | "txt";

type DriveFile = {
  /** The ViVeSecBox path — also the citation's fileId, so the two line up. */
  id: string;
  name: string;
  type: DriveFileType;
  size: string;
  modified: string;
  /** Folder inside the drive, shown as the file's location. */
  folder?: string;
  preview?: string[];
  pages?: FilePage[];
};

type FilePage = { num: number; heading?: string; paragraphs: string[] };

type ActionItem = { id: string; text: string; assignee: string; done: boolean };

type ReportTask = {
  id: string;
  title: string;
  owner: string;
  status: "On Track" | "Delayed" | "At Risk" | "Done";
};

type Citation = {
  rank?: number;
  fileId: string;
  page: number;
  label: string;
  snippet?: string;
  terms?: string[];
  score?: number;
};

/** A file whose NAME matches the search term (spec F7 2.A), shown alongside
 *  the content answer (2.B) so one query covers both. */
type FileHit = { id: string; name: string; folder?: string; type: DriveFileType };

type SlideLayout = "title" | "bullets" | "chart" | "big-number" | "two-column" | "timeline" | "cta";
type SlideChart = { unit?: string; points: { label: string; value: number; display: string }[] };
type Slide = {
  id: string;
  layout: SlideLayout;
  title: string;
  subtitle?: string;
  bullets?: string[];
  chart?: SlideChart;
  bigNumber?: string;
  bigCaption?: string;
  left?: { title: string; body: string };
  right?: { title: string; body: string };
  milestones?: { phase: string; label: string; detail: string }[];
  cta?: string[];
  source?: Citation;
};

/* Every AI card is rendered from a real AI Box answer, so each carries the
   grounding evidence: the C6 confidence band, the audit footer lines, the
   verbatim answer (what gets saved to the drive) and the citations. */
type LiveMeta = {
  profile?: ChatProfile;
  confidence?: { score: number; band: "green" | "amber" | "red" };
  audit?: string[];
  raw?: string;
  citations?: Citation[];
  /** C6 audit id — the join key for the answer-rating feedback. */
  auditId?: string;
  /** The question that produced this card (feedback fallback echo). */
  question?: string;
  /** The drive the answer was scoped to (feedback routing on the demo build). */
  feedbackDrive?: string;
};

type Message =
  | { id: string; role: "system"; text: string }
  | { id: string; role: "user"; text: string; ts: string; name: string }
  | {
      id: string;
      role: "ai";
      ts: string;
      name: string;
      kind: "text";
      text: string;
      tone?: "normal" | "error";
    }
  | { id: string; role: "ai"; ts: string; name: string; kind: "thinking"; startedAt: number }
  | ({
      id: string;
      role: "ai";
      ts: string;
      name: string;
      kind: "summary";
      title: string;
      bullets: string[];
      actions: ActionItem[];
      email: string;
      edited?: boolean;
    } & LiveMeta)
  | ({
      id: string;
      role: "ai";
      ts: string;
      name: string;
      kind: "report";
      title: string;
      tasks: ReportTask[];
      metrics: { label: string; value: string; tone: "good" | "warn" | "bad" }[];
      narrative?: string[];
    } & LiveMeta)
  | ({
      id: string;
      role: "ai";
      ts: string;
      name: string;
      kind: "memo";
      title: string;
      body: string;
      edited?: boolean;
    } & LiveMeta)
  | ({
      id: string;
      role: "ai";
      ts: string;
      name: string;
      kind: "search";
      query: string;
      answer: string;
      citations: Citation[];
      fileHits?: FileHit[];
    } & LiveMeta)
  | ({
      id: string;
      role: "ai";
      ts: string;
      name: string;
      kind: "deck";
      title: string;
      slides: Slide[];
      exported?: boolean;
      edited?: boolean;
    } & LiveMeta)
  | { id: string; role: "ai"; ts: string; name: string; kind: "file"; fileId: string }
  | {
      id: string;
      role: "ai";
      ts: string;
      name: string;
      kind: "data";
      insight: PlatformInsight | null;
      error?: string;
    };

/* ---------- Column routing ----------
   Where an answer lands is decided by where it was STARTED, not by its shape:
   a question typed in the chat is answered in the chat, a quick action is a
   deliverable and runs entirely in the results column. */

const AGENTS: {
  id: string;
  name: string;
  icon: typeof Sparkles;
  desc: string;
  commands: SlashCmd[];
  /** Only the operations agent is wired to the box; the rest are roadmap. */
  available: boolean;
}[] = [
  {
    id: "operations",
    name: "Operations Assistant",
    icon: Workflow,
    desc: "Day-to-day operations & playbooks",
    commands: ["/summary", "/report", "/tracking", "/memo", "/search", "/presentation", "/data"],
    available: true,
  },
  {
    id: "legal",
    name: "Legal Operations",
    icon: Scale,
    desc: "Contracts, clauses & legal risk",
    commands: [],
    available: false,
  },
  {
    id: "finance",
    name: "Financial Desk",
    icon: Coins,
    desc: "Budgets, forecasts & invoicing",
    commands: [],
    available: false,
  },
  {
    id: "talent",
    name: "Talent & Workforce",
    icon: Users,
    desc: "Hiring, policies & people ops",
    commands: [],
    available: false,
  },
  {
    id: "strategy",
    name: "Grant Strategist",
    icon: Target,
    desc: "Grants, proposals & impact",
    commands: [],
    available: false,
  },
  {
    id: "sales",
    name: "Sales Operations",
    icon: TrendingUp,
    desc: "Pipeline, accounts & outreach",
    commands: [],
    available: false,
  },
  {
    id: "compliance",
    name: "Compliance Officer",
    icon: ShieldCheck,
    desc: "Controls, audits & regulation",
    commands: [],
    available: false,
  },
  {
    id: "chiefofstaff",
    name: "Chief of Staff",
    icon: Crown,
    desc: "Executive decisions & board briefs",
    commands: [],
    available: false,
  },
];

const SLASH_COMMANDS: { cmd: SlashCmd; label: string; desc: string; icon: typeof Sparkles }[] = [
  {
    cmd: "/summary",
    label: "/summary",
    desc: "Create meeting summary & follow-up",
    icon: ClipboardList,
  },
  { cmd: "/report", label: "/report", desc: "Generate weekly status report", icon: BarChart3 },
  {
    cmd: "/tracking",
    label: "/tracking",
    desc: "Project summary & task tracking",
    icon: CheckSquare,
  },
  { cmd: "/memo", label: "/memo", desc: "Draft a decision memo", icon: StickyNote },
  { cmd: "/search", label: "/search", desc: "Intelligent knowledge retrieval", icon: SearchIcon },
  {
    cmd: "/presentation",
    label: "/presentation",
    desc: "Draft a slide deck from a document",
    icon: Presentation,
  },
  {
    cmd: "/data",
    label: "/data",
    desc: "Corporate data insight & platform status",
    icon: Database,
  },
];

const LIME = "#C6F24E";
const PANEL = "#2A2F35";
const PANEL_HI = "#333941";
const BG = "#1E2225";

/** Staging delay before an assistant message appears (the "typing" beat). */
const AI_REPLY_DELAY_MS = 350;

/** Image types the document viewer renders inline (adapter _INLINE_TYPES). */
const INLINE_IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);
/** Characters of a text document rendered at once; the rest is announced. */
const TEXT_VIEW_LIMIT = 400_000;

/* ---------- Active drive ----------
   Cards deep in the tree (save buttons) need the drive that produced them; the
   drive is the ACL boundary, so it must travel with every adapter call. */
const DriveContext = createContext<{ drive: string }>({ drive: "" });
function useDrive() {
  return useContext(DriveContext);
}

/* ---------- Slash command -> AIBox quick action ----------
   The adapter (adapter/service.py parse_action) routes the spec quick actions
   F1-F7 from an explicit payload action or a typed "#action" prefix. The demo
   UI uses "/" commands, so the mapping has to be explicit. /data has no adapter
   counterpart — it reads platform telemetry, not the document corpus. */
type LiveAction =
  | "search"
  | "summary"
  | "report"
  | "tracking"
  | "presentation"
  | "memo"
  | "analyze";
const LIVE_ACTIONS: Record<string, LiveAction> = {
  "/search": "search",
  "/summary": "summary",
  "/report": "report",
  "/tracking": "tracking",
  "/memo": "memo",
  "/presentation": "presentation",
  // Not offered in the slash menu: it needs a document, so it is reached from
  // the preview pane's "Analyze with AI" button.
  "/analyze": "analyze",
};

/** Dialog answers collected before a quick-action run (spec "UX logic"). */
type LiveExtras = {
  audience?: string;
  purpose?: string;
  /** Source files picked in the drive dialog — the adapter filters on these. */
  files?: string[];
  extra?: string;
  situation?: string;
  coverage?: string;
  reportType?: string;
  aspect?: string;
  keywords?: string;
  outcome?: string;
};

/* ---------- Guided dialogs (spec F1/F3/F5 "UX logic") ----------
   The spec walks the user through audience / purpose / source files before the
   model runs. The answers are not decoration: audience+purpose travel to the
   generator as PARAMETERS, the picked files become the adapter's retrieval
   source filter. */
type WizardKind = "summary" | "presentation" | "memo" | "report" | "search";
type WizardStep =
  | "audience"
  | "purpose"
  | "sources"
  | "extra"
  | "situation"
  | "subject"
  | "coverage"
  | "reportType"
  | "aspect"
  | "keywords";

const WIZARD_STEPS: Record<WizardKind, WizardStep[]> = {
  summary: ["coverage", "purpose", "sources", "extra"],
  presentation: ["audience", "purpose", "sources", "extra"],
  memo: ["audience", "purpose", "sources", "extra", "situation"],
  report: ["reportType", "coverage", "aspect", "audience", "sources"],
  search: ["subject", "keywords"],
};

/** The "subject" question only makes sense when the user has not already
 *  typed what they are after: "/search Q2 revenue" has answered it. */
function stepsFor(kind: WizardKind, brief: string): WizardStep[] {
  const steps = WIZARD_STEPS[kind];
  return brief.trim() ? steps.filter((s) => s !== "subject") : steps;
}

const REPORT_TYPE_OPTIONS = [
  "Quick report — short, summarized, bullet points",
  "General report — detailed and descriptive",
];

const ASPECT_OPTIONS = [
  "General (no specific aspect)",
  "Marketing",
  "Sales",
  "Financial",
  "IT",
  "HR",
  "Business strategy",
];

/** Steps answered by picking one prebuilt option. */
const CHOICE_OPTIONS: Partial<Record<WizardStep, string[]>> = {
  reportType: REPORT_TYPE_OPTIONS,
  aspect: ASPECT_OPTIONS,
};

/** Steps answered by typing, with the hint shown in the input. */
const TEXT_PLACEHOLDER: Partial<Record<WizardStep, string>> = {
  audience: "e.g. the board of directors",
  situation: "Describe the decision to be made",
  subject: "Describe what you are looking for",
  coverage: "e.g. the Q2 results and the risks behind them",
  keywords: "e.g. budget, deadline, risk",
};

/** Spec 3.2 / 5.2: prebuilt purpose answers; "Else" opens a free-text field.
 *  A kind without options goes straight to the free-text field. */
const PURPOSE_OPTIONS: Partial<Record<WizardKind, string[]>> = {
  presentation: ["Project summary & review (general)", "Project pitch", "Board update"],
  memo: [
    "Situation analysis with 3 suggested solution scenarios",
    "Decision making support backed up by data",
    "Find new perspectives",
  ],
};

const WIZARD_NOUN: Record<WizardKind, string> = {
  summary: "meeting summary",
  presentation: "presentation",
  memo: "decision making memo",
  report: "report",
  search: "search",
};

const WIZARD_CMD: Record<WizardKind, SlashCmd> = {
  summary: "/summary",
  presentation: "/presentation",
  memo: "/memo",
  report: "/report",
  search: "/search",
};

const WIZARD_OF: Record<string, WizardKind> = {
  "/summary": "summary",
  "/presentation": "presentation",
  "/memo": "memo",
  "/report": "report",
  "/search": "search",
};

function wizardQuestion(kind: WizardKind, step: WizardStep): string {
  const noun = WIZARD_NOUN[kind];
  switch (step) {
    case "audience":
      return `Who is the audience of the ${noun}?`;
    case "purpose":
      return `What is the purpose of the ${noun}?`;
    case "sources":
      return `Select the file(s) to be used as source for the ${noun}.`;
    case "extra":
      return `Is there any other relevant file or information that could be important for getting the best result with the ${noun}?`;
    case "situation":
      return "Please describe the decision making situation.";
    case "subject":
      return kind === "search"
        ? "What are you searching for?"
        : `What would you like the ${noun} to be about?`;
    case "coverage":
      return `What should the ${noun} cover?`;
    case "reportType":
      return "What type of report do you want?";
    case "aspect":
      return "Select the aspect of the report.";
    case "keywords":
      return "What words or keywords should it focus on?";
  }
}

type WizardState = {
  kind: WizardKind;
  /** The free text typed after the command, kept for the final request. */
  brief: string;
  /** Which column the run was started from, so the answer lands there. */
  pane: "chat" | "results";
  stepIndex: number;
  audience: string;
  purpose: string;
  files: string[];
  extra: string;
  situation: string;
  subject: string;
  coverage: string;
  reportType: string;
  aspect: string;
  keywords: string;
};

/** Split "/report last week" into { cmd: "/report", rest: "last week" }. */
function splitCommand(v: string): { cmd: string | null; rest: string } {
  const trimmed = v.trim();
  if (!trimmed.startsWith("/")) return { cmd: null, rest: trimmed };
  const sp = trimmed.search(/\s/);
  if (sp < 0) return { cmd: trimmed.toLowerCase(), rest: "" };
  return { cmd: trimmed.slice(0, sp).toLowerCase(), rest: trimmed.slice(sp + 1).trim() };
}

/* ---------- Helpers ---------- */
const nowTs = () => {
  const d = new Date();
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
};
const uid = () => Math.random().toString(36).slice(2, 9);

const fileIcon = (t: DriveFile["type"]) => {
  switch (t) {
    case "pdf":
      return FileText;
    case "xlsx":
      return FileSpreadsheet;
    case "csv":
      return FileSpreadsheet;
    case "docx":
      return FileText;
    case "md":
      return FileCode;
    default:
      return FileBarChart;
  }
};
const fileTint = (t: DriveFile["type"]) => {
  switch (t) {
    case "pdf":
      return "text-rose-400 bg-rose-400/10";
    case "xlsx":
      return "text-emerald-400 bg-emerald-400/10";
    case "csv":
      return "text-emerald-400 bg-emerald-400/10";
    case "docx":
      return "text-sky-400 bg-sky-400/10";
    case "md":
      return "text-violet-400 bg-violet-400/10";
    default:
      return "text-amber-400 bg-amber-400/10";
  }
};

/* ---------- Real drive files (adapter metadata mirror) ---------- */

const FILE_TYPES: DriveFileType[] = ["pdf", "docx", "xlsx", "csv", "md", "txt"];

function fileTypeOf(name: string): DriveFileType {
  const ext = (name.split(".").pop() ?? "").toLowerCase();
  return (FILE_TYPES as string[]).includes(ext) ? (ext as DriveFileType) : "txt";
}

function humanSize(bytes: number | null): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The box reports mtime in nanoseconds; tolerate s / ms / ns. */
function humanMtime(mtime: number | null): string {
  if (!mtime) return "—";
  let ms = mtime;
  while (ms > 4_102_444_800_000) ms = Math.floor(ms / 1000); // > year 2100 -> too fine-grained
  if (ms < 4_102_444_800) ms *= 1000; // seconds -> ms
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "—";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} · ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** The drive segment of a box-absolute path (`/storage/drives/<name>/...`). */
function driveNameOf(path?: string): string {
  const parts = (path ?? "").split("/").filter(Boolean);
  return parts[0] === "storage" && parts[1] === "drives" ? (parts[2] ?? "") : "";
}

function toDriveFile(
  f: { path: string; name: string; size: number | null; mtime: number | null },
  driveRoot: string,
): DriveFile {
  const rel = f.path.startsWith(driveRoot) ? f.path.slice(driveRoot.length) : f.path;
  const folder = rel.includes("/") ? rel.slice(0, rel.lastIndexOf("/")) : "";
  return {
    id: f.path,
    name: f.name,
    type: fileTypeOf(f.name),
    size: humanSize(f.size),
    modified: humanMtime(f.mtime),
    folder,
  };
}

/* ---------- Live task status mapping ---------- */
/** Map the model's free-form status word onto the tracker chip states. */
function toTaskStatus(s: string): ReportTask["status"] {
  const v = (s || "").toLowerCase();
  if (/done|complete|achieved|closed/.test(v)) return "Done";
  if (/delayed|late|overdue|blocked/.test(v)) return "Delayed";
  if (/risk|attention|warning/.test(v)) return "At Risk";
  return "On Track";
}

/* =========================================================== */

export function ViveSecApp() {
  return (
    <LanguageProvider>
      <ViveSecAppInner />
    </LanguageProvider>
  );
}

function ViveSecAppInner() {
  const { t, tm, lang } = useLang();
  const [messages, setMessages] = useState<Message[]>(() => [
    {
      id: "sys1",
      role: "system",
      text: "This is the beginning of 'ViVeSec AI'. All messages are protected by strong encryption.",
    },
  ]);
  const [files, setFiles] = useState<DriveFile[]>([]);
  const [folders, setFolders] = useState<{ path: string; name: string }[]>([]);
  const [cwd, setCwd] = useState("");
  const [searchHits, setSearchHits] = useState<DriveFile[] | null>(null);
  const [searchTruncated, setSearchTruncated] = useState(false);
  const [filesLoading, setFilesLoading] = useState(false);
  const [filesError, setFilesError] = useState<string>("");
  const [input, setInput] = useState("");
  const [showSlash, setShowSlash] = useState(false);
  const [driveOpen, setDriveOpen] = useState(false);
  const [previewFile, setPreviewFile] = useState<DriveFile | null>(null);
  const [previewCitation, setPreviewCitation] = useState<Citation | null>(null);
  const [viewerMode, setViewerMode] = useState<"side" | "split">("side");
  const [viewerCitations, setViewerCitations] = useState<Citation[]>([]);
  const [viewerIndex, setViewerIndex] = useState(0);
  const [highlightedFileId, setHighlightedFileId] = useState<string | null>(null);
  const [driveSearch, setDriveSearch] = useState("");
  const [driveView, setDriveView] = useState<"grid" | "list">("list");
  const [activeAgent, setActiveAgent] = useState("operations");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [explorerOpen, setExplorerOpen] = useState(false);
  const [agentMenuOpen, setAgentMenuOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const resultScrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Below lg the three columns cannot coexist, so chat and results take turns.
  const [mobilePane, setMobilePane] = useState<"chat" | "results">("chat");

  // On wide screens the drive is part of the workspace, not an overlay, so it
  // starts open; the document viewer then opens over it. Set after mount to
  // keep the server-rendered markup and the first client render identical.
  useEffect(() => {
    if (window.matchMedia("(min-width: 1024px)").matches) setDriveOpen(true);
  }, []);
  // Message ids that belong to the results column (quick-action runs).
  const [resultIds, setResultIds] = useState<ReadonlySet<string>>(() => new Set());
  const [pendingAction, setPendingAction] = useState<SlashCmd | null>(null);
  const [actionBrief, setActionBrief] = useState("");

  /* ---------- AI Box connection ----------
     Every answer is produced by the AI Box (adapter + local LLM); there is no
     offline/mock answer path. The connection state only decides whether the
     composer accepts input, so the user is never shown a fabricated answer. */
  const [boxState, setBoxState] = useState<"probing" | "online" | "offline">("probing");
  const [boxDetail, setBoxDetail] = useState<string>("");
  const [liveBusy, setLiveBusy] = useState(false);
  const [chatPolicy, setChatPolicy] = useState<ChatPolicy>({
    policy: "locked_grounded", default_profile: "grounded", allow_switch: false,
  });
  const [chosenProfile, setChosenProfile] = useState<ChatProfile | null>(null);
  const chatProfile: ChatProfile = chatPolicy.allow_switch
    ? chosenProfile ?? chatPolicy.default_profile
    : chatPolicy.default_profile;
  const [messageProfiles, setMessageProfiles] = useState<Record<string, ChatProfile>>({});
  const [backgroundJobs, setBackgroundJobs] = useState<BackgroundJob[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [jobsError, setJobsError] = useState("");
  const [jobsSupported, setJobsSupported] = useState<boolean | null>(null);
  const jobsSupportedRef = useRef<boolean | null>(null);
  const hydratedJobsRef = useRef(new Set<string>());
  // Which speech engines this box actually has (adapter/voice.py); both off by
  // default, so a box without a voice backend looks exactly as it does today.
  const [voiceCaps, setVoiceCaps] = useState({ stt: false, tts: false });

  const probeBox = useCallback(async () => {
    setBoxState((s) => (s === "online" ? s : "probing"));
    try {
      const h = await ragHealth();
      setBoxState(h.ok ? "online" : "offline");
      setBoxDetail(h.ok ? (h.mode ?? "") : (h.error ?? ""));
      setVoiceCaps({ stt: Boolean(h.voice?.stt), tts: Boolean(h.voice?.tts) });
      if (h.ok) {
        const policy = h.chatPolicy;
        setChatPolicy(policy && ["locked_hybrid", "selectable_grounded", "selectable_hybrid"].includes(policy.policy)
          ? { ...policy, allow_switch: policy.policy !== "locked_hybrid",
              default_profile: policy.policy === "selectable_grounded" ? "grounded" : "hybrid" }
          : { policy: "locked_grounded", default_profile: "grounded", allow_switch: false });
      }
      return h.ok;
    } catch (err) {
      setBoxState("offline");
      setBoxDetail(err instanceof Error ? err.message : "unreachable");
      return false;
    }
  }, []);

  useEffect(() => {
    void probeBox();
    // Re-probe while offline so the UI recovers on its own once the box is back.
    const id = window.setInterval(() => {
      setBoxState((s) => {
        if (s !== "offline") return s;
        void probeBox();
        return s;
      });
    }, 15_000);
    return () => window.clearInterval(id);
  }, [probeBox]);

  /* ---------- Drive (the ACL boundary: one drive = one corpus) ----------
     In production the ViVeSecBox session pins the drive. The picker below is a
     demo/test affordance the server only advertises when it is explicitly
     enabled (ADAPTER_DEMO_DRIVE_PICKER=1). */
  const [drives, setDrives] = useState<DriveInfo[]>([]);
  const [drivePicker, setDrivePicker] = useState(false);
  const [drive, setDrive] = useState<string>("");

  /* ---------- Search scope (which drives the answer may draw from) --------
     The entitlement is resolved on the box and never reaches the browser, so
     the UI can only narrow what /ui/scope reports. An empty selection means
     "every entitled drive"; the adapter re-validates every request anyway. */
  const [scopeDrives, setScopeDrives] = useState<ScopeDrive[]>([]);
  const [selectedDrives, setSelectedDrives] = useState<string[]>([]);

  // The drive binding comes from the box (/ui/init). If the box was unreachable
  // when the page loaded, keep asking whenever it is reachable again: every
  // drive-bound feature (file panel, scope, jobs) waits on this value.
  useEffect(() => {
    if (drive || boxState === "offline") return;
    let cancelled = false;
    listDrives()
      .then((d) => {
        if (cancelled) return;
        setDrivePicker(d.picker);
        setDrives(d.drives);
        const saved =
          typeof window !== "undefined" ? window.localStorage.getItem("vivesec_drive") : null;
        const known = (p: string | null) =>
          !!p && (!d.drives.length || d.drives.some((x) => x.path === p));
        setDrive(d.picker && known(saved) ? (saved as string) : d.current);
      })
      .catch(() => {
        if (!cancelled) setDrive("");
      });
    return () => {
      cancelled = true;
    };
  }, [drive, boxState]);

  useEffect(() => {
    const saved = window.localStorage.getItem("vivesec_scope");
    if (!saved) return;
    try {
      const parsed = JSON.parse(saved);
      if (Array.isArray(parsed)) setSelectedDrives(parsed.map(String));
    } catch {
      window.localStorage.removeItem("vivesec_scope");
    }
  }, []);

  useEffect(() => {
    if (!drive || boxState !== "online") return;
    let cancelled = false;
    void listScope(drive).then((scope) => {
      if (cancelled) return;
      setScopeDrives(scope.drives);
      // A grant can be withdrawn between sessions; drop anything stale so the
      // box never has to answer 403 for a selection the UI still remembers.
      const entitled = new Set(scope.drives.map((d) => d.path));
      setSelectedDrives((current) => current.filter((path) => entitled.has(path)));
    });
    return () => {
      cancelled = true;
    };
  }, [drive, boxState]);

  useEffect(() => {
    window.localStorage.setItem("vivesec_scope", JSON.stringify(selectedDrives));
  }, [selectedDrives]);

  const toggleScopeDrive = useCallback((path: string) => {
    setSelectedDrives((current) =>
      current.includes(path) ? current.filter((p) => p !== path) : [...current, path],
    );
  }, []);

  /** Metadata for every file seen so far, keyed by box path. Browsing only
   *  loads one folder at a time, but citations point anywhere in the drive. */
  const fileIndexRef = useRef(new Map<string, DriveFile>());

  const rememberFiles = useCallback((list: DriveFile[]) => {
    for (const f of list) fileIndexRef.current.set(f.id, f);
  }, []);

  const loadFolder = useCallback(
    async (drivePath: string, folderPath?: string) => {
      const root = drivePath.replace(/\/+$/, "");
      const path = folderPath || root;
      setFilesLoading(true);
      setFilesError("");
      try {
        const res = await listDriveChildren({ data: { drive: drivePath, path } });
        if (!res.ok) throw new Error(res.error || "file listing failed");
        const rootSlash = root + "/";
        const nextFiles = res.entries
          .filter((e) => e.file)
          .map((e) => toDriveFile(e, rootSlash))
          .sort((a, b) => a.name.localeCompare(b.name));
        const nextFolders = res.entries
          .filter((e) => !e.file)
          .map((e) => ({ path: e.path, name: e.name }))
          .sort((a, b) => a.name.localeCompare(b.name));
        rememberFiles(nextFiles);
        setFiles(nextFiles);
        setFolders(nextFolders);
        setCwd(path);
      } catch (err) {
        setFiles([]);
        setFolders([]);
        setFilesError(err instanceof Error ? err.message : "file listing failed");
      } finally {
        setFilesLoading(false);
      }
    },
    [rememberFiles],
  );

  useEffect(() => {
    if (!drive || boxState !== "online") return;
    void loadFolder(drive);
  }, [drive, boxState, loadFolder]);

  // Searching spans the whole drive, so it goes back to the box (`#search
  // files:`) instead of filtering the folder currently on screen.
  useEffect(() => {
    const term = driveSearch.trim();
    if (term.length < 2) {
      setSearchHits(null);
      setSearchTruncated(false);
      return;
    }
    if (!drive || boxState !== "online") return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      const res = await listDriveFiles({ data: { drive, pattern: term } });
      if (cancelled) return;
      const rootSlash = drive.replace(/\/+$/, "") + "/";
      const hits = res.ok ? res.files.map((f) => toDriveFile(f, rootSlash)) : [];
      rememberFiles(hits);
      setSearchHits(hits);
      setSearchTruncated(Boolean(res.truncated));
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [driveSearch, drive, boxState, rememberFiles]);

  function selectDrive(path: string) {
    setDrive(path);
    if (typeof window !== "undefined") window.localStorage.setItem("vivesec_drive", path);
    setPreviewFile(null);
    setPreviewCitation(null);
    setDriveSearch("");
  }

  const driveName = (drive || "").replace(/\/+$/, "").split("/").pop() ?? "";

  /* ---------- Results column ---------- */
  const markResult = useCallback((id: string) => {
    setResultIds((prev) => new Set(prev).add(id));
  }, []);
  const chatMessages = useMemo(
    () => messages.filter((m) => !resultIds.has(m.id) &&
      (m.role === "system" || (messageProfiles[m.id] ?? "grounded") === chatProfile)),
    [messages, resultIds, messageProfiles, chatProfile],
  );
  const resultMessages = useMemo(
    () => messages.filter((m) => resultIds.has(m.id)),
    [messages, resultIds],
  );
  const activeBackgroundJobs = backgroundJobs.filter(
    (job) => job.status === "queued" || job.status === "running",
  ).length;
  const unseenBackgroundJobs = backgroundJobs.filter(
    (job) => job.status === "done" && !job.seen_ts,
  ).length;
  const visibleResultMessages = resultMessages.filter(
    (message) =>
      !message.id.startsWith("job-") || message.id === `job-${selectedJobId ?? ""}`,
  );
  const lastResultId = resultMessages[resultMessages.length - 1]?.id ?? null;

  const scrollResultIntoView = useCallback((id: string) => {
    window.requestAnimationFrame(() => {
      const container = resultScrollRef.current;
      const el = container?.querySelector<HTMLElement>(`[data-result-id="${id}"]`);
      if (!container || !el) return;
      const top =
        el.getBoundingClientRect().top -
        container.getBoundingClientRect().top +
        container.scrollTop -
        8;
      container.scrollTo({ top, behavior: "smooth" });
    });
  }, []);

  /** Jump from the conversation reference to the card itself. */
  const focusResult = useCallback(
    (id: string) => {
      setMobilePane("results");
      scrollResultIntoView(id);
    },
    [scrollResultIntoView],
  );

  useEffect(() => {
    if (lastResultId) scrollResultIntoView(lastResultId);
  }, [lastResultId, scrollResultIntoView]);

  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    const last = chatMessages[chatMessages.length - 1];
    if (!last) return;
    // For AI answers, align the start of the answer with the top of the viewport
    // so users don't have to scroll back up to read the beginning. For user
    // messages, keep the classic scroll-to-bottom so the just-sent message and
    // typing indicator are visible.
    const id = window.requestAnimationFrame(() => {
      if (last.role === "ai") {
        const el = container.querySelector<HTMLElement>(`[data-msg-id="${last.id}"]`);
        if (el) {
          // Compute offset using bounding rects to avoid offsetParent quirks
          // (offsetTop is relative to the nearest positioned ancestor, which
          // is not always the scroll container). This keeps the AI message
          // header (avatar + name) anchored to the top of the visible area.
          const cRect = container.getBoundingClientRect();
          const eRect = el.getBoundingClientRect();
          const top = eRect.top - cRect.top + container.scrollTop - 8;
          container.scrollTo({ top, behavior: "smooth" });
          return;
        }
      }
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    });
    return () => window.cancelAnimationFrame(id);
  }, [chatMessages]);

  /* ---------- Tail spacer ----------
     The last answer has to be able to scroll its top to the top of the
     viewport, which needs some empty room below it. A fixed spacer (e.g. 70vh)
     overshoots: once the last answer is tall enough you can keep scrolling into
     nothing. So the spacer is exactly "viewport minus the last message" — and
     only for answers, so a short conversation has nothing to scroll at all. */
  const [tailSpace, setTailSpace] = useState(0);
  useEffect(() => {
    const container = scrollRef.current;
    const last = chatMessages[chatMessages.length - 1];
    if (!container || !last || last.role !== "ai") {
      setTailSpace(0);
      return;
    }
    const el = container.querySelector<HTMLElement>(`[data-msg-id="${last.id}"]`);
    if (!el) {
      setTailSpace(0);
      return;
    }
    const measure = () => {
      // 24px keeps the column gap + bottom padding from adding dead space.
      const need = container.clientHeight - el.getBoundingClientRect().height - 24;
      setTailSpace(Math.max(0, Math.round(need)));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(container);
    ro.observe(el);
    return () => ro.disconnect();
  }, [chatMessages]);

  // Global keyboard shortcuts: "/" focuses input + opens slash menu, Esc closes it
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName;
      const typing = tag === "INPUT" || tag === "TEXTAREA" || (t?.isContentEditable ?? false);
      if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        textareaRef.current?.focus();
        setInput((v) => (v.startsWith("/") ? v : "/"));
        setShowSlash(true);
      } else if (e.key === "Escape" && showSlash) {
        setShowSlash(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showSlash]);

  /* ---------- Signed-in identity ----------
     The ViVeSecBox session carries the user; until it hands one over, the
     localized label stands in. */
  const [userName, setUserName] = useState("");
  const senderName = userName || t.youLabel;

  useEffect(() => {
    if (userName || boxState === "offline") return;
    let cancelled = false;
    sessionUser()
      .then((name) => {
        if (!cancelled) setUserName(name);
      })
      .catch(() => {
        if (!cancelled) setUserName("");
      });
    return () => {
      cancelled = true;
    };
  }, [userName, boxState]);

  /* ---------- Send / commands ---------- */
  function pushUser(text: string) {
    const id = uid();
    setMessageProfiles((profiles) => ({ ...profiles, [id]: chatProfile }));
    setMessages((m) => [...m, { id, role: "user", name: senderName, ts: nowTs(), text }]);
    return id;
  }
  function pushAi(msg: Message) {
    setMessageProfiles((profiles) => ({ ...profiles, [msg.id]: chatProfile }));
    setTimeout(() => setMessages((m) => [...m, msg]), AI_REPLY_DELAY_MS);
  }

  // Corporate Data Insight (/data): platform telemetry read from the box
  // itself. Values the box does not report are shown as "no data".
  function runData(pane: "chat" | "results" = "chat") {
    const c = getDataInsightCopy(lang);
    const introId = uid();
    const cardId = uid();
    if (pane === "results") {
      markResult(introId);
      markResult(cardId);
    }
    const request = platformInsight().then(
      (insight) => ({ insight: insight.ok ? insight : null, error: insight.error }),
      (err: unknown) => ({ insight: null, error: err instanceof Error ? err.message : "error" }),
    );
    pushAi({ id: introId, role: "ai", name: c.persona, ts: nowTs(), kind: "text", text: c.intro });
    pushAi({ id: cardId, role: "ai", name: c.persona, ts: nowTs(), kind: "data", insight: null });
    // On the box the telemetry answers in ~200 ms, i.e. before the staged card
    // is in state; patching it right away would land on nothing and leave the
    // card loading forever. This timer is queued after the card's own.
    setTimeout(() => {
      void request.then(({ insight, error }) =>
        setMessages((all) =>
          all.map((m) =>
            m.id === cardId && "kind" in m && m.kind === "data" ? { ...m, insight, error } : m,
          ),
        ),
      );
    }, AI_REPLY_DELAY_MS);
  }

  /* ---------- The single answer path: the AI Box ----------
     Every command maps to a spec quick action (F1-F7); /data is the only
     UI-side command and reads platform telemetry instead of the corpus. */

  /** Build a live card out of an adapter answer, keeping the structure the
   *  task instruction asked for. Falls back to the plain answer card whenever
   *  the model's output does not match — never invents content. */
  function buildLiveMessage(
    id: string,
    action: LiveAction | undefined,
    label: string,
    res: Awaited<ReturnType<typeof askRag>>,
    fileHits?: FileHit[],
    question?: string,
  ): Message {
    const citations: Citation[] = res.citations.map((c) => ({
      rank: c.rank,
      fileId: c.fileId || c.source,
      page: typeof c.chunk === "number" ? c.chunk : 0,
      label: c.label,
      snippet: c.snippet,
      score: c.score,
      terms: [],
    }));
    const { body, audit } = splitAuditFooter(res.answer);
    const base = {
      id,
      role: "ai" as const,
      name: "ViVeSec AI",
      ts: nowTs(),
      confidence: res.confidence,
      profile: res.profile,
      audit,
      raw: res.answer,
      citations,
      auditId: res.auditId ?? res.confidence?.auditId,
      question: question ?? label,
      feedbackDrive: drive || undefined,
    };
    const plain: Message = {
      ...base,
      kind: "search",
      query: label,
      answer: res.answer,
      citations,
      fileHits,
    };
    if (res.refused || !body.trim()) return plain;

    if (action === "summary") {
      const p = parseSummary(body);
      if (p) {
        return {
          ...base,
          kind: "summary",
          title: label,
          bullets: p.bullets,
          actions: p.actions.map((a) => ({
            id: uid(),
            text: a.text,
            assignee: a.assignee,
            done: false,
          })),
          email: p.email,
        };
      }
    }
    if (action === "report") {
      const p = parseReport(body);
      if (p) {
        return {
          ...base,
          kind: "report",
          title: label,
          metrics: p.metrics.map((m) => ({ ...m, tone: "good" as const })),
          narrative: p.narrative,
          tasks: p.tasks.map((tk) => ({
            id: uid(),
            title: tk.title,
            owner: tk.owner,
            status: toTaskStatus(tk.status),
          })),
        };
      }
    }
    if (action === "tracking") {
      const p = parseTracking(body);
      if (p) {
        return {
          ...base,
          kind: "report",
          title: label,
          metrics: p.health
            ? [
                {
                  label: "Health",
                  value: p.health,
                  tone:
                    p.health === "On Track"
                      ? ("good" as const)
                      : p.health === "At Risk"
                        ? ("warn" as const)
                        : ("bad" as const),
                },
              ]
            : [],
          narrative: p.healthNote ? [p.healthNote] : [],
          tasks: [
            ...p.milestones.map((m) => ({
              id: uid(),
              title: m,
              owner: "◆",
              status: "On Track" as const,
            })),
            ...p.tasks.map((tk) => ({
              id: uid(),
              title: tk.title,
              owner: tk.owner,
              status: toTaskStatus(tk.status),
            })),
          ],
        };
      }
    }
    if (action === "presentation") {
      const slides = parseSlides(body);
      if (slides) {
        return {
          ...base,
          kind: "deck",
          title: parseMemoTitle(body) ?? label,
          slides: slides.map((s, i) => ({
            id: uid(),
            layout:
              i === 0 ? ("title" as const) : s.chart ? ("chart" as const) : ("bullets" as const),
            title: s.title,
            subtitle: s.subtitle ?? (i === 0 ? s.bullets[0] : undefined),
            bullets: i === 0 ? undefined : s.bullets,
            chart: s.chart,
          })),
        };
      }
    }
    if (action === "memo") {
      return { ...base, kind: "memo", title: parseMemoTitle(body) ?? label, body };
    }
    return plain;
  }

  /** Spec F7 2.A — filename lookup, run alongside the content search so one
   *  /search covers both. The adapter's `#search files:` does the matching over
   *  the whole drive; one call per token keeps the old any-token behaviour. */
  async function searchFileNames(term: string): Promise<FileHit[]> {
    const tokens = Array.from(
      new Set(
        term
          .toLowerCase()
          .split(/[^\p{L}\p{N}_.-]+/u)
          .filter((t) => t.length >= 3),
      ),
    ).slice(0, 3);
    if (!tokens.length) return [];
    const rootSlash = (drive || "").replace(/\/+$/, "") + "/";
    const results = await Promise.all(
      tokens.map((t) => listDriveFiles({ data: { drive: drive || undefined, pattern: t } })),
    );
    const seen = new Map<string, FileHit>();
    for (const res of results) {
      if (!res.ok) continue;
      for (const f of res.files) {
        if (seen.has(f.path)) continue;
        const df = toDriveFile(f, rootSlash);
        fileIndexRef.current.set(df.id, df);
        seen.set(f.path, { id: df.id, name: df.name, folder: df.folder, type: df.type });
      }
    }
    return Array.from(seen.values()).slice(0, 8);
  }

  useEffect(() => {
    if (!drive || boxState !== "online") return;
    let disposed = false;
    let syncing = false;

    async function syncJobs() {
      if (syncing) return;
      syncing = true;
      try {
        const jobs = await listRagJobs(drive);
        if (disposed) return;
        if (jobs === null) {
          jobsSupportedRef.current = false;
          setJobsSupported(false);
          setBackgroundJobs([]);
          setJobsError("");
          return;
        }
        jobsSupportedRef.current = true;
        setJobsSupported(true);
        setBackgroundJobs(jobs);
        setSelectedJobId((current) => {
          if (current && jobs.some((job) => job.job_id === current && job.status === "done")) {
            return current;
          }
          return jobs.find((job) => job.status === "done")?.job_id ?? null;
        });
        setJobsError("");
        for (const job of jobs) {
          if (job.status !== "done" || hydratedJobsRef.current.has(job.job_id)) continue;
          hydratedJobsRef.current.add(job.job_id);
          try {
            const restored = await getRagJob(job.job_id, drive);
            if (disposed || !restored.result?.ok) continue;
            const action = restored.job.action as LiveAction | undefined;
            const label = restored.job.query || tm("Background result");
            const fileHits =
              action === "search" && restored.job.query
                ? await searchFileNames(restored.job.query)
                : undefined;
            const messageId = `job-${job.job_id}`;
            const message = buildLiveMessage(
              messageId,
              action,
              label,
              restored.result,
              fileHits,
              restored.job.query,
            );
            markResult(messageId);
            setSelectedJobId((current) => current ?? job.job_id);
            setMessages((current) =>
              current.some((item) => item.id === messageId) ? current : [...current, message],
            );
            await markRagJobSeen(job.job_id, drive);
          } catch {
            hydratedJobsRef.current.delete(job.job_id);
          }
        }
      } catch (error) {
        if (!disposed) setJobsError(error instanceof Error ? error.message : "job sync failed");
      } finally {
        syncing = false;
      }
    }

    hydratedJobsRef.current.clear();
    jobsSupportedRef.current = null;
    setJobsSupported(null);
    setSelectedJobId(null);
    void syncJobs();
    const timer = window.setInterval(() => {
      if (jobsSupportedRef.current !== false) void syncJobs();
    }, 3_000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [boxState, drive]);

  async function cancelBackgroundJob(jobId: string) {
    try {
      const response = await cancelRagJob(jobId, drive || undefined);
      setBackgroundJobs((jobs) =>
        jobs.map((job) =>
          job.job_id === jobId
            ? {
                ...job,
                status: response.cancel_requested ? "running" : "cancelled",
                cancel_requested: Boolean(response.cancel_requested),
              }
            : job,
        ),
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : tm("The job could not be cancelled."));
    }
  }

  /** Citations and filename hits can point at any file in the drive, not just
   *  the folder on screen; fall back to what the path itself tells us. */
  function resolveFile(path: string): DriveFile {
    const known = fileIndexRef.current.get(path);
    if (known) return known;
    const rootSlash = (drive || "").replace(/\/+$/, "") + "/";
    return toDriveFile(
      { path, name: path.split("/").pop() ?? path, size: null, mtime: null },
      rootSlash,
    );
  }

  function openFile(id: string) {
    const f = resolveFile(id);
    setDriveOpen(true);
    setPreviewFile(f);
    setPreviewCitation(null);
    setViewerMode("side");
    setHighlightedFileId(f.id);
    setTimeout(() => setHighlightedFileId(null), 2200);
  }

  async function runLive(v: string, extras?: LiveExtras, pane: "chat" | "results" = "chat") {
    // The adapter routes quick actions from an explicit payload action or a
    // typed "#action" prefix (adapter/service.py parse_action) — the UI's "/"
    // commands must be translated, otherwise the structured task instruction
    // (F1-F7) is lost and every command degrades to a plain question.
    const { cmd, rest } = splitCommand(v);
    const action = cmd ? LIVE_ACTIONS[cmd] : undefined;
    // The dialog answers that describe WHAT to produce belong in the question;
    // audience/purpose only shape tone and travel as adapter PARAMETERS. The
    // picked file names go in too: the adapter filters retrieved contexts on
    // them, but only naming them makes the retrieval surface those files.
    // #analyze is the exception: it reads the named file whole, so repeating
    // the name as a retrieval hint would only clutter the question.
    const sourceHint =
      extras?.files?.length && action !== "analyze"
        ? `based on ${extras.files.map((f) => f.split("/").pop()).join(", ")}`
        : "";
    const brief = [rest, extras?.situation, extras?.extra, sourceHint].filter(Boolean).join(" — ");
    // Bare "/summary" -> send the "#summary" prefix form so the adapter applies
    // its own retrieval seed (_BARE_QUERY); with an explicit action an empty
    // query would be rejected (400).
    const query = action ? brief || `#${action}` : cmd ? brief || cmd.slice(1) : v;
    const label = cmd ? (rest ? `${cmd} ${rest}` : cmd).slice(0, 90) : v.slice(0, 80);
    const requestData = {
      query,
      profile: action || pane === "results" ? "grounded" as const : chatProfile,
      lang,
      action: brief ? action : undefined,
      drive: drive || undefined,
      audience: extras?.audience || undefined,
      purpose: extras?.purpose || undefined,
      coverage: extras?.coverage || undefined,
      report_type: extras?.reportType || undefined,
      aspect: extras?.aspect || undefined,
      keywords: extras?.keywords || undefined,
      outcome: extras?.outcome || undefined,
      situation: extras?.situation || undefined,
      extra: extras?.extra || undefined,
      files: extras?.files?.length ? extras.files : undefined,
      drives: selectedDrives.length ? selectedDrives : undefined,
    };
    if (pane === "results" && jobsSupported === true) {
      try {
        const submitted = await submitRagJob({ data: requestData });
        setBackgroundJobs((jobs) => [
          {
            job_id: submitted.jobId,
            status: submitted.status,
            action,
            query: label,
            created: Date.now() / 1000,
            queue_position: submitted.queuePosition,
          },
          ...jobs.filter((job) => job.job_id !== submitted.jobId),
        ]);
        setMobilePane("results");
      } catch (error) {
        toast.error(error instanceof Error ? error.message : tm("The job could not be started."));
      }
      return;
    }
    const loadingId = uid();
    setMessageProfiles((profiles) => ({ ...profiles, [loadingId]: chatProfile }));
    if (pane === "results") markResult(loadingId);
    setLiveBusy(true);
    setMessages((m) => [
      ...m,
      {
        id: loadingId,
        role: "ai",
        name: "ViVeSec AI",
        ts: nowTs(),
        kind: "thinking",
        startedAt: Date.now(),
      },
    ]);
    try {
      const res = await askRag({
        data: requestData,
      });
      if (res.backgroundJobId) {
        setMessages((all) => all.filter((message) => message.id !== loadingId));
        setBackgroundJobs((jobs) => [
          {
            job_id: res.backgroundJobId as string,
            status: "running",
            action,
            query: label,
            created: Date.now() / 1000,
            queue_position: null,
          },
          ...jobs.filter((job) => job.job_id !== res.backgroundJobId),
        ]);
        setMobilePane("results");
        toast.info(tm("The request continues in the background."));
        return;
      }
      if (!res.ok) throw new Error(res.error || "adapter error");
      const term = cmd ? rest : v;
      const fileHits = action === "search" && term ? await searchFileNames(term) : undefined;
      const full = buildLiveMessage(loadingId, action, label, res, fileHits, query);
      setMessages((all) => all.map((m) => (m.id === loadingId ? full : m)));
    } catch (err) {
      // No mock fallback: the failure is shown as a failure.
      const detail = err instanceof Error ? err.message : "error";
      setMessages((all) =>
        all.map((m) =>
          m.id === loadingId
            ? {
                id: loadingId,
                role: "ai",
                name: "ViVeSec AI",
                ts: nowTs(),
                kind: "text",
                tone: "error" as const,
                text: `${tm("The AI Box did not answer this question.")} (${detail})`,
              }
            : m,
        ),
      );
      toast.error(tm("The AI Box did not answer this question."));
      void probeBox();
    } finally {
      setLiveBusy(false);
    }
  }

  function handleSend() {
    sendText(input.trim());
  }

  /** One submit path for typed and spoken input alike: a dictated question is
   *  routed, grounded and cited exactly like a typed one. */
  function sendText(v: string) {
    if (!v) return;
    if (boxState !== "online") {
      toast.error(tm("AI Box unreachable — no answer can be produced."));
      return;
    }
    setInput("");
    setShowSlash(false);
    pushUser(v);

    const { cmd, rest } = splitCommand(v);
    // /data is UI-side: platform telemetry, not a corpus answer.
    if (cmd === "/data") {
      runData();
      return;
    }
    // F1/F3/F5 open the guided dialog before the model runs.
    const wk = cmd ? WIZARD_OF[cmd] : undefined;
    if (wk) {
      startWizard(wk, rest, "chat");
      return;
    }
    void runLive(v);
  }

  /* ---------- Quick actions (results column) ----------
     A quick action produces a deliverable, so it runs — and lands — in the
     results column instead of the conversation. */
  function launchAction(cmd: SlashCmd) {
    if (boxState !== "online") {
      toast.error(tm("AI Box unreachable — no answer can be produced."));
      return;
    }
    if (liveBusy || wizard) return;
    setMobilePane("results");
    if (cmd === "/data") {
      runData("results");
      return;
    }
    setPendingAction(cmd);
    setActionBrief("");
  }

  function submitAction() {
    const cmd = pendingAction;
    if (!cmd) return;
    const brief = actionBrief.trim();
    if (cmd === "/search" && !brief) return;
    setPendingAction(null);
    setActionBrief("");
    const wk = WIZARD_OF[cmd];
    if (wk) {
      startWizard(wk, brief, "results");
      return;
    }
    void runLive(brief ? `${cmd} ${brief}` : cmd, undefined, "results");
  }

  /* ---------- Voice mode (spoken question in, answer read back) ----------
     Recognition runs on the box (adapter /ui/stt); the browser's own
     SpeechRecognition is not used because it uploads audio to a cloud service,
     which this appliance exists to avoid. */
  const voice = useVoice({
    boxStt: voiceCaps.stt,
    boxTts: voiceCaps.tts,
    lang,
    onTranscript: (text) => {
      if (wizard) {
        // Mid-dialog the answer belongs to the wizard, not to a new question.
        setInput(text);
        textareaRef.current?.focus();
        return;
      }
      sendText(text);
    },
    onError: (message) => toast.error(tm(message)),
  });

  /** Read the most recent AI answer out loud. */
  const lastSpeakable = useMemo(() => {
    for (let index = chatMessages.length - 1; index >= 0; index--) {
      const m = chatMessages[index] as Message & { kind?: string; text?: string; body?: string; raw?: string };
      if (m.role !== "ai") continue;
      const body = splitAuditFooter(m.raw ?? m.text ?? m.body ?? "").body;
      if (body.trim()) return body;
    }
    return "";
  }, [chatMessages]);

  /* ---------- Guided dialog (spec F1/F3/F5) ---------- */
  const [wizard, setWizard] = useState<WizardState | null>(null);
  const wizardSteps = wizard ? stepsFor(wizard.kind, wizard.brief) : [];
  const wizardStep: WizardStep | null = wizard ? wizardSteps[wizard.stepIndex] : null;

  function askWizard(kind: WizardKind, step: WizardStep) {
    pushAi({
      id: uid(),
      role: "ai",
      name: "ViVeSec AI",
      ts: nowTs(),
      kind: "text",
      text: tm(wizardQuestion(kind, step)),
    });
  }

  function startWizard(kind: WizardKind, brief: string, pane: "chat" | "results") {
    setWizard({
      kind,
      brief,
      pane,
      stepIndex: 0,
      audience: "",
      purpose: "",
      files: [],
      extra: "",
      situation: "",
      subject: "",
      coverage: "",
      reportType: "",
      aspect: "",
      keywords: "",
    });
    if (pane === "chat") askWizard(kind, stepsFor(kind, brief)[0]);
  }

  /** Record one dialog answer and move on; the last step fires the request. */
  function answerWizard(patch: Partial<WizardState>, echo: string) {
    if (!wizard) return;
    const next = { ...wizard, ...patch };
    if (next.pane === "chat") pushUser(echo);
    const steps = stepsFor(next.kind, next.brief);
    if (next.stepIndex + 1 < steps.length) {
      setWizard({ ...next, stepIndex: next.stepIndex + 1 });
      if (next.pane === "chat") askWizard(next.kind, steps[next.stepIndex + 1]);
      return;
    }
    setWizard(null);
    // The "subject" answer is the query itself, not a framing parameter.
    const brief = next.brief.trim() || next.subject.trim();
    void runLive(
      `${WIZARD_CMD[next.kind]} ${brief}`.trim(),
      {
        audience: next.audience,
        purpose: next.purpose,
        files: next.files,
        extra: next.extra,
        situation: next.situation,
        coverage: next.coverage,
        reportType: next.reportType,
        aspect: next.aspect,
        keywords: next.keywords,
      },
      next.pane,
    );
  }

  function cancelWizard() {
    const pane = wizard?.pane;
    setWizard(null);
    if (pane === "chat") {
      pushAi({
        id: uid(),
        role: "ai",
        name: "ViVeSec AI",
        ts: nowTs(),
        kind: "text",
        text: tm("Cancelled."),
      });
    }
  }

  /* ---------- Deck mutations ---------- */
  function updateDeck(
    deckId: string,
    updater: (d: Extract<Message, { kind: "deck" }>) => Extract<Message, { kind: "deck" }>,
  ) {
    setMessages((all) =>
      all.map((m) => (m.id === deckId && (m as any).kind === "deck" ? updater(m as any) : m)),
    );
  }
  function updateSlide(deckId: string, slideId: string, patch: Partial<Slide>) {
    updateDeck(deckId, (d) => ({
      ...d,
      edited: true,
      slides: d.slides.map((s) => (s.id === slideId ? { ...s, ...patch } : s)),
    }));
  }

  /** Manual editing (spec F1/F3/F5): the edited card replaces what gets saved. */
  function patchMessage(id: string, patch: Record<string, unknown>) {
    setMessages((all) =>
      all.map((m) => (m.id === id ? ({ ...m, ...patch, edited: true } as Message) : m)),
    );
  }

  /* ---------- Action item toggle ---------- */
  function toggleAction(msgId: string, actionId: string) {
    setMessages((all) =>
      all.map((m) => {
        if (m.id !== msgId || (m as any).kind !== "summary") return m;
        const sm = m as Extract<Message, { kind: "summary" }>;
        return {
          ...sm,
          actions: sm.actions.map((a) => (a.id === actionId ? { ...a, done: !a.done } : a)),
        };
      }),
    );
  }

  /* ---------- Citation click ---------- */
  function openCitation(c: Citation, siblings?: Citation[]) {
    const f = resolveFile(c.fileId);
    setDriveOpen(true);
    setHighlightedFileId(f.id);
    setPreviewFile(f);
    setPreviewCitation(c);
    if (siblings && siblings.length) {
      setViewerCitations(siblings);
      setViewerIndex(
        Math.max(
          0,
          siblings.findIndex(
            (s) => s === c || (s.fileId === c.fileId && s.page === c.page && s.label === c.label),
          ),
        ),
      );
    } else {
      setViewerCitations([c]);
      setViewerIndex(0);
    }
    setViewerMode("split");
    setTimeout(() => setHighlightedFileId(null), 2200);
  }

  function goMatch(delta: 1 | -1) {
    if (viewerCitations.length < 2) return;
    const next = (viewerIndex + delta + viewerCitations.length) % viewerCitations.length;
    const c = viewerCitations[next];
    setViewerIndex(next);
    setPreviewCitation(c);
    setPreviewFile(resolveFile(c.fileId));
  }

  /** "Analyze with AI": the adapter's #analyze action reads the WHOLE file
   *  (adapter/service.py -> /rag/document_context), so the analysis covers the
   *  document end to end instead of the passages a search happened to rank. */
  function analyzeDocument(f: DriveFile) {
    if (boxState !== "online") {
      toast.error(tm("AI Box unreachable — no answer can be produced."));
      return;
    }
    if (liveBusy || wizard) return;
    setPreviewFile(f);
    setPreviewCitation(null);
    markResult(pushUser(`/analyze ${f.name}`));
    // f.id is the drive-absolute path — the adapter resolves the document by it.
    void runLive(`/analyze ${f.name}`, { files: [f.id] }, "results");
  }

  /* ---------- Drive listing: folder contents, or drive-wide search hits ---- */
  const filteredFiles = searchHits ?? files;

  /* ---------- Render ---------- */
  return (
    <DriveContext.Provider value={{ drive }}>
      {/* One viewport, no page scroll: only the message list scrolls, so the
        composer and the quick actions stay put. dvh so the mobile URL bar
        cannot push the composer off-screen. */}
      <div
        className="flex w-full flex-col overflow-hidden text-[#E6EAEE]"
        style={{
          height: "100dvh",
          backgroundColor: BG,
          fontFamily:
            "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, Inter, sans-serif",
        }}
      >
        {/* Header */}
        <header
          className="z-30 shrink-0 border-b border-white/5"
          style={{ backgroundColor: "#15181B" }}
        >
          <div className="flex items-center gap-3 px-4 py-3">
            <LogoMark />
            <div className="min-w-0">
              <div className="truncate text-[15px] font-semibold tracking-tight">
                {t.headerTitle}
              </div>
              <div className="flex items-center gap-1.5 text-[11px] text-white/50">
                <Lock className="h-3 w-3" />
                <span>{t.localSecure}</span>
                <CircleDot className="ml-1 h-2.5 w-2.5" style={{ color: LIME }} />
              </div>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <button
                onClick={() => setDriveOpen((v) => !v)}
                className="hidden md:inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-white/80 hover:bg-white/10 transition"
              >
                <Laptop className="h-3.5 w-3.5" /> {driveOpen ? t.hideDrive : t.browseDrive}
              </button>
              {/* AI Box connection state. There is no offline answer mode: when
                the box is unreachable the composer is blocked instead of
                showing anything the box did not produce. */}
              <button
                onClick={() => void probeBox()}
                disabled={boxState === "probing"}
                title={
                  boxState === "online"
                    ? `${tm("Connected to the AI Box")}${boxDetail ? ` · ${boxDetail}` : ""}`
                    : `${tm("AI Box unreachable — click to retry")}${boxDetail ? ` · ${boxDetail}` : ""}`
                }
                className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs transition disabled:opacity-60 ${boxState === "online" ? "text-white" : "text-rose-200/90 hover:bg-rose-400/15"}`}
                style={
                  boxState === "online"
                    ? { backgroundColor: `${LIME}15`, borderColor: `${LIME}55` }
                    : {
                        backgroundColor: "rgba(244,63,94,0.12)",
                        borderColor: "rgba(244,63,94,0.45)",
                      }
                }
              >
                <Sparkles
                  className="h-3.5 w-3.5"
                  style={{ color: boxState === "online" ? LIME : "#FB7185" }}
                />
                <span className="hidden sm:inline">
                  {boxState === "online"
                    ? tm("AI Box connected")
                    : boxState === "probing"
                      ? tm("Connecting…")
                      : tm("AI Box offline")}
                </span>
                <CircleDot
                  className="h-2.5 w-2.5"
                  style={{ color: boxState === "online" ? LIME : "#FB7185" }}
                />
              </button>
              <LanguageSelector />
            </div>
          </div>
        </header>

        <div className="flex w-full min-h-0 flex-1 gap-0">
          {/* Chat | Results | Drive-or-preview */}
          <div className="relative flex min-w-0 flex-1">
            {/* Chat column — no upload path: files reach the corpus from the
              ViVeSecBox, never from here. */}
            <main
              className={`h-full min-w-0 flex-1 basis-0 flex-col transition-all duration-500 ${
                mobilePane === "chat" ? "flex" : "hidden"
              } ${previewFile && viewerMode === "split" ? "lg:hidden" : "lg:order-3 lg:flex lg:border-l lg:border-white/5"}`}
            >
              {/* Pinned chat header strip */}
              <div className="relative flex flex-wrap items-center gap-2 border-b border-white/5 px-4 py-2.5">
                <div className="hidden xl:block text-xs text-white/55">
                  Encrypted channel · Edge inference only
                </div>
                {chatPolicy.allow_switch && <div role="group" aria-label={tm("Chat profile")} className="flex shrink-0 items-center rounded-md border border-white/10 p-0.5">
                  {(["grounded", "hybrid"] as const).map((profile) => (
                    <button
                      key={profile}
                      type="button"
                      aria-pressed={chatProfile === profile}
                      disabled={!chatPolicy.allow_switch || boxState !== "online" || liveBusy || !!wizard}
                      onClick={() => { voice.stopSpeaking(); setChosenProfile(profile); setPreviewFile(null); setPreviewCitation(null); }}
                      className={`inline-flex min-h-8 items-center gap-1 rounded px-2 text-[11px] disabled:cursor-default ${chatProfile === profile ? "bg-white/15 text-white" : "text-white/55 hover:bg-white/5"}`}
                      title={tm(profile === "grounded" ? "Grounded" : "Hybrid")}
                    >
                      {profile === "grounded" ? <Lock className="h-3 w-3" /> : <Sparkles className="h-3 w-3" />}
                      {tm(profile === "grounded" ? "Grounded" : "Hybrid")}
                    </button>
                  ))}
                </div>}
                <button
                  onClick={() => setMobilePane("results")}
                  className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[11px] text-white/70 transition hover:bg-white/10 lg:hidden"
                >
                  <LayoutTemplate className="h-3.5 w-3.5" style={{ color: LIME }} />
                  {tm("Results")}
                  {resultMessages.length + activeBackgroundJobs + unseenBackgroundJobs > 0 && (
                    <span
                      className="rounded-full px-1.5 text-[10px] font-semibold"
                      style={{ backgroundColor: `${LIME}22`, color: LIME }}
                    >
                      {resultMessages.length + activeBackgroundJobs + unseenBackgroundJobs}
                    </span>
                  )}
                </button>
                <div className="relative ml-auto">
                  <button
                    onClick={() => setAgentMenuOpen((v) => !v)}
                    className="flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[11px] text-white/70 transition hover:bg-white/10"
                    aria-label="AI agents"
                  >
                    <Bot className="h-3.5 w-3.5" style={{ color: LIME }} />
                    <span className="text-white/80">
                      {tm(AGENTS.find((a) => a.id === activeAgent)?.name ?? "")}
                    </span>
                    <ChevronDown
                      className={`h-3 w-3 text-white/45 transition-transform ${agentMenuOpen ? "rotate-180" : ""}`}
                    />
                  </button>
                  {agentMenuOpen && (
                    <>
                      <div className="fixed inset-0 z-40" onClick={() => setAgentMenuOpen(false)} />
                      <div
                        role="dialog"
                        aria-label="AI agents"
                        className="absolute right-0 z-50 mt-2 w-72 overflow-hidden rounded-xl border border-white/10 bg-[#23272B] shadow-2xl shadow-black/60"
                      >
                        <div className="px-3 py-2 text-[11px] uppercase tracking-wider text-white/40">
                          {t.activeAgents}
                        </div>
                        {AGENTS.map((a) => {
                          const Icon = a.icon;
                          const active = activeAgent === a.id;
                          return (
                            <button
                              key={a.id}
                              disabled={!a.available}
                              onClick={() => {
                                setActiveAgent(a.id);
                                setAgentMenuOpen(false);
                              }}
                              className={`flex w-full items-center gap-3 px-3 py-2.5 text-left transition ${a.available ? "hover:bg-white/5" : "cursor-not-allowed opacity-40"} ${active ? "bg-white/[0.06]" : ""}`}
                            >
                              <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-white/5">
                                <Icon
                                  className="h-4 w-4"
                                  style={{ color: active ? LIME : "rgba(255,255,255,0.7)" }}
                                />
                              </div>
                              <div className="min-w-0 flex-1">
                                <div className="truncate text-[13px] font-medium text-white">
                                  {tm(a.name)}
                                </div>
                                <div className="truncate text-[11px] text-white/50">
                                  {tm(a.desc)}
                                </div>
                              </div>
                              {active ? (
                                <span
                                  className="h-2 w-2 shrink-0 rounded-full"
                                  style={{ backgroundColor: LIME }}
                                />
                              ) : (
                                !a.available && (
                                  <span className="shrink-0 rounded-md border border-white/10 px-1.5 py-0.5 text-[9.5px] uppercase tracking-wide text-white/40">
                                    {tm("Soon")}
                                  </span>
                                )
                              )}
                            </button>
                          );
                        })}
                      </div>
                    </>
                  )}
                </div>
              </div>

              {/* Messages */}
              {/* Messages — slim styled scrollbar: with the composer pinned
                below, a hidden one left long answers looking complete. */}
              <div ref={scrollRef} className="vvs-scroll flex-1 overflow-y-auto px-3 py-6 sm:px-6">
                <div className="mx-auto flex max-w-3xl flex-col gap-5">
                  {chatMessages.map((m) => (
                    <div key={m.id} data-msg-id={m.id} className="scroll-mt-2">
                      <MessageView
                        msg={m}
                        inChat
                        onToggleAction={(aid) => toggleAction(m.id, aid)}
                        onCitation={openCitation}
                        onUpdateSlide={(sid, patch) => updateSlide(m.id, sid, patch)}
                        onEdit={(patch) => patchMessage(m.id, patch)}
                        onOpenFile={openFile}
                      />
                    </div>
                  ))}
                  {/* Room for the last answer to reach the top — no more (see tailSpace) */}
                  {tailSpace > 0 && (
                    <div aria-hidden className="shrink-0" style={{ height: tailSpace }} />
                  )}
                </div>
              </div>

              {/* Composer */}
              <div className="relative border-t border-white/5 px-3 pt-2 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-6">
                {/* Floating browse drive (matches screenshot) */}
                {!driveOpen && (
                  <div className="pointer-events-none absolute -top-12 right-4 sm:right-8">
                    <button
                      onClick={() => setDriveOpen(true)}
                      className="pointer-events-auto inline-flex items-center gap-2 rounded-full border border-white/10 bg-[#222629]/90 px-3 py-1.5 text-xs text-white/80 shadow-lg backdrop-blur hover:bg-white/10 transition"
                    >
                      <Laptop className="h-3.5 w-3.5" /> {t.browseDriveFloating}
                    </button>
                  </div>
                )}

                {/* Quick actions live in the results column now (they produce
                  deliverables, not chat turns) — on mobile that column is a tab
                  away, so a shortcut back to it stays here. */}
                <div className="mx-auto mb-2 flex max-w-3xl lg:hidden">
                  <button
                    onClick={() => setMobilePane("results")}
                    disabled={!!wizard}
                    className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-[11px] text-white/75 transition hover:bg-white/10 disabled:opacity-40"
                  >
                    <Zap className="h-3 w-3" style={{ color: LIME }} />
                    {tm("Quick actions")}
                  </button>
                </div>

                {/* Guided dialog (spec F1/F3/F5 UX logic) */}
                {wizard?.pane === "chat" && wizardStep && (
                  <WizardPanel
                    kind={wizard.kind}
                    step={wizardStep}
                    stepIndex={wizard.stepIndex}
                    stepCount={wizardSteps.length}
                    files={files}
                    picked={wizard.files}
                    onPick={(ids) => setWizard((w) => (w ? { ...w, files: ids } : w))}
                    onAnswer={answerWizard}
                    onCancel={cancelWizard}
                  />
                )}

                {/* Slash menu — a command picker only. The task description
                  lives in the adapter (llm.py TASK_INSTRUCTIONS); canned
                  sub-prompts here would land in the retrieval query and
                  dilute it. */}
                {showSlash && !wizard && (
                  <div className="mx-auto mb-2 max-w-3xl overflow-hidden rounded-xl border border-white/10 bg-[#23272B] shadow-xl max-h-[55vh] overflow-y-auto">
                    {SLASH_COMMANDS.filter((s) => s.cmd.startsWith(input.split(" ")[0] || "/")).map(
                      (s) => {
                        const Icon = s.icon;
                        return (
                          <button
                            key={s.cmd}
                            onClick={() => {
                              setInput(s.cmd + " ");
                              setShowSlash(false);
                              setTimeout(() => textareaRef.current?.focus(), 0);
                            }}
                            className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-white/5 transition"
                          >
                            <div className="grid h-7 w-7 place-items-center rounded-md bg-white/5">
                              <Icon className="h-3.5 w-3.5" style={{ color: LIME }} />
                            </div>
                            <div className="min-w-0">
                              <div className="text-sm font-medium" style={{ color: LIME }}>
                                {tm(s.label)}
                              </div>
                              <div className="truncate text-xs text-white/55">
                                {t.slashDesc[s.cmd] ?? s.desc}
                              </div>
                            </div>
                            <ChevronRight className="ml-auto h-4 w-4 text-white/30" />
                          </button>
                        );
                      },
                    )}
                  </div>
                )}

                {/* No answer can be produced without the box: say so and block
                  the composer rather than degrade to something fabricated. */}
                {boxState !== "online" && (
                  <div className="mx-auto mb-2 flex max-w-3xl items-center gap-2 rounded-xl border border-rose-400/40 bg-rose-500/10 px-3 py-2 text-[12px] text-rose-100">
                    <AlertTriangle className="h-4 w-4 shrink-0" />
                    <span className="flex-1">
                      {boxState === "probing"
                        ? tm("Connecting to the AI Box…")
                        : tm("AI Box unreachable — no answer can be produced.")}
                      {boxDetail ? ` (${boxDetail})` : ""}
                    </span>
                    <button
                      onClick={() => void probeBox()}
                      disabled={boxState === "probing"}
                      className="shrink-0 rounded-lg border border-rose-300/40 px-2 py-1 text-[11px] font-semibold transition hover:bg-rose-400/20 disabled:opacity-50"
                    >
                      <RefreshCw className="mr-1 inline h-3 w-3" />
                      {tm("Retry")}
                    </button>
                  </div>
                )}

                <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-white/10 bg-[#23272B] px-2 py-1.5 focus-within:border-white/20 transition">
                  <textarea
                    ref={textareaRef}
                    rows={1}
                    value={input}
                    disabled={boxState !== "online" || liveBusy || !!wizard}
                    onChange={(e) => {
                      setInput(e.target.value);
                      // The menu picks a command; once one is chosen it gets out of the way.
                      setShowSlash(e.target.value.startsWith("/") && !e.target.value.includes(" "));
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        handleSend();
                      }
                    }}
                    placeholder={
                      boxState !== "online"
                        ? tm("AI Box unreachable — no answer can be produced.")
                        : voice.micState === "recording"
                          ? tm("Listening… press the square to send.")
                          : voice.micState === "transcribing"
                            ? tm("Transcribing on the AI Box…")
                            : wizard
                              ? tm("Answer the question above to continue.")
                              : liveBusy
                                ? tm("The local model is thinking…")
                                : t.inputPlaceholder
                    }
                    className="max-h-40 flex-1 resize-none bg-transparent py-2 text-sm text-white placeholder:text-white/40 outline-none disabled:cursor-not-allowed"
                  />
                  {/* Voice mode: dictate a question, and read the last answer
                      back. Both engines run on the box. */}
                  {(voice.micSupported || voiceCaps.stt) && (
                    <button
                      onClick={() =>
                        voice.micState === "recording"
                          ? voice.stopRecording()
                          : void voice.startRecording()
                      }
                      className="grid h-9 w-9 place-items-center rounded-lg transition hover:bg-white/10 disabled:opacity-40"
                      style={{
                        color: voice.micState === "recording" ? "#FB7185" : "rgba(255,255,255,0.6)",
                      }}
                      disabled={
                        !voice.micSupported ||
                        voice.micState === "transcribing" ||
                        boxState !== "online" ||
                        liveBusy
                      }
                      title={
                        voice.micBlockedReason
                          ? tm(voice.micBlockedReason)
                          : voice.micState === "recording"
                            ? tm("Stop recording and send")
                            : tm("Ask by voice")
                      }
                    >
                      {voice.micState === "transcribing" ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : voice.micState === "recording" ? (
                        <Square className="h-3.5 w-3.5 fill-current" />
                      ) : (
                        <Mic className="h-4 w-4" />
                      )}
                    </button>
                  )}
                  {lastSpeakable && (
                    <button
                      onClick={() =>
                        voice.speaking ? voice.stopSpeaking() : void voice.speak(lastSpeakable)
                      }
                      className="grid h-9 w-9 place-items-center rounded-lg text-white/60 transition hover:bg-white/10"
                      title={voice.speaking ? tm("Stop reading") : tm("Read the last answer")}
                    >
                      {voice.speaking ? (
                        <VolumeX className="h-4 w-4" />
                      ) : (
                        <Volume2 className="h-4 w-4" />
                      )}
                    </button>
                  )}
                  <button
                    onClick={handleSend}
                    className="grid h-9 w-9 place-items-center rounded-lg transition disabled:opacity-40"
                    style={{
                      backgroundColor: input.trim() ? LIME : "transparent",
                      color: input.trim() ? "#15181B" : "rgba(255,255,255,0.6)",
                    }}
                    disabled={!input.trim() || boxState !== "online" || liveBusy || !!wizard}
                    title="Send"
                  >
                    <Send className="h-4 w-4" />
                  </button>
                </div>
                <div className="mx-auto mt-1.5 flex max-w-3xl items-center justify-center gap-1.5 text-[10px] text-white/35">
                  <Lock className="h-3 w-3" /> {t.encryptedFooter}
                </div>
              </div>
            </main>

            {/* Results column — quick actions and the cards they produce */}
            <section
              className={`h-full min-w-0 flex-1 basis-0 flex-col border-white/5 transition-all duration-500 lg:order-2 lg:flex lg:border-l ${
                mobilePane === "results" ? "flex" : "hidden"
              }`}
            >
              <div className="flex items-center gap-2 border-b border-white/5 px-4 py-2.5">
                <button
                  onClick={() => setMobilePane("chat")}
                  className="grid h-7 w-7 place-items-center rounded-md hover:bg-white/10 lg:hidden"
                  aria-label={tm("Conversation")}
                >
                  <ArrowLeft className="h-4 w-4 text-white/60" />
                </button>
                <Zap className="h-4 w-4" style={{ color: LIME }} />
                <div className="text-sm font-medium">{tm("Quick actions")}</div>
                <div className="ml-auto text-[11px] text-white/40">
                  {unseenBackgroundJobs > 0
                    ? `${unseenBackgroundJobs} ${tm("new")}`
                    : activeBackgroundJobs > 0
                      ? `${activeBackgroundJobs} ${tm("active")}`
                      : resultMessages.length > 0
                        ? resultMessages.length
                        : ""}
                </div>
              </div>

              {/* Action launcher — the agent decides which quick actions exist */}
              <div className="shrink-0 border-b border-white/5 p-3">
                <div className="grid grid-cols-2 gap-1.5 xl:grid-cols-3">
                  {(() => {
                    const agent = AGENTS.find((a) => a.id === activeAgent);
                    const allowed = new Set<SlashCmd>(agent?.commands ?? []);
                    return SLASH_COMMANDS.filter((s) => allowed.has(s.cmd));
                  })().map((s) => {
                    const Icon = s.icon;
                    const active = pendingAction === s.cmd;
                    return (
                      <button
                        key={s.cmd}
                        disabled={!!wizard || boxState !== "online" || liveBusy}
                        onClick={() => launchAction(s.cmd)}
                        title={t.slashDesc[s.cmd] ?? s.desc}
                        className={`flex items-center gap-2 rounded-lg border px-2.5 py-2 text-left transition hover:bg-white/10 disabled:opacity-40 ${
                          active
                            ? "border-white/30 bg-white/[0.08]"
                            : "border-white/10 bg-white/[0.04] hover:border-white/25"
                        }`}
                      >
                        <Icon className="h-3.5 w-3.5 shrink-0" style={{ color: LIME }} />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[12px] text-white/85">{s.cmd}</span>
                          <span className="block truncate text-[10.5px] text-white/40">
                            {t.slashDesc[s.cmd] ?? s.desc}
                          </span>
                        </span>
                      </button>
                    );
                  })}
                </div>

                {pendingAction && (
                  <div className="mt-2 flex gap-2">
                    <input
                      autoFocus
                      value={actionBrief}
                      onChange={(e) => setActionBrief(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") submitAction();
                        if (e.key === "Escape") setPendingAction(null);
                      }}
                      placeholder={
                        pendingAction === "/search"
                          ? tm("What are you looking for?")
                          : tm("What should it cover? (optional)")
                      }
                      className="min-w-0 flex-1 rounded-lg border border-white/10 bg-black/25 px-3 py-2 text-[12.5px] text-white placeholder:text-white/35 outline-none focus:border-white/25"
                    />
                    <button
                      onClick={submitAction}
                      disabled={pendingAction === "/search" && !actionBrief.trim()}
                      className="rounded-lg px-3 py-2 text-[12px] font-semibold disabled:opacity-40"
                      style={{ backgroundColor: LIME, color: "#15181B" }}
                    >
                      {tm("Run")}
                    </button>
                    <button
                      onClick={() => setPendingAction(null)}
                      className="grid h-9 w-9 shrink-0 place-items-center rounded-lg hover:bg-white/10"
                      aria-label={tm("Cancel")}
                    >
                      <X className="h-4 w-4 text-white/50" />
                    </button>
                  </div>
                )}

                {wizard?.pane === "results" && wizardStep && (
                  <WizardPanel
                    kind={wizard.kind}
                    step={wizardStep}
                    stepIndex={wizard.stepIndex}
                    stepCount={wizardSteps.length}
                    files={files}
                    picked={wizard.files}
                    onPick={(ids) => setWizard((w) => (w ? { ...w, files: ids } : w))}
                    onAnswer={answerWizard}
                    onCancel={cancelWizard}
                  />
                )}
              </div>

              <div
                ref={resultScrollRef}
                className="vvs-scroll min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-4"
              >
                <div className="mx-auto flex max-w-2xl flex-col gap-5">
                  {(backgroundJobs.length > 0 || jobsError || jobsSupported === false) && (
                    <div className="border-b border-white/10 pb-3">
                      <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase text-white/45">
                        <Loader2
                          className={`h-3.5 w-3.5 ${activeBackgroundJobs ? "animate-spin" : ""}`}
                        />
                        {tm("Background jobs")}
                      </div>
                      {jobsError && <div className="mb-2 text-[11px] text-rose-300">{jobsError}</div>}
                      {jobsSupported === false && (
                        <div className="mb-2 text-[11px] leading-relaxed text-amber-200/80">
                          {tm(
                            "Background recovery will be available after the AI Box adapter is updated.",
                          )}
                        </div>
                      )}
                      <div className="divide-y divide-white/5 border-y border-white/5">
                        {backgroundJobs.map((job) => {
                          const active = job.status === "queued" || job.status === "running";
                          const status =
                            job.cancel_requested
                              ? tm("Cancel requested")
                              : job.status === "queued" && job.queue_position
                              ? `${tm("Queued")} · ${job.queue_position}`
                              : tm(
                                  job.status === "running"
                                    ? "Running"
                                    : job.status === "done"
                                      ? "Completed"
                                      : job.status === "cancelled"
                                        ? "Cancelled"
                                        : job.status === "interrupted"
                                          ? "Interrupted"
                                          : "Failed",
                                );
                          return (
                            <div
                              key={job.job_id}
                              className="flex min-h-12 items-center gap-1 py-1"
                            >
                              <button
                                disabled={job.status !== "done"}
                                onClick={() => setSelectedJobId(job.job_id)}
                                className={`flex min-w-0 flex-1 items-center gap-2 rounded-md px-1 py-1 text-left transition disabled:cursor-default ${
                                  selectedJobId === job.job_id
                                    ? "bg-white/10"
                                    : job.status === "done"
                                      ? "hover:bg-white/5"
                                      : ""
                                }`}
                              >
                                <div className="grid h-7 w-7 shrink-0 place-items-center">
                                  {active ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin text-lime-300" />
                                  ) : job.status === "done" ? (
                                    <Check className="h-3.5 w-3.5 text-lime-300" />
                                  ) : (
                                    <AlertTriangle className="h-3.5 w-3.5 text-amber-300" />
                                  )}
                                </div>
                                <div className="min-w-0 flex-1">
                                  <div className="truncate text-[12px] text-white/80">
                                    {job.query || tm("Background task")}
                                  </div>
                                  <div className="text-[10.5px] text-white/40">{status}</div>
                                  {job.status === "running" && (job.progress_tokens ?? 0) > 0 && (
                                    <div className="text-[10px] text-lime-200/60">
                                      {job.progress_tokens} {tm("tokens")} · {job.progress_chars ?? 0}{" "}
                                      {tm("chars")}
                                    </div>
                                  )}
                                </div>
                                {job.status === "done" && (
                                  <ChevronRight className="h-3.5 w-3.5 shrink-0 text-white/30" />
                                )}
                              </button>
                              {(job.status === "queued" || job.status === "running") &&
                                !job.cancel_requested && (
                                <button
                                  onClick={() => void cancelBackgroundJob(job.job_id)}
                                  className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-white/45 hover:bg-white/10 hover:text-white"
                                  title={tm("Cancel job")}
                                >
                                  <X className="h-3.5 w-3.5" />
                                </button>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                  {visibleResultMessages.length === 0 && backgroundJobs.length === 0 ? (
                    <div className="mx-auto mt-10 max-w-xs text-center text-[12px] leading-relaxed text-white/40">
                      {tm(
                        "Pick a quick action above — the generated documents, decks and search results appear here.",
                      )}
                    </div>
                  ) : (
                    visibleResultMessages.map((m) => (
                      <div key={m.id} data-result-id={m.id} className="scroll-mt-2">
                        <MessageView
                          msg={m}
                          onToggleAction={(aid) => toggleAction(m.id, aid)}
                          onCitation={openCitation}
                          onUpdateSlide={(sid, patch) => updateSlide(m.id, sid, patch)}
                          onEdit={(patch) => patchMessage(m.id, patch)}
                          onOpenFile={openFile}
                        />
                      </div>
                    ))
                  )}
                </div>
              </div>
            </section>

            {/* PDF preview pane — takes the drive's place on the right */}
            {previewFile && driveOpen && (
              <PdfPreviewPane
                file={previewFile}
                citation={previewCitation}
                evidence={viewerCitations}
                drive={drive}
                mode={viewerMode}
                matchCount={viewerCitations.length}
                matchIndex={viewerIndex}
                onPrevMatch={() => goMatch(-1)}
                onNextMatch={() => goMatch(1)}
                onToggleMode={() => setViewerMode((m) => (m === "split" ? "side" : "split"))}
                onClose={() => {
                  setPreviewFile(null);
                  setPreviewCitation(null);
                }}
                onClearCitation={() => setPreviewCitation(null)}
              >
                <div className="border-t border-white/5 p-3">
                  <button
                    className="flex w-full items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs font-medium disabled:opacity-40"
                    style={{ backgroundColor: LIME, color: "#15181B" }}
                    disabled={boxState !== "online" || liveBusy || !!wizard}
                    onClick={() => analyzeDocument(previewFile)}
                  >
                    <Zap className="h-3.5 w-3.5" /> Analyze with AI
                  </button>
                </div>
              </PdfPreviewPane>
            )}

            {/* Drive — hidden while a document is open above it */}
            {!previewFile && (
              <DrivePanel
                open={driveOpen}
                onClose={() => {
                  setDriveOpen(false);
                  setPreviewFile(null);
                  setPreviewCitation(null);
                }}
                files={filteredFiles}
                search={driveSearch}
                setSearch={setDriveSearch}
                view={driveView}
                setView={setDriveView}
                highlightedId={highlightedFileId}
                onPickerOpen={() => setPickerOpen((v) => !v)}
                pickerOpen={pickerOpen}
                onOpenExplorer={() => setExplorerOpen(true)}
                onSelectFile={(f) => {
                  setPickerOpen(false);
                  setPreviewFile(f);
                  setPreviewCitation(null);
                  setViewerMode("side");
                }}
                onSnippetSelect={(c) => openCitation(c)}
                drives={drives}
                drive={drive}
                driveName={driveName}
                drivePicker={drivePicker}
                onSelectDrive={selectDrive}
                folders={folders}
                cwd={cwd}
                onOpenFolder={(p) => {
                  setDriveSearch("");
                  void loadFolder(drive, p);
                }}
                searching={searchHits !== null}
                searchTruncated={searchTruncated}
                loading={filesLoading}
                error={filesError}
                onRefresh={() => void loadFolder(drive, cwd)}
                scopeDrives={scopeDrives}
                selectedDrives={selectedDrives}
                onToggleScopeDrive={toggleScopeDrive}
                onClearScope={() => setSelectedDrives([])}
                onInsertFile={(f) => {
                  setPickerOpen(false);
                  setMessages((m) => [
                    ...m,
                    {
                      id: uid(),
                      role: "ai",
                      name: "ViVeSec AI",
                      ts: nowTs(),
                      kind: "file",
                      fileId: f.id,
                    },
                  ]);
                }}
                onAnalyze={(f) => {
                  setPickerOpen(false);
                  analyzeDocument(f);
                }}
                getFile={(id) => files.find((f) => f.id === id)}
              />
            )}
          </div>
        </div>
      </div>
      {explorerOpen && (
        <DriveExplorerModal
          files={filteredFiles}
          folders={folders}
          drive={drive}
          driveName={driveName}
          cwd={cwd}
          search={driveSearch}
          searching={searchHits !== null}
          searchTruncated={searchTruncated}
          loading={filesLoading}
          error={filesError}
          onSearch={setDriveSearch}
          onNavigate={(path) => void loadFolder(drive, path)}
          onRefresh={() => void loadFolder(drive, cwd)}
          onOpen={(file) => {
            setExplorerOpen(false);
            setPreviewFile(file);
            setPreviewCitation(null);
            setViewerMode("side");
          }}
          onClose={() => setExplorerOpen(false)}
        />
      )}
    </DriveContext.Provider>
  );
}

/* ---------- Subcomponents ---------- */

function LanguageSelector() {
  const { lang, setLang } = useLang();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/5 px-2.5 py-1.5 text-xs font-medium text-white/85 hover:bg-white/10 transition"
        title="Language"
      >
        {lang}
        <ChevronDown
          className={`h-3.5 w-3.5 text-white/50 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      <AnimatePresence>
        {open && (
          <>
            <div className="fixed inset-0 z-40 bg-black/30" onClick={() => setOpen(false)} />
            <motion.div
              initial={{ opacity: 0, y: -6, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -6, scale: 0.97 }}
              transition={{ type: "spring", stiffness: 320, damping: 26 }}
              className="absolute right-0 z-50 mt-2 w-44 overflow-hidden rounded-xl border border-white/10 bg-[#23272B] shadow-2xl shadow-black/60"
            >
              {LANGS.map((l) => {
                const active = l.code === lang;
                return (
                  <button
                    key={l.code}
                    onClick={() => {
                      setLang(l.code as Lang);
                      setOpen(false);
                    }}
                    className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-[13px] text-white/80 transition hover:text-[#C6F24E] hover:bg-white/5"
                  >
                    <span
                      className="w-7 shrink-0 font-semibold"
                      style={active ? { color: LIME } : undefined}
                    >
                      {l.code}
                    </span>
                    <span className="flex-1 truncate text-white/60">{l.native}</span>
                    {active && <Check className="h-3.5 w-3.5" style={{ color: LIME }} />}
                  </button>
                );
              })}
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}

/** The original document, fetched from the ViVeSecBox on demand.
 *
 *  Nothing is cached on the AI Box, so this is a live round trip over the ws-fs
 *  channel. Only formats we can render safely are put behind a blob: URL — an
 *  inline .html would run in this origin, so anything else is a download.
 */
function DocumentView({
  path,
  name,
  drive,
  page,
  terms,
}: {
  path: string;
  name: string;
  drive?: string;
  /** Page to open on for PDFs — the citation the user clicked. */
  page?: number;
  terms?: string[];
}) {
  const { tm } = useLang();
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "error"; message: string }
    | { kind: "pdf" | "image" | "download"; url: string }
    | { kind: "text"; body: string; truncated: boolean }
  >({ kind: "loading" });

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    setState({ kind: "loading" });
    void openDriveDocument(path, drive).then(async (res) => {
      if (cancelled) return;
      if (!res.ok || !res.blob) {
        setState({ kind: "error", message: res.error ?? "the box did not return the file" });
        return;
      }
      const type = (res.contentType ?? "").split(";")[0].trim().toLowerCase();
      const buf = await res.blob.arrayBuffer();
      if (cancelled) return;
      const show = (kind: "pdf" | "image" | "download", mime: string) => {
        objectUrl = URL.createObjectURL(new Blob([buf], { type: mime }));
        setState({ kind, url: objectUrl });
      };
      if (type === "application/pdf") show("pdf", "application/pdf");
      else if (INLINE_IMAGE_TYPES.has(type)) show("image", type);
      else if (type === "text/plain") {
        const full = new TextDecoder().decode(buf);
        // A megabyte of log text would lock the tab up; say so rather than
        // silently showing a part of the document as if it were all of it.
        const truncated = full.length > TEXT_VIEW_LIMIT;
        setState({
          kind: "text",
          body: truncated ? full.slice(0, TEXT_VIEW_LIMIT) : full,
          truncated,
        });
      } else show("download", "application/octet-stream");
    });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path, drive]);

  if (state.kind === "loading") {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-[12.5px] text-white/50">
        <Loader2 className="h-5 w-5 animate-spin" style={{ color: LIME }} />
        {tm("Fetching the document from the ViVeSecBox…")}
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
        <AlertTriangle className="h-5 w-5 text-amber-300" />
        <div className="text-[12.5px] text-white/70">
          {tm("The ViVeSecBox did not hand over this document.")}
        </div>
        <div className="max-w-xs text-[11.5px] text-white/40">{state.message}</div>
      </div>
    );
  }

  if (state.kind === "pdf") {
    return (
      <iframe
        // Remount on a new citation so the viewer actually jumps to the page.
        key={`${state.url}#${page ?? 1}`}
        src={`${state.url}#page=${page ?? 1}&view=FitH`}
        title={name}
        className="flex-1 border-0 bg-[#23272B]"
      />
    );
  }

  if (state.kind === "image") {
    return (
      <div className="vvs-scroll flex-1 overflow-auto bg-[#15181B] p-3">
        <img src={state.url} alt={name} className="mx-auto max-w-full" />
      </div>
    );
  }

  if (state.kind === "text") {
    return (
      <div className="vvs-scroll flex-1 overflow-auto p-3 sm:p-4">
        <div className="mx-auto max-w-3xl rounded-md bg-[#F6F4EE] p-4 sm:p-6 text-[12.5px] leading-relaxed text-[#1E2225] shadow-2xl">
          <pre className="whitespace-pre-wrap break-words font-sans">
            <HighlightedText text={state.body} terms={terms ?? []} />
          </pre>
          {state.truncated && (
            <p className="mt-4 border-t border-black/10 pt-3 text-[11.5px] text-black/50">
              {tm("Only the first part of this document is shown here.")}
            </p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
      <FileText className="h-6 w-6 text-white/40" />
      <div className="text-[12.5px] text-white/70">
        {tm("This format cannot be shown in the browser.")}
      </div>
      <a
        href={state.url}
        download={name}
        className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold"
        style={{ backgroundColor: LIME, color: "#15181B" }}
      >
        <Download className="h-3.5 w-3.5" /> {tm("Download")}
      </a>
    </div>
  );
}

function PdfPreviewPane({
  file,
  citation,
  evidence,
  drive,
  mode = "side",
  matchCount = 0,
  matchIndex = 0,
  onPrevMatch,
  onNextMatch,
  onToggleMode,
  onClose,
  onClearCitation,
  children,
}: {
  file: DriveFile;
  citation: Citation | null;
  /** Every passage the box retrieved from this document in the current answer. */
  evidence?: Citation[];
  /** Active drive, forwarded as the demo picker's hint. */
  drive?: string;
  mode?: "side" | "split";
  matchCount?: number;
  matchIndex?: number;
  onPrevMatch?: () => void;
  onNextMatch?: () => void;
  onToggleMode?: () => void;
  onClose: () => void;
  onClearCitation: () => void;
  children?: React.ReactNode;
}) {
  // The document itself stays on the ViVeSecBox: what the AI Box has is the
  // evidence it retrieved. The "Document" tab asks the box for the real file;
  // "Passages" shows exactly what the answer was grounded in.
  const [tab, setTab] = useState<"document" | "passages">("document");
  type Para = { page: number; heading?: string; text: string };
  const paras: Para[] = useMemo(() => {
    const fromEvidence = (evidence ?? [])
      .filter((c) => c.fileId === file.id && c.snippet)
      .map((c) => ({ page: c.page, text: c.snippet as string }));
    if (fromEvidence.length) return fromEvidence;
    if (citation?.snippet) return [{ page: citation.page, text: citation.snippet }];
    if (file.pages?.length) {
      return file.pages.flatMap((pg) =>
        pg.paragraphs.map((text) => ({ page: pg.num, heading: pg.heading, text })),
      );
    }
    if (file.preview?.length) return file.preview.map((text, i) => ({ page: i + 1, text }));
    return [];
  }, [file, citation, evidence]);
  const distinctPages = useMemo(
    () => Array.from(new Set(paras.map((p) => p.page))).sort((a, b) => a - b),
    [paras],
  );
  const totalPages = Math.max(distinctPages[distinctPages.length - 1] ?? 1, 1);
  const { tm } = useLang();
  const [page, setPage] = useState<number>(citation?.page ?? 1);
  const highlightRef = useRef<HTMLParagraphElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Sync page + scroll when a new citation arrives
  useEffect(() => {
    if (citation) setPage(citation.page);
  }, [citation?.fileId, citation?.page, citation?.label]);

  useEffect(() => {
    if (!citation) return;
    const id = window.setTimeout(() => {
      highlightRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 80);
    return () => window.clearTimeout(id);
  }, [citation, page]);

  const safePage = Math.min(Math.max(1, page), totalPages);
  const highlightedParaIdx = useMemo(() => {
    if (!citation) return -1;
    if (citation.snippet) {
      const idx = paras.findIndex((p) => p.text === citation.snippet);
      if (idx >= 0) return idx;
    }
    return paras.findIndex((p) => p.page === citation.page);
  }, [citation, paras]);

  const isSplit = mode === "split";

  return (
    <section
      className={`fixed inset-0 z-[60] flex h-[100dvh] flex-col lg:static lg:order-1 lg:z-auto lg:h-full animate-[fadeSlide_320ms_ease-out] ${
        isSplit ? "lg:w-3/5 lg:shrink-0" : "lg:min-w-0 lg:flex-1 lg:basis-0"
      }`}
      style={{ backgroundColor: "#1B1F22" }}
    >
      {/* Mobile drag handle — affordance for sheet-style dismissal */}
      <div className="flex justify-center pt-2 pb-1 lg:hidden">
        <button
          onClick={onClose}
          aria-label="Close document viewer"
          className="h-1.5 w-12 rounded-full bg-white/20 active:bg-white/40"
        />
      </div>
      <div className="flex items-center gap-2 border-b border-white/5 px-3 py-2.5">
        <FileText className="h-4 w-4 text-rose-400" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{file.name}</div>
          <div className="text-[10.5px] text-white/45">
            {isSplit ? "Split-screen viewer · linked to chat" : "Side preview · linked to chat"}
          </div>
        </div>
        {onToggleMode && (
          <button
            onClick={onToggleMode}
            className="hidden lg:grid h-7 w-7 place-items-center rounded-md hover:bg-white/10"
            title={isSplit ? "Shrink to side preview" : "Expand to split view"}
          >
            <LayoutTemplate className="h-4 w-4 text-white/60" />
          </button>
        )}
        <button
          onClick={onClose}
          className="grid h-7 w-7 place-items-center rounded-md hover:bg-white/10"
        >
          <X className="h-4 w-4 text-white/60" />
        </button>
      </div>

      <div className="flex items-center gap-1 border-b border-white/5 px-3 py-1.5 text-[11.5px]">
        {(["document", "passages"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-md px-2.5 py-1 transition ${
              tab === t ? "bg-white/[0.08] text-white" : "text-white/55 hover:text-white"
            }`}
          >
            {t === "document" ? tm("Document") : tm("Retrieved passages")}
            {t === "passages" && paras.length > 0 && (
              <span className="ml-1 text-white/40">{paras.length}</span>
            )}
          </button>
        ))}
      </div>

      {citation && (
        <div
          className="flex items-center gap-2 border-b border-white/5 px-3 py-2 text-[11.5px]"
          style={{ backgroundColor: "rgba(204,255,0,0.06)" }}
        >
          <Link2 className="h-3.5 w-3.5" style={{ color: LIME }} />
          <span className="text-white/70">Jumped to</span>
          <span className="truncate font-medium" style={{ color: LIME }}>
            {citation.label}
          </span>
          {typeof citation.score === "number" && (
            <span className="rounded-full border border-white/10 bg-black/30 px-1.5 py-0.5 text-[10px] text-white/70">
              {citation.score}% match
            </span>
          )}
          <span className="ml-auto whitespace-nowrap text-white/45">p. {citation.page}</span>
          <button
            onClick={onClearCitation}
            className="ml-1 grid h-5 w-5 place-items-center rounded hover:bg-white/10"
          >
            <X className="h-3 w-3 text-white/60" />
          </button>
        </div>
      )}

      {tab === "document" && (
        <DocumentView
          path={file.id}
          name={file.name}
          drive={drive}
          page={citation?.page}
          terms={citation?.terms}
        />
      )}

      {tab === "passages" && (
      <div ref={scrollRef} className="vvs-scroll relative flex-1 overflow-y-auto p-3 sm:p-4">
        <div
          className={`mx-auto rounded-md bg-[#F6F4EE] p-4 sm:p-6 text-[13px] leading-relaxed text-[#1E2225] shadow-2xl ${isSplit ? "max-w-2xl" : "w-full lg:max-w-xl"}`}
        >
          <div className="mb-3 flex items-center justify-between border-b border-black/10 pb-2 text-[10px] uppercase tracking-wider text-black/50">
            <span className="truncate">{file.name}</span>
            <span>{paras.length ? tm("Retrieved passages") : ""}</span>
          </div>
          {paras.length === 0 && (
            <p className="py-6 text-center text-[12px] text-black/50">
              {tm(
                "The document stays on the ViVeSecBox — ask a question to see the passages the AI Box retrieved from it.",
              )}
            </p>
          )}
          {paras.map((p, i) => {
            const isHi = i === highlightedParaIdx;
            const showPageMarker = i === 0 || paras[i - 1].page !== p.page;
            return (
              <div key={i}>
                {showPageMarker && (
                  <div className="mb-2 mt-3 flex items-center gap-2 text-[10px] uppercase tracking-wider text-black/45">
                    <span className="rounded-sm bg-black/5 px-1.5 py-0.5">Page {p.page}</span>
                    {p.heading && <span className="font-semibold text-black/70">{p.heading}</span>}
                  </div>
                )}
                <p
                  ref={isHi ? highlightRef : undefined}
                  className={`mb-2.5 rounded-sm px-1.5 py-1.5 transition-colors ${isHi ? "ring-1" : ""}`}
                  style={
                    isHi
                      ? {
                          backgroundColor: "rgba(198,242,78,0.35)",
                          boxShadow: "inset 0 0 0 1px rgba(198,242,78,0.7)",
                        }
                      : undefined
                  }
                >
                  <HighlightedText text={p.text} terms={citation?.terms ?? []} />
                </p>
              </div>
            );
          })}
        </div>

        {/* Floating toolbar — safe-area inset for iOS home indicator */}
        <div
          className="pointer-events-none sticky mt-4 flex justify-center"
          style={{ bottom: "max(0.75rem, env(safe-area-inset-bottom))" }}
        >
          <div className="pointer-events-auto inline-flex items-center gap-1 rounded-full border border-white/10 bg-[#15181B]/95 px-1.5 py-1 shadow-2xl shadow-black/50 backdrop-blur">
            <button
              onClick={onPrevMatch}
              disabled={!matchCount || matchCount < 2}
              className="inline-flex items-center gap-1 rounded-full px-2.5 py-1.5 text-[11.5px] text-white/80 hover:bg-white/10 disabled:opacity-40"
              title="Previous match"
            >
              <ChevronLeft className="h-3.5 w-3.5" /> Prev
            </button>
            <span className="px-1.5 text-[11px] text-white/45">
              {matchCount > 0 ? `${matchIndex + 1} / ${matchCount}` : "—"}
            </span>
            <button
              onClick={onNextMatch}
              disabled={!matchCount || matchCount < 2}
              className="inline-flex items-center gap-1 rounded-full px-2.5 py-1.5 text-[11.5px] text-white/80 hover:bg-white/10 disabled:opacity-40"
              title="Next match"
            >
              Next <ChevronRight className="h-3.5 w-3.5" />
            </button>
            <div className="mx-1 h-4 w-px bg-white/10" />
            <button
              onClick={onClose}
              className="inline-flex items-center gap-1 rounded-full px-2.5 py-1.5 text-[11.5px] text-white/80 hover:bg-white/10"
              title="Close viewer"
            >
              <X className="h-3.5 w-3.5" /> Close
            </button>
          </div>
        </div>
      </div>
      )}

      {tab === "passages" && (
      <div className="flex items-center justify-between gap-2 border-t border-white/5 px-3 py-2 text-[11.5px] text-white/60">
        <button
          onClick={() => setPage((p) => Math.max(1, p - 1))}
          className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 hover:bg-white/10 disabled:opacity-40"
          disabled={safePage <= 1}
        >
          <ArrowLeft className="h-3 w-3" /> Prev
        </button>
        <span className="inline-flex items-center gap-1.5">
          <ShieldCheck className="h-3 w-3" style={{ color: LIME }} />
          Searched 100% locally on ViVeSecBox
        </span>
        <button
          onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 hover:bg-white/10 disabled:opacity-40"
          disabled={safePage >= totalPages}
        >
          Next <ArrowRight className="h-3 w-3" />
        </button>
      </div>
      )}

      {children}
    </section>
  );
}

/* Highlight matching terms inside an arbitrary string */
function HighlightedText({ text, terms }: { text: string; terms: string[] }) {
  const clean = terms.filter((t) => t && t.length >= 2);
  if (!clean.length) return <>{text}</>;
  const escaped = clean.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const splitRe = new RegExp(`(${escaped.join("|")})`, "gi");
  const testRe = new RegExp(`^(?:${escaped.join("|")})$`, "i");
  const parts = text.split(splitRe);
  return (
    <>
      {parts.map((p, i) =>
        testRe.test(p) ? (
          <mark
            key={i}
            style={{ backgroundColor: LIME, color: "#15181B", padding: "0 2px", borderRadius: 3 }}
          >
            {p}
          </mark>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

function LogoMark() {
  return (
    <div
      className="relative grid h-9 w-9 place-items-center rounded-full"
      style={{ backgroundColor: "#0F1113" }}
    >
      <Sparkles className="h-4 w-4" style={{ color: LIME }} />
      <span
        className="absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full ring-2 ring-[#15181B]"
        style={{ backgroundColor: LIME }}
      />
    </div>
  );
}

function Avatar({ name }: { name: string }) {
  const initials = name
    .split(/[\s.]/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0])
    .join("")
    .toUpperCase();
  const palette = ["#3B82F6", "#10B981", "#F59E0B", "#EC4899", "#8B5CF6"];
  const color = palette[name.charCodeAt(0) % palette.length];
  return (
    <div
      className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-[10px] font-bold text-white"
      style={{ backgroundColor: color }}
    >
      {initials}
    </div>
  );
}

function MessageView({
  msg,
  onToggleAction,
  onCitation,
  onUpdateSlide,
  onEdit,
  onOpenFile,
  inChat,
}: {
  msg: Message;
  onToggleAction: (aid: string) => void;
  onCitation: (c: Citation, siblings?: Citation[]) => void;
  onUpdateSlide: (sid: string, patch: Partial<Slide>) => void;
  onEdit: (patch: Record<string, unknown>) => void;
  onOpenFile: (id: string) => void;
  /** In the conversation the user's own message already states the request. */
  inChat?: boolean;
}) {
  const { t, tm } = useLang();
  if (msg.role === "system") {
    return (
      <div className="mx-auto max-w-md text-center text-[11px] leading-relaxed text-white/45">
        {t.bannerLine1.split(`'${t.headerTitle}'`)[0]}
        <span className="text-white/70">'{t.headerTitle}'</span>
        {t.bannerLine1.split(`'${t.headerTitle}'`)[1] ?? ""}
        <br />
        {t.bannerLine2}
      </div>
    );
  }

  if (msg.role === "user") {
    // Only the signed-in user produces user-role messages, so these are always "mine".
    return (
      <div className="flex flex-row-reverse gap-2.5">
        <Avatar name={msg.name} />
        <div className="flex max-w-[78%] flex-col items-end">
          <div className="mb-1 flex items-center gap-1.5">
            <span className="text-[12px] font-semibold" style={{ color: LIME }}>
              {msg.name}
            </span>
            <span className="text-[11px] text-white/40">{msg.ts}</span>
          </div>
          <div className="rounded-2xl px-3.5 py-2 text-[14px]" style={{ backgroundColor: PANEL }}>
            {msg.text}
          </div>
        </div>
      </div>
    );
  }

  // AI
  return (
    <div className="min-w-0">
      <div className="mb-1 flex items-center gap-1.5">
        <Sparkles className="h-3 w-3" style={{ color: LIME }} />
        <span className="text-[12px] font-semibold" style={{ color: LIME }}>
          {msg.name}
        </span>
        <span className="text-[11px] text-white/40">{msg.ts}</span>
      </div>

      {msg.kind === "text" && (
        <div
          className={`max-w-[80%] rounded-2xl px-3.5 py-2 text-[14px] ${msg.tone === "error" ? "border border-rose-400/40 text-rose-100" : ""}`}
          style={{ backgroundColor: msg.tone === "error" ? "rgba(244,63,94,0.12)" : PANEL }}
        >
          {renderInline(msg.text)}
        </div>
      )}

      {msg.kind === "thinking" && <ThinkingBubble startedAt={msg.startedAt} />}

      {msg.kind === "summary" && (
        <SummaryCard msg={msg} onToggle={onToggleAction} onEdit={onEdit} />
      )}
      {msg.kind === "report" && <ReportCard msg={msg} />}
      {msg.kind === "memo" && <MemoCard msg={msg} onEdit={onEdit} />}
      {msg.kind === "search" && (
        <SearchCard msg={msg} onCitation={onCitation} onOpenFile={onOpenFile} showQuery={!inChat} />
      )}
      {msg.kind === "deck" && (
        <DeckCard msg={msg} onUpdateSlide={onUpdateSlide} onCitation={onCitation} />
      )}
      {msg.kind === "file" && <FileInlineCard msg={msg} />}
      {msg.kind === "data" && (
        <DataInsightCard
          insight={msg.insight}
          loading={!msg.insight && !msg.error}
          error={msg.error}
        />
      )}
    </div>
  );
}

/** Waiting state for a local answer. The elapsed counter is the honest part:
 *  it proves the request is still alive when generation takes a while. */
function ThinkingBubble({ startedAt }: { startedAt: number }) {
  const { tm } = useLang();
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const tick = () => setElapsed(Math.floor((Date.now() - startedAt) / 1000));
    tick();
    const timer = setInterval(tick, 500);
    return () => clearInterval(timer);
  }, [startedAt]);

  return (
    <div
      className="inline-flex items-center gap-2.5 rounded-2xl px-3.5 py-2.5"
      style={{ backgroundColor: PANEL }}
      role="status"
      aria-live="polite"
    >
      <span className="flex items-center gap-1">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="vvs-think-dot h-1.5 w-1.5 rounded-full"
            style={{ backgroundColor: LIME, animationDelay: `${i * 160}ms` }}
          />
        ))}
      </span>
      <span className="vvs-think-label text-[13.5px]">{tm("The local model is thinking…")}</span>
      {elapsed >= 3 && <span className="text-[11px] tabular-nums text-white/35">{elapsed}s</span>}
    </div>
  );
}

function renderInline(text: string) {
  // Bold **x**
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) =>
    p.startsWith("**") && p.endsWith("**") ? (
      <strong key={i} className="font-semibold text-white">
        {p.slice(2, -2)}
      </strong>
    ) : (
      <span key={i}>{p}</span>
    ),
  );
}

/* ----- Guided dialog (spec F1/F3/F5 UX logic) ----- */

/** One question at a time. Chat-launched answers are echoed into the
 *  transcript; quick-action answers stay in the results-column workflow. */
function WizardPanel({
  kind,
  step,
  stepIndex,
  stepCount,
  files,
  picked,
  onPick,
  onAnswer,
  onCancel,
}: {
  kind: WizardKind;
  step: WizardStep;
  stepIndex: number;
  stepCount: number;
  files: DriveFile[];
  picked: string[];
  onPick: (ids: string[]) => void;
  onAnswer: (patch: Partial<WizardState>, echo: string) => void;
  onCancel: () => void;
}) {
  const { tm } = useLang();
  const [text, setText] = useState("");
  const [elseMode, setElseMode] = useState(false);
  const [search, setSearch] = useState("");

  useEffect(() => {
    setText("");
    setElseMode(false);
    setSearch("");
  }, [step]);

  const shown = files.filter((f) =>
    `${f.name} ${f.folder ?? ""}`.toLowerCase().includes(search.toLowerCase()),
  );
  const pickedNames = picked.map((id) => files.find((f) => f.id === id)?.name ?? id).join(", ");

  const submitText = (key: keyof WizardState) => {
    const v = text.trim();
    if (!v) return;
    onAnswer({ [key]: v } as Partial<WizardState>, v);
  };

  const purposeOptions = PURPOSE_OPTIONS[kind] ?? [];
  const choiceOptions = CHOICE_OPTIONS[step];

  return (
    <div
      className="mx-auto mb-2 max-w-3xl overflow-hidden rounded-xl border bg-[#23272B] shadow-xl"
      style={{ borderColor: `${LIME}44` }}
    >
      <div className="flex items-center gap-2 border-b border-white/5 px-3 py-2">
        <Sparkles className="h-3.5 w-3.5" style={{ color: LIME }} />
        <span className="text-[11px] uppercase tracking-wider text-white/45">
          {tm(WIZARD_NOUN[kind])} · {tm("Step")} {stepIndex + 1}/{stepCount}
        </span>
        <button
          onClick={onCancel}
          className="ml-auto grid h-7 w-7 place-items-center rounded-full hover:bg-white/10"
          aria-label="Cancel"
        >
          <X className="h-3.5 w-3.5 text-white/50" />
        </button>
      </div>

      <div className="p-3">
        <div className="mb-2.5 text-[13px] text-white/85">{tm(wizardQuestion(kind, step))}</div>

        {(step === "audience" ||
          step === "situation" ||
          step === "subject" ||
          step === "coverage" ||
          step === "keywords") && (
          <div className="flex gap-2">
            <input
              autoFocus
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitText(step);
              }}
              placeholder={tm(TEXT_PLACEHOLDER[step] ?? "")}
              className="flex-1 rounded-lg border border-white/10 bg-black/25 px-3 py-2 text-[13px] text-white placeholder:text-white/35 outline-none focus:border-white/25"
            />
            <button
              onClick={() => submitText(step)}
              disabled={!text.trim()}
              className="rounded-lg px-3 py-2 text-xs font-semibold disabled:opacity-40"
              style={{ backgroundColor: LIME, color: "#15181B" }}
            >
              {tm("Continue")}
            </button>
          </div>
        )}

        {choiceOptions && (
          <div className="space-y-1.5">
            {choiceOptions.map((o) => (
              <button
                key={o}
                onClick={() => onAnswer({ [step]: o } as Partial<WizardState>, tm(o))}
                className="flex w-full items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-left text-[13px] text-white/85 transition hover:border-white/25 hover:bg-white/[0.07]"
              >
                <ChevronRight className="h-3.5 w-3.5 shrink-0" style={{ color: LIME }} />
                {tm(o)}
              </button>
            ))}
          </div>
        )}

        {step === "purpose" && (
          <div className="space-y-1.5">
            {!elseMode &&
              purposeOptions.map((p) => (
                <button
                  key={p}
                  onClick={() => onAnswer({ purpose: p }, tm(p))}
                  className="flex w-full items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-left text-[13px] text-white/85 transition hover:border-white/25 hover:bg-white/[0.07]"
                >
                  <ChevronRight className="h-3.5 w-3.5 shrink-0" style={{ color: LIME }} />
                  {tm(p)}
                </button>
              ))}
            {!elseMode && purposeOptions.length > 0 ? (
              <button
                onClick={() => setElseMode(true)}
                className="w-full rounded-lg border border-dashed border-white/15 px-3 py-2 text-left text-[13px] text-white/55 transition hover:text-white/80"
              >
                {tm("Else — describe it yourself")}
              </button>
            ) : (
              <div className="flex gap-2">
                <input
                  autoFocus
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") submitText("purpose");
                  }}
                  placeholder={tm("Add the purpose to find the right context")}
                  className="flex-1 rounded-lg border border-white/10 bg-black/25 px-3 py-2 text-[13px] text-white placeholder:text-white/35 outline-none focus:border-white/25"
                />
                <button
                  onClick={() => submitText("purpose")}
                  disabled={!text.trim()}
                  className="rounded-lg px-3 py-2 text-xs font-semibold disabled:opacity-40"
                  style={{ backgroundColor: LIME, color: "#15181B" }}
                >
                  {tm("Continue")}
                </button>
              </div>
            )}
          </div>
        )}

        {step === "sources" && (
          <div>
            <div className="mb-2 flex items-center gap-2 rounded-lg border border-white/10 bg-black/25 px-2.5 py-1.5">
              <Search className="h-3.5 w-3.5 text-white/40" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={tm("Search the drive")}
                className="flex-1 bg-transparent text-[12.5px] text-white placeholder:text-white/35 outline-none"
              />
            </div>
            <div className="max-h-52 overflow-y-auto rounded-lg border border-white/5">
              {shown.length === 0 && (
                <div className="px-3 py-3 text-[12px] text-white/40">
                  {tm("No files on this drive.")}
                </div>
              )}
              {shown.map((f) => {
                const on = picked.includes(f.id);
                const Icon = fileIcon(f.type);
                return (
                  <button
                    key={f.id}
                    onClick={() =>
                      onPick(on ? picked.filter((x) => x !== f.id) : [...picked, f.id])
                    }
                    className="flex w-full items-center gap-2.5 border-b border-white/5 px-2.5 py-2 text-left transition last:border-0 hover:bg-white/5"
                  >
                    <span
                      className={`grid h-4 w-4 shrink-0 place-items-center rounded border ${on ? "" : "border-white/20"}`}
                      style={on ? { backgroundColor: LIME, borderColor: LIME } : {}}
                    >
                      {on && <Check className="h-3 w-3 text-[#15181B]" />}
                    </span>
                    <Icon className={`h-3.5 w-3.5 shrink-0 ${fileTint(f.type).split(" ")[0]}`} />
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-white/85">
                      {f.name}
                    </span>
                    <span className="shrink-0 text-[10.5px] text-white/35">{f.folder}</span>
                  </button>
                );
              })}
            </div>
            <div className="mt-2 flex items-center gap-2">
              <button
                onClick={() => onAnswer({ files: [] }, tm("Use the whole drive"))}
                className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-white/65 transition hover:bg-white/10"
              >
                {tm("Use the whole drive")}
              </button>
              <button
                onClick={() => onAnswer({ files: picked }, pickedNames)}
                disabled={picked.length === 0}
                className="ml-auto rounded-lg px-3 py-1.5 text-xs font-semibold disabled:opacity-40"
                style={{ backgroundColor: LIME, color: "#15181B" }}
              >
                {tm("Use selected")} ({picked.length})
              </button>
            </div>
          </div>
        )}

        {step === "extra" && (
          <div>
            <textarea
              autoFocus
              rows={2}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={tm("Anything else the model should know (optional)")}
              className="w-full resize-none rounded-lg border border-white/10 bg-black/25 px-3 py-2 text-[13px] text-white placeholder:text-white/35 outline-none focus:border-white/25"
            />
            <div className="mt-2 flex items-center gap-2">
              <button
                onClick={() => onAnswer({ extra: "" }, tm("No, nothing else"))}
                className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-white/65 transition hover:bg-white/10"
              >
                {tm("No, nothing else")}
              </button>
              <button
                onClick={() => submitText("extra")}
                disabled={!text.trim()}
                className="ml-auto rounded-lg px-3 py-1.5 text-xs font-semibold disabled:opacity-40"
                style={{ backgroundColor: LIME, color: "#15181B" }}
              >
                {tm("Continue")}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ----- Cards ----- */

/** C6 — the grounding evidence that must accompany every displayed output:
 *  confidence band + the adapter's audit footer (Score / Sources / Audit ID). */
function LiveFooter({ meta }: { meta: LiveMeta }) {
  const { tm } = useLang();
  const conf = meta.confidence;
  const color = conf?.band === "green" ? "#a3e635" : conf?.band === "amber" ? "#fbbf24" : "#f87171";
  if (!conf && !meta.audit?.length && !meta.auditId && !meta.profile) return null;
  return (
    <div className="mt-3 border-t border-white/10 pt-2">
      <div className="flex flex-wrap items-center gap-2">
        {meta.profile && <span className="text-[11px] text-white/60">{tm(meta.profile === "hybrid" ? "Hybrid" : "Grounded")}</span>}
        {conf && (
          <span
            className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10.5px] font-semibold"
            style={{ borderColor: `${color}55`, color, backgroundColor: `${color}15` }}
            title={tm("Confidence score (retrieval + generation)")}
          >
            <CircleDot className="h-2.5 w-2.5" /> {tm("Confidence")}: {conf.score}%
          </span>
        )}
        {meta.auditId && <FeedbackButtons meta={meta} />}
      </div>
      {!!meta.audit?.length && (
        <ul className="mt-2 space-y-0.5 text-[11px] leading-relaxed text-white/55">
          {meta.audit.map((line, i) => (
            <li key={i}>{renderInline(line)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The down-vote failure taxonomy — maps onto the eval categories (retrieval
 *  miss / hallucination / refusal correctness / coverage / format). */
const FEEDBACK_REASONS: { key: string; label: string }[] = [
  { key: "wrong-source", label: "Wrong source" },
  { key: "ungrounded", label: "Made-up data" },
  { key: "unnecessary-refusal", label: "Should have answered" },
  { key: "incomplete", label: "Incomplete" },
  { key: "format", label: "Wrong format" },
];

/** Rate the answer (good / not good). Each click is stored on the box joined
 *  with the answer trace; a down-vote offers an optional reason refinement. */
function FeedbackButtons({ meta }: { meta: LiveMeta }) {
  const { tm } = useLang();
  const [rating, setRating] = useState<"up" | "down" | null>(null);
  const [reasonSent, setReasonSent] = useState(false);

  async function rate(value: "up" | "down", reason?: string) {
    if (!meta.auditId) return;
    const res = await sendFeedback({
      data: {
        auditId: meta.auditId,
        rating: value,
        reason,
        question: meta.question,
        answer: meta.raw,
        drive: meta.feedbackDrive,
      },
    });
    if (!res.ok) {
      toast.error(tm("Could not save the feedback."));
      return false;
    }
    return true;
  }

  function onVote(value: "up" | "down") {
    if (rating) return;
    setRating(value);
    void rate(value).then((ok) => {
      if (ok === false) setRating(null);
      else if (value === "up") toast.success(tm("Feedback saved on the AI Box."));
    });
  }

  function onReason(key: string) {
    setReasonSent(true);
    void rate("down", key).then((ok) => {
      if (ok !== false) toast.success(tm("Feedback saved on the AI Box."));
    });
  }

  const voteBtn = (value: "up" | "down", Icon: typeof ThumbsUp, label: string) => (
    <button
      onClick={() => onVote(value)}
      disabled={rating !== null}
      title={tm(label)}
      aria-label={tm(label)}
      className={`inline-flex items-center rounded-full border px-1.5 py-0.5 transition ${
        rating === value
          ? "border-white/30 text-white"
          : "border-white/10 text-white/45 hover:text-white/80 disabled:opacity-40"
      }`}
    >
      <Icon className="h-3 w-3" />
    </button>
  );

  return (
    <span className="ml-auto inline-flex flex-wrap items-center gap-1.5">
      {voteBtn("up", ThumbsUp, "Good answer")}
      {voteBtn("down", ThumbsDown, "Bad answer")}
      {rating === "down" && !reasonSent && (
        <>
          <span className="text-[10.5px] text-white/45">{tm("What was wrong?")}</span>
          {FEEDBACK_REASONS.map((r) => (
            <button
              key={r.key}
              onClick={() => onReason(r.key)}
              className="rounded-full border border-white/10 px-2 py-0.5 text-[10.5px] text-white/55 transition hover:bg-white/10 hover:text-white/85"
            >
              {tm(r.label)}
            </button>
          ))}
        </>
      )}
    </span>
  );
}

function CardShell({
  children,
  title,
  icon: Icon,
  editing,
  onToggleEdit,
}: {
  children: React.ReactNode;
  /** null hides the header strip entirely (the context already names the run). */
  title: string | null;
  icon: typeof Sparkles;
  editing?: boolean;
  onToggleEdit?: () => void;
}) {
  const { tm } = useLang();
  return (
    <div
      className="overflow-hidden rounded-2xl border border-white/[0.07]"
      style={{ backgroundColor: PANEL }}
    >
      {(title !== null || onToggleEdit) && (
        <div className="flex items-center gap-2 border-b border-white/[0.07] px-4 py-2.5">
          <Icon className="h-4 w-4" style={{ color: LIME }} />
          <div className="min-w-0 flex-1 truncate text-[13px] font-semibold">
            {title === null ? "" : tm(title)}
          </div>
          {onToggleEdit && (
            <button
              onClick={onToggleEdit}
              className="inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px] font-semibold transition"
              style={
                editing
                  ? { borderColor: LIME, color: "#15181B", backgroundColor: LIME }
                  : { borderColor: "rgba(255,255,255,0.15)", color: "rgba(255,255,255,0.7)" }
              }
              title={tm("Edit this output before saving")}
            >
              {editing ? <Check className="h-3 w-3" /> : <Pencil className="h-3 w-3" />}
              {editing ? tm("Done") : tm("Edit")}
            </button>
          )}
        </div>
      )}
      <div className="p-4">{children}</div>
    </div>
  );
}

function SummaryCard({
  msg,
  onToggle,
  onEdit,
}: {
  msg: Extract<Message, { kind: "summary" }>;
  onToggle: (aid: string) => void;
  onEdit: (patch: Record<string, unknown>) => void;
}) {
  const [tab, setTab] = useState<"summary" | "actions" | "email">("summary");
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const { tm } = useLang();
  return (
    <CardShell
      title={msg.title}
      icon={ClipboardList}
      editing={editing}
      onToggleEdit={() => setEditing((v) => !v)}
    >
      <div className="mb-3 flex gap-1 rounded-lg bg-black/30 p-1 text-[12px]">
        {(["summary", "actions", "email"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 rounded-md px-2 py-1.5 transition ${tab === t ? "bg-white/[0.08] text-white" : "text-white/55 hover:text-white"}`}
          >
            {t === "summary"
              ? tm("Summary")
              : t === "actions"
                ? tm("Action Items")
                : tm("Follow-up Email")}
          </button>
        ))}
      </div>

      {tab === "summary" && (
        <ul className="space-y-2 text-[13.5px] text-white/85">
          {msg.bullets.map((b, i) => (
            <li key={i} className="flex gap-2">
              <span
                className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full"
                style={{ backgroundColor: LIME }}
              />
              <EditableText
                editing={editing}
                multiline
                value={tm(b)}
                onChange={(v) =>
                  onEdit({ bullets: msg.bullets.map((x, idx) => (idx === i ? v : x)) })
                }
              />
            </li>
          ))}
        </ul>
      )}

      {tab === "actions" && (
        <ul className="space-y-1.5">
          {msg.actions.map((a) => (
            <li key={a.id}>
              <div className="group flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left hover:bg-white/[0.04]">
                <button
                  onClick={() => onToggle(a.id)}
                  className={`grid h-5 w-5 shrink-0 place-items-center rounded-md border transition ${a.done ? "" : "border-white/20"}`}
                  style={a.done ? { backgroundColor: LIME, borderColor: LIME } : {}}
                  aria-label="Toggle"
                >
                  {a.done && <Check className="h-3.5 w-3.5 text-[#15181B]" />}
                </button>
                <span
                  className={`flex-1 text-[13.5px] ${a.done ? "text-white/40 line-through" : "text-white/90"}`}
                >
                  <EditableText
                    editing={editing}
                    value={tm(a.text)}
                    onChange={(v) =>
                      onEdit({
                        actions: msg.actions.map((x) => (x.id === a.id ? { ...x, text: v } : x)),
                      })
                    }
                  />
                </span>
                <span className="text-[11px] text-white/50">
                  <EditableText
                    editing={editing}
                    value={a.assignee}
                    onChange={(v) =>
                      onEdit({
                        actions: msg.actions.map((x) =>
                          x.id === a.id ? { ...x, assignee: v } : x,
                        ),
                      })
                    }
                  />
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}

      {tab === "email" && (
        <div>
          {editing ? (
            <textarea
              value={msg.email}
              onChange={(e) => onEdit({ email: e.target.value })}
              rows={Math.min(18, Math.max(6, msg.email.split("\n").length))}
              className="w-full resize-y rounded-lg border border-white/15 bg-black/30 p-3 font-sans text-[12.5px] leading-relaxed text-white outline-none focus:border-white/30"
            />
          ) : (
            <pre className="whitespace-pre-wrap rounded-lg bg-black/30 p-3 font-sans text-[12.5px] leading-relaxed text-white/85">
              {tm(msg.email)}
            </pre>
          )}
          <div className="mt-3 flex gap-2">
            <button
              onClick={() => {
                navigator.clipboard?.writeText(msg.email);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs hover:bg-white/10 transition"
            >
              <Copy className="h-3.5 w-3.5" /> {copied ? tm("Copied!") : tm("Copy to clipboard")}
            </button>
            <SaveToDriveButton
              variant="chip"
              text={msg.email}
              defaultName={saveName("follow-up-email")}
            />
          </div>
        </div>
      )}
      <div className="mt-3 flex">
        <SaveToDriveButton text={summaryMarkdown(msg)} defaultName={saveName("meeting-summary")} />
      </div>
      <LiveFooter meta={msg} />
    </CardShell>
  );
}

/** Summary -> markdown. Rebuilt from the card so manual edits are saved. */
function summaryMarkdown(msg: Extract<Message, { kind: "summary" }>): string {
  if (!msg.edited && msg.raw) return msg.raw;
  return [
    `# ${msg.title}`,
    "## Executive summary",
    ...msg.bullets.map((b) => `- ${b}`),
    "",
    "## Action items",
    ...msg.actions.map((a) => `- ${a.text} | ${a.assignee}`),
    "",
    "## Follow-up email",
    msg.email,
  ].join("\n");
}

function ReportCard({ msg }: { msg: Extract<Message, { kind: "report" }> }) {
  const { tm } = useLang();
  const toneCls = (t: "good" | "warn" | "bad") =>
    t === "good" ? "text-emerald-400" : t === "warn" ? "text-amber-400" : "text-rose-400";
  const statusChip = (s: ReportTask["status"]) => {
    const map = {
      "On Track": "bg-emerald-400/15 text-emerald-300",
      Delayed: "bg-rose-400/15 text-rose-300",
      "At Risk": "bg-amber-400/15 text-amber-300",
      Done: "bg-white/10 text-white/60",
    } as const;
    return (
      <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${map[s]}`}>{tm(s)}</span>
    );
  };
  return (
    <CardShell title={msg.title} icon={BarChart3}>
      {msg.metrics.length > 0 && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {msg.metrics.map((m) => (
            <div key={m.label} className="rounded-lg bg-black/25 p-3">
              <div className="text-[10.5px] uppercase tracking-wider text-white/45">
                {tm(m.label)}
              </div>
              <div className={`mt-1 text-lg font-semibold ${toneCls(m.tone)}`}>{m.value}</div>
            </div>
          ))}
        </div>
      )}
      {!!msg.narrative?.length && (
        <div className="mt-3 space-y-1.5 text-[13px] leading-relaxed text-white/75">
          {msg.narrative.map((n, i) => (
            <p key={i}>{renderInline(n)}</p>
          ))}
        </div>
      )}
      <div className="mt-3 divide-y divide-white/5 rounded-lg border border-white/5">
        {msg.tasks.map((t) => (
          <div key={t.id} className="flex items-center gap-3 px-3 py-2.5">
            <div className="grid h-7 w-7 place-items-center rounded-full bg-white/10 text-[10px] font-bold">
              {t.owner}
            </div>
            <div className="flex-1 text-[13.5px]">{tm(t.title)}</div>
            {statusChip(t.status)}
          </div>
        ))}
      </div>
      <div className="mt-3 flex">
        <SaveToDriveButton
          text={
            msg.raw ??
            `# ${msg.title}\n\n${msg.metrics.map((m) => `- ${m.label}: ${m.value}`).join("\n")}\n\n${msg.tasks.map((t) => `- ${t.title} | ${t.owner} | ${t.status}`).join("\n")}\n`
          }
          defaultName={saveName("report")}
        />
      </div>
      <LiveFooter meta={msg} />
    </CardShell>
  );
}

function MemoCard({
  msg,
  onEdit,
}: {
  msg: Extract<Message, { kind: "memo" }>;
  onEdit: (patch: Record<string, unknown>) => void;
}) {
  const { tm } = useLang();
  const [editing, setEditing] = useState(false);
  return (
    <CardShell
      title={msg.title}
      icon={StickyNote}
      editing={editing}
      onToggleEdit={() => setEditing((v) => !v)}
    >
      {editing ? (
        <>
          <input
            value={msg.title}
            onChange={(e) => onEdit({ title: e.target.value })}
            className="mb-2 w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-[13px] font-semibold text-white outline-none focus:border-white/30"
          />
          <textarea
            value={msg.body}
            onChange={(e) => onEdit({ body: e.target.value })}
            rows={Math.min(28, Math.max(8, msg.body.split("\n").length))}
            className="w-full resize-y rounded-lg border border-white/15 bg-black/30 p-3 font-sans text-[13px] leading-relaxed text-white outline-none focus:border-white/30"
          />
        </>
      ) : (
        <pre className="whitespace-pre-wrap rounded-lg bg-black/25 p-3 font-sans text-[13px] leading-relaxed text-white/85">
          {tm(msg.body)}
        </pre>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <SaveToDriveButton
          variant="chip"
          text={msg.edited || !msg.raw ? `# ${msg.title}\n\n${msg.body}` : msg.raw}
          defaultName={saveName("memo")}
        />
        <button
          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium"
          style={{ backgroundColor: LIME, color: "#15181B" }}
        >
          <Mail className="h-3.5 w-3.5" /> {tm("Send for approval")}
        </button>
      </div>
      <LiveFooter meta={msg} />
    </CardShell>
  );
}

// C7 — Save a generated output to the ViVeSecBox drive over the adapter's ws-fs
// channel (B6). On permission/no-channel the AIBox keeps a session copy and we
// fall back to a browser download of that stored copy. Shared by every card
// that produces a saveable document.
function SaveToDriveButton({
  text,
  defaultName,
  variant = "subtle",
  formats = ["pdf", "docx", "md"],
  defaultFormat,
  title,
}: {
  text: string;
  defaultName: string;
  variant?: "subtle" | "chip";
  /** Offered output formats; the adapter (docgen.py) renders them. */
  formats?: SaveFormat[];
  defaultFormat?: SaveFormat;
  title?: string;
}) {
  const { tm } = useLang();
  const { drive } = useDrive();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(defaultName);
  const [fmt, setFmt] = useState<SaveFormat>(defaultFormat ?? formats[0]);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ path?: string; name: string; downloaded: boolean } | null>(
    null,
  );

  async function handleSave() {
    setSaving(true);
    try {
      const res = await saveToDrive({
        data: {
          name: withExtension(name, fmt),
          text,
          format: fmt,
          title,
          drive: drive || undefined,
        },
      });
      if (!res.ok) throw new Error(res.error || "save failed");
      if (res.transferred) {
        toast.success(`${tm("Saved to drive")}: ${res.path}`);
        setResult({ path: res.path, name: res.name, downloaded: false });
      } else if (res.canDownload) {
        toast.info(
          res.reason === "permission"
            ? tm("No write permission on the drive — downloading the AIBox copy instead.")
            : tm("ViVeSecBox not connected — downloading the AIBox copy instead."),
        );
        await downloadStoredFile(res.name, drive);
        setResult({ name: res.name, downloaded: true });
      } else {
        toast.error(`${tm("Save failed")}: ${res.reason || res.error || "?"}`);
      }
      notifyGeneratedFilesChanged();
      setOpen(false);
    } catch (err) {
      toast.error(`${tm("Save failed")}: ${err instanceof Error ? err.message : "?"}`);
    } finally {
      setSaving(false);
    }
  }

  const cls =
    variant === "chip"
      ? "inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs transition hover:bg-white/10 disabled:opacity-50"
      : "ml-auto inline-flex items-center gap-1.5 rounded-full border border-white/15 px-2.5 py-1 text-[10.5px] font-semibold text-white/75 transition hover:border-white/30 hover:text-white disabled:opacity-50";

  return (
    <>
      <button
        onClick={() => {
          const initial = defaultFormat ?? formats[0];
          setFmt(initial);
          setName(withExtension(defaultName, initial));
          setOpen(true);
        }}
        disabled={saving}
        className={cls}
        title={tm("Save this answer to the ViVeSecBox drive")}
      >
        <Save className={variant === "chip" ? "h-3.5 w-3.5" : "h-3 w-3"} style={{ color: LIME }} />
        {saving ? tm("Saving…") : tm("Save to drive")}
      </button>

      {/* Confirmation stays on the card: the user can see the save happened. */}
      {result && (
        <div className="mt-2 flex w-full items-center gap-1.5 text-[11px] text-white/60">
          <Check className="h-3 w-3 shrink-0" style={{ color: LIME }} />
          <span className="truncate">
            {result.downloaded
              ? `${tm("Downloaded from the AI Box")}: ${result.name}`
              : `${tm("Saved to drive")}: ${result.path ?? result.name}`}
          </span>
        </div>
      )}

      {open && (
        <SaveDialog
          name={name}
          setName={setName}
          fmt={fmt}
          setFmt={(f) => {
            setFmt(f);
            setName((n) => withExtension(n, f));
          }}
          formats={formats}
          saving={saving}
          onCancel={() => setOpen(false)}
          onSave={() => void handleSave()}
        />
      )}
    </>
  );
}

const FORMAT_LABEL: Record<SaveFormat, string> = {
  pdf: "PDF",
  docx: "Word (.docx)",
  pptx: "PowerPoint (.pptx)",
  md: "Markdown (.md)",
  txt: "Plain text (.txt)",
};

const FORMAT_EXT: Record<SaveFormat, string> = {
  pdf: ".pdf",
  docx: ".docx",
  pptx: ".pptx",
  md: ".md",
  txt: ".txt",
};

/** Mirrors adapter/docgen.py with_extension, so the dialog shows the name the
 *  file will actually be saved under. */
function withExtension(name: string, fmt: SaveFormat): string {
  let base = (name || "").trim() || "document";
  for (const ext of Object.values(FORMAT_EXT)) {
    if (base.toLowerCase().endsWith(ext)) {
      base = base.slice(0, -ext.length);
      break;
    }
  }
  return base + FORMAT_EXT[fmt];
}

function SaveDialog({
  name,
  setName,
  fmt,
  setFmt,
  formats,
  saving,
  onCancel,
  onSave,
}: {
  name: string;
  setName: (v: string) => void;
  fmt: SaveFormat;
  setFmt: (f: SaveFormat) => void;
  formats: SaveFormat[];
  saving: boolean;
  onCancel: () => void;
  onSave: () => void;
}) {
  const { tm } = useLang();
  return (
    <div
      className="fixed inset-0 z-[70] grid place-items-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-full max-w-sm rounded-2xl border border-white/10 p-4 shadow-2xl shadow-black/60"
        style={{ backgroundColor: "#1B1F22" }}
      >
        <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold">
          <Save className="h-4 w-4" style={{ color: LIME }} />
          {tm("Save to drive")}
        </div>

        <label className="mb-1 block text-[11px] uppercase tracking-wider text-white/45">
          {tm("File name")}
        </label>
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && name.trim() && !saving) onSave();
            if (e.key === "Escape") onCancel();
          }}
          className="mb-3 w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-[13px] text-white outline-none focus:border-white/30"
        />

        <div className="mb-1 text-[11px] uppercase tracking-wider text-white/45">
          {tm("Format")}
        </div>
        <div className="mb-4 flex flex-wrap gap-1.5">
          {formats.map((f) => (
            <button
              key={f}
              onClick={() => setFmt(f)}
              className={`rounded-lg border px-2.5 py-1.5 text-[12px] transition ${
                f === fmt
                  ? "border-white/30 bg-white/[0.10] text-white"
                  : "border-white/10 text-white/60 hover:bg-white/5"
              }`}
            >
              {FORMAT_LABEL[f]}
            </button>
          ))}
        </div>

        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-white/70 transition hover:bg-white/5"
          >
            {tm("Cancel")}
          </button>
          <button
            onClick={onSave}
            disabled={saving || !name.trim()}
            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium disabled:opacity-50"
            style={{ backgroundColor: LIME, color: "#15181B" }}
          >
            {saving ? tm("Saving…") : tm("Save to drive")}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Fetch a session-stored generated file and hand it to the browser. */
async function downloadStoredFile(name: string, drive?: string) {
  const dl = await downloadGenerated({ data: { name, drive: drive || undefined } });
  if (!dl.ok || !dl.contentB64) return false;
  const bytes = Uint8Array.from(atob(dl.contentB64), (ch) => ch.charCodeAt(0));
  const url = URL.createObjectURL(
    new Blob([bytes], { type: dl.contentType || "application/octet-stream" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = dl.name;
  a.click();
  URL.revokeObjectURL(url);
  return true;
}

/** The generated-files list lives in the drive panel while saves happen on the
 *  cards, so a save pings the panel instead of threading state through it. */
const generatedFilesListeners = new Set<() => void>();
function notifyGeneratedFilesChanged() {
  generatedFilesListeners.forEach((fn) => fn());
}

/** Date-stamped default filename for a saved output (the adapter appends the
 *  extension of the chosen format). */
function saveName(prefix: string) {
  return `${prefix}-${new Date().toISOString().slice(0, 10)}`;
}
function SearchCard({
  msg,
  onCitation,
  onOpenFile,
  showQuery,
}: {
  msg: Extract<Message, { kind: "search" }>;
  onCitation: (c: Citation, siblings?: Citation[]) => void;
  onOpenFile: (id: string) => void;
  showQuery: boolean;
}) {
  const { tm } = useLang();
  const { body: answerBody } = splitAuditFooter(msg.answer);
  const [showAllSources, setShowAllSources] = useState(false);
  // Only the best passage is shown up front: with 8-10 hits the evidence list
  // outgrew the answer itself and buried it.
  const visibleCitations = showAllSources ? msg.citations : msg.citations.slice(0, 1);
  const hiddenCount = msg.citations.length - visibleCitations.length;
  // Which drive a passage came from only matters once an answer mixes several;
  // on a single-drive box the badge would be noise on every card.
  const crossDrive =
    new Set(msg.citations.map((c) => driveNameOf(c.fileId)).filter(Boolean)).size > 1;
  // The card icon already says "search"; repeating the "/search" prefix does not.
  const term = msg.query.replace(/^\/\w+\s*/, "").trim();

  return (
    <CardShell
      title={showQuery ? (term ? `Search · "${term}"` : "Search") : null}
      icon={SearchIcon}
    >
      {!!msg.fileHits?.length && (
        <div className="mb-3 rounded-xl border border-white/10 bg-black/20 p-2.5">
          <div className="mb-1.5 flex items-center gap-1.5 text-[10.5px] uppercase tracking-wider text-white/45">
            <FileText className="h-3 w-3" style={{ color: LIME }} />
            {tm("Matching file names")} ({msg.fileHits.length})
          </div>
          <div className="flex flex-wrap gap-1.5">
            {msg.fileHits.map((f) => {
              const Icon = fileIcon(f.type);
              return (
                <button
                  key={f.id}
                  onClick={() => onOpenFile(f.id)}
                  title={f.folder ? `${f.folder}/${f.name}` : f.name}
                  className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-2 py-1 text-[11.5px] text-white/80 transition hover:border-white/25 hover:bg-white/10"
                >
                  <Icon className={`h-3 w-3 shrink-0 ${fileTint(f.type).split(" ")[0]}`} />
                  <span className="truncate">{f.name}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
      <p className="whitespace-pre-wrap text-[13.5px] leading-relaxed text-white/85">
        {renderInline(answerBody)}
      </p>
      {msg.citations.length > 0 && (
        <div className="mt-3">
          <div
            className={`space-y-2 ${showAllSources && msg.citations.length > 4 ? "vvs-scroll max-h-72 overflow-y-auto pr-1" : ""}`}
          >
            {visibleCitations.map((c, i) => (
              <button
                key={i}
                onClick={() => onCitation(c, msg.citations)}
                className="group w-full rounded-xl border border-white/10 bg-white/[0.03] p-2.5 text-left transition hover:border-white/20 hover:bg-white/[0.06]"
              >
                <div className="flex items-center gap-1.5 text-[11.5px]">
                  <FileText className="h-3 w-3 text-rose-400" />
                  {c.rank && <span className="shrink-0 text-white/55">[#{c.rank}]</span>}
                  <span className="truncate font-medium text-white/85">{c.label}</span>
                  {crossDrive && driveNameOf(c.fileId) && (
                    <span className="ml-auto shrink-0 rounded-full border border-white/10 bg-black/30 px-1.5 py-0.5 text-[10px] text-white/60">
                      {driveNameOf(c.fileId)}
                    </span>
                  )}
                </div>
                {c.snippet && (
                  <div className="mt-1.5 line-clamp-2 text-[12px] leading-relaxed text-white/65">
                    <HighlightedText text={c.snippet} terms={c.terms ?? []} />
                  </div>
                )}
              </button>
            ))}
          </div>
          {msg.citations.length > 1 && (
            <button
              onClick={() => setShowAllSources((v) => !v)}
              className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2 py-1 text-[11px] text-white/60 transition hover:border-white/20 hover:text-white/85"
            >
              <ChevronDown
                className={`h-3 w-3 transition-transform ${showAllSources ? "rotate-180" : ""}`}
              />
              {showAllSources
                ? tm("Show fewer passages")
                : `${tm("Show all retrieved passages")} (+${hiddenCount})`}
            </button>
          )}
        </div>
      )}
      <div className="mt-3 flex items-center gap-3">
        <span className="inline-flex items-center gap-1.5 text-[10.5px] text-white/45">
          <ShieldCheck className="h-3 w-3" style={{ color: LIME }} />
          {tm("Searched 100% locally on ViVeSecBox")}
        </span>
        <SaveToDriveButton text={msg.answer} defaultName={saveName("ai-answer")} />
      </div>
      <LiveFooter meta={msg} />
    </CardShell>
  );
}

function FileInlineCard({ msg }: { msg: Extract<Message, { kind: "file" }> }) {
  return (
    <div
      className="inline-flex items-center gap-3 rounded-xl border border-white/10 px-3 py-2.5"
      style={{ backgroundColor: PANEL }}
    >
      <div className="grid h-9 w-9 place-items-center rounded-md bg-rose-400/10">
        <FileText className="h-4 w-4 text-rose-400" />
      </div>
      <div className="text-[13px]">
        Inserted from Drive · <span className="text-white/55">{msg.fileId}</span>
      </div>
    </div>
  );
}

/* ---------- Presentation ---------- */

const LAYOUT_OPTIONS: { id: SlideLayout; label: string }[] = [
  { id: "bullets", label: "Bullets" },
  { id: "chart", label: "Chart" },
  { id: "big-number", label: "Big Number" },
  { id: "two-column", label: "Two-Column" },
];

function DeckCard({
  msg,
  onUpdateSlide,
  onExport,
  onCitation,
}: {
  msg: Extract<Message, { kind: "deck" }>;
  onUpdateSlide: (sid: string, patch: Partial<Slide>) => void;
  onExport?: () => void;
  onCitation: (c: Citation) => void;
}) {
  const [idx, setIdx] = useState(0);
  const [editing, setEditing] = useState(false);
  const slide = msg.slides[idx];
  const { tm } = useLang();

  function go(delta: number) {
    setEditing(false);
    setIdx((i) => Math.min(msg.slides.length - 1, Math.max(0, i + delta)));
  }

  function switchLayout(layout: SlideLayout) {
    const patch: Partial<Slide> = { layout };
    if (layout === "bullets" && !slide.bullets) {
      patch.bullets = ["Point one", "Point two", "Point three"];
    }
    if (layout === "big-number" && !slide.bigNumber) {
      patch.bigNumber = slide.bigNumber ?? "100%";
      patch.bigCaption = slide.bigCaption ?? "Headline metric to anchor the conversation.";
    }
    if (layout === "two-column" && (!slide.left || !slide.right)) {
      patch.left = slide.left ?? { title: "Current state", body: "Where we are today." };
      patch.right = slide.right ?? { title: "Target state", body: "Where Q2 takes us." };
    }
    onUpdateSlide(slide.id, patch);
  }

  return (
    <div
      className="overflow-hidden rounded-2xl border border-white/[0.07]"
      style={{ backgroundColor: PANEL }}
    >
      {/* Header */}
      <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.07] px-4 py-2.5">
        <Presentation className="h-4 w-4" style={{ color: LIME }} />
        <div className="text-[13px] font-semibold">{tm(msg.title)}</div>
        <span className="ml-1 rounded-full border border-white/10 bg-black/30 px-2 py-0.5 text-[10.5px] uppercase tracking-wider text-white/55">
          Slide {idx + 1} of {msg.slides.length}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => go(-1)}
            disabled={idx === 0}
            className="grid h-7 w-7 place-items-center rounded-md border border-white/10 bg-white/[0.04] text-white/70 transition hover:bg-white/10 disabled:opacity-40"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <button
            onClick={() => go(1)}
            disabled={idx === msg.slides.length - 1}
            className="grid h-7 w-7 place-items-center rounded-md border border-white/10 bg-white/[0.04] text-white/70 transition hover:bg-white/10 disabled:opacity-40"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Slide canvas */}
      <div className="p-3 sm:p-4">
        <div
          className="relative w-full overflow-hidden rounded-xl border border-white/10 bg-gradient-to-br from-[#15181B] to-[#1B1F22] shadow-inner"
          style={{ aspectRatio: "16 / 9" }}
        >
          <div key={slide.id} className="absolute inset-0 animate-[fadeSlide_320ms_ease-out]">
            <SlideCanvas
              slide={slide}
              editing={editing}
              onPatch={(p) => onUpdateSlide(slide.id, p)}
            />
          </div>
          {slide.source && (
            <button
              onClick={() => slide.source && onCitation(slide.source)}
              className="absolute bottom-2 right-2 inline-flex items-center gap-1 rounded-full border border-white/10 bg-black/50 px-2 py-1 text-[10.5px] text-white/75 backdrop-blur hover:bg-black/70 hover:text-white"
              title="Open source in Drive"
            >
              <Link2 className="h-3 w-3" style={{ color: LIME }} /> {tm("Source")}
            </button>
          )}
        </div>

        {/* Filmstrip */}
        <div className="mt-3 flex gap-1.5 overflow-x-auto pb-1">
          {msg.slides.map((s, i) => (
            <button
              key={s.id}
              onClick={() => {
                setEditing(false);
                setIdx(i);
              }}
              className={`group relative h-12 w-20 shrink-0 overflow-hidden rounded-md border text-[9px] transition ${
                i === idx
                  ? "border-[color:var(--lime)]"
                  : "border-white/10 opacity-70 hover:opacity-100"
              }`}
              style={{ ["--lime" as any]: LIME, backgroundColor: "#15181B" }}
            >
              <div className="absolute left-1 top-1 rounded bg-black/40 px-1 text-white/70">
                {i + 1}
              </div>
              <div className="flex h-full items-center justify-center px-1 text-center text-[8.5px] leading-tight text-white/70">
                {tm(s.title)}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-2 border-t border-white/[0.07] px-4 py-3">
        <button
          onClick={() => setEditing((v) => !v)}
          className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11.5px] transition ${
            editing
              ? "border-[color:var(--lime)] text-[color:var(--lime)]"
              : "border-white/10 bg-white/[0.04] text-white/80 hover:bg-white/10"
          }`}
          style={{ ["--lime" as any]: LIME }}
        >
          <Pencil className="h-3.5 w-3.5" /> {editing ? tm("Done editing") : tm("Edit Content")}
        </button>

        <div className="inline-flex items-center gap-1 rounded-lg border border-white/10 bg-black/30 p-1">
          <LayoutTemplate className="ml-1 h-3.5 w-3.5 text-white/50" />
          {/* Chart is offered only where the answer actually carried figures. */}
          {LAYOUT_OPTIONS.filter((l) => l.id !== "chart" || slide.chart).map((l) => (
            <button
              key={l.id}
              onClick={() => switchLayout(l.id)}
              className={`rounded-md px-2 py-1 text-[11px] transition ${
                slide.layout === l.id
                  ? "bg-white/[0.10] text-white"
                  : "text-white/55 hover:text-white"
              }`}
            >
              {tm(l.label)}
            </button>
          ))}
        </div>

        <div className="ml-auto">
          <SaveToDriveButton
            variant="chip"
            text={deckMarkdown(msg)}
            title={msg.title}
            defaultName={saveName("presentation")}
            formats={["pptx", "pdf", "docx", "md"]}
          />
        </div>
      </div>
      <div className="px-4 pb-3">
        <LiveFooter meta={msg} />
      </div>
    </div>
  );
}

/** Deck -> markdown; the adapter turns '## ' headings into slides, so every
 *  layout has to put its content under its own heading. */
function deckMarkdown(msg: Extract<Message, { kind: "deck" }>): string {
  return [
    `# ${msg.title}`,
    ...msg.slides.map((s, i) => {
      const lines = [`## Slide ${i + 1}: ${s.title}`];
      if (s.subtitle) lines.push(s.subtitle);
      for (const b of s.bullets ?? []) lines.push(`- ${b}`);
      if (s.chart) {
        const cells = s.chart.points.map((p) => `${p.label} = ${p.display}`);
        if (s.chart.unit) cells.push(`unit: ${s.chart.unit}`);
        lines.push(`- Chart: ${cells.join(" | ")}`);
      }
      if (s.bigNumber) lines.push(`- ${s.bigNumber}${s.bigCaption ? ` — ${s.bigCaption}` : ""}`);
      if (s.left) lines.push(`- ${s.left.title}: ${s.left.body}`);
      if (s.right) lines.push(`- ${s.right.title}: ${s.right.body}`);
      for (const m of s.milestones ?? []) lines.push(`- ${m.phase} · ${m.label}: ${m.detail}`);
      for (const c of s.cta ?? []) lines.push(`- ${c}`);
      return lines.join("\n");
    }),
  ].join("\n\n");
}

function EditableText({
  value,
  onChange,
  editing,
  className,
  multiline,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  editing: boolean;
  className?: string;
  multiline?: boolean;
  placeholder?: string;
}) {
  if (!editing)
    return (
      <span className={className}>
        {value || <span className="text-white/30">{placeholder}</span>}
      </span>
    );
  if (multiline) {
    return (
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`${className ?? ""} w-full resize-none rounded-md border border-white/20 bg-black/40 px-1.5 py-1 outline-none focus:border-white/40`}
        rows={2}
      />
    );
  }
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`${className ?? ""} w-full rounded-md border border-white/20 bg-black/40 px-1.5 py-1 outline-none focus:border-white/40`}
    />
  );
}

/** Values clustered far from zero (99.79–99.87%) all render as full-height
 *  slabs on a zero axis, so there the axis starts at the data instead — and
 *  the slide says so, because a cropped axis exaggerates the differences. */
function chartScale(values: number[]) {
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const spread = hi - lo;
  if (lo > 0 && spread > 0 && spread < hi * 0.25) {
    return { floor: Math.max(0, lo - spread * 0.6), ceil: hi + spread * 0.2, zoomed: true };
  }
  return { floor: Math.min(0, lo), ceil: Math.max(0, hi), zoomed: false };
}

/** Bars for the figures the answer itself carried (adapter/llm.py "Chart:"
 *  line); the values are already number-checked against the sources. */
function BarChart({ chart }: { chart: SlideChart }) {
  const points = chart.points.slice(0, 8);
  const { floor, ceil, zoomed } = chartScale(points.map((p) => p.value));
  const span = ceil - floor || 1;
  /** Height in % of the plot area, measured up from the axis floor. */
  const pct = (v: number) => ((v - floor) / span) * 100;
  const basePct = zoomed ? 0 : pct(0);
  const peak = Math.max(...points.map((p) => Math.abs(p.value)));
  // Two or three figures would otherwise render as slabs half a slide wide.
  const inset = points.length <= 2 ? 26 : points.length === 3 ? 18 : 10;
  const axisNote = zoomed ? `axis starts at ${Number(floor.toFixed(2))}` : null;

  return (
    <>
      <div className="mt-3 flex flex-1 gap-2 pt-4 sm:gap-3">
        {points.map((p, i) => {
          const valuePct = pct(p.value);
          const bottomPct = Math.min(valuePct, basePct);
          const height = Math.max(Math.abs(valuePct - basePct), 1.5);
          const up = valuePct >= basePct;
          return (
            <div key={i} className="flex min-w-0 flex-1 flex-col">
              <div className="relative flex-1">
                <div
                  className="absolute inset-x-0 border-t border-dashed border-white/12"
                  style={{ top: `${100 - basePct}%` }}
                />
                <div
                  className="absolute"
                  style={{
                    left: `${inset}%`,
                    right: `${inset}%`,
                    top: `${100 - bottomPct - height}%`,
                    height: `${height}%`,
                  }}
                >
                  <div
                    className="h-full w-full"
                    style={{
                      backgroundColor: LIME,
                      opacity: Math.abs(p.value) === peak ? 1 : 0.5,
                      borderRadius: up ? "3px 3px 0 0" : "0 0 3px 3px",
                    }}
                  />
                  <div
                    className={`absolute inset-x-0 text-center text-[9.5px] font-semibold text-white/85 ${up ? "-top-4" : "-bottom-4"}`}
                  >
                    {p.display}
                  </div>
                </div>
              </div>
              <div className="mt-1.5 truncate text-center text-[9px] text-white/55" title={p.label}>
                {p.label}
              </div>
            </div>
          );
        })}
      </div>
      {axisNote && <div className="mt-1 text-right text-[8.5px] text-white/40">{axisNote}</div>}
    </>
  );
}

function SlideCanvas({
  slide,
  editing,
  onPatch,
}: {
  slide: Slide;
  editing: boolean;
  onPatch: (p: Partial<Slide>) => void;
}) {
  const { tm } = useLang();
  const base = "absolute inset-0 flex flex-col p-5 sm:p-7 text-white";

  if (slide.layout === "title") {
    return (
      <div
        className={`${base} justify-center`}
        style={{
          background: `radial-gradient(ellipse at 20% 10%, ${LIME}22, transparent 60%), #15181B`,
        }}
      >
        <div className="mb-2 text-[10px] uppercase tracking-[0.18em] text-white/45">
          ViVeSec · Board Briefing
        </div>
        <EditableText
          editing={editing}
          value={tm(slide.title)}
          onChange={(v) => onPatch({ title: v })}
          className="text-2xl font-semibold tracking-tight sm:text-3xl"
        />
        <div className="mt-2 max-w-md text-[12px] text-white/65">
          <EditableText
            editing={editing}
            value={tm(slide.subtitle ?? "")}
            onChange={(v) => onPatch({ subtitle: v })}
          />
        </div>
        <div className="mt-6 h-1 w-16 rounded-full" style={{ backgroundColor: LIME }} />
      </div>
    );
  }

  if (slide.layout === "bullets") {
    return (
      <div className={base}>
        <EditableText
          editing={editing}
          value={tm(slide.title)}
          onChange={(v) => onPatch({ title: v })}
          className="text-base font-semibold tracking-tight sm:text-lg"
        />
        {(editing || slide.subtitle) && (
          <div className="mb-3 text-[11px] text-white/55">
            <EditableText
              editing={editing}
              value={tm(slide.subtitle ?? "")}
              onChange={(v) => onPatch({ subtitle: v })}
            />
          </div>
        )}
        {!slide.subtitle && !editing && <div className="mb-3" />}
        <ul className="space-y-2">
          {(slide.bullets ?? []).map((b, i) => (
            <li
              key={i}
              className="flex items-start gap-2 text-[11px] leading-relaxed text-white/85 sm:text-[12.5px]"
            >
              <span
                className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full"
                style={{ backgroundColor: LIME }}
              />
              <EditableText
                editing={editing}
                value={tm(b)}
                onChange={(v) =>
                  onPatch({ bullets: (slide.bullets ?? []).map((x, idx) => (idx === i ? v : x)) })
                }
              />
            </li>
          ))}
        </ul>
      </div>
    );
  }

  if (slide.layout === "chart" && slide.chart?.points.length) {
    return (
      <div className={base}>
        <EditableText
          editing={editing}
          value={tm(slide.title)}
          onChange={(v) => onPatch({ title: v })}
          className="text-base font-semibold tracking-tight sm:text-lg"
        />
        {slide.chart.unit && (
          <div className="mt-0.5 text-[10px] uppercase tracking-[0.16em] text-white/45">
            {slide.chart.unit}
          </div>
        )}
        <BarChart chart={slide.chart} />
        <ul className="mt-2 space-y-1">
          {(slide.bullets ?? []).slice(0, 2).map((b, i) => (
            <li
              key={i}
              className="flex items-start gap-2 text-[10.5px] leading-snug text-white/75 sm:text-[11.5px]"
            >
              <span
                className="mt-1.5 h-1 w-1 shrink-0 rounded-full"
                style={{ backgroundColor: LIME }}
              />
              <EditableText
                editing={editing}
                value={tm(b)}
                onChange={(v) =>
                  onPatch({ bullets: (slide.bullets ?? []).map((x, idx) => (idx === i ? v : x)) })
                }
              />
            </li>
          ))}
        </ul>
      </div>
    );
  }

  if (slide.layout === "big-number") {
    return (
      <div
        className={`${base} justify-center`}
        style={{ background: `linear-gradient(135deg, ${LIME}18, transparent 55%), #15181B` }}
      >
        <EditableText
          editing={editing}
          value={tm(slide.title)}
          onChange={(v) => onPatch({ title: v })}
          className="mb-2 text-[11px] uppercase tracking-[0.18em] text-white/55"
        />
        <div
          className="text-5xl font-bold leading-none tracking-tight sm:text-6xl"
          style={{ color: LIME }}
        >
          <EditableText
            editing={editing}
            value={slide.bigNumber ?? ""}
            onChange={(v) => onPatch({ bigNumber: v })}
          />
        </div>
        <div className="mt-3 max-w-md text-[11.5px] leading-relaxed text-white/75 sm:text-[12.5px]">
          <EditableText
            editing={editing}
            multiline
            value={tm(slide.bigCaption ?? "")}
            onChange={(v) => onPatch({ bigCaption: v })}
          />
        </div>
      </div>
    );
  }

  if (slide.layout === "two-column") {
    return (
      <div className={base}>
        <EditableText
          editing={editing}
          value={tm(slide.title)}
          onChange={(v) => onPatch({ title: v })}
          className="mb-3 text-base font-semibold tracking-tight sm:text-lg"
        />
        <div className="grid flex-1 grid-cols-2 gap-3">
          {(["left", "right"] as const).map((side) => {
            const col = slide[side] ?? { title: "", body: "" };
            return (
              <div key={side} className="rounded-lg border border-white/10 bg-white/[0.03] p-3">
                <EditableText
                  editing={editing}
                  value={tm(col.title)}
                  onChange={(v) => onPatch({ [side]: { ...col, title: v } } as any)}
                  className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider"
                />
                <div className="text-[11px] leading-relaxed text-white/75 sm:text-[12px]">
                  <EditableText
                    editing={editing}
                    multiline
                    value={tm(col.body)}
                    onChange={(v) => onPatch({ [side]: { ...col, body: v } } as any)}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  if (slide.layout === "timeline") {
    return (
      <div className={base}>
        <EditableText
          editing={editing}
          value={tm(slide.title)}
          onChange={(v) => onPatch({ title: v })}
          className="mb-3 text-base font-semibold tracking-tight sm:text-lg"
        />
        <div className="space-y-2">
          {(slide.milestones ?? []).map((m, i) => (
            <div key={i} className="flex items-start gap-3">
              <div
                className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full text-[10px] font-bold"
                style={{ backgroundColor: `${LIME}22`, color: LIME }}
              >
                {i + 1}
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[12px] font-semibold text-white">
                  {tm(m.phase)} <span className="text-white/55">· {tm(m.label)}</span>
                </div>
                <div className="text-[11px] leading-relaxed text-white/70">{tm(m.detail)}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // cta
  return (
    <div
      className={base}
      style={{ background: `linear-gradient(180deg, transparent, ${LIME}10), #15181B` }}
    >
      <EditableText
        editing={editing}
        value={tm(slide.title)}
        onChange={(v) => onPatch({ title: v })}
        className="mb-3 text-base font-semibold tracking-tight sm:text-lg"
      />
      <ol className="space-y-2">
        {(slide.cta ?? []).map((c, i) => (
          <li
            key={i}
            className="flex items-start gap-2 text-[11.5px] leading-relaxed text-white/85 sm:text-[12.5px]"
          >
            <span
              className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-md text-[10px] font-bold"
              style={{ backgroundColor: LIME, color: "#15181B" }}
            >
              {i + 1}
            </span>
            <EditableText
              editing={editing}
              value={tm(c)}
              onChange={(v) =>
                onPatch({ cta: (slide.cta ?? []).map((x, idx) => (idx === i ? v : x)) })
              }
            />
          </li>
        ))}
      </ol>
      <div className="mt-auto pt-3 text-[10px] uppercase tracking-[0.18em] text-white/45">
        Prepared by ViVeSec AI · Confidential
      </div>
    </div>
  );
}

/* ---------- Drive ---------- */

/** Generated documents that are still on the AI Box: either the ViVeSecBox was
 *  not connected or it refused the write, so the drive never got them. */
function GeneratedFilesSection({ drive }: { drive: string }) {
  const { tm } = useLang();
  const [files, setFiles] = useState<GeneratedFile[]>([]);

  const load = useCallback(async () => {
    const res = await listGeneratedFiles({ data: { drive: drive || undefined } });
    setFiles(res.ok ? res.files : []);
  }, [drive]);

  useEffect(() => {
    void load();
    const listener = () => void load();
    generatedFilesListeners.add(listener);
    return () => {
      generatedFilesListeners.delete(listener);
    };
  }, [load]);

  if (!files.length) return null;

  return (
    <div className="mb-2 rounded-xl border border-amber-300/20 bg-amber-300/[0.04] p-2">
      <div className="mb-1 flex items-center gap-1.5 px-1 text-[10px] uppercase tracking-wider text-amber-200/70">
        <AlertTriangle className="h-3 w-3" />
        {tm("Generated, kept on the AI Box")}
      </div>
      {files.map((f) => (
        <button
          key={f.name}
          onClick={() => void downloadStoredFile(f.name, drive)}
          className="flex w-full items-center gap-2 rounded-lg px-1.5 py-1.5 text-left transition hover:bg-white/[0.05]"
          title={tm("Download to this device")}
        >
          <FileText className="h-3.5 w-3.5 shrink-0 text-white/60" />
          <span className="truncate text-[12.5px]">{f.name}</span>
          <span className="ml-auto shrink-0 text-[11px] text-white/40">{formatBytes(f.size)}</span>
        </button>
      ))}
    </div>
  );
}

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function DrivePanel(props: {
  open: boolean;
  onClose: () => void;
  files: DriveFile[];
  search: string;
  setSearch: (s: string) => void;
  view: "grid" | "list";
  setView: (v: "grid" | "list") => void;
  highlightedId: string | null;
  onSelectFile: (f: DriveFile) => void;
  onInsertFile: (f: DriveFile) => void;
  onAnalyze: (f: DriveFile) => void;
  pickerOpen: boolean;
  onPickerOpen: () => void;
  onOpenExplorer: () => void;
  getFile: (id: string) => DriveFile | undefined;
  onSnippetSelect?: (c: Citation) => void;
  searchSnippets?: Citation[];
  drives: DriveInfo[];
  drive: string;
  driveName: string;
  drivePicker: boolean;
  onSelectDrive: (path: string) => void;
  folders: { path: string; name: string }[];
  cwd: string;
  onOpenFolder: (path: string) => void;
  searching: boolean;
  searchTruncated: boolean;
  loading: boolean;
  error: string;
  onRefresh: () => void;
  scopeDrives: ScopeDrive[];
  selectedDrives: string[];
  onToggleScopeDrive: (path: string) => void;
  onClearScope: () => void;
}) {
  const {
    open,
    onClose,
    files,
    search,
    setSearch,
    view,
    setView,
    highlightedId,
    onSelectFile,
    onInsertFile,
    onAnalyze,
    onSnippetSelect,
    searchSnippets = [],
  } = props;
  const { folders, cwd, onOpenFolder, searching, searchTruncated, drive } = props;

  // Breadcrumb from the drive root down to the folder on screen.
  const driveRoot = drive.replace(/\/+$/, "");
  const crumbs = cwd.startsWith(driveRoot)
    ? cwd.slice(driveRoot.length).split("/").filter(Boolean)
    : [];
  const parent = crumbs.length ? cwd.slice(0, cwd.lastIndexOf("/")) : "";
  const { drives, driveName, drivePicker, onSelectDrive, loading, error, onRefresh } = props;
  const { t, tm } = useLang();
  const [actionFor, setActionFor] = useState<string | null>(null);
  const [autoOpen, setAutoOpen] = useState(false);
  const [driveMenu, setDriveMenu] = useState(false);

  return (
    <>
      {/* Mobile backdrop */}
      <div
        onClick={onClose}
        className={`fixed inset-0 z-40 bg-black/50 backdrop-blur-sm transition-opacity lg:hidden ${open ? "opacity-100" : "pointer-events-none opacity-0"}`}
      />
      <aside
        data-tour="drive"
        className={`fixed inset-y-0 right-0 z-50 flex min-h-0 w-[88vw] max-w-[400px] flex-col border-l border-white/5 transition-transform duration-500 ease-out lg:static lg:order-1 lg:max-w-none lg:translate-x-0 lg:border-l-0 ${
          open ? "translate-x-0" : "translate-x-full lg:hidden"
        } ${open ? "lg:flex lg:min-w-0 lg:flex-1 lg:basis-0" : ""}`}
        style={{ backgroundColor: "#1B1F22" }}
      >
        {/* Header — matches screenshot */}
        <div className="shrink-0 border-b border-white/5">
          <div
            className="flex items-center gap-3 px-4 py-3 lg:hidden"
            style={{ backgroundColor: "#15181B" }}
          >
            <button
              onClick={onClose}
              className="grid h-9 w-9 place-items-center rounded-full bg-white/5"
            >
              <ArrowLeft className="h-4 w-4" />
            </button>
            <div className="text-sm font-semibold">{t.headerTitle}</div>
          </div>

          <div className="flex items-center justify-between px-3 py-2">
            {/* Demo/test only: the production build gets its drive from the box
                session, so the picker is not advertised there. */}
            {drivePicker ? (
              <div className="relative ml-2 min-w-0">
                <button
                  onClick={() => setDriveMenu((v) => !v)}
                  className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-[12px] text-white/85 hover:bg-white/10"
                  title={tm("Demo only: switch drive")}
                >
                  <Laptop className="h-3.5 w-3.5 shrink-0" style={{ color: LIME }} />
                  <span className="truncate">{driveName || t.driveRoot}</span>
                  <ChevronDown className="h-3 w-3 shrink-0 text-white/50" />
                </button>
                {driveMenu && (
                  <div
                    className="absolute left-0 top-full z-50 mt-1 w-56 overflow-hidden rounded-lg border border-white/10 shadow-xl"
                    style={{ backgroundColor: "#23272B" }}
                  >
                    <div className="border-b border-white/5 px-3 py-1.5 text-[10px] uppercase tracking-wider text-white/40">
                      {tm("Demo only: switch drive")}
                    </div>
                    {props.drives.map((d) => (
                      <button
                        key={d.path}
                        onClick={() => {
                          setDriveMenu(false);
                          onSelectDrive(d.path);
                        }}
                        className={`flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] transition hover:bg-white/10 ${d.path === props.drive ? "text-white" : "text-white/70"}`}
                      >
                        <Laptop
                          className="h-3.5 w-3.5"
                          style={{ color: d.path === props.drive ? LIME : "rgba(255,255,255,0.4)" }}
                        />
                        <span className="flex-1 truncate">{d.name}</span>
                        {d.path === props.drive && (
                          <Check className="h-3.5 w-3.5" style={{ color: LIME }} />
                        )}
                      </button>
                    ))}
                    {!props.drives.length && (
                      <div className="px-3 py-2 text-[12px] text-white/50">
                        {tm("No drives found on the box.")}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <>
                <div className="ml-2 hidden sm:block text-[12px] uppercase tracking-wider text-white/45">
                  {t.driveRootFull}
                </div>
                <div className="ml-2 sm:hidden text-[11px] uppercase tracking-wider text-white/45">
                  {t.driveRoot}
                </div>
              </>
            )}
            <div className="ml-auto flex items-center gap-1">
              <button
                onClick={props.onOpenExplorer}
                className="grid h-8 w-8 place-items-center rounded-md hover:bg-white/10"
                title={tm("Open File Explorer")}
                aria-label={tm("Open File Explorer")}
              >
                <Home className="h-4 w-4" style={{ color: LIME }} />
              </button>
              <button
                onClick={() => {
                  onRefresh();
                  notifyGeneratedFilesChanged();
                }}
                disabled={loading}
                className="grid h-8 w-8 place-items-center rounded-md hover:bg-white/10 disabled:opacity-40"
                title={t.refresh}
              >
                <RefreshCw className={`h-4 w-4 text-white/70 ${loading ? "animate-spin" : ""}`} />
              </button>
              <button
                onClick={() => setView(view === "list" ? "grid" : "list")}
                className="grid h-8 w-8 place-items-center rounded-md hover:bg-white/10"
                title="Toggle view"
              >
                <List className="h-4 w-4 text-white/70" />
              </button>
              <button
                onClick={onClose}
                className="ml-1 hidden lg:grid h-8 w-8 place-items-center rounded-md hover:bg-white/10"
              >
                <X className="h-4 w-4 text-white/70" />
              </button>
            </div>
          </div>

          <div className="flex items-center gap-2 px-3 pb-3">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-white/40" />
              <input
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setAutoOpen(true);
                }}
                onFocus={() => setAutoOpen(true)}
                onBlur={() => setTimeout(() => setAutoOpen(false), 180)}
                placeholder={t.searchInDriveRoot}
                className="w-full rounded-md border border-white/10 bg-[#23272B] py-2 pl-7 pr-2 text-[12.5px] text-white placeholder:text-white/40 outline-none focus:border-white/20"
              />
              {autoOpen &&
                search.trim().length >= 2 &&
                (files.length > 0 || searchSnippets.length > 0) && (
                  <div className="vvs-scroll absolute left-0 right-0 top-full z-30 mt-1 max-h-72 overflow-y-auto rounded-md border border-white/10 bg-[#1E2225] shadow-2xl shadow-black/60">
                    {files.length > 0 && (
                      <>
                        <div className="px-2.5 pt-2 pb-1 text-[10px] uppercase tracking-wider text-white/40">
                          {tm("Files")}
                        </div>
                        {files.slice(0, 4).map((f) => {
                          const Icon = fileIcon(f.type);
                          return (
                            <button
                              key={f.id}
                              onMouseDown={() => {
                                onSelectFile(f);
                                setAutoOpen(false);
                              }}
                              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12.5px] hover:bg-white/5"
                            >
                              <Icon className="h-3.5 w-3.5 text-white/60" />
                              <span className="truncate">{f.name}</span>
                            </button>
                          );
                        })}
                      </>
                    )}
                    {searchSnippets.length > 0 && (
                      <>
                        <div className="border-t border-white/5 px-2.5 pt-2 pb-1 text-[10px] uppercase tracking-wider text-white/40">
                          {tm("In-document matches")}
                        </div>
                        {searchSnippets.map((c, i) => (
                          <button
                            key={i}
                            onMouseDown={() => {
                              onSnippetSelect?.(c);
                              setAutoOpen(false);
                            }}
                            className="flex w-full flex-col gap-0.5 px-2.5 py-2 text-left hover:bg-white/5"
                          >
                            <div className="flex items-center gap-1.5 text-[11px] text-white/55">
                              <FileText className="h-3 w-3 text-rose-400" /> {c.label}
                              <span className="ml-auto rounded-full border border-white/10 bg-black/30 px-1.5 text-[10px] text-white/70">
                                {c.score}%
                              </span>
                            </div>
                            <div className="line-clamp-2 text-[11.5px] text-white/80">
                              <HighlightedText text={c.snippet ?? ""} terms={c.terms ?? []} />
                            </div>
                          </button>
                        ))}
                      </>
                    )}
                  </div>
                )}
            </div>
          </div>
          <div className="flex items-center justify-center gap-1.5 px-3 pb-2 text-[10px] text-white/45">
            <ShieldCheck className="h-3 w-3" style={{ color: LIME }} />
            {tm("Searched 100% locally on ViVeSecBox")}
          </div>

          {/* Search scope. Only shown when the box granted more than one
              drive, so the single-drive deployment looks exactly as before. */}
          {props.scopeDrives.length > 1 && (
            <div className="border-t border-white/5 px-3 py-2">
              <div className="mb-1.5 text-[10px] uppercase tracking-wider text-white/40">
                {tm("Search in")}
              </div>
              <div className="flex flex-wrap gap-1.5">
                <button
                  onClick={props.onClearScope}
                  className={`rounded-full border px-2 py-1 text-[11px] transition ${
                    props.selectedDrives.length === 0
                      ? "border-transparent text-black"
                      : "border-white/10 text-white/70 hover:bg-white/10"
                  }`}
                  style={
                    props.selectedDrives.length === 0 ? { backgroundColor: LIME } : undefined
                  }
                >
                  {tm("All drives")}
                </button>
                {props.scopeDrives.map((d) => {
                  const on = props.selectedDrives.includes(d.path);
                  return (
                    <button
                      key={d.path}
                      onClick={() => props.onToggleScopeDrive(d.path)}
                      title={d.path}
                      className={`inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[11px] transition ${
                        on
                          ? "border-transparent text-black"
                          : "border-white/10 text-white/70 hover:bg-white/10"
                      }`}
                      style={on ? { backgroundColor: LIME } : undefined}
                    >
                      {d.active && (
                        <CircleDot
                          className="h-2.5 w-2.5"
                          style={{ color: on ? "#15181B" : LIME }}
                        />
                      )}
                      {d.name}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Breadcrumb — hidden while searching, which spans the whole drive */}
        {!searching && (
          <div className="flex items-center gap-1 border-b border-white/5 px-3 py-1.5 text-[11.5px] text-white/55">
            <button
              onClick={() => onOpenFolder(driveRoot)}
              className={`rounded px-1.5 py-0.5 transition hover:bg-white/10 ${crumbs.length ? "text-white/70" : "text-white"}`}
            >
              {props.driveName || t.driveRoot}
            </button>
            {crumbs.map((seg, i) => (
              <span key={seg + i} className="flex min-w-0 items-center gap-1">
                <ChevronRight className="h-3 w-3 shrink-0 text-white/30" />
                <button
                  onClick={() => onOpenFolder(`${driveRoot}/${crumbs.slice(0, i + 1).join("/")}`)}
                  className={`truncate rounded px-1.5 py-0.5 transition hover:bg-white/10 ${i === crumbs.length - 1 ? "text-white" : "text-white/70"}`}
                >
                  {seg}
                </button>
              </span>
            ))}
          </div>
        )}

        {searching && (
          <div className="border-b border-white/5 px-3 py-1.5 text-[11.5px] text-white/55">
            {files.length === 1
              ? tm("1 matching file in this drive")
              : `${files.length} ${tm("matching files in this drive")}`}
            {searchTruncated && (
              <span className="ml-1 text-amber-300/80">({tm("first results only")})</span>
            )}
          </div>
        )}

        {/* File list */}
        <div className="vvs-scroll min-h-0 flex-1 overflow-y-auto p-2">
          {!searching && <GeneratedFilesSection drive={drive} />}

          {!searching && parent && !loading && (
            <button
              onClick={() => onOpenFolder(parent)}
              className="mb-1 flex w-full items-center gap-3 rounded-xl px-2.5 py-2 text-left transition hover:bg-white/[0.04]"
            >
              <div className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-white/5">
                <CornerLeftUp className="h-4 w-4 text-white/60" />
              </div>
              <div className="text-[13px] text-white/70">..</div>
            </button>
          )}

          {!searching &&
            folders.map((d) => (
              <button
                key={d.path}
                onClick={() => onOpenFolder(d.path)}
                className="mb-1 flex w-full items-center gap-3 rounded-xl px-2.5 py-2 text-left transition hover:bg-white/[0.04]"
              >
                <div className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-sky-400/10">
                  <Folder className="h-4 w-4 text-sky-300" />
                </div>
                <div className="min-w-0 flex-1 truncate text-[13px] font-medium">{d.name}</div>
                <ChevronRight className="h-4 w-4 shrink-0 text-white/30" />
              </button>
            ))}

          {loading && files.length === 0 && (
            <div className="grid h-full place-items-center text-center text-xs text-white/40">
              <div>
                <RefreshCw className="mx-auto mb-2 h-6 w-6 animate-spin" />
                {tm("Loading files from the box…")}
              </div>
            </div>
          )}

          {!loading && error && (
            <div className="grid h-full place-items-center px-4 text-center text-xs text-rose-200/80">
              <div>
                <AlertTriangle className="mx-auto mb-2 h-6 w-6" />
                {tm("Could not read the drive from the box.")}
                <div className="mt-1 text-[11px] text-white/40">{error}</div>
                <button
                  onClick={onRefresh}
                  className="mt-3 rounded-lg border border-white/15 px-2.5 py-1 text-[11px] font-semibold text-white/80 hover:bg-white/10"
                >
                  {tm("Retry")}
                </button>
              </div>
            </div>
          )}

          {!loading && !error && files.length === 0 && folders.length === 0 && (
            <div className="grid h-full place-items-center text-center text-xs text-white/40">
              <div>
                <Database className="mx-auto mb-2 h-6 w-6" />
                {searching ? tm("No files match your search.") : tm("This folder is empty.")}
              </div>
            </div>
          )}

          {view === "list" ? (
            <ul className="space-y-1">
              {files.map((f) => {
                const Icon = fileIcon(f.type);
                const hi = highlightedId === f.id;
                return (
                  <li key={f.id}>
                    <div
                      className={`group rounded-xl border transition ${hi ? "border-[color:var(--lime)] bg-[color:var(--lime)]/10" : "border-transparent hover:bg-white/[0.04]"}`}
                      style={{ ["--lime" as any]: LIME }}
                    >
                      <div className="flex w-full items-center gap-3 px-2.5 py-2.5">
                        <button
                          onClick={() => onSelectFile(f)}
                          className="flex min-w-0 flex-1 items-center gap-3 text-left"
                        >
                          <div
                            className={`grid h-9 w-9 shrink-0 place-items-center rounded-md ${fileTint(f.type)}`}
                          >
                            <Icon className="h-4 w-4" />
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-[13px] font-medium">{f.name}</div>
                            <div className="truncate text-[11px] text-white/45">
                              {f.folder ? `${f.folder} · ` : ""}
                              {f.size} · {f.modified}
                            </div>
                          </div>
                        </button>
                        <button
                          onClick={() => setActionFor(actionFor === f.id ? null : f.id)}
                          aria-label="File actions"
                          className="grid h-9 w-9 shrink-0 place-items-center rounded-md transition hover:bg-white/10 lg:h-7 lg:w-7 lg:opacity-0 lg:group-hover:opacity-100"
                        >
                          <MoreVertical className="h-4 w-4 text-white/60 lg:h-3.5 lg:w-3.5" />
                        </button>
                      </div>
                      {actionFor === f.id && (
                        <div className="border-t border-white/5 px-2.5 py-2">
                          <div className="flex flex-wrap gap-2">
                            <button
                              onClick={() => {
                                onAnalyze(f);
                                setActionFor(null);
                              }}
                              className="inline-flex flex-1 sm:flex-none items-center justify-center gap-1.5 rounded-md px-3 py-2 text-[12px] font-medium"
                              style={{ backgroundColor: LIME, color: "#15181B" }}
                            >
                              <Zap className="h-3.5 w-3.5" /> {tm("Analyze with AI")}
                            </button>
                            <button
                              onClick={() => {
                                onInsertFile(f);
                                setActionFor(null);
                              }}
                              className="inline-flex flex-1 sm:flex-none items-center justify-center gap-1.5 rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-[12px] hover:bg-white/10"
                            >
                              <Send className="h-3.5 w-3.5" /> {tm("Insert into chat")}
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="grid grid-cols-2 gap-2 p-1">
              {files.map((f) => {
                const Icon = fileIcon(f.type);
                const hi = highlightedId === f.id;
                return (
                  <button
                    key={f.id}
                    onClick={() => onSelectFile(f)}
                    className={`rounded-xl border p-3 text-left transition ${hi ? "border-[color:var(--lime)] bg-[color:var(--lime)]/10" : "border-white/10 bg-white/[0.03] hover:bg-white/[0.06]"}`}
                    style={{ ["--lime" as any]: LIME }}
                  >
                    <div
                      className={`mb-2 grid h-9 w-9 place-items-center rounded-md ${fileTint(f.type)}`}
                    >
                      <Icon className="h-4 w-4" />
                    </div>
                    <div className="truncate text-[12.5px] font-medium">{f.name}</div>
                    <div className="mt-0.5 text-[10.5px] text-white/45">{f.size}</div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
