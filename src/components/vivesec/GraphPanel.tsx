import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  CalendarDays,
  ChevronRight,
  Download,
  ExternalLink,
  Eye,
  FileText,
  GitMerge,
  Loader2,
  Network,
  RefreshCw,
  Route,
  ScrollText,
  Search,
  Settings2,
  ShieldAlert,
  Split,
  Undo2,
  Upload,
  X,
} from "lucide-react";
import { useLang } from "./i18n";
import { GraphCanvas, type Emphasis, type ExportRequest } from "./GraphCanvas";
import { mergeGraphs, withDetails } from "./graph-model";
import { pairKey, TYPE_COLOR, TYPE_NAME, type RelationInfo } from "./graph-style";
import { saveToDrive } from "@/lib/api/rag.functions";
import { AdapterGraphError } from "@/lib/rag/adapter-client";
import {
  graphApi,
  graphFailure,
  type GraphDocuments,
  type GraphEdge,
  type GraphEvidence,
  type GraphFailure,
  type GraphImportAnswer,
  type GraphImportRag,
  type GraphImportSource,
  type GraphLabel,
  type GraphNode,
  type GraphProfile,
  type GraphReviewDecision,
  type GraphReviewField,
  type GraphReviewItem,
  type GraphReviewList,
  type GraphStatus,
  type GraphTimelineItem,
  type GraphWatermark,
  type GraphReprocessRun,
  type GraphReprocessStatus,
  type GraphInvestigationView,
  type IndexSkipped,
  type QueryLog,
  type QueryLogRecord,
} from "@/lib/api/graph.functions";
import { ACCENT, ACCENT_TEXT, ON_ACCENT } from "./brand";
import { FolderPicker } from "./FolderPicker";
import { ProfileSuggestions } from "./ProfileSuggestions";

const PANEL = "var(--surface-3)";
const BG = "var(--surface-2)";

const FAILURE_TEXT: Record<GraphFailure, string> = {
  disabled: "Relationship analysis is not enabled on this AI Box.",
  extraction_off: "Document processing for relationships is not enabled on this AI Box.",
  forbidden: "Only graph administrators may do this.",
  syncing: "Access rules are being synchronised — try again shortly.",
  purpose: "Give the case reference or the legal basis first (chat header).",
  error: "The relationship service did not answer.",
};
const STATUS_TEXT: Record<string, string> = {
  done: "Done",
  partial: "Partial",
  failed: "Failed",
  cancelled: "Cancelled",
  ocr_unavailable: "Scanned, no OCR",
  unsupported: "Unsupported",
  empty: "Empty",
};

type Tab = "explore" | "review" | "processing" | "profile" | "querylog";
/** Nodes the picture may hold before an added neighbourhood replaces it. */
const MAX_PICTURE = 5000;
type Graph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  documents: GraphDocuments;
  truncated?: boolean;
  compact?: boolean;
};

const byDate = (x: GraphEdge, y: GraphEdge) => (x.start ?? "").localeCompare(y.start ?? "");

export type GraphSource = { path: string; page: number; quote: string; label: string };

export function GraphPanel({
  drive,
  initial,
  watchlist = false,
  onClose,
  onOpenSource,
}: {
  drive: string;
  /** The watchlist pilot is on: entities can be watched. */
  watchlist?: boolean;
  /** An investigation subgraph the Explore tab shows instead of the search. */
  initial?: GraphInvestigationView | null;
  onClose: () => void;
  onOpenSource: (source: GraphSource) => void;
}) {
  const { tm, lang } = useLang();
  const code = lang.toLowerCase() as keyof GraphLabel;
  const [tab, setTab] = useState<Tab>("explore");
  /** People the Explore tab opens with (a review pair to look at). */
  const [exploreSeed, setExploreSeed] = useState<string[] | null>(null);
  const [profile, setProfile] = useState<GraphProfile | null>(null);
  const [canEdit, setCanEdit] = useState(false);
  const [failure, setFailure] = useState<GraphFailure | null>(null);

  const loadProfile = useCallback(async () => {
    try {
      const res = await graphApi.profile(drive);
      setProfile(res.profile);
      setCanEdit(res.can_edit);
      setFailure(null);
    } catch (err) {
      setFailure(graphFailure(err));
    }
  }, [drive]);

  useEffect(() => {
    void loadProfile();
  }, [loadProfile]);

  const relLabel = useCallback(
    (type: string) => {
      const label = profile?.relation_types.find((r) => r.id === type)?.label;
      return label?.[code] ?? label?.en ?? type;
    },
    [profile, code],
  );
  const subtypeLabel = useCallback(
    (sub: string) => {
      const label = profile?.entity_subtypes.find((s) => s.id === sub)?.label;
      return label ? (label[code] ?? label.en ?? sub) : "";
    },
    [profile, code],
  );
  const relInfo = useCallback(
    (type: string): RelationInfo => {
      const rel = profile?.relation_types.find((r) => r.id === type);
      return {
        directed: Boolean(rel && !rel.symmetric),
        ongoing: Boolean(rel?.presence && rel.to.includes("place")),
      };
    },
    [profile],
  );

  const tabs: { id: Tab; label: string; icon: typeof Network }[] = [
    { id: "explore", label: tm("Explore"), icon: Network },
    { id: "review", label: tm("Review"), icon: GitMerge },
    { id: "processing", label: tm("Processing"), icon: RefreshCw },
    { id: "profile", label: tm("Domain profile"), icon: Settings2 },
    { id: "querylog", label: tm("Query log"), icon: ScrollText },
  ];

  return (
    <div
      className="absolute inset-0 z-[60] flex flex-col text-foreground"
      style={{ backgroundColor: BG }}
    >
      <div className="flex shrink-0 items-center gap-2 border-b border-ink/5 px-4 py-2">
        <Network className="h-4 w-4" style={{ color: ACCENT_TEXT }} />
        <div className="text-sm font-semibold">{tm("Relationships")}</div>
        <div className="ml-4 flex gap-1">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs transition ${
                tab === id ? "text-white" : "text-ink/70 hover:bg-ink/10"
              }`}
              style={tab === id ? { backgroundColor: ACCENT } : undefined}
            >
              <Icon className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">{label}</span>
            </button>
          ))}
        </div>
        <button
          onClick={onClose}
          className="ml-auto rounded-full p-1.5 text-ink/60 hover:bg-ink/10"
          title={tm("Close")}
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      {failure === "disabled" ? (
        <Notice text={tm(FAILURE_TEXT.disabled)} />
      ) : (
        <div className="min-h-0 flex-1">
          {tab === "explore" && (
            <ExploreTab
              drive={drive}
              initial={initial ?? null}
              seed={exploreSeed}
              onSeedUsed={() => setExploreSeed(null)}
              relLabel={relLabel}
              relInfo={relInfo}
              subtypeLabel={subtypeLabel}
              watchlist={watchlist}
              onOpenSource={onOpenSource}
            />
          )}
          {tab === "review" && (
            <ReviewTab
              drive={drive}
              canEdit={canEdit}
              relLabel={relLabel}
              subtypeLabel={subtypeLabel}
              onOpenSource={onOpenSource}
              onOpenPair={(a, b) => {
                setExploreSeed([a, b]);
                setTab("explore");
              }}
            />
          )}
          {tab === "processing" && (
            <ProcessingTab drive={drive} canEdit={canEdit} onOpenSource={onOpenSource} />
          )}
          {tab === "profile" && (
            <ProfileTab
              drive={drive}
              profile={profile}
              canEdit={canEdit}
              code={code}
              onSaved={loadProfile}
            />
          )}
          {tab === "querylog" && <QueryLogTab drive={drive} />}
        </div>
      )}
    </div>
  );
}

function Notice({ text, tone = "muted" }: { text: string; tone?: "muted" | "warn" }) {
  return (
    <div
      className={`m-4 flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${
        tone === "warn"
          ? "border-rose-400/40 bg-rose-400/10 text-rose-800 dark:text-rose-100"
          : "border-ink/10 bg-ink/5 text-ink/60"
      }`}
    >
      {tone === "warn" && <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
      <span>{text}</span>
    </div>
  );
}

function useFailure() {
  const { tm } = useLang();
  const [failure, setFailure] = useState<GraphFailure | null>(null);
  const run = useCallback(async <T,>(work: () => Promise<T>): Promise<T | null> => {
    try {
      const out = await work();
      setFailure(null);
      return out;
    } catch (err) {
      setFailure(graphFailure(err));
      return null;
    }
  }, []);
  const notice = failure ? <Notice text={tm(FAILURE_TEXT[failure])} tone="warn" /> : null;
  return { run, notice };
}

/* ---------------------------------------------------------------- Explore */

function ExploreTab({
  drive,
  initial,
  seed,
  onSeedUsed,
  relLabel,
  relInfo,
  subtypeLabel,
  watchlist,
  onOpenSource,
}: {
  drive: string;
  initial: GraphInvestigationView | null;
  seed: string[] | null;
  onSeedUsed: () => void;
  relLabel: (type: string) => string;
  relInfo: (type: string) => RelationInfo;
  subtypeLabel: (sub: string) => string;
  watchlist: boolean;
  onOpenSource: (source: GraphSource) => void;
}) {
  const { tm } = useLang();
  const { run, notice } = useFailure();
  const startId = initial?.rows.find((r) => r.level === 0)?.id ?? null;
  const [text, setText] = useState("");
  const [results, setResults] = useState<GraphNode[] | null>(null);
  const [focus, setFocus] = useState<string | null>(startId);
  const [hops, setHops] = useState(1);
  const [graph, setGraph] = useState<Graph | null>(initial);
  // The investigation is on screen: a click focuses a node instead of
  // replacing the subgraph with its neighbourhood.
  const [investigating, setInvestigating] = useState(!!initial);
  const [pathMode, setPathMode] = useState(false);
  const [pathNote, setPathNote] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Earlier pictures, for "Back" (an added neighbourhood, a path, a new start).
  const [history, setHistory] = useState<{ graph: Graph; focus: string | null }[]>([]);
  // Two nodes picked with Shift+click, and the path or common relations found.
  const [picked, setPicked] = useState<string[]>([]);
  const [emphasis, setEmphasis] = useState<Emphasis | null>(null);

  // A review pair opened here: both people with their relations, picked, so
  // "path" and "common relations" work on them right away.
  useEffect(() => {
    if (!seed?.length) return;
    const ids = seed;
    onSeedUsed();
    void (async () => {
      setBusy(true);
      const parts = await run(() =>
        Promise.all(ids.map((id) => graphApi.neighborhood(drive, id, 1))),
      );
      setBusy(false);
      if (!parts?.length) return;
      remember();
      setGraph(parts.slice(1).reduce((all, part) => mergeGraphs(all, part), parts[0]));
      setFocus(ids[0]);
      setPicked(ids.slice(0, 2));
      setSelectedEdge(null);
      setEmphasis(null);
      setPathNote(null);
      setInvestigating(false);
    })();
    // Runs once per seed; remember() reads the picture on screen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed]);

  function remember() {
    if (graph) setHistory((h) => [...h.slice(-19), { graph, focus }]);
  }

  function back() {
    const last = history[history.length - 1];
    if (!last) return;
    setHistory((h) => h.slice(0, -1));
    setGraph(last.graph);
    setFocus(last.focus);
    setSelectedEdge(null);
    setEmphasis(null);
    setPathNote(null);
    setInvestigating(last.graph === initial);
  }

  function showInvestigation(id: string | null = startId) {
    if (!initial) return;
    if (graph !== initial) remember();
    setGraph(initial);
    setFocus(id);
    setSelectedEdge(null);
    setPathNote(null);
    setInvestigating(true);
  }

  async function search(target: "focus" | "path") {
    if (!text.trim()) return;
    setBusy(true);
    const res = await run(() => graphApi.find(drive, text.trim()));
    setBusy(false);
    if (res) {
      setResults(res.entities);
      setPathMode(target === "path");
    }
  }

  const showNeighbourhood = useCallback(
    async (id: string, depth: number) => {
      setBusy(true);
      const res = await run(() => graphApi.neighborhood(drive, id, depth));
      setBusy(false);
      if (res) {
        remember();
        setFocus(id);
        setGraph(res);
        setSelectedEdge(null);
        setPathNote(null);
        setPicked([]);
        setEmphasis(null);
        setInvestigating(false);
      }
    },
    // remember() reads the picture on screen
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [drive, run, graph, focus],
  );

  /** A node's direct relations added to the picture on screen. */
  async function expand(id: string) {
    if (!graph) return;
    setBusy(true);
    const res = await run(() => graphApi.neighborhood(drive, id, 1));
    setBusy(false);
    if (!res) return;
    remember();
    const merged = mergeGraphs(graph, res);
    if (merged.nodes.length > MAX_PICTURE) {
      setGraph(res);
      setPathNote(tm("The picture would be too large: it now shows only this neighbourhood."));
    } else {
      setGraph(merged);
      setPathNote(null);
    }
    setFocus(id);
    setSelectedEdge(null);
    setInvestigating(false);
  }

  function pickNode(id: string, shift: boolean) {
    if (shift) {
      setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id].slice(-2)));
      setEmphasis(null);
      return;
    }
    setFocus(id);
    setSelectedEdge(null);
  }

  /** The shortest chain between the two picked nodes, added and highlighted. */
  async function pickedPath() {
    if (picked.length !== 2 || !graph) return;
    setBusy(true);
    const res = await run(() => graphApi.path(drive, picked[0], picked[1]));
    setBusy(false);
    if (!res) return;
    if (!res.path) {
      setPathNote(tm("No connection found within 6 steps."));
      return;
    }
    remember();
    const found = { nodes: res.path.nodes, edges: res.path.edges, documents: res.documents };
    const merged = mergeGraphs(graph, found);
    setGraph(merged.nodes.length > MAX_PICTURE ? found : merged);
    setEmphasis({
      nodes: new Set(res.path.nodes.filter(Boolean).map((n) => n.id)),
      pairs: new Set(res.path.edges.map(pairKey)),
    });
    setPathNote(null);
    setInvestigating(false);
  }

  /** Who in the picture is related to both picked nodes. */
  function pickedCommon() {
    if (picked.length !== 2 || !graph) return;
    const [a, b] = picked;
    const around = (id: string) =>
      new Set(graph.edges.flatMap((e) => (e.a === id ? [e.b] : e.b === id ? [e.a] : [])));
    const mine = around(a);
    const shared = [...around(b)].filter((x) => mine.has(x));
    if (!shared.length) {
      setPathNote(tm("No common relations in the picture."));
      setEmphasis(null);
      return;
    }
    setPathNote(null);
    const touches = (e: GraphEdge) =>
      (picked.includes(e.a) && shared.includes(e.b)) ||
      (picked.includes(e.b) && shared.includes(e.a));
    setEmphasis({
      nodes: new Set([a, b, ...shared]),
      pairs: new Set(graph.edges.filter(touches).map(pairKey)),
    });
  }

  async function logExport(req: ExportRequest): Promise<GraphWatermark | null> {
    return run(() => graphApi.imageLog(drive, req));
  }

  async function connectTo(target: string) {
    if (!focus) return;
    setBusy(true);
    const res = await run(() => graphApi.path(drive, focus, target));
    setBusy(false);
    setPathMode(false);
    setResults(null);
    if (!res) return;
    if (!res.path) {
      setPathNote(tm("No connection found within 6 steps."));
      return;
    }
    remember();
    setGraph({ nodes: res.path.nodes, edges: res.path.edges, documents: res.documents });
    setPicked([]);
    setEmphasis(null);
    setSelectedEdge(res.path.edges[0] ? pairKey(res.path.edges[0]) : null);
    setPathNote(null);
    setInvestigating(false);
  }

  function pick(node: GraphNode) {
    if (pathMode) void connectTo(node.id);
    else {
      setResults(null);
      void showNeighbourhood(node.id, hops);
    }
  }

  const focusNode = graph?.nodes.find((n) => n?.id === focus) ?? null;
  const pairEdges =
    graph && selectedEdge
      ? graph.edges.filter((e) => pairKey(e) === selectedEdge).sort(byDate)
      : [];

  // A compact (large) picture has no quotes: the opened node or relation gets
  // its full copy from the box, once per copy shown.
  const detailed = useRef(new WeakSet<object>());
  const [loadingDetails, setLoadingDetails] = useState(false);
  useEffect(() => {
    if (!graph?.compact) return;
    const todo: object[] = pairEdges.filter((e) => !detailed.current.has(e));
    const id = todo.length
      ? pairEdges[0].a
      : !pairEdges.length && focusNode && !detailed.current.has(focusNode)
        ? focusNode.id
        : null;
    if (!id) return;
    if (!todo.length && focusNode) todo.push(focusNode);
    const asked = detailed.current;
    for (const o of todo) asked.add(o);
    let live = true;
    let done = false;
    setLoadingDetails(true);
    void run(() => graphApi.entity(drive, id)).then((res) => {
      done = true;
      if (!live) return;
      setLoadingDetails(false);
      if (!res) return;
      asked.add(res.entity);
      for (const e of res.edges) asked.add(e);
      setGraph((g) => (g ? withDetails(g, res) : g));
    });
    return () => {
      live = false;
      // Left before the answer came: asked again when shown again.
      if (!done) for (const o of todo) asked.delete(o);
      setLoadingDetails(false);
    };
    // the opened node or relation of this picture
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, focus, selectedEdge, drive]);

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-2 border-b border-ink/5 px-4 py-2">
          <div className="flex min-w-[220px] flex-1 items-center gap-2 rounded-full border border-ink/10 bg-ink/5 px-3 py-1.5">
            <Search className="h-3.5 w-3.5 text-ink/50" />
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void search(pathMode ? "path" : "focus")}
              placeholder={tm("Search people, organisations, places, numbers…")}
              className="w-full bg-transparent text-xs outline-none placeholder:text-ink/40"
            />
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin text-ink/50" />}
          </div>
          <select
            value={hops}
            onChange={(e) => {
              const depth = Number(e.target.value);
              setHops(depth);
              if (focus) void showNeighbourhood(focus, depth);
            }}
            className="rounded-full border border-ink/10 bg-surface-3 px-2 py-1.5 text-xs"
          >
            <option value={1}>{tm("Direct relations")}</option>
            <option value={2}>{tm("Two steps")}</option>
          </select>
          <button
            disabled={!focus || !text.trim()}
            onClick={() => void search("path")}
            title={tm("Find the shortest chain of relations to another entity")}
            className="inline-flex items-center gap-1.5 rounded-full border border-ink/10 px-3 py-1.5 text-xs text-ink/80 hover:bg-ink/10 disabled:opacity-40"
          >
            <Route className="h-3.5 w-3.5" /> {tm("Connection to…")}
          </button>
          {history.length > 0 && (
            <button
              onClick={back}
              title={tm("Back to the previous picture")}
              className="inline-flex items-center gap-1.5 rounded-full border border-ink/10 px-3 py-1.5 text-xs text-ink/80 hover:bg-ink/10"
            >
              <Undo2 className="h-3.5 w-3.5" /> {tm("Back")}
            </button>
          )}
        </div>
        {notice}
        {pathNote && <Notice text={pathNote} />}
        {initial &&
          (investigating ? (
            <InvestigationLevels
              view={initial}
              focus={focus}
              onPick={(id) => showInvestigation(id)}
            />
          ) : (
            <div className="border-b border-ink/5 px-4 py-1.5">
              <button
                onClick={() => showInvestigation()}
                className="inline-flex items-center gap-1 text-[11px] text-ink/60 hover:text-ink"
              >
                <ArrowLeft className="h-3 w-3" /> {tm("Back to the investigation")}
              </button>
            </div>
          ))}
        <div className="relative min-h-0 flex-1">
          {results && (
            <ResultList
              results={results}
              subtypeLabel={subtypeLabel}
              onPick={pick}
              onClose={() => setResults(null)}
            />
          )}
          {graph && graph.nodes.length > 0 ? (
            <GraphCanvas
              graph={graph}
              focus={focus}
              selectedEdge={selectedEdge}
              picked={picked}
              emphasis={emphasis}
              relLabel={relLabel}
              relInfo={relInfo}
              subtypeLabel={subtypeLabel}
              onNode={pickNode}
              onExpand={(id) => void expand(id)}
              onEdge={setSelectedEdge}
              onExport={logExport}
            />
          ) : (
            !results && <Notice text={tm("Search for a name to start.")} />
          )}
          {picked.length > 0 && graph && (
            <div
              className="absolute left-1/2 top-3 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-ink/10 px-3 py-1 text-[11px] shadow-sm"
              style={{ backgroundColor: "var(--surface-3)" }}
            >
              <span className="text-ink/55">{tm("Picked")}:</span>
              {picked.map((id) => (
                <span key={id} className="font-medium" style={{ color: ACCENT_TEXT }}>
                  {graph.nodes.find((n) => n?.id === id)?.label ?? id}
                </span>
              ))}
              <button
                disabled={picked.length !== 2 || busy}
                onClick={() => void pickedPath()}
                className="ml-1 rounded-full border border-ink/10 px-2 py-0.5 hover:bg-ink/10 disabled:opacity-40"
              >
                {tm("Shortest path")}
              </button>
              <button
                disabled={picked.length !== 2}
                onClick={pickedCommon}
                className="rounded-full border border-ink/10 px-2 py-0.5 hover:bg-ink/10 disabled:opacity-40"
              >
                {tm("Common relations")}
              </button>
              <button
                onClick={() => {
                  setPicked([]);
                  setEmphasis(null);
                }}
                title={tm("Clear the selection")}
                className="rounded-full p-0.5 text-ink/60 hover:bg-ink/10"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          )}
          {graph?.truncated && (
            <div className="absolute bottom-2 left-3 text-[11px] text-ink/45">
              {investigating
                ? tm("The investigation was cut; more people may exist.")
                : tm("Showing the first 2000 entities.")}
            </div>
          )}
        </div>
      </div>
      <aside
        className="hidden w-[360px] shrink-0 flex-col overflow-y-auto border-l border-ink/5 md:flex"
        style={{ backgroundColor: PANEL }}
      >
        {loadingDetails && (
          <div className="flex items-center gap-1.5 px-4 pt-3 text-[11px] text-ink/55">
            <Loader2 className="h-3 w-3 animate-spin" /> {tm("Loading the evidence…")}
          </div>
        )}
        {pairEdges.length > 0 && graph ? (
          <EdgeSheet
            edges={pairEdges}
            graph={graph}
            relLabel={relLabel}
            onBack={() => setSelectedEdge(null)}
            onOpenSource={onOpenSource}
          />
        ) : focusNode && graph ? (
          <EntitySheet
            drive={drive}
            node={focusNode}
            graph={graph}
            relLabel={relLabel}
            subtypeLabel={subtypeLabel}
            watchlist={watchlist}
            onEdge={setSelectedEdge}
            onExpand={() => void expand(focusNode.id)}
            onOpenSource={onOpenSource}
          />
        ) : (
          <Notice
            text={tm(
              "Click a node to focus it, double-click to add its relations, a line to see its evidence.",
            )}
          />
        )}
      </aside>
    </div>
  );
}

const LEVEL_LABEL = ["Starting point", "Level 1", "Level 2", "Level 3", "Level 4"];

/** The rows of an investigation by level: the starting point first, each name
 *  focuses its node; the focused row's path of the link below. */
function InvestigationLevels({
  view,
  focus,
  onPick,
}: {
  view: GraphInvestigationView;
  focus: string | null;
  onPick: (id: string) => void;
}) {
  const { tm } = useLang();
  const levels = [...new Set(view.rows.map((r) => r.level))].sort((a, b) => a - b);
  const row = view.rows.find((r) => r.id === focus);
  return (
    <div className="max-h-[30%] overflow-y-auto border-b border-ink/5 px-4 py-2 text-[11px]">
      <div className="mb-1 text-ink/50">
        {tm("Investigation")}: <span className="text-ink/85">{view.start}</span>
      </div>
      {levels.map((level) => (
        <div key={level} className="flex flex-wrap items-center gap-1 py-0.5">
          <span className="w-24 shrink-0 text-ink/45">
            {tm(LEVEL_LABEL[level] ?? LEVEL_LABEL[LEVEL_LABEL.length - 1])}
          </span>
          {view.rows
            .filter((r) => r.level === level)
            .map((r) => (
              <button
                key={r.id}
                onClick={() => onPick(r.id)}
                className={`rounded-full border px-2 py-0.5 transition ${
                  r.id === focus
                    ? "border-transparent text-white"
                    : "border-ink/10 text-ink/75 hover:bg-ink/10"
                }`}
                style={r.id === focus ? { backgroundColor: ACCENT } : undefined}
              >
                {r.name}
              </button>
            ))}
        </div>
      ))}
      {row && row.level > 0 && (
        <div className="mt-1 text-ink/60">
          {tm("Path of the link")}: <span className="text-ink/85">{row.path}</span>
          {row.also.length > 0 && (
            <span>
              {" · "}
              {tm("Also through")}: {row.also.join(", ")}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function ResultList({
  results,
  subtypeLabel,
  onPick,
  onClose,
}: {
  results: GraphNode[];
  subtypeLabel: (sub: string) => string;
  onPick: (node: GraphNode) => void;
  onClose: () => void;
}) {
  const { tm } = useLang();
  return (
    <div
      className="absolute left-4 top-3 z-10 max-h-[70%] w-[min(420px,90%)] overflow-y-auto rounded-xl border border-ink/10 shadow-xl"
      style={{ backgroundColor: PANEL }}
    >
      <div className="flex items-center justify-between px-3 py-2 text-[11px] text-ink/50">
        <span>
          {results.length} {tm("results")}
        </span>
        <button onClick={onClose} className="rounded p-0.5 hover:bg-ink/10">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      {results.length === 0 && (
        <div className="px-3 pb-3 text-xs text-ink/60">
          {tm("No match in the documents you may see.")}
        </div>
      )}
      {results.map((n) => (
        <button
          key={n.id}
          onClick={() => onPick(n)}
          className="flex w-full items-center gap-2 border-t border-ink/5 px-3 py-2 text-left text-xs hover:bg-ink/5"
        >
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-full"
            style={{ backgroundColor: TYPE_COLOR[n.type] }}
          />
          <span className="min-w-0 flex-1 truncate">{n.label}</span>
          <span className="shrink-0 text-[10px] text-ink/45">
            {subtypeLabel(n.subtype) || tm(TYPE_NAME[n.type])} · {n.documents.length}{" "}
            {tm("documents")}
          </span>
        </button>
      ))}
    </div>
  );
}

function EvidenceList({
  evidence,
  documents,
  label,
  onOpenSource,
}: {
  evidence: GraphEvidence[];
  documents: GraphDocuments;
  label: string;
  onOpenSource: (source: GraphSource) => void;
}) {
  const { tm } = useLang();
  return (
    <div className="space-y-2">
      {evidence.map((ev, i) => {
        const doc = documents[ev.doc_id];
        return (
          <div key={i} className="rounded-lg border border-ink/10 bg-shade/20 p-2 text-xs">
            {ev.quote && <div className="italic text-ink/80">„{ev.quote}”</div>}
            {doc && (
              <button
                onClick={() =>
                  onOpenSource({
                    path: doc.source_path,
                    page: ev.page ?? 0,
                    quote: ev.quote,
                    label: label,
                  })
                }
                className="mt-1.5 inline-flex items-center gap-1 text-[11px] hover:underline"
                style={{ color: ACCENT_TEXT }}
              >
                <ExternalLink className="h-3 w-3" />
                {doc.title || doc.source_path.split("/").pop()}
                {ev.page ? ` · ${tm("page")} ${ev.page}` : ""}
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

function EdgeSheet({
  edges,
  graph,
  relLabel,
  onBack,
  onOpenSource,
}: {
  edges: GraphEdge[];
  graph: Graph;
  relLabel: (type: string) => string;
  onBack: () => void;
  onOpenSource: (source: GraphSource) => void;
}) {
  const { tm } = useLang();
  const name = (id: string) => graph.nodes.find((n) => n?.id === id)?.label ?? id;
  return (
    <div className="p-4">
      <button
        onClick={onBack}
        className="mb-3 inline-flex items-center gap-1 text-[11px] text-ink/60 hover:text-ink"
      >
        <ArrowLeft className="h-3 w-3" /> {tm("Back")}
      </button>
      <div className="text-sm font-semibold">
        {name(edges[0].a)} ↔ {name(edges[0].b)}
      </div>
      {edges.map((edge) => {
        const title = `${name(edge.a)} — ${relLabel(edge.type)} — ${name(edge.b)}`;
        return (
          <div key={edge.id} className="mt-4">
            <div className="text-xs font-medium" style={{ color: ACCENT_TEXT }}>
              {relLabel(edge.type)}
              <span className="ml-2 text-[11px] font-normal text-ink/50">
                {edge.start ?? ""}
                {edge.end && edge.end !== edge.start ? ` – ${edge.end}` : ""}
                {edge.raw_label && edge.raw_label !== edge.type ? ` · ${edge.raw_label}` : ""}
              </span>
            </div>
            <div className="mb-1.5 mt-1 text-[11px] uppercase tracking-wide text-ink/45">
              {tm("Evidence")}
            </div>
            <EvidenceList
              evidence={edge.evidence}
              documents={graph.documents}
              label={title}
              onOpenSource={onOpenSource}
            />
          </div>
        );
      })}
    </div>
  );
}

function EntitySheet({
  drive,
  node,
  graph,
  relLabel,
  subtypeLabel,
  watchlist,
  onEdge,
  onExpand,
  onOpenSource,
}: {
  drive: string;
  node: GraphNode;
  graph: Graph;
  relLabel: (type: string) => string;
  subtypeLabel: (sub: string) => string;
  watchlist: boolean;
  onEdge: (id: string) => void;
  onExpand: () => void;
  onOpenSource: (source: GraphSource) => void;
}) {
  const { tm } = useLang();
  const { run, notice } = useFailure();
  const [timeline, setTimeline] = useState<{
    items: GraphTimelineItem[];
    documents: GraphDocuments;
  } | null>(null);
  useEffect(() => setTimeline(null), [node.id]);

  const edges = graph.edges.filter((e) => e.a === node.id || e.b === node.id);
  const other = (e: GraphEdge) => graph.nodes.find((n) => n?.id === (e.a === node.id ? e.b : e.a));

  return (
    <div className="p-4">
      <div className="flex items-center gap-2">
        <span className="h-3 w-3 rounded-full" style={{ backgroundColor: TYPE_COLOR[node.type] }} />
        <div className="text-sm font-semibold">{node.label}</div>
      </div>
      <div className="mt-0.5 text-[11px] text-ink/50">
        {subtypeLabel(node.subtype) || tm(TYPE_NAME[node.type])} · {node.mentions} {tm("mentions")}{" "}
        · {node.documents.length} {tm("documents")}
      </div>
      <button
        onClick={onExpand}
        className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-ink/10 px-3 py-1 text-[11px] text-ink/80 hover:bg-ink/10"
      >
        <Network className="h-3 w-3" /> {tm("Add its relations to the picture")}
      </button>
      {watchlist && <WatchToggle drive={drive} node={node} />}
      {node.aliases.length > 1 && (
        <Section title={tm("Also known as")}>
          <div className="text-xs text-ink/75">{node.aliases.join(" · ")}</div>
        </Section>
      )}
      {node.attributes.length > 0 && (
        <Section title={tm("Attributes")}>
          {node.attributes.map((a, i) => (
            <div key={i} className="flex justify-between gap-2 text-xs">
              <span className="text-ink/55">{tm(a.name)}</span>
              <span>{a.value}</span>
            </div>
          ))}
        </Section>
      )}
      <DossierActions drive={drive} node={node} />
      <Section title={tm("Relations")}>
        <div className="divide-y divide-ink/10 border-y border-ink/10">
          {edges.map((e) => (
            <button
              key={e.id}
              onClick={() => onEdge(pairKey(e))}
              className="group flex w-full items-center gap-2 px-1 py-2 text-left text-xs hover:bg-ink/5"
            >
              <span className="shrink-0 text-ink/55">{relLabel(e.type)}</span>
              <span className="min-w-0 flex-1 truncate">{other(e)?.label ?? ""}</span>
              {e.start && <span className="shrink-0 text-[10px] text-ink/45">{e.start}</span>}
              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink/30 group-hover:text-ink/70" />
            </button>
          ))}
        </div>
      </Section>
      <Section title={tm("Timeline")}>
        {notice}
        {!timeline ? (
          <button
            onClick={async () => {
              const res = await run(() => graphApi.timeline(drive, node.id));
              if (res) setTimeline({ items: res.timeline, documents: res.documents });
            }}
            className="inline-flex items-center gap-1.5 rounded-full border border-ink/10 px-3 py-1 text-xs hover:bg-ink/10"
          >
            <CalendarDays className="h-3.5 w-3.5" /> {tm("Timeline")}
          </button>
        ) : timeline.items.length === 0 ? (
          <div className="text-xs text-ink/55">{tm("No dated relations.")}</div>
        ) : (
          <div className="space-y-2">
            {timeline.items.map((item, i) => (
              <div key={i} className="border-l-2 pl-2" style={{ borderColor: `${ACCENT}88` }}>
                <div className="text-[11px] text-ink/50">
                  {item.date}
                  {item.end && item.end !== item.date ? ` – ${item.end}` : ""}
                </div>
                <div className="text-xs">
                  {relLabel(item.type)} · {item.other?.label}
                </div>
                <EvidenceList
                  evidence={item.evidence.slice(0, 1)}
                  documents={timeline.documents}
                  label={node.label}
                  onOpenSource={onOpenSource}
                />
              </div>
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}

/** Watchlist pilot: watch the focused entity, so an empty chat tells what is new
 *  about it (adapter watch.py). Adding is purpose-bound and logged. */
function WatchToggle({ drive, node }: { drive: string; node: GraphNode }) {
  const { tm } = useLang();
  const { run, notice } = useFailure();
  // undefined: not known yet; null: not watched.
  const [watchId, setWatchId] = useState<string | null | undefined>(undefined);
  const [full, setFull] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    setWatchId(undefined);
    setFull(false);
    graphApi
      .watchList(drive)
      .then((res) => live && setWatchId(res.items.find((i) => i.entity_id === node.id)?.id ?? null))
      .catch(() => live && setWatchId(null));
    return () => {
      live = false;
    };
  }, [drive, node.id]);
  if (watchId === undefined) return null;

  async function toggle() {
    setBusy(true);
    setFull(false);
    if (watchId) {
      const res = await run(() => graphApi.watchRemove(drive, watchId));
      if (res) setWatchId(null);
    } else {
      const res = await run(async () => {
        try {
          return await graphApi.watchAdd(drive, node.id);
        } catch (err) {
          if (err instanceof AdapterGraphError && err.status === 409) {
            setFull(true);
            return null;
          }
          throw err;
        }
      });
      if (res) setWatchId(res.item.id);
    }
    setBusy(false);
  }

  return (
    <>
      <button
        onClick={() => void toggle()}
        disabled={busy}
        title={tm(watchId ? "Stop watching" : "Watch: an empty chat tells what is new about it")}
        className="ml-1.5 mt-2 inline-flex items-center gap-1.5 rounded-full border border-ink/10 px-3 py-1 text-[11px] text-ink/80 hover:bg-ink/10 disabled:opacity-50"
        style={watchId ? { borderColor: ACCENT, color: ACCENT_TEXT } : undefined}
      >
        {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Eye className="h-3 w-3" />}
        {tm(watchId ? "Watching" : "Watch")}
      </button>
      {full && (
        <Notice text={tm("The watchlist is full: stop watching something first.")} tone="warn" />
      )}
      {notice}
    </>
  );
}

/** Dossier of the focused entity: relations, timeline and a numbered source
 *  list, rendered by the adapter within the caller's scope. */
function DossierActions({ drive, node }: { drive: string; node: GraphNode }) {
  const { tm, lang } = useLang();
  const { run, notice } = useFailure();
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => setNote(null), [node.id]);

  async function download(format: "docx" | "pdf") {
    setBusy(format);
    setNote(null);
    const res = await run(() => graphApi.dossier(drive, node.id, format, lang));
    setBusy(null);
    if (!res) return;
    const bytes = Uint8Array.from(atob(res.content_b64), (ch) => ch.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: res.content_type }));
    const a = document.createElement("a");
    a.href = url;
    a.download = res.name;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function save() {
    setBusy("save");
    setNote(null);
    const res = await run(() => graphApi.dossier(drive, node.id, "docx", lang));
    if (!res) {
      setBusy(null);
      return;
    }
    const saved = await saveToDrive({
      data: {
        name: res.name,
        text: res.text,
        format: "docx",
        drive: drive || undefined,
      },
    });
    setBusy(null);
    if (saved.ok && saved.transferred) setNote(`${tm("Saved to drive")}: ${saved.path}`);
    else if (saved.ok && saved.canDownload)
      setNote(tm("The drive is not writable from here — use the download instead."));
    else setNote(`${tm("Save failed")}: ${saved.reason || saved.error || "?"}`);
  }

  const button =
    "inline-flex items-center gap-1.5 rounded-full border border-ink/10 px-3 py-1 text-xs hover:bg-ink/10 disabled:opacity-40";
  return (
    <Section title={tm("Dossier")}>
      {notice}
      <div className="flex flex-wrap gap-1.5">
        <button disabled={!!busy} onClick={() => void download("docx")} className={button}>
          {busy === "docx" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <FileText className="h-3.5 w-3.5" />
          )}
          Word
        </button>
        <button disabled={!!busy} onClick={() => void download("pdf")} className={button}>
          {busy === "pdf" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Download className="h-3.5 w-3.5" />
          )}
          PDF
        </button>
        <button disabled={!!busy} onClick={() => void save()} className={button}>
          {busy === "save" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {tm("Save to drive")}
        </button>
      </div>
      {note && <div className="mt-1.5 text-[11px] text-ink/60">{note}</div>}
    </Section>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-4">
      <div className="mb-1.5 text-[11px] uppercase tracking-wide text-ink/45">{title}</div>
      {children}
    </div>
  );
}

/* ----------------------------------------------------------------- Review */

type ReviewView = "open" | "decided";
type Pair = { a: string; b: string };
type Decision = "merge" | "separate";

const pairId = (p: Pair) => `${p.a}|${p.b}`;

/** A pair's reason ("same name + birth date"), translated part by part. */
function useReasonText() {
  const { tm } = useLang();
  return useCallback(
    (text: string) =>
      text
        .split(" + ")
        .map((part) =>
          part
            .split(", ")
            .map((p) => tm(p))
            .join(", "),
        )
        .join(" + "),
    [tm],
  );
}

type ReviewProps = {
  drive: string;
  canEdit: boolean;
  relLabel: (type: string) => string;
  subtypeLabel: (sub: string) => string;
  onOpenSource: (source: GraphSource) => void;
  onOpenPair: (a: string, b: string) => void;
};

function ReviewTab(props: ReviewProps) {
  const { tm } = useLang();
  const [view, setView] = useState<ReviewView>("open");
  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex items-center gap-1 text-xs">
        {(["open", "decided"] as const).map((v) => (
          <button
            key={v}
            onClick={() => setView(v)}
            className={`rounded-full px-3 py-1 transition ${
              view === v ? "text-white" : "text-ink/70 hover:bg-ink/10"
            }`}
            style={view === v ? { backgroundColor: ACCENT } : undefined}
          >
            {tm(v === "open" ? "Open pairs" : "Decisions")}
          </button>
        ))}
      </div>
      {view === "open" ? <OpenReview {...props} /> : <DecidedReview drive={props.drive} />}
    </div>
  );
}

function OpenReview({
  drive,
  canEdit,
  relLabel,
  subtypeLabel,
  onOpenSource,
  onOpenPair,
}: ReviewProps) {
  const { tm } = useLang();
  const reason = useReasonText();
  const { run, notice } = useFailure();
  const [list, setList] = useState<GraphReviewList | null>(null);
  const [items, setItems] = useState<GraphReviewItem[]>([]);
  const [more, setMore] = useState(false);
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [why, setWhy] = useState("");
  const [roles, setRoles] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [done, setDone] = useState<Record<string, Decision>>({});
  const [confirm, setConfirm] = useState<Decision | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setApplied(query.trim()), 350);
    return () => clearTimeout(timer);
  }, [query]);

  const load = useCallback(
    async (offset = 0) => {
      const res = await run(() =>
        graphApi.reviewList(drive, {
          q: applied || undefined,
          reason: why || undefined,
          include_role_mismatch: roles || undefined,
          offset,
        }),
      );
      if (!res) return;
      setList(res);
      setMore(offset + res.review.length < res.total);
      setItems((cur) => (offset ? [...cur, ...res.review] : res.review));
      if (!offset) {
        setDone({});
        setSelected(new Set());
        setConfirm(null);
      }
    },
    [drive, run, applied, why, roles],
  );
  useEffect(() => {
    void load();
  }, [load]);

  const pending = items.filter((i) => !done[pairId(i)]);
  const allSelected = pending.length > 0 && pending.every((i) => selected.has(pairId(i)));

  function toggle(key: string) {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function mark(pairs: Pair[], decision: Decision) {
    setDone((d) => ({ ...d, ...Object.fromEntries(pairs.map((p) => [pairId(p), decision])) }));
    setSelected((cur) => {
      const next = new Set(cur);
      pairs.forEach((p) => next.delete(pairId(p)));
      return next;
    });
  }

  async function decide(item: GraphReviewItem, decision: Decision) {
    setResult(null);
    const res = await run(() => graphApi.reviewDecide(drive, item.a, item.b, decision));
    if (res) mark([item], decision);
  }

  async function decideSelected(decision: Decision) {
    setConfirm(null);
    setResult(null);
    setBusy(true);
    const pairs = pending.filter((i) => selected.has(pairId(i))).map(({ a, b }) => ({ a, b }));
    const res = await run(() => graphApi.reviewDecideMany(drive, pairs, decision));
    setBusy(false);
    if (!res) return;
    const skipped = new Set(res.skipped.map(pairId));
    mark(
      pairs.filter((p) => !skipped.has(pairId(p))),
      decision,
    );
    setResult(`${tm("Decided")}: ${res.done} · ${tm("Skipped")}: ${res.skipped.length}`);
  }

  async function undo(item: GraphReviewItem) {
    setResult(null);
    const res = await run(() => graphApi.reviewUndo(drive, item.a, item.b));
    if (res) {
      setDone((d) => {
        const next = { ...d };
        delete next[pairId(item)];
        return next;
      });
    }
  }

  const reasons = Object.entries(list?.reasons ?? {}).sort((x, y) => y[1] - x[1]);
  return (
    <>
      <div className="mb-3 flex items-center gap-2">
        <div className="text-sm font-semibold">{tm("Possible same person")}</div>
        <span className="text-xs text-ink/50">({list?.open_total ?? 0})</span>
        <button
          onClick={() => void load()}
          className="ml-auto rounded-full p-1.5 hover:bg-ink/10"
          title={tm("Refresh")}
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      </div>
      {notice}
      {!canEdit && <Notice text={tm("Only graph administrators may do this.")} />}
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink/40" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={tm("Filter by name…")}
            className="w-full rounded-lg border border-ink/15 bg-shade/30 py-1 pl-7 pr-2 text-xs text-ink outline-none focus:border-ink/30"
          />
        </div>
        <select
          value={why}
          onChange={(e) => setWhy(e.target.value)}
          className="rounded-lg border border-ink/15 bg-shade/30 px-2 py-1 text-xs text-ink outline-none"
        >
          <option value="">{`${tm("All reasons")} (${list?.open_total ?? 0})`}</option>
          {reasons.map(([r, n]) => (
            <option key={r} value={r}>{`${reason(r)} (${n})`}</option>
          ))}
        </select>
        {Boolean(list?.role_mismatch) && (
          <label
            className="flex cursor-pointer items-center gap-1.5 text-xs text-ink/70"
            title={tm(
              "An inmate and a visitor of the same name are taken as two people unless more than the name links them.",
            )}
          >
            <input type="checkbox" checked={roles} onChange={() => setRoles((v) => !v)} />
            {`${tm("Also pairs of different roles")} (${list?.role_mismatch ?? 0})`}
          </label>
        )}
      </div>
      {canEdit && pending.length > 0 && (
        <div
          className="mb-2 flex flex-wrap items-center gap-2 rounded-xl border border-ink/10 px-3 py-2 text-xs"
          style={{ backgroundColor: PANEL }}
        >
          <label className="flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              checked={allSelected}
              onChange={() => setSelected(allSelected ? new Set() : new Set(pending.map(pairId)))}
            />
            {`${tm("Select all shown")} (${pending.length})`}
          </label>
          <span className="text-ink/50">{`${tm("Selected")}: ${selected.size}`}</span>
          {confirm ? (
            <span className="ml-auto flex flex-wrap items-center gap-2">
              <span>
                {`${tm(
                  confirm === "merge"
                    ? "Merge the selected pairs?"
                    : "Keep the selected pairs apart?",
                )} (${selected.size})`}
              </span>
              <button
                onClick={() => void decideSelected(confirm)}
                className="rounded-full px-3 py-1 text-white"
                style={{ backgroundColor: ACCENT }}
              >
                {tm("Confirm")}
              </button>
              <button
                onClick={() => setConfirm(null)}
                className="rounded-full border border-ink/15 px-3 py-1 hover:bg-ink/10"
              >
                {tm("Cancel")}
              </button>
            </span>
          ) : (
            <span className="ml-auto flex gap-2">
              <button
                onClick={() => setConfirm("merge")}
                disabled={!selected.size || busy}
                className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-white disabled:opacity-50"
                style={{ backgroundColor: ACCENT }}
              >
                {busy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <GitMerge className="h-3.5 w-3.5" />
                )}
                {tm("Merge")}
              </button>
              <button
                onClick={() => setConfirm("separate")}
                disabled={!selected.size || busy}
                className="inline-flex items-center gap-1.5 rounded-full border border-ink/15 px-3 py-1 hover:bg-ink/10 disabled:opacity-50"
              >
                <Split className="h-3.5 w-3.5" /> {tm("Keep apart")}
              </button>
            </span>
          )}
        </div>
      )}
      {result && (
        <div className="mb-2 text-xs" style={{ color: ACCENT_TEXT }}>
          {result}
        </div>
      )}
      {list && items.length === 0 && (
        <Notice
          text={tm(
            list.open_total ? "No pair matches the filter." : "Nothing to review in your scope.",
          )}
        />
      )}
      <div className="space-y-2">
        {items.map((item) => {
          const key = pairId(item);
          const state = done[key];
          return (
            <div
              key={key}
              className="rounded-xl border border-ink/10 p-3"
              style={{ backgroundColor: PANEL }}
            >
              <div className="flex flex-wrap items-center gap-2 text-sm">
                {canEdit && !state && (
                  <input
                    type="checkbox"
                    checked={selected.has(key)}
                    onChange={() => toggle(key)}
                    aria-label={tm("Select")}
                  />
                )}
                <span className="font-medium">{item.a_label}</span>
                <span className="text-ink/40">↔</span>
                <span className="font-medium">{item.b_label}</span>
                <span className="ml-auto text-[11px] text-ink/50">
                  {reason(item.reason)} · {Math.round(item.score * 100)}%
                </span>
              </div>
              <PairDetails
                item={item}
                relLabel={relLabel}
                subtypeLabel={subtypeLabel}
                onOpenSource={onOpenSource}
                onOpenPair={onOpenPair}
              />
              {state ? (
                <div
                  className="mt-2 flex items-center gap-3 text-xs"
                  style={{ color: ACCENT_TEXT }}
                >
                  {tm(state === "merge" ? "Merged." : "Kept apart.")}
                  {canEdit && (
                    <button
                      onClick={() => void undo(item)}
                      className="text-ink/70 underline hover:text-ink"
                    >
                      {tm("Undo")}
                    </button>
                  )}
                </div>
              ) : (
                canEdit && (
                  <div className="mt-2 flex gap-2">
                    <button
                      onClick={() => void decide(item, "merge")}
                      className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs text-white"
                      style={{ backgroundColor: ACCENT }}
                    >
                      <GitMerge className="h-3.5 w-3.5" /> {tm("Merge")}
                    </button>
                    <button
                      onClick={() => void decide(item, "separate")}
                      className="inline-flex items-center gap-1.5 rounded-full border border-ink/15 px-3 py-1 text-xs hover:bg-ink/10"
                    >
                      <Split className="h-3.5 w-3.5" /> {tm("Keep apart")}
                    </button>
                  </div>
                )
              )}
            </div>
          );
        })}
      </div>
      {more && (
        <button
          onClick={() => void load(pending.length)}
          className="mt-3 w-full rounded-lg border border-ink/15 py-1.5 text-xs hover:bg-ink/10"
        >
          {tm("Load more")}
        </button>
      )}
    </>
  );
}

const FIELD_STATE: Record<GraphReviewField["state"], { text: string; className: string }> = {
  same: { text: "Matches", className: "text-emerald-700 dark:text-emerald-300" },
  conflict: { text: "Conflicts", className: "text-rose-700 dark:text-rose-300" },
  different: { text: "Differs", className: "text-ink/60" },
  a_only: { text: "Only in record 1", className: "text-ink/50" },
  b_only: { text: "Only in record 2", className: "text-ink/50" },
};

/** What tells the two records of a review pair apart: the stated details side
 *  by side, then each record's role, sources and relations. */
function PairDetails({
  item,
  relLabel,
  subtypeLabel,
  onOpenSource,
  onOpenPair,
}: {
  item: GraphReviewItem;
  relLabel: (type: string) => string;
  subtypeLabel: (sub: string) => string;
  onOpenSource: (source: GraphSource) => void;
  onOpenPair: (a: string, b: string) => void;
}) {
  const { tm } = useLang();
  if (!item.a_info || !item.b_info) return null;
  const fields = item.fields ?? [];
  const fieldName = (name: string) =>
    name === "identifiers" ? tm("Phones, documents, addresses") : tm(name);
  return (
    <div className="mt-2 space-y-2">
      {fields.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[11px]">
            <thead className="text-ink/45">
              <tr>
                <th className="py-0.5 pr-3 font-normal" />
                <th className="py-0.5 pr-3 font-normal">{tm("Record 1")}</th>
                <th className="py-0.5 pr-3 font-normal">{tm("Record 2")}</th>
                <th className="py-0.5 font-normal" />
              </tr>
            </thead>
            <tbody>
              {fields.map((f) => (
                <tr key={f.name} className="border-t border-ink/5">
                  <td className="py-0.5 pr-3 text-ink/60">{fieldName(f.name)}</td>
                  <td className="py-0.5 pr-3 text-ink">{f.a.join(", ") || "—"}</td>
                  <td className="py-0.5 pr-3 text-ink">{f.b.join(", ") || "—"}</td>
                  <td className={`py-0.5 whitespace-nowrap ${FIELD_STATE[f.state].className}`}>
                    {tm(FIELD_STATE[f.state].text)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="text-[11px] text-ink/50">
          {tm("Neither record states a birth date, a mother's name or an identifier.")}
        </div>
      )}
      <div className="grid gap-2 md:grid-cols-2">
        {(
          [
            [tm("Record 1"), item.a_info],
            [tm("Record 2"), item.b_info],
          ] as const
        ).map(([title, side]) => (
          <div key={title} className="rounded-lg border border-ink/10 bg-shade/20 p-2 text-[11px]">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-ink/50">{title}</span>
              <span className="rounded-full border border-ink/15 px-1.5 py-px text-ink/80">
                {(side.subtype && subtypeLabel(side.subtype)) ||
                  side.subtype ||
                  tm("No role given")}
              </span>
              <span className="ml-auto text-ink/45">
                {`${side.counts.documents} ${tm("documents")} · ${side.counts.links} ${tm("relations")}`}
              </span>
            </div>
            {side.documents.map((d) => (
              <button
                key={d.doc_id}
                onClick={() => onOpenSource({ path: d.path, page: 0, quote: "", label: d.name })}
                className="mt-1 flex max-w-full items-center gap-1 truncate hover:underline"
                style={{ color: ACCENT_TEXT }}
                title={d.path}
              >
                <FileText className="h-3 w-3 shrink-0" />
                <span className="truncate">{d.name}</span>
              </button>
            ))}
            {side.links.map((l, i) => {
              const doc = side.documents.find((d) => d.doc_id === l.doc_id);
              return (
                <div key={i} className="mt-1.5">
                  <div className="text-ink/80">
                    {l.raw_label || relLabel(l.type)} · <span className="text-ink">{l.other}</span>
                    {l.start ? <span className="text-ink/45">{` · ${l.start}`}</span> : null}
                  </div>
                  {l.quote &&
                    (doc ? (
                      <button
                        onClick={() =>
                          onOpenSource({ path: doc.path, page: 0, quote: l.quote, label: doc.name })
                        }
                        className="line-clamp-2 text-left italic text-ink/55 hover:underline"
                      >
                        „{l.quote}”
                      </button>
                    ) : (
                      <div className="line-clamp-2 italic text-ink/55">„{l.quote}”</div>
                    ))}
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <button
        onClick={() => onOpenPair(item.a, item.b)}
        className="inline-flex items-center gap-1 text-[11px] hover:underline"
        style={{ color: ACCENT_TEXT }}
      >
        <Network className="h-3 w-3" /> {tm("Open both in the graph")}
      </button>
    </div>
  );
}

function DecidedReview({ drive }: { drive: string }) {
  const { tm } = useLang();
  const reason = useReasonText();
  const { run, notice } = useFailure();
  const [items, setItems] = useState<GraphReviewDecision[] | null>(null);
  const [total, setTotal] = useState(0);
  const [canUndo, setCanUndo] = useState(false);
  const [undone, setUndone] = useState<Set<string>>(new Set());

  const load = useCallback(
    async (offset = 0) => {
      const res = await run(() => graphApi.reviewHistory(drive, offset));
      if (!res) return;
      setItems((cur) => (offset && cur ? [...cur, ...res.history] : res.history));
      setTotal(res.total);
      setCanUndo(res.can_undo);
      if (!offset) setUndone(new Set());
    },
    [drive, run],
  );
  useEffect(() => {
    void load();
  }, [load]);

  async function undo(item: GraphReviewDecision) {
    const res = await run(() => graphApi.reviewUndo(drive, item.a, item.b));
    if (res) setUndone((cur) => new Set(cur).add(pairId(item)));
  }

  const loaded = items?.length ?? 0;
  return (
    <>
      <div className="mb-3 flex items-center gap-2">
        <div className="text-sm font-semibold">{tm("Decisions")}</div>
        <span className="text-xs text-ink/50">({total})</span>
        <button
          onClick={() => void load()}
          className="ml-auto rounded-full p-1.5 hover:bg-ink/10"
          title={tm("Refresh")}
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      </div>
      {notice}
      {items && items.length === 0 && <Notice text={tm("No decisions in your scope yet.")} />}
      <div className="space-y-2">
        {items?.map((item) => {
          const key = pairId(item);
          const merge = item.decision === "merge";
          return (
            <div
              key={key}
              className="rounded-xl border border-ink/10 p-3"
              style={{ backgroundColor: PANEL }}
            >
              <div className="flex flex-wrap items-center gap-2 text-sm">
                {merge ? (
                  <GitMerge className="h-3.5 w-3.5 text-ink/60" />
                ) : (
                  <Split className="h-3.5 w-3.5 text-ink/60" />
                )}
                <span className="font-medium">{item.a_label}</span>
                <span className="text-ink/40">{merge ? "→" : "↔"}</span>
                <span className="font-medium">{item.b_label}</span>
                <span className="ml-auto text-[11px] text-ink/60">
                  {tm(merge ? "Merged." : "Kept apart.")}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-ink/50">
                <span>
                  {[
                    item.by,
                    item.at ? new Date(item.at * 1000).toLocaleString() : "",
                    item.reason ? reason(item.reason) : "",
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
                {undone.has(key) ? (
                  <span className="ml-auto" style={{ color: ACCENT_TEXT }}>
                    {tm("Undone — the pair is open for review again.")}
                  </span>
                ) : (
                  canUndo &&
                  (item.undoable ? (
                    <button
                      onClick={() => void undo(item)}
                      className="ml-auto rounded-full border border-ink/15 px-3 py-0.5 text-ink/80 hover:bg-ink/10"
                    >
                      {tm("Undo")}
                    </button>
                  ) : (
                    <span className="ml-auto">{tm("Cannot be undone")}</span>
                  ))
                )}
              </div>
            </div>
          );
        })}
      </div>
      {loaded < total && (
        <button
          onClick={() => void load(loaded - undone.size)}
          className="mt-3 w-full rounded-lg border border-ink/15 py-1.5 text-xs hover:bg-ink/10"
        >
          {tm("Load more")}
        </button>
      )}
    </>
  );
}
/* -------------------------------------------------------------- Query log */

const QUERY_KIND: Record<string, string> = {
  ask: "Question",
  "export:investigation": "Spreadsheet export",
  "export:dossier": "Dossier export",
  "graph:investigation": "Investigation diagram",
  "summary:investigation": "Person summaries",
  "querylog:view": "Query log view",
};

function queryKind(kind: string): string {
  return QUERY_KIND[kind] ?? (kind.startsWith("graph:") ? "Relationship query" : kind);
}

/** The query log (concept 5.3): an analyst sees their own queries, a leader
 *  everyone's. Documents outside the viewer's access are only counted. */
function QueryLogTab({ drive }: { drive: string }) {
  const { tm } = useLang();
  const [inputs, setInputs] = useState({ from: "", to: "", user: "", caseRef: "" });
  const [applied, setApplied] = useState(inputs);
  const [data, setData] = useState<QueryLog | null>(null);
  const [items, setItems] = useState<QueryLogRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  // The first page's time: later pages leave out records written since.
  const asOf = useRef<string | undefined>(undefined);

  const load = useCallback(
    async (offset = 0) => {
      setLoading(true);
      try {
        const res = await graphApi.queryLog(drive, {
          ...applied,
          offset,
          before: offset ? asOf.current : undefined,
        });
        if (!offset) asOf.current = res.as_of;
        setData(res);
        setItems((cur) => (offset ? [...cur, ...res.records] : res.records));
        setError(false);
      } catch {
        setError(true);
      } finally {
        setLoading(false);
      }
    },
    [drive, applied],
  );
  useEffect(() => {
    void load();
  }, [load]);

  const leader = data?.role === "leader";
  const field =
    "rounded-lg border border-ink/15 bg-shade/30 px-2 py-1 text-xs text-ink outline-none focus:border-ink/30";
  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex items-center gap-2">
        <div className="text-sm font-semibold">{tm("Query log")}</div>
        {data && <span className="text-xs text-ink/50">({data.total})</span>}
        {data && (
          <span className="rounded-full border border-ink/15 px-2 py-0.5 text-[11px] text-ink/60">
            {tm(leader ? "Everyone's queries (leader)" : "Your own queries")}
          </span>
        )}
        <button
          onClick={() => void load()}
          className="ml-auto rounded-full p-1.5 hover:bg-ink/10"
          title={tm("Refresh")}
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
        </button>
      </div>
      <form
        className="mb-3 flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied({ ...inputs });
        }}
      >
        <label className="flex flex-col gap-1 text-[11px] text-ink/50">
          {tm("From")}
          <input
            type="date"
            value={inputs.from}
            onChange={(e) => setInputs((s) => ({ ...s, from: e.target.value }))}
            className={field}
          />
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-ink/50">
          {tm("To")}
          <input
            type="date"
            value={inputs.to}
            onChange={(e) => setInputs((s) => ({ ...s, to: e.target.value }))}
            className={field}
          />
        </label>
        {leader && (
          <label className="flex flex-col gap-1 text-[11px] text-ink/50">
            {tm("User")}
            <input
              value={inputs.user}
              onChange={(e) => setInputs((s) => ({ ...s, user: e.target.value }))}
              maxLength={200}
              className={field}
            />
          </label>
        )}
        <label className="flex flex-col gap-1 text-[11px] text-ink/50">
          {tm("Case reference")}
          <input
            value={inputs.caseRef}
            onChange={(e) => setInputs((s) => ({ ...s, caseRef: e.target.value }))}
            maxLength={64}
            className={field}
          />
        </label>
        <button
          type="submit"
          className="inline-flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs font-medium"
          style={{ backgroundColor: ACCENT, color: ON_ACCENT }}
        >
          <Search className="h-3.5 w-3.5" />
          {tm("Search")}
        </button>
      </form>
      {error && <Notice text={tm("The query log could not be loaded.")} tone="warn" />}
      {data && !data.enabled && <Notice text={tm("The query log is off on this AI Box.")} />}
      {data && (
        <div className="mb-3 text-[11px] text-ink/45">
          {`${data.from} – ${data.to} · ${tm("Retention (days)")}: ${
            data.retention_days || tm("No time limit")
          }`}
        </div>
      )}
      {data && data.enabled && items.length === 0 && (
        <Notice text={tm("No queries in this period.")} />
      )}
      <div className="space-y-2">
        {items.map((rec) => {
          const result = rec.result ?? {};
          const sources = result.sources ?? [];
          return (
            <div
              key={`${rec.ts}-${rec.hash ?? rec.question_sha256}`}
              className="rounded-xl border border-ink/10 p-3 text-xs"
              style={{ backgroundColor: PANEL }}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-ink">{tm(queryKind(rec.kind))}</span>
                <span className="text-ink/50">{new Date(rec.ts).toLocaleString()}</span>
                {leader && <span className="text-ink/70">{rec.user}</span>}
                {rec.case_ref && (
                  <span className="rounded-full border border-ink/15 px-2 py-0.5 text-[11px] text-ink/70">
                    {`${tm("Case reference")}: ${rec.case_ref}`}
                  </span>
                )}
                {rec.legal_basis && (
                  <span className="rounded-full border border-ink/15 px-2 py-0.5 text-[11px] text-ink/70">
                    {`${tm("Legal basis")}: ${rec.legal_basis}`}
                  </span>
                )}
              </div>
              <div className="mt-1 break-words text-ink/80">
                {rec.question ?? (
                  <span className="text-ink/45">
                    {`${tm(
                      rec.sealed
                        ? "Protected query: only authorised members see its text and sources (fingerprint)"
                        : "Question text not kept (fingerprint only)",
                    )}: ${rec.question_sha256.slice(0, 12)}…`}
                  </span>
                )}
              </div>
              {sources.length > 0 && (
                <div className="mt-1 space-y-0.5 text-[11px] text-ink/50">
                  {sources.map((path) => (
                    <div key={path} className="truncate" title={path}>
                      {path}
                    </div>
                  ))}
                </div>
              )}
              {(result.hidden_sources ?? 0) > 0 && (
                <div className="mt-1 text-[11px] text-ink/45">
                  {`${tm("Documents outside your access")}: ${result.hidden_sources}`}
                </div>
              )}
              {!rec.intact && (
                <div className="mt-1 flex items-center gap-1 text-[11px] text-rose-700 dark:text-rose-300">
                  <ShieldAlert className="h-3 w-3" />
                  {tm("This day's log was altered: its hash chain is broken.")}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {data && items.length < data.total && (
        <button
          onClick={() => void load(items.length)}
          className="mt-3 w-full rounded-lg border border-ink/15 py-1.5 text-xs hover:bg-ink/10"
        >
          {tm("Load more")}
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- Processing */

function ProcessingTab({
  drive,
  canEdit,
  onOpenSource,
}: {
  drive: string;
  canEdit: boolean;
  onOpenSource: (source: GraphSource) => void;
}) {
  const { tm } = useLang();
  const { run, notice } = useFailure();
  const [status, setStatus] = useState<GraphStatus | null>(null);

  const load = useCallback(async () => {
    const res = await run(() => graphApi.status(drive));
    if (res) setStatus(res);
  }, [drive, run]);
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [load]);

  const totals = status?.totals ?? {};
  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex items-center gap-2">
        <div className="text-sm font-semibold">{tm("Processing")}</div>
        <button
          onClick={() => void load()}
          className="ml-auto rounded-full p-1.5 hover:bg-ink/10"
          title={tm("Refresh")}
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      </div>
      {notice}
      {status && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label={tm("Queued")} value={status.queued} />
            <Stat label={tm("Waiting to be handed over")} value={status.forward.pending} />
            <Stat label={tm("Done")} value={totals.done ?? 0} />
            <Stat label={tm("Failed")} value={totals.failed ?? 0} />
          </div>
          {status.busy && (
            <div className="mt-3 flex items-center gap-2 text-xs text-ink/70">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {tm("Processing now")}
              {status.running ? `: ${status.running.source_path.split("/").pop()}` : ""}
            </div>
          )}
          {status.gpu?.foreground_active ? (
            <div className="mt-2 text-xs text-ink/55">
              {tm("The AI Box is answering a question; extraction waits.")}
            </div>
          ) : null}
          <div className="mb-1.5 mt-5 text-[11px] uppercase tracking-wide text-ink/45">
            {tm("Recently processed")}
          </div>
          <div className="space-y-1">
            {status.recent
              .slice()
              .reverse()
              .map((r, i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <span className="min-w-0 flex-1 truncate" title={r.source_path}>
                    {r.source_path.split("/").pop()}
                  </span>
                  <span
                    className={
                      r.status === "done" ? "text-ink/60" : "text-amber-700 dark:text-amber-300"
                    }
                  >
                    {tm(STATUS_TEXT[r.status] ?? r.status)}
                  </span>
                </div>
              ))}
          </div>
        </>
      )}
      <ImportSection drive={drive} canEdit={canEdit} />
      <ReprocessSection drive={drive} canEdit={canEdit} />
      <UnreadableDocuments drive={drive} onOpenSource={onOpenSource} />
    </div>
  );
}

const IMPORT_MAX_BYTES = 50 * 1024 * 1024;
/** Until the box says otherwise (import/sources `formats`). */
const IMPORT_FORMATS = [".csv", ".tsv", ".txt", ".xlsx", ".xlsm"];
const RAG_STATE: Record<GraphImportRag["state"], string> = {
  none: "",
  pending: "Waiting",
  running: "Updating…",
  done: "Up to date",
  failed: "Failed",
};

function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

/** E28: a graph administrator imports one periodic export (CSV, XLSX) of a
 *  structured source into a folder. The box reads it by the profile's rules;
 *  the answer is the change it made, and (E29) how far the search copy of the
 *  folder's records got. A full export that would remove too much is refused
 *  first and can be repeated on purpose. */
function ImportSection({ drive, canEdit }: { drive: string; canEdit: boolean }) {
  const { tm, lang } = useLang();
  const code = lang.toLowerCase() as keyof GraphLabel;
  const { run: call, notice } = useFailure();
  const [sources, setSources] = useState<GraphImportSource[] | null>(null);
  const [formats, setFormats] = useState<string[]>(IMPORT_FORMATS);
  const [sourceId, setSourceId] = useState("");
  const [folder, setFolder] = useState(drive.replace(/\/+$/, ""));
  const [asOf, setAsOf] = useState(() => new Date().toISOString().slice(0, 10));
  const [mode, setMode] = useState<"" | "delta" | "full">("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refused, setRefused] = useState("");
  const [answer, setAnswer] = useState<GraphImportAnswer | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!canEdit) return;
    void call(() => graphApi.importSources(drive)).then((res) => {
      if (!res) return;
      setSources(res.sources);
      if (res.formats?.length) setFormats(res.formats);
      setSourceId((current) => current || res.sources[0]?.id || "");
    });
  }, [drive, canEdit, call]);

  // The search copy is aligned after the import; follow it until it settles.
  const ragFolder = answer?.import.source_path;
  const ragState = answer?.rag?.state;
  useEffect(() => {
    if (!ragFolder || (ragState !== "pending" && ragState !== "running")) return;
    const timer = setTimeout(async () => {
      try {
        const res = await graphApi.importRag(drive, ragFolder);
        setAnswer((current) => (current ? { ...current, rag: res.rag } : current));
      } catch {
        // The next import or a refresh shows it again.
      }
    }, 3000);
    return () => clearTimeout(timer);
  }, [drive, ragFolder, ragState]);

  if (!canEdit) return null;
  const source = sources?.find((s) => s.id === sourceId) ?? null;
  const effectiveMode = mode || source?.mode || "delta";

  async function start(force = false) {
    if (!source || !file || !folder.trim()) {
      setError(tm("Choose a source, a target folder and a file."));
      return;
    }
    if (file.size > IMPORT_MAX_BYTES) {
      setError(tm("The file is larger than 50 MB."));
      return;
    }
    setBusy(true);
    setError("");
    setRefused("");
    setAnswer(null);
    try {
      let res: GraphImportAnswer = await graphApi.importRecords(drive, {
        source: source.id,
        asOf,
        folder: folder.trim(),
        filename: file.name,
        contentB64: toBase64(await file.arrayBuffer()),
        mode: mode || undefined,
        force,
      });
      while (
        (res.import.status === "queued" || res.import.status === "running") &&
        res.import.job
      ) {
        res = await graphApi.importStatus(drive, res.import.job);
      }
      setAnswer(res);
    } catch (err) {
      if (err instanceof AdapterGraphError && err.status === 409) setRefused(err.message);
      else if (err instanceof AdapterGraphError && err.status !== 503)
        setError(`${tm("The import failed.")} ${err.message}`);
      else await call(() => Promise.reject(err));
    } finally {
      setBusy(false);
    }
  }

  const stats = answer?.import.stats;
  const rag = answer?.rag;
  const input =
    "min-w-0 rounded-lg border border-ink/15 bg-shade/30 px-2 py-1 text-xs text-ink outline-none focus:border-ink/30 disabled:opacity-50";
  return (
    <div className="mt-6">
      <div className="mb-1.5 flex items-center gap-2 text-[11px] uppercase tracking-wide text-ink/45">
        <Upload className="h-3.5 w-3.5" />
        {tm("Structured import")}
      </div>
      {notice}
      <div className="text-xs text-ink/55">
        {tm(
          "Load a register export (for example a database dump or a spreadsheet). The box reads it by the domain profile's rules, without the language model.",
        )}
      </div>
      {sources && sources.length === 0 && (
        <div className="mt-2 text-xs text-ink/55">{tm("No structured source in the profile.")}</div>
      )}
      {sources && sources.length > 0 && (
        <div
          className="mt-2 space-y-2 rounded-xl border border-ink/10 p-2.5 text-xs"
          style={{ backgroundColor: PANEL }}
        >
          <label className="flex flex-wrap items-center gap-2">
            <span className="w-28 shrink-0 text-ink/60">{tm("Source")}</span>
            <select
              value={sourceId}
              onChange={(e) => {
                setSourceId(e.target.value);
                setMode("");
              }}
              disabled={busy}
              className={`${input} flex-1`}
            >
              {sources.map((s) => (
                <option key={s.id} value={s.id} className="bg-surface-2">
                  {s.label[code] ?? s.label.en ?? s.title}
                </option>
              ))}
            </select>
          </label>
          {source && (
            <div className="pl-0 text-[11px] text-ink/50 sm:pl-30">
              <span className="text-ink/60">{tm("Expected columns")}:</span>{" "}
              {source.columns.join(", ")}
              {source.rag && (
                <div className="mt-0.5" style={{ color: ACCENT_TEXT }}>
                  {tm("Its free text also goes into the search.")}
                </div>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-start gap-2">
            <span className="w-28 shrink-0 pt-0.5 text-ink/60">{tm("Target folder")}</span>
            <div className="min-w-0 flex-1">
              <FolderPicker drive={drive} value={folder} onChange={setFolder} disabled={busy} />
              <div className="mt-0.5 text-[11px] text-ink/50">
                {tm("Whoever may see this folder sees its records too.")}
              </div>
            </div>
          </div>
          <label className="flex flex-wrap items-center gap-2">
            <span className="w-28 shrink-0 text-ink/60">{tm("Export date")}</span>
            <input
              type="date"
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
              disabled={busy}
              className={input}
            />
            <span className="text-[11px] text-ink/50">
              {tm("An older export never overwrites a newer one.")}
            </span>
          </label>
          <div className="flex flex-wrap items-start gap-2">
            <span className="w-28 shrink-0 text-ink/60">{tm("The file holds")}</span>
            <div className="min-w-0 flex-1 space-y-1.5">
              {(
                [
                  [
                    "delta",
                    "Only the changes",
                    "New and changed records, and those marked deleted. Records the file does not list stay.",
                  ],
                  [
                    "full",
                    "The whole register",
                    "Every record of the source for this folder. Records the file no longer lists are removed.",
                  ],
                ] as const
              ).map(([value, title, hint]) => (
                <label key={value} className="flex cursor-pointer items-start gap-2">
                  <input
                    type="radio"
                    name="import-mode"
                    checked={effectiveMode === value}
                    onChange={() => setMode(value)}
                    disabled={busy}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="text-ink">{tm(title)}</span>
                    {source?.mode === value && (
                      <span className="text-ink/45">{` (${tm("as in the profile")})`}</span>
                    )}
                    <span className="block text-[11px] text-ink/50">{tm(hint)}</span>
                  </span>
                </label>
              ))}
            </div>
          </div>
          <label className="flex flex-wrap items-center gap-2">
            <span className="w-28 shrink-0 text-ink/60">{tm("Export file")}</span>
            <input
              ref={fileRef}
              type="file"
              accept={formats.join(",")}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              disabled={busy}
              className="min-w-0 flex-1 text-xs text-ink/70 file:mr-2 file:rounded-md file:border-0 file:bg-ink/10 file:px-2 file:py-1 file:text-xs file:text-ink"
            />
          </label>
          <div className="pl-0 text-[11px] text-ink/50 sm:pl-30">
            {`${tm("Accepted")}: ${formats.map((f) => f.slice(1).toUpperCase()).join(", ")}`}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => void start()}
              disabled={busy || !source || !file}
              className="rounded-lg px-3 py-1 text-xs font-medium disabled:opacity-50"
              style={{ backgroundColor: ACCENT, color: ON_ACCENT }}
            >
              {tm("Import")}
            </button>
            {busy && (
              <span className="inline-flex items-center gap-1.5 text-ink/60">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                {tm("Importing…")}
              </span>
            )}
          </div>
          {error && <div className="text-[11px] text-amber-700 dark:text-amber-300">{error}</div>}
          {refused && (
            <div className="space-y-1.5 rounded-lg border border-amber-300/30 bg-amber-300/10 p-2">
              <div className="text-[11.5px] text-amber-800 dark:text-amber-100">
                {tm(
                  "This full export would remove many records of this folder. Import it anyway only if that is right.",
                )}
              </div>
              <div className="text-[11px] text-amber-800/60 dark:text-amber-100/60">{refused}</div>
              <button
                onClick={() => void start(true)}
                disabled={busy}
                className="rounded-lg border border-amber-300/40 px-3 py-1 text-xs text-amber-800 dark:text-amber-100 hover:bg-amber-300/10 disabled:opacity-50"
              >
                {tm("Import anyway")}
              </button>
            </div>
          )}
        </div>
      )}
      {stats && (
        <div className="mt-2 space-y-2">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label={tm("Rows")} value={stats.rows} />
            <Stat label={tm("Records")} value={stats.records} />
            <Stat label={tm("New")} value={stats.created} />
            <Stat label={tm("Updated")} value={stats.updated} />
            <Stat label={tm("Unchanged")} value={stats.unchanged} />
            <Stat label={tm("Removed")} value={stats.removed} />
            <Stat label={tm("Older export, skipped")} value={stats.older} />
            <Stat label={tm("Problems")} value={stats.problems} />
          </div>
          <div className="space-y-0.5 text-[11px] text-ink/60">
            {stats.protected > 0 && <div>{`${tm("Protected parts")}: ${stats.protected}`}</div>}
            {!!stats.review_new && <div>{`${tm("New review pairs")}: ${stats.review_new}`}</div>}
            {stats.duplicates > 0 && (
              <div>{`${tm("Repeated record ids")}: ${stats.duplicates}`}</div>
            )}
            {stats.acl_nobody > 0 && (
              <div className="text-amber-700 dark:text-amber-300">
                {`${tm("Records no one may see (unknown access code)")}: ${stats.acl_nobody}`}
              </div>
            )}
            {stats.missing_columns.length > 0 && (
              <div className="text-amber-700 dark:text-amber-300">
                {`${tm("Missing columns")}: ${stats.missing_columns.join(", ")}`}
              </div>
            )}
            {Object.entries(stats.unknown_codes).map(([list, codes]) => (
              <div key={list} className="text-amber-700 dark:text-amber-300">
                {`${tm("Unknown codes")} (${list}): ${codes.join(", ")}`}
              </div>
            ))}
          </div>
          {stats.problem_rows.length > 0 && (
            <div className="max-h-40 space-y-0.5 overflow-y-auto pr-1 text-[11px] text-ink/60">
              {stats.problem_rows.map((p, i) => (
                <div key={i}>{`${tm("Row")} ${p.row}: ${p.reason}`}</div>
              ))}
            </div>
          )}
          {rag && rag.state !== "none" && (
            <div
              className="flex flex-wrap items-center gap-2 rounded-xl border border-ink/10 p-2.5 text-xs"
              style={{ backgroundColor: PANEL }}
            >
              {(rag.state === "pending" || rag.state === "running") && (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              )}
              <span className="text-ink/60">{tm("Search copy")}:</span>
              <span
                className={
                  rag.state === "failed" ? "text-amber-700 dark:text-amber-300" : "text-ink"
                }
              >
                {tm(RAG_STATE[rag.state])}
              </span>
              {rag.state === "done" && (
                <span className="text-ink/50">
                  {`${tm("Written")}: ${rag.written ?? 0} · ${tm("Removed")}: ${rag.removed ?? 0} · ${tm("Unchanged")}: ${rag.unchanged ?? 0}`}
                </span>
              )}
              {rag.error && (
                <span className="text-[11px] text-amber-700 dark:text-amber-300">{rag.error}</span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const RUN_STATE: Record<GraphReprocessRun["state"], string> = {
  idle: "",
  running: "Reprocessing…",
  done: "Reprocessing finished",
  cancelled: "Reprocessing stopped",
  failed: "Reprocessing stopped with an error",
};

/** Which profile the documents were extracted with, and (graph administrators)
 *  sending through extraction again: synced files the graph never got, those
 *  read without the language model during a long queue, and those of an older
 *  profile. The files are fetched again from the ViVeSecBox, one at a time. */
function ReprocessSection({ drive, canEdit }: { drive: string; canEdit: boolean }) {
  const { tm } = useLang();
  const { run: call, notice } = useFailure();
  const [data, setData] = useState<GraphReprocessStatus | null>(null);
  const [folder, setFolder] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const res = await call(() => graphApi.reprocessStatus(drive));
    if (res) setData(res);
  }, [drive, call]);
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [load]);

  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    await call(work);
    await load();
    setBusy(false);
  }

  if (!data) return notice;
  const run = data.run;
  const running = run.state === "running";
  const versions = Object.entries(data.counts).sort(([a], [b]) => a.localeCompare(b));
  const partial = data.partial_total ?? 0;
  const older = data.stale_total - partial;
  return (
    <div className="mt-6">
      <div className="mb-1.5 flex items-center gap-2 text-[11px] uppercase tracking-wide text-ink/45">
        <Settings2 className="h-3.5 w-3.5" />
        {tm("Reprocessing")}
      </div>
      {notice}
      <div className="text-xs text-ink/70">
        {tm("Current profile")}: <span className="font-medium text-ink">{data.current}</span>
      </div>
      <div className="mt-1 text-xs text-ink/70">
        {older
          ? `${tm("Extracted with an older or unknown profile")}: ${older}`
          : tm("Every document was extracted with the current profile.")}
      </div>
      {partial > 0 && (
        <div className="mt-1 text-xs text-ink/70">
          {`${tm("Waiting for the language model")}: ${partial}`}
        </div>
      )}
      {data.absent_total > 0 && (
        <div className="mt-1 text-xs text-ink/70">
          {`${tm("Synced files not yet in the graph")}: ${data.absent_total}`}
        </div>
      )}
      {versions.length > 1 && (
        <div className="mt-1 text-[11px] text-ink/45">
          {versions.map(([v, n]) => `${v === "unknown" ? tm("unknown") : v}: ${n}`).join(" · ")}
        </div>
      )}
      {run.state !== "idle" && (
        <div
          className="mt-2 rounded-xl border border-ink/10 p-2.5 text-xs"
          style={{ backgroundColor: PANEL }}
        >
          <div className="flex items-center gap-2">
            {running && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            <span>{tm(RUN_STATE[run.state])}</span>
            <span className="ml-auto text-ink/60">
              {run.done ?? 0}/{run.total ?? 0}
            </span>
          </div>
          {running && run.current && (
            <div className="mt-1 truncate text-[11px] text-ink/50" title={run.current}>
              {run.current.split("/").pop()}
            </div>
          )}
          <div className="mt-1 text-[11px] text-ink/50">
            {`${tm("Sent")}: ${run.sent ?? 0} · ${tm("Missing")}: ${run.missing ?? 0} · ${tm("Failed")}: ${run.failed ?? 0}`}
          </div>
          {run.error && (
            <div className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">{run.error}</div>
          )}
        </div>
      )}
      {canEdit && data.can_start && (
        <div
          className="mt-2 space-y-2 rounded-xl border border-ink/10 p-2.5 text-xs"
          style={{ backgroundColor: PANEL }}
        >
          <div className="text-ink/70">
            {data.stale_total || data.absent_total
              ? `${tm("To reprocess")}: ${data.stale_total + data.absent_total} · ${tm(
                  "Reprocessing reads them again with the current profile and the language model, one at a time, without holding up the questions.",
                )}`
              : tm("Nothing to reprocess.")}
          </div>
          <div className="flex flex-wrap items-start gap-2">
            <span className="w-28 shrink-0 pt-0.5 text-ink/60">{tm("Limit to")}</span>
            <FolderPicker
              drive={drive}
              value={folder}
              onChange={setFolder}
              allowAll
              disabled={running}
            />
          </div>
          <div>
            {running ? (
              <button
                onClick={() => void act(() => graphApi.reprocessCancel(drive))}
                disabled={busy}
                className="rounded-lg border border-ink/15 px-3 py-1 text-xs transition hover:bg-ink/10 disabled:opacity-50"
              >
                {tm("Stop")}
              </button>
            ) : (
              <button
                onClick={() => void act(() => graphApi.reprocessStart(drive, folder.trim()))}
                disabled={busy || !(data.stale_total || data.absent_total) || !data.source_ready}
                className="rounded-lg px-3 py-1 text-xs font-medium text-white disabled:opacity-50"
                style={{ backgroundColor: ACCENT }}
              >
                {tm("Reprocess")}
              </button>
            )}
          </div>
        </div>
      )}
      {canEdit && data.can_start && !data.source_ready && (
        <div className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">
          {tm("The ViVeSecBox is not connected: the files cannot be fetched again.")}
        </div>
      )}
    </div>
  );
}

/** E10b: why a document of this scope is not in the search -- grouped by
 *  reason, each with what it means and what can be done. On a standalone box a
 *  store writer reads the files again (OCR included) or deletes them here. */
const SKIP_REASON: Record<string, string> = {
  extraction_empty: "No text layer (scanned image)",
  ocr_pending: "Waiting for OCR",
  ocr_interrupted: "OCR interrupted",
  ocr_deferred: "OCR deferred",
  ocr_failed: "OCR failed",
  ocr_no_text: "No text recognised",
  ocr_pages_skipped: "Some pages could not be read",
  unsupported_type: "Unsupported format",
  empty_content: "Empty file",
  too_large: "Too large",
  invalid_content: "Invalid content",
  extractor_unavailable: "No reader for this format on the AI Box",
};
/** What a reason means and what to do about it. */
const SKIP_HELP: Record<string, string> = {
  extraction_empty: "The file holds only images of text. Reading it again runs OCR on its pages.",
  ocr_pending: "OCR is reading it now or soon; nothing to do.",
  ocr_interrupted: "A restart stopped the OCR. Reading it again finishes it.",
  ocr_deferred: "The OCR queue was full when it arrived. Reading it again queues it now.",
  ocr_failed: "The OCR stopped with an error. Read it again; if it fails again, replace the file.",
  ocr_no_text:
    "OCR found no readable text (a blank or very poor scan). Replace it with a better scan.",
  ocr_pages_skipped:
    "Some pages were too poor to read; the rest is searchable. A better scan fixes it.",
  unsupported_type: "The search cannot read this file type. Save it as PDF, DOCX or text.",
  empty_content: "The file is empty. Delete it or upload it again.",
  too_large: "The file is larger than the search reads. Split it into smaller files.",
  invalid_content: "The content does not match the file type or is damaged. Upload a good copy.",
  extractor_unavailable: "The AI Box has no reader for this format installed.",
};
/** Reasons a fresh reading of the stored file can fix. */
const REREADABLE = new Set(["extraction_empty", "ocr_interrupted", "ocr_deferred", "ocr_failed"]);
const REREAD_STATE: Record<string, string> = {
  queued: "Waiting to be read again",
  running: "Being read again…",
  done: "Reread finished",
  failed: "Reading again failed",
};
const PAGE_REASON: Record<string, string> = {
  low_quality: "low quality",
  no_text: "no text",
  failed: "failed",
};
const GROUP_SHOWN = 20;

function UnreadableDocuments({
  drive,
  onOpenSource,
}: {
  drive: string;
  onOpenSource: (source: GraphSource) => void;
}) {
  const { tm } = useLang();
  const [data, setData] = useState<IndexSkipped | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<Record<string, number>>({});
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await graphApi.skipped(drive));
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [drive]);
  const rereading = data?.rereading ?? 0;
  useEffect(() => {
    void load();
    // Faster while rereads are running, so their outcome shows up.
    const timer = setInterval(() => void load(), rereading ? 5000 : 30000);
    return () => clearInterval(timer);
  }, [load, rereading]);

  async function act(work: () => Promise<unknown>, done?: (out: unknown) => string) {
    setBusy(true);
    setNote("");
    try {
      const out = await work();
      if (done) setNote(done(out));
    } catch (err) {
      setNote(err instanceof Error ? err.message : tm("The action failed."));
    } finally {
      setBusy(false);
      setConfirm(null);
      await load();
    }
  }

  function reread(target: { paths: string[] } | { reason: string }) {
    void act(
      () => graphApi.reread(drive, target),
      (out) => {
        const res = out as { queued: number; skipped: number };
        return `${tm("Queued to be read again")}: ${res.queued}${
          res.skipped ? ` · ${tm("Skipped")}: ${res.skipped}` : ""
        }`;
      },
    );
  }

  const canReread = Boolean(data?.can_reread);
  const canDelete = Boolean(data?.can_delete);
  const groups = Object.entries(data?.reasons ?? {}).sort((a, b) => b[1] - a[1]);
  const small =
    "rounded-md border border-ink/15 px-2 py-0.5 text-[11px] transition hover:bg-ink/10 disabled:opacity-50";
  return (
    <div className="mt-6">
      <div className="mb-1.5 flex items-center gap-2 text-[11px] uppercase tracking-wide text-ink/45">
        <ShieldAlert className="h-3.5 w-3.5" />
        {tm("Not readable for search")}
        {data && <span className="normal-case tracking-normal text-ink/60">({data.count})</span>}
      </div>
      {failed && <Notice text={tm("The list could not be loaded.")} tone="warn" />}
      {data && data.count === 0 && (
        <div className="text-xs text-ink/55">{tm("Every document in this scope is readable.")}</div>
      )}
      {data && data.count > 0 && (
        <div className="mb-2 text-xs text-ink/55">
          {tm(
            canReread
              ? "These documents are not in the search, or only in part. Each group says why and what to do."
              : "These documents are not in the search, or only in part. Each group says why; a scan is read again after it is synced again from the ViVeSecBox.",
          )}
        </div>
      )}
      {note && (
        <div className="mb-2 text-xs" style={{ color: ACCENT_TEXT }}>
          {note}
        </div>
      )}
      <div className="space-y-2">
        {groups.map(([reason, n]) => {
          const docs = (data?.documents ?? []).filter((d) => d.reason === reason);
          const shown = open[reason] ?? 0;
          const fixable = canReread && REREADABLE.has(reason);
          // Indexed by an earlier sync, not held on the box: no reread, no download.
          const stored = docs.filter((d) => d.stored).length;
          const notHeld = docs.some((d) => d.stored === false);
          return (
            <div
              key={reason}
              className="rounded-xl border border-ink/10 p-2.5 text-xs"
              style={{ backgroundColor: PANEL }}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-amber-700/90 dark:text-amber-300/90">
                  {tm(SKIP_REASON[reason] ?? reason)}
                </span>
                <span className="text-ink/50">{n}</span>
                <span className="ml-auto flex flex-wrap gap-1.5">
                  {fixable && stored > 0 && (
                    <button
                      onClick={() => reread({ reason })}
                      disabled={busy}
                      className={small}
                      style={{ color: ACCENT_TEXT }}
                    >
                      {`${tm("Read all again")} (${stored})`}
                    </button>
                  )}
                  {docs.length > 0 && (
                    <button
                      onClick={() => setOpen((o) => ({ ...o, [reason]: shown ? 0 : GROUP_SHOWN }))}
                      className={small}
                    >
                      {tm(shown ? "Hide the documents" : "Show the documents")}
                    </button>
                  )}
                </span>
              </div>
              {SKIP_HELP[reason] && (
                <div className="mt-1 text-[11px] text-ink/55">{tm(SKIP_HELP[reason])}</div>
              )}
              {canReread && notHeld && (
                <div className="mt-1 text-[11px] text-ink/55">
                  {tm(
                    "Some of these files came from an earlier sync and are not held on the AI Box: upload them again to read them.",
                  )}
                </div>
              )}
              {shown > 0 && (
                <div className="mt-2 space-y-1.5">
                  {docs.slice(0, shown).map((d) => {
                    const name = d.source_path.split("/").pop() ?? d.source_path;
                    return (
                      <div key={d.source_path} className="border-t border-ink/5 pt-1.5">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="min-w-0 flex-1 truncate" title={d.source_path}>
                            {name}
                          </span>
                          {d.reread && (
                            <span
                              className={
                                d.reread.state === "failed"
                                  ? "text-amber-700 dark:text-amber-300"
                                  : "text-ink/55"
                              }
                              title={d.reread.error ?? undefined}
                            >
                              {tm(REREAD_STATE[d.reread.state] ?? d.reread.state)}
                            </span>
                          )}
                          {d.stored !== false && (
                            <button
                              onClick={() =>
                                onOpenSource({
                                  path: d.source_path,
                                  page: 0,
                                  quote: "",
                                  label: name,
                                })
                              }
                              className={small}
                            >
                              {tm("Open")}
                            </button>
                          )}
                          {fixable && d.stored && (
                            <button
                              onClick={() => reread({ paths: [d.source_path] })}
                              disabled={
                                busy ||
                                d.reread?.state === "queued" ||
                                d.reread?.state === "running"
                              }
                              className={small}
                            >
                              {tm("Read again")}
                            </button>
                          )}
                          {canDelete &&
                            (confirm === d.source_path ? (
                              <span className="inline-flex items-center gap-1.5">
                                <span className="text-ink/60">{tm("Delete the file?")}</span>
                                <button
                                  onClick={() =>
                                    void act(
                                      () => graphApi.deleteStored(drive, d.source_path),
                                      () => `${tm("Deleted")}: ${name}`,
                                    )
                                  }
                                  disabled={busy}
                                  className="rounded-md bg-rose-600 px-2 py-0.5 text-[11px] text-white disabled:opacity-50"
                                >
                                  {tm("Delete")}
                                </button>
                                <button onClick={() => setConfirm(null)} className={small}>
                                  {tm("Cancel")}
                                </button>
                              </span>
                            ) : (
                              <button
                                onClick={() => setConfirm(d.source_path)}
                                disabled={busy}
                                className={small}
                              >
                                {tm("Delete")}
                              </button>
                            ))}
                        </div>
                        {!!d.skipped_pages?.length && (
                          <div className="pl-2 text-[11px] text-ink/50">
                            {tm("Pages not read")}:{" "}
                            {d.skipped_pages
                              .map(
                                (p) =>
                                  `${p.page} (${tm(PAGE_REASON[p.reason] ?? p.reason)}${p.quality != null ? ` ${p.quality}%` : ""})`,
                              )
                              .join(", ")}
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {docs.length > shown && (
                    <button
                      onClick={() => setOpen((o) => ({ ...o, [reason]: shown + GROUP_SHOWN * 5 }))}
                      className={small}
                    >
                      {`${tm("Show more")} (${docs.length - shown})`}
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {data?.truncated && (
        <div className="mt-1 text-[11px] text-ink/45">
          {tm("Only the first documents are listed; the counts cover all.")}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-ink/10 p-3" style={{ backgroundColor: PANEL }}>
      <div className="text-lg font-semibold">{value}</div>
      <div className="text-[11px] text-ink/50">{label}</div>
    </div>
  );
}

/* ---------------------------------------------------------------- Profile */

function ProfileTab({
  drive,
  profile,
  canEdit,
  code,
  onSaved,
}: {
  drive: string;
  profile: GraphProfile | null;
  canEdit: boolean;
  code: keyof GraphLabel;
  onSaved: () => Promise<void>;
}) {
  const { tm } = useLang();
  const { run, notice } = useFailure();
  const [draft, setDraft] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  if (!profile) return notice ?? null;
  const label = (l: GraphLabel) => l[code] ?? l.en ?? "";

  async function save() {
    let parsed: unknown;
    try {
      parsed = JSON.parse(draft ?? "");
    } catch {
      setMessage(tm("Invalid JSON."));
      return;
    }
    const res = await run(() => graphApi.saveProfile(drive, parsed));
    if (res) {
      setDraft(null);
      setMessage(
        tm("Saved. Documents are re-read with the new rules when they are processed again."),
      );
      await onSaved();
    }
  }

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="text-sm font-semibold">{label(profile.name)}</div>
      <div className="text-[11px] text-ink/50">
        {profile.id} · {tm("Version")} {profile.version}
      </div>
      {notice}
      {message && <Notice text={message} />}
      <Section title={tm("Entity kinds")}>
        <div className="flex flex-wrap gap-1.5">
          {profile.entity_subtypes.map((s) => (
            <span key={s.id} className="rounded-full border border-ink/10 px-2 py-0.5 text-[11px]">
              <span
                className="mr-1 inline-block h-2 w-2 rounded-full"
                style={{ backgroundColor: TYPE_COLOR[s.type] }}
              />
              {label(s.label)}
            </span>
          ))}
        </div>
      </Section>
      <Section title={tm("Relation types")}>
        <div className="space-y-1">
          {profile.relation_types.map((r) => (
            <div key={r.id} className="flex gap-2 text-xs">
              <span className="w-40 shrink-0 font-medium">{label(r.label)}</span>
              <span className="text-ink/50">
                {r.from.map((t) => tm(TYPE_NAME[t])).join("/")} →{" "}
                {r.to.map((t) => tm(TYPE_NAME[t])).join("/")}
              </span>
            </div>
          ))}
        </div>
      </Section>
      {canEdit ? (
        <>
          <Section title={tm("Suggestions from the documents")}>
            <ProfileSuggestions
              drive={drive}
              profile={profile}
              code={code}
              onSaved={async (text) => {
                setMessage(text);
                await onSaved();
              }}
            />
          </Section>
          <Section title={tm("Edit profile (JSON)")}>
            {draft === null ? (
              <button
                onClick={() => {
                  setMessage(null);
                  setDraft(JSON.stringify({ ...profile, version: profile.version + 1 }, null, 2));
                }}
                className="rounded-full border border-ink/15 px-3 py-1 text-xs hover:bg-ink/10"
              >
                {tm("Edit profile (JSON)")}
              </button>
            ) : (
              <>
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  spellCheck={false}
                  className="h-80 w-full rounded-lg border border-ink/10 bg-shade/30 p-2 font-mono text-[11px] outline-none"
                />
                <div className="mt-2 flex gap-2">
                  <button
                    onClick={() => void save()}
                    className="rounded-full px-3 py-1 text-xs text-white"
                    style={{ backgroundColor: ACCENT }}
                  >
                    {tm("Save as new version")}
                  </button>
                  <button
                    onClick={() => setDraft(null)}
                    className="rounded-full border border-ink/15 px-3 py-1 text-xs hover:bg-ink/10"
                  >
                    {tm("Cancel")}
                  </button>
                </div>
              </>
            )}
          </Section>
        </>
      ) : (
        <Notice text={tm("Only graph administrators can change the profile.")} />
      )}
    </div>
  );
}
