import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  CalendarDays,
  Download,
  ExternalLink,
  FileText,
  GitMerge,
  Loader2,
  Network,
  RefreshCw,
  Route,
  Search,
  Settings2,
  ShieldAlert,
  Split,
  X,
} from "lucide-react";
import { useLang } from "./i18n";
import { layoutGraph } from "./graph-layout";
import { saveToDrive } from "@/lib/api/rag.functions";
import {
  graphApi,
  graphFailure,
  type GraphDocuments,
  type GraphEdge,
  type GraphEntityType,
  type GraphEvidence,
  type GraphFailure,
  type GraphLabel,
  type GraphNode,
  type GraphProfile,
  type GraphReviewDecision,
  type GraphReviewItem,
  type GraphReviewList,
  type GraphStatus,
  type GraphTimelineItem,
  type GraphReprocessRun,
  type GraphReprocessStatus,
  type IndexSkipped,
} from "@/lib/api/graph.functions";

const LIME = "#C6F24E";
const PANEL = "#23272B";
const BG = "#1E2225";

const TYPE_COLOR: Record<GraphEntityType, string> = {
  person: "#C6F24E",
  organization: "#60A5FA",
  place: "#F59E0B",
  identifier: "#C084FC",
  event: "#FB7185",
  object: "#2DD4BF",
};
const TYPE_NAME: Record<GraphEntityType, string> = {
  person: "Person",
  organization: "Organisation",
  place: "Place",
  identifier: "Identifier",
  event: "Event",
  object: "Object",
};
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

type Tab = "explore" | "review" | "processing" | "profile";
type Graph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  documents: GraphDocuments;
  truncated?: boolean;
};

const pairKey = (e: GraphEdge) => (e.a < e.b ? `${e.a}|${e.b}` : `${e.b}|${e.a}`);
const byDate = (x: GraphEdge, y: GraphEdge) => (x.start ?? "").localeCompare(y.start ?? "");

export type GraphSource = { path: string; page: number; quote: string; label: string };

export function GraphPanel({
  drive,
  onClose,
  onOpenSource,
}: {
  drive: string;
  onClose: () => void;
  onOpenSource: (source: GraphSource) => void;
}) {
  const { tm, lang } = useLang();
  const code = lang.toLowerCase() as keyof GraphLabel;
  const [tab, setTab] = useState<Tab>("explore");
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

  const tabs: { id: Tab; label: string; icon: typeof Network }[] = [
    { id: "explore", label: tm("Explore"), icon: Network },
    { id: "review", label: tm("Review"), icon: GitMerge },
    { id: "processing", label: tm("Processing"), icon: RefreshCw },
    { id: "profile", label: tm("Domain profile"), icon: Settings2 },
  ];

  return (
    <div
      className="absolute inset-0 z-[60] flex flex-col text-[#E6EAEE]"
      style={{ backgroundColor: BG }}
    >
      <div className="flex shrink-0 items-center gap-2 border-b border-white/5 px-4 py-2">
        <Network className="h-4 w-4" style={{ color: LIME }} />
        <div className="text-sm font-semibold">{tm("Relationships")}</div>
        <div className="ml-4 flex gap-1">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs transition ${
                tab === id ? "text-black" : "text-white/70 hover:bg-white/10"
              }`}
              style={tab === id ? { backgroundColor: LIME } : undefined}
            >
              <Icon className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">{label}</span>
            </button>
          ))}
        </div>
        <button
          onClick={onClose}
          className="ml-auto rounded-full p-1.5 text-white/60 hover:bg-white/10"
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
              relLabel={relLabel}
              subtypeLabel={subtypeLabel}
              onOpenSource={onOpenSource}
            />
          )}
          {tab === "review" && <ReviewTab drive={drive} canEdit={canEdit} />}
          {tab === "processing" && <ProcessingTab drive={drive} canEdit={canEdit} />}
          {tab === "profile" && (
            <ProfileTab
              drive={drive}
              profile={profile}
              canEdit={canEdit}
              code={code}
              onSaved={loadProfile}
            />
          )}
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
          ? "border-rose-400/40 bg-rose-400/10 text-rose-100"
          : "border-white/10 bg-white/5 text-white/60"
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
  relLabel,
  subtypeLabel,
  onOpenSource,
}: {
  drive: string;
  relLabel: (type: string) => string;
  subtypeLabel: (sub: string) => string;
  onOpenSource: (source: GraphSource) => void;
}) {
  const { tm } = useLang();
  const { run, notice } = useFailure();
  const [text, setText] = useState("");
  const [results, setResults] = useState<GraphNode[] | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [hops, setHops] = useState(1);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [pathMode, setPathMode] = useState(false);
  const [pathNote, setPathNote] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
        setFocus(id);
        setGraph(res);
        setSelectedEdge(null);
        setPathNote(null);
      }
    },
    [drive, run],
  );

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
    setGraph({ nodes: res.path.nodes, edges: res.path.edges, documents: res.documents });
    setSelectedEdge(res.path.edges[0] ? pairKey(res.path.edges[0]) : null);
    setPathNote(null);
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

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-2 border-b border-white/5 px-4 py-2">
          <div className="flex min-w-[220px] flex-1 items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1.5">
            <Search className="h-3.5 w-3.5 text-white/50" />
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void search(pathMode ? "path" : "focus")}
              placeholder={tm("Search people, organisations, places, numbers…")}
              className="w-full bg-transparent text-xs outline-none placeholder:text-white/40"
            />
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin text-white/50" />}
          </div>
          <select
            value={hops}
            onChange={(e) => {
              const depth = Number(e.target.value);
              setHops(depth);
              if (focus) void showNeighbourhood(focus, depth);
            }}
            className="rounded-full border border-white/10 bg-[#23272B] px-2 py-1.5 text-xs"
          >
            <option value={1}>{tm("Direct relations")}</option>
            <option value={2}>{tm("Two steps")}</option>
          </select>
          <button
            disabled={!focus || !text.trim()}
            onClick={() => void search("path")}
            title={tm("Find the shortest chain of relations to another entity")}
            className="inline-flex items-center gap-1.5 rounded-full border border-white/10 px-3 py-1.5 text-xs text-white/80 hover:bg-white/10 disabled:opacity-40"
          >
            <Route className="h-3.5 w-3.5" /> {tm("Connection to…")}
          </button>
        </div>
        {notice}
        {pathNote && <Notice text={pathNote} />}
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
              relLabel={relLabel}
              onNode={(id) => void showNeighbourhood(id, hops)}
              onEdge={setSelectedEdge}
            />
          ) : (
            !results && <Notice text={tm("Search for a name to start.")} />
          )}
          {graph?.truncated && (
            <div className="absolute bottom-2 left-3 text-[11px] text-white/45">
              {tm("Showing the first 150 entities.")}
            </div>
          )}
        </div>
      </div>
      <aside
        className="hidden w-[360px] shrink-0 flex-col overflow-y-auto border-l border-white/5 md:flex"
        style={{ backgroundColor: PANEL }}
      >
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
            onEdge={setSelectedEdge}
            onOpenSource={onOpenSource}
          />
        ) : (
          <Notice text={tm("Click a node to focus it, a line to see its evidence.")} />
        )}
      </aside>
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
      className="absolute left-4 top-3 z-10 max-h-[70%] w-[min(420px,90%)] overflow-y-auto rounded-xl border border-white/10 shadow-xl"
      style={{ backgroundColor: PANEL }}
    >
      <div className="flex items-center justify-between px-3 py-2 text-[11px] text-white/50">
        <span>
          {results.length} {tm("results")}
        </span>
        <button onClick={onClose} className="rounded p-0.5 hover:bg-white/10">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      {results.length === 0 && (
        <div className="px-3 pb-3 text-xs text-white/60">
          {tm("No match in the documents you may see.")}
        </div>
      )}
      {results.map((n) => (
        <button
          key={n.id}
          onClick={() => onPick(n)}
          className="flex w-full items-center gap-2 border-t border-white/5 px-3 py-2 text-left text-xs hover:bg-white/5"
        >
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-full"
            style={{ backgroundColor: TYPE_COLOR[n.type] }}
          />
          <span className="min-w-0 flex-1 truncate">{n.label}</span>
          <span className="shrink-0 text-[10px] text-white/45">
            {subtypeLabel(n.subtype) || tm(TYPE_NAME[n.type])} · {n.documents.length}{" "}
            {tm("documents")}
          </span>
        </button>
      ))}
    </div>
  );
}

function GraphCanvas({
  graph,
  focus,
  selectedEdge,
  relLabel,
  onNode,
  onEdge,
}: {
  graph: Graph;
  focus: string | null;
  selectedEdge: string | null;
  relLabel: (type: string) => string;
  onNode: (id: string) => void;
  onEdge: (id: string) => void;
}) {
  const nodes = useMemo(() => graph.nodes.filter(Boolean), [graph]);
  const layout = useMemo(
    () =>
      layoutGraph(
        nodes.map((n) => n.id),
        graph.edges,
        focus,
      ),
    [nodes, graph.edges, focus],
  );
  const [view, setView] = useState({ k: 1, x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  useEffect(() => setView({ k: 1, x: 0, y: 0 }), [graph]);

  const bounds = useMemo(() => {
    const pts = [...layout.values()];
    const xs = pts.map((p) => p.x);
    const ys = pts.map((p) => p.y);
    const pad = 60;
    const minX = Math.min(...xs, 0) - pad;
    const minY = Math.min(...ys, 0) - pad;
    return { minX, minY, w: Math.max(...xs, 0) + pad - minX, h: Math.max(...ys, 0) + pad - minY };
  }, [layout]);

  const pairs = useMemo(() => {
    const out = new Map<string, GraphEdge[]>();
    for (const e of graph.edges) out.set(pairKey(e), [...(out.get(pairKey(e)) ?? []), e]);
    return [...out.entries()];
  }, [graph.edges]);
  const showLabels = pairs.length <= 40;
  const maxMentions = Math.max(1, ...nodes.map((n) => n.mentions));
  const pathIds = new Set<string>();

  return (
    <svg
      className="h-full w-full cursor-grab active:cursor-grabbing"
      viewBox={`${bounds.minX} ${bounds.minY} ${bounds.w} ${bounds.h}`}
      onWheel={(e) => {
        const k = Math.min(4, Math.max(0.3, view.k * (e.deltaY < 0 ? 1.12 : 0.89)));
        setView((v) => ({ ...v, k }));
      }}
      onPointerDown={(e) => {
        drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d) return;
        const scale = bounds.w / (e.currentTarget.clientWidth || 1) / view.k;
        setView((v) => ({
          ...v,
          x: d.vx + (e.clientX - d.x) * scale,
          y: d.vy + (e.clientY - d.y) * scale,
        }));
      }}
      onPointerUp={() => (drag.current = null)}
      onPointerLeave={() => (drag.current = null)}
    >
      <g
        transform={`translate(${view.x} ${view.y}) translate(${bounds.minX + bounds.w / 2} ${
          bounds.minY + bounds.h / 2
        }) scale(${view.k}) translate(${-(bounds.minX + bounds.w / 2)} ${-(bounds.minY + bounds.h / 2)})`}
      >
        {pairs.map(([key, edges]) => {
          const e = edges[0];
          const a = layout.get(e.a);
          const b = layout.get(e.b);
          if (!a || !b) return null;
          const active = key === selectedEdge;
          if (active) pathIds.add(e.a).add(e.b);
          const counts = new Map<string, number>();
          for (const x of edges) counts.set(x.type, (counts.get(x.type) ?? 0) + 1);
          const label = [...counts.entries()]
            .map(([type, n]) => (n > 1 ? `${relLabel(type)} ×${n}` : relLabel(type)))
            .join(", ");
          // Labels sit away from the focused node, where every line starts.
          const t = e.a === focus ? 0.62 : e.b === focus ? 0.38 : 0.5;
          const lx = a.x + (b.x - a.x) * t;
          const ly = a.y + (b.y - a.y) * t;
          return (
            <g key={key} onClick={() => onEdge(key)} className="cursor-pointer">
              <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="transparent" strokeWidth={10} />
              <line
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={active ? LIME : "rgba(255,255,255,0.28)"}
                strokeWidth={(active ? 2.4 : 1.2) + Math.min(edges.length - 1, 4) * 0.5}
                strokeDasharray={edges.every((x) => x.weak) ? "4 4" : undefined}
              />
              {(showLabels || active) && (
                <text
                  x={lx}
                  y={ly - 3}
                  textAnchor="middle"
                  fontSize={9}
                  fill={active ? LIME : "rgba(255,255,255,0.55)"}
                >
                  {label}
                  {edges.length === 1 && e.start ? ` · ${e.start}` : ""}
                </text>
              )}
            </g>
          );
        })}
        {nodes.map((n) => {
          const p = layout.get(n.id);
          if (!p) return null;
          const r = 6 + 8 * Math.sqrt(n.mentions / maxMentions);
          const isFocus = n.id === focus;
          return (
            <g key={n.id} onClick={() => onNode(n.id)} className="cursor-pointer">
              <circle
                cx={p.x}
                cy={p.y}
                r={r}
                fill={TYPE_COLOR[n.type]}
                fillOpacity={isFocus || pathIds.has(n.id) ? 1 : 0.8}
                stroke={isFocus ? "#fff" : "rgba(0,0,0,0.4)"}
                strokeWidth={isFocus ? 2.5 : 1}
              />
              <text x={p.x} y={p.y + r + 11} textAnchor="middle" fontSize={10} fill="#E6EAEE">
                {n.label.length > 28 ? `${n.label.slice(0, 27)}…` : n.label}
              </text>
            </g>
          );
        })}
      </g>
    </svg>
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
          <div key={i} className="rounded-lg border border-white/10 bg-black/20 p-2 text-xs">
            {ev.quote && <div className="italic text-white/80">„{ev.quote}”</div>}
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
                style={{ color: LIME }}
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
        className="mb-3 inline-flex items-center gap-1 text-[11px] text-white/60 hover:text-white"
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
            <div className="text-xs font-medium" style={{ color: LIME }}>
              {relLabel(edge.type)}
              <span className="ml-2 text-[11px] font-normal text-white/50">
                {edge.start ?? ""}
                {edge.end && edge.end !== edge.start ? ` – ${edge.end}` : ""}
                {edge.raw_label && edge.raw_label !== edge.type ? ` · ${edge.raw_label}` : ""}
              </span>
            </div>
            <div className="mb-1.5 mt-1 text-[11px] uppercase tracking-wide text-white/45">
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
  onEdge,
  onOpenSource,
}: {
  drive: string;
  node: GraphNode;
  graph: Graph;
  relLabel: (type: string) => string;
  subtypeLabel: (sub: string) => string;
  onEdge: (id: string) => void;
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
      <div className="mt-0.5 text-[11px] text-white/50">
        {subtypeLabel(node.subtype) || tm(TYPE_NAME[node.type])} · {node.mentions} {tm("mentions")}{" "}
        · {node.documents.length} {tm("documents")}
      </div>
      {node.aliases.length > 1 && (
        <Section title={tm("Also known as")}>
          <div className="text-xs text-white/75">{node.aliases.join(" · ")}</div>
        </Section>
      )}
      {node.attributes.length > 0 && (
        <Section title={tm("Attributes")}>
          {node.attributes.map((a, i) => (
            <div key={i} className="flex justify-between gap-2 text-xs">
              <span className="text-white/55">{tm(a.name)}</span>
              <span>{a.value}</span>
            </div>
          ))}
        </Section>
      )}
      <DossierActions drive={drive} node={node} />
      <Section title={tm("Relations")}>
        {edges.map((e) => (
          <button
            key={e.id}
            onClick={() => onEdge(pairKey(e))}
            className="flex w-full items-center gap-2 rounded px-1 py-1 text-left text-xs hover:bg-white/5"
          >
            <span className="shrink-0 text-white/55">{relLabel(e.type)}</span>
            <span className="min-w-0 flex-1 truncate">{other(e)?.label ?? ""}</span>
            {e.start && <span className="shrink-0 text-[10px] text-white/45">{e.start}</span>}
          </button>
        ))}
      </Section>
      <Section title={tm("Timeline")}>
        {notice}
        {!timeline ? (
          <button
            onClick={async () => {
              const res = await run(() => graphApi.timeline(drive, node.id));
              if (res) setTimeline({ items: res.timeline, documents: res.documents });
            }}
            className="inline-flex items-center gap-1.5 rounded-full border border-white/10 px-3 py-1 text-xs hover:bg-white/10"
          >
            <CalendarDays className="h-3.5 w-3.5" /> {tm("Timeline")}
          </button>
        ) : timeline.items.length === 0 ? (
          <div className="text-xs text-white/55">{tm("No dated relations.")}</div>
        ) : (
          <div className="space-y-2">
            {timeline.items.map((item, i) => (
              <div key={i} className="border-l-2 pl-2" style={{ borderColor: `${LIME}88` }}>
                <div className="text-[11px] text-white/50">
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
    "inline-flex items-center gap-1.5 rounded-full border border-white/10 px-3 py-1 text-xs hover:bg-white/10 disabled:opacity-40";
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
      {note && <div className="mt-1.5 text-[11px] text-white/60">{note}</div>}
    </Section>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-4">
      <div className="mb-1.5 text-[11px] uppercase tracking-wide text-white/45">{title}</div>
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

function ReviewTab({ drive, canEdit }: { drive: string; canEdit: boolean }) {
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
              view === v ? "text-black" : "text-white/70 hover:bg-white/10"
            }`}
            style={view === v ? { backgroundColor: LIME } : undefined}
          >
            {tm(v === "open" ? "Open pairs" : "Decisions")}
          </button>
        ))}
      </div>
      {view === "open" ? (
        <OpenReview drive={drive} canEdit={canEdit} />
      ) : (
        <DecidedReview drive={drive} />
      )}
    </div>
  );
}

function OpenReview({ drive, canEdit }: { drive: string; canEdit: boolean }) {
  const { tm } = useLang();
  const reason = useReasonText();
  const { run, notice } = useFailure();
  const [list, setList] = useState<GraphReviewList | null>(null);
  const [items, setItems] = useState<GraphReviewItem[]>([]);
  const [more, setMore] = useState(false);
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [why, setWhy] = useState("");
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
    [drive, run, applied, why],
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
        <span className="text-xs text-white/50">({list?.open_total ?? 0})</span>
        <button
          onClick={() => void load()}
          className="ml-auto rounded-full p-1.5 hover:bg-white/10"
          title={tm("Refresh")}
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      </div>
      {notice}
      {!canEdit && <Notice text={tm("Only graph administrators may do this.")} />}
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-white/40" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={tm("Filter by name…")}
            className="w-full rounded-lg border border-white/15 bg-black/30 py-1 pl-7 pr-2 text-xs text-white outline-none focus:border-white/30"
          />
        </div>
        <select
          value={why}
          onChange={(e) => setWhy(e.target.value)}
          className="rounded-lg border border-white/15 bg-black/30 px-2 py-1 text-xs text-white outline-none"
        >
          <option value="">{`${tm("All reasons")} (${list?.open_total ?? 0})`}</option>
          {reasons.map(([r, n]) => (
            <option key={r} value={r}>{`${reason(r)} (${n})`}</option>
          ))}
        </select>
      </div>
      {canEdit && pending.length > 0 && (
        <div
          className="mb-2 flex flex-wrap items-center gap-2 rounded-xl border border-white/10 px-3 py-2 text-xs"
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
          <span className="text-white/50">{`${tm("Selected")}: ${selected.size}`}</span>
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
                className="rounded-full px-3 py-1 text-black"
                style={{ backgroundColor: LIME }}
              >
                {tm("Confirm")}
              </button>
              <button
                onClick={() => setConfirm(null)}
                className="rounded-full border border-white/15 px-3 py-1 hover:bg-white/10"
              >
                {tm("Cancel")}
              </button>
            </span>
          ) : (
            <span className="ml-auto flex gap-2">
              <button
                onClick={() => setConfirm("merge")}
                disabled={!selected.size || busy}
                className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-black disabled:opacity-50"
                style={{ backgroundColor: LIME }}
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
                className="inline-flex items-center gap-1.5 rounded-full border border-white/15 px-3 py-1 hover:bg-white/10 disabled:opacity-50"
              >
                <Split className="h-3.5 w-3.5" /> {tm("Keep apart")}
              </button>
            </span>
          )}
        </div>
      )}
      {result && (
        <div className="mb-2 text-xs" style={{ color: LIME }}>
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
              className="rounded-xl border border-white/10 p-3"
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
                <span className="text-white/40">↔</span>
                <span className="font-medium">{item.b_label}</span>
                <span className="ml-auto text-[11px] text-white/50">
                  {reason(item.reason)} · {Math.round(item.score * 100)}%
                </span>
              </div>
              {state ? (
                <div className="mt-2 flex items-center gap-3 text-xs" style={{ color: LIME }}>
                  {tm(state === "merge" ? "Merged." : "Kept apart.")}
                  {canEdit && (
                    <button
                      onClick={() => void undo(item)}
                      className="text-white/70 underline hover:text-white"
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
                      className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs text-black"
                      style={{ backgroundColor: LIME }}
                    >
                      <GitMerge className="h-3.5 w-3.5" /> {tm("Merge")}
                    </button>
                    <button
                      onClick={() => void decide(item, "separate")}
                      className="inline-flex items-center gap-1.5 rounded-full border border-white/15 px-3 py-1 text-xs hover:bg-white/10"
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
          className="mt-3 w-full rounded-lg border border-white/15 py-1.5 text-xs hover:bg-white/10"
        >
          {tm("Load more")}
        </button>
      )}
    </>
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
        <span className="text-xs text-white/50">({total})</span>
        <button
          onClick={() => void load()}
          className="ml-auto rounded-full p-1.5 hover:bg-white/10"
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
              className="rounded-xl border border-white/10 p-3"
              style={{ backgroundColor: PANEL }}
            >
              <div className="flex flex-wrap items-center gap-2 text-sm">
                {merge ? (
                  <GitMerge className="h-3.5 w-3.5 text-white/60" />
                ) : (
                  <Split className="h-3.5 w-3.5 text-white/60" />
                )}
                <span className="font-medium">{item.a_label}</span>
                <span className="text-white/40">{merge ? "→" : "↔"}</span>
                <span className="font-medium">{item.b_label}</span>
                <span className="ml-auto text-[11px] text-white/60">
                  {tm(merge ? "Merged." : "Kept apart.")}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-white/50">
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
                  <span className="ml-auto" style={{ color: LIME }}>
                    {tm("Undone — the pair is open for review again.")}
                  </span>
                ) : (
                  canUndo &&
                  (item.undoable ? (
                    <button
                      onClick={() => void undo(item)}
                      className="ml-auto rounded-full border border-white/15 px-3 py-0.5 text-white/80 hover:bg-white/10"
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
          className="mt-3 w-full rounded-lg border border-white/15 py-1.5 text-xs hover:bg-white/10"
        >
          {tm("Load more")}
        </button>
      )}
    </>
  );
}
/* ------------------------------------------------------------- Processing */

function ProcessingTab({ drive, canEdit }: { drive: string; canEdit: boolean }) {
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
          className="ml-auto rounded-full p-1.5 hover:bg-white/10"
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
            <div className="mt-3 flex items-center gap-2 text-xs text-white/70">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {tm("Processing now")}
              {status.running ? `: ${status.running.source_path.split("/").pop()}` : ""}
            </div>
          )}
          {status.gpu?.foreground_active ? (
            <div className="mt-2 text-xs text-white/55">
              {tm("The AI Box is answering a question; extraction waits.")}
            </div>
          ) : null}
          <div className="mb-1.5 mt-5 text-[11px] uppercase tracking-wide text-white/45">
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
                  <span className={r.status === "done" ? "text-white/60" : "text-amber-300"}>
                    {tm(STATUS_TEXT[r.status] ?? r.status)}
                  </span>
                </div>
              ))}
          </div>
        </>
      )}
      <ReprocessSection drive={drive} canEdit={canEdit} />
      <UnreadableDocuments drive={drive} />
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

/** After a domain profile change: which profile the documents were extracted
 *  with, and (graph administrators) reprocessing those of an older one. The
 *  files are fetched again from the ViVeSecBox, one at a time, in the background. */
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
  return (
    <div className="mt-6">
      <div className="mb-1.5 flex items-center gap-2 text-[11px] uppercase tracking-wide text-white/45">
        <Settings2 className="h-3.5 w-3.5" />
        {tm("Domain profile version")}
      </div>
      {notice}
      <div className="text-xs text-white/70">
        {tm("Current profile")}: <span className="font-medium text-white">{data.current}</span>
      </div>
      <div className="mt-1 text-xs text-white/70">
        {data.stale_total
          ? `${tm("Extracted with an older or unknown profile")}: ${data.stale_total}`
          : tm("Every document was extracted with the current profile.")}
      </div>
      {versions.length > 1 && (
        <div className="mt-1 text-[11px] text-white/45">
          {versions.map(([v, n]) => `${v === "unknown" ? tm("unknown") : v}: ${n}`).join(" · ")}
        </div>
      )}
      {run.state !== "idle" && (
        <div
          className="mt-2 rounded-xl border border-white/10 p-2.5 text-xs"
          style={{ backgroundColor: PANEL }}
        >
          <div className="flex items-center gap-2">
            {running && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            <span>{tm(RUN_STATE[run.state])}</span>
            <span className="ml-auto text-white/60">
              {run.done ?? 0}/{run.total ?? 0}
            </span>
          </div>
          {running && run.current && (
            <div className="mt-1 truncate text-[11px] text-white/50" title={run.current}>
              {run.current.split("/").pop()}
            </div>
          )}
          <div className="mt-1 text-[11px] text-white/50">
            {`${tm("Sent")}: ${run.sent ?? 0} · ${tm("Missing")}: ${run.missing ?? 0} · ${tm("Failed")}: ${run.failed ?? 0}`}
          </div>
          {run.error && <div className="mt-1 text-[11px] text-amber-300">{run.error}</div>}
        </div>
      )}
      {canEdit && data.can_start && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            value={folder}
            onChange={(e) => setFolder(e.target.value)}
            disabled={running}
            placeholder={tm("Folder (optional), e.g. /storage/drives/…")}
            className="min-w-0 flex-1 rounded-lg border border-white/15 bg-black/30 px-2 py-1 text-xs text-white outline-none focus:border-white/30"
          />
          {running ? (
            <button
              onClick={() => void act(() => graphApi.reprocessCancel(drive))}
              disabled={busy}
              className="rounded-lg border border-white/15 px-3 py-1 text-xs transition hover:bg-white/10 disabled:opacity-50"
            >
              {tm("Stop")}
            </button>
          ) : (
            <button
              onClick={() => void act(() => graphApi.reprocessStart(drive, folder.trim()))}
              disabled={busy || !data.stale_total || !data.source_ready}
              className="rounded-lg px-3 py-1 text-xs font-medium text-black disabled:opacity-50"
              style={{ backgroundColor: LIME }}
            >
              {tm("Reprocess")}
            </button>
          )}
        </div>
      )}
      {canEdit && data.can_start && !data.source_ready && (
        <div className="mt-1 text-[11px] text-amber-300">
          {tm("The ViVeSecBox is not connected: the files cannot be fetched again.")}
        </div>
      )}
    </div>
  );
}

/** E10b: why a document of this scope is not in the search -- reason
 *  counts, and the documents with any pages OCR could not read. */
const SKIP_REASON: Record<string, string> = {
  extraction_empty: "No text layer — resync to read it with OCR",
  ocr_pending: "Waiting for OCR",
  ocr_interrupted: "OCR interrupted — resync needed",
  ocr_deferred: "OCR deferred — resync needed",
  ocr_failed: "OCR failed",
  ocr_no_text: "No text recognised",
  ocr_pages_skipped: "Some pages could not be read",
  unsupported_type: "Unsupported format",
  empty_content: "Empty file",
  too_large: "Too large",
  invalid_content: "Invalid content",
  extractor_unavailable: "No reader for this format on the AI Box",
};
const PAGE_REASON: Record<string, string> = {
  low_quality: "low quality",
  no_text: "no text",
  failed: "failed",
};

function UnreadableDocuments({ drive }: { drive: string }) {
  const { tm } = useLang();
  const [data, setData] = useState<IndexSkipped | null>(null);
  const [failed, setFailed] = useState(false);
  const [reason, setReason] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await graphApi.skipped(drive));
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [drive]);
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 30000);
    return () => clearInterval(timer);
  }, [load]);

  const reasons = Object.entries(data?.reasons ?? {}).sort((a, b) => b[1] - a[1]);
  const shown = (data?.documents ?? []).filter((d) => !reason || d.reason === reason);
  return (
    <div className="mt-6">
      <div className="mb-1.5 flex items-center gap-2 text-[11px] uppercase tracking-wide text-white/45">
        <ShieldAlert className="h-3.5 w-3.5" />
        {tm("Not readable for search")}
        {data && <span className="normal-case tracking-normal text-white/60">({data.count})</span>}
      </div>
      {failed && <Notice text={tm("The list could not be loaded.")} tone="warn" />}
      {data && data.count === 0 && (
        <div className="text-xs text-white/55">
          {tm("Every document in this scope is readable.")}
        </div>
      )}
      {reasons.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {reasons.map(([key, n]) => (
            <button
              key={key}
              onClick={() => setReason(reason === key ? null : key)}
              className={`rounded-full border px-2 py-0.5 text-[11px] transition ${reason === key ? "border-transparent text-black" : "border-white/15 text-white/70 hover:bg-white/10"}`}
              style={reason === key ? { backgroundColor: LIME } : undefined}
            >
              {tm(SKIP_REASON[key] ?? key)}: {n}
            </button>
          ))}
        </div>
      )}
      <div className="max-h-72 space-y-1 overflow-y-auto pr-1">
        {shown.map((d) => (
          <div key={d.source_path} className="text-xs">
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate" title={d.source_path}>
                {d.source_path.split("/").pop()}
              </span>
              <span className="shrink-0 text-amber-300/90">
                {tm(SKIP_REASON[d.reason] ?? d.reason)}
              </span>
            </div>
            {!!d.skipped_pages?.length && (
              <div className="pl-2 text-[11px] text-white/50">
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
        ))}
      </div>
      {data?.truncated && (
        <div className="mt-1 text-[11px] text-white/45">
          {tm("Only the first documents are listed; the counts cover all.")}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-white/10 p-3" style={{ backgroundColor: PANEL }}>
      <div className="text-lg font-semibold">{value}</div>
      <div className="text-[11px] text-white/50">{label}</div>
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
      <div className="text-[11px] text-white/50">
        {profile.id} · {tm("Version")} {profile.version}
      </div>
      {notice}
      {message && <Notice text={message} />}
      <Section title={tm("Entity kinds")}>
        <div className="flex flex-wrap gap-1.5">
          {profile.entity_subtypes.map((s) => (
            <span
              key={s.id}
              className="rounded-full border border-white/10 px-2 py-0.5 text-[11px]"
            >
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
              <span className="text-white/50">
                {r.from.map((t) => tm(TYPE_NAME[t])).join("/")} →{" "}
                {r.to.map((t) => tm(TYPE_NAME[t])).join("/")}
              </span>
            </div>
          ))}
        </div>
      </Section>
      {canEdit ? (
        <Section title={tm("Edit profile (JSON)")}>
          {draft === null ? (
            <button
              onClick={() => {
                setMessage(null);
                setDraft(JSON.stringify({ ...profile, version: profile.version + 1 }, null, 2));
              }}
              className="rounded-full border border-white/15 px-3 py-1 text-xs hover:bg-white/10"
            >
              {tm("Edit profile (JSON)")}
            </button>
          ) : (
            <>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                spellCheck={false}
                className="h-80 w-full rounded-lg border border-white/10 bg-black/30 p-2 font-mono text-[11px] outline-none"
              />
              <div className="mt-2 flex gap-2">
                <button
                  onClick={() => void save()}
                  className="rounded-full px-3 py-1 text-xs text-black"
                  style={{ backgroundColor: LIME }}
                >
                  {tm("Save as new version")}
                </button>
                <button
                  onClick={() => setDraft(null)}
                  className="rounded-full border border-white/15 px-3 py-1 text-xs hover:bg-white/10"
                >
                  {tm("Cancel")}
                </button>
              </div>
            </>
          )}
        </Section>
      ) : (
        <Notice text={tm("Only graph administrators can change the profile.")} />
      )}
    </div>
  );
}
