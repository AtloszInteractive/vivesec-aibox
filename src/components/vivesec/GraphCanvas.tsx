import { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  Banknote,
  BadgeCheck,
  Box,
  Building,
  Building2,
  CalendarClock,
  CalendarRange,
  Car,
  ClipboardList,
  Contact,
  Crosshair,
  DoorClosed,
  Download,
  Eye,
  Hash,
  House,
  IdCard,
  Info,
  Landmark,
  LayoutGrid,
  Lock,
  Mail,
  MapPin,
  Maximize,
  Minimize,
  Network,
  Package,
  Pause,
  Phone,
  Pin,
  PinOff,
  Play,
  Search,
  TriangleAlert,
  User,
  UserLock,
  UserRound,
  Users,
  ZoomIn,
  ZoomOut,
  type LucideIcon,
} from "lucide-react";
import type {
  GraphDocuments,
  GraphEdge,
  GraphNode,
  GraphWatermark,
} from "@/lib/api/graph.functions";
import { useLang } from "./i18n";
import { ACCENT_TEXT, inkA, inkText } from "./brand";
import {
  edgeInPeriod,
  layoutGraph,
  layoutGraph3d,
  monthsOf,
  placeLabels,
  project,
  type Point,
} from "./graph-layout";
import {
  clusterLeaves,
  edgeSource,
  findNodes,
  isProtectedEdge,
  isProtectedNode,
  type EdgeSource,
} from "./graph-model";
import { downloadPicture, pictureSvg } from "./graph-export";
import { pairKey, TYPE_COLOR, TYPE_NAME, type RelationInfo } from "./graph-style";

type Graph = { nodes: GraphNode[]; edges: GraphEdge[]; documents: GraphDocuments };
type Mode = "2d" | "3d";
type Pt = { x: number; y: number; r: number; scale: number; depth: number };
type Frame3 = { minX: number; minY: number; w: number; h: number; distance: number };
export type Emphasis = { nodes: Set<string>; pairs: Set<string> };
export type ExportRequest = {
  format: "png" | "svg";
  view: Mode;
  nodes: number;
  edges: number;
};

const MODE_KEY = "vvs.graph.view";
const LEGEND_KEY = "vvs.graph.legend";
/** A band in the background colour behind text, `px` user units per screen pixel. */
const halo = (px: number) =>
  ({ stroke: "var(--surface-2)", strokeWidth: 3 * px, paintOrder: "stroke" }) as const;
const FADED = 0.13;
const PROTECTED = "#F43F5E";
const DASH: Record<EdgeSource, string | undefined> = {
  register: undefined,
  text: "7 4",
  comention: "2 3",
};
const SUBTYPE_ICON: Record<string, LucideIcon> = {
  inmate: UserLock,
  visitor: UserRound,
  staff: BadgeCheck,
  contact: Contact,
  institution: Landmark,
  facility: Building,
  unit: LayoutGrid,
  cell: DoorClosed,
  address: House,
  phone: Phone,
  id_card: IdCard,
  vehicle_plate: Car,
  bank_account: Banknote,
  case_number: Hash,
  email: Mail,
  incident: TriangleAlert,
  meeting: Users,
  intel_report: Eye,
  odin_entry: ClipboardList,
};
const TYPE_ICON: Record<GraphNode["type"], LucideIcon> = {
  person: User,
  organization: Building2,
  place: MapPin,
  identifier: Hash,
  event: CalendarClock,
  object: Package,
};

function storedMode(): Mode {
  try {
    return window.localStorage.getItem(MODE_KEY) === "3d" ? "3d" : "2d";
  } catch {
    return "2d";
  }
}

/** The relationship picture (E26): 2D, or 3D to turn around. Zoom, fit, full
 *  screen; a legend that also filters; arrows for directed relations and line
 *  styles by source; crowded nodes' leaves grouped; the neighbours of the node
 *  under the pointer brought forward; a time filter that can play; nodes to
 *  drag and pin; finding a name; a logged, watermarked picture export. */
export function GraphCanvas({
  graph,
  focus,
  selectedEdge,
  picked,
  emphasis,
  relLabel,
  relInfo,
  subtypeLabel,
  onNode,
  onExpand,
  onEdge,
  onExport,
}: {
  graph: Graph;
  focus: string | null;
  selectedEdge: string | null;
  /** Nodes picked with Shift+click (two: path and common relations). */
  picked: string[];
  /** A path or the common relations found for the picked nodes. */
  emphasis: Emphasis | null;
  relLabel: (type: string) => string;
  relInfo: (type: string) => RelationInfo;
  subtypeLabel: (sub: string) => string;
  onNode: (id: string, shift: boolean) => void;
  onExpand: (id: string) => void;
  onEdge: (id: string) => void;
  onExport: (req: ExportRequest) => Promise<GraphWatermark | null>;
}) {
  const { tm } = useLang();
  const uid = useId().replace(/:/g, "");
  const box = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [mode, setMode] = useState<Mode>("2d");
  const [view, setView] = useState({ k: 1, x: 0, y: 0 });
  const [rot, setRot] = useState({ yaw: 0.5, pitch: -0.35 });
  const [spin, setSpin] = useState(true);
  const [hovered, setHover] = useState<string | null>(null);
  const [legend, setLegend] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [canFullscreen, setCanFullscreen] = useState(false);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());
  const [hiddenRels, setHiddenRels] = useState<Set<string>>(new Set());
  const [openHubs, setOpenHubs] = useState<Set<string>>(new Set());
  const [pinned, setPinned] = useState<Map<string, Point>>(new Map());
  const [dragging, setDragging] = useState<{ id: string; at: Point } | null>(null);
  // Pins move a node at once; the next layout (nodes added) keeps them fixed.
  const pinnedRef = useRef(pinned);
  pinnedRef.current = pinned;
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [match, setMatch] = useState(0);
  const [exportOpen, setExportOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const drag = useRef<{
    x: number;
    y: number;
    vx: number;
    vy: number;
    yaw: number;
    pitch: number;
    node?: string;
    start?: Point;
  } | null>(null);
  const moved = useRef(false);
  const placed = useRef(new Map<string, Point>());
  const focusRef = useRef(focus);
  focusRef.current = focus;

  const allNodes = useMemo(() => graph.nodes.filter(Boolean), [graph]);
  const months = useMemo(() => monthsOf(graph.edges), [graph.edges]);
  const [timeOn, setTimeOn] = useState(false);
  const [range, setRange] = useState<[number, number]>([0, 0]);
  const [undated, setUndated] = useState(true);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    setMode(storedMode());
    try {
      setLegend(window.localStorage.getItem(LEGEND_KEY) === "1");
    } catch {
      /* private mode: closed */
    }
  }, []);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) =>
      setSize({ w: entry.contentRect.width || 800, h: entry.contentRect.height || 600 }),
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    setRange([0, Math.max(0, months.length - 1)]);
    setHover(null);
    setPlaying(false);
  }, [months.length]);
  useEffect(() => {
    setCanFullscreen(Boolean(document.fullscreenEnabled));
    const sync = () => setFullscreen(document.fullscreenElement === box.current);
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);
  // The 3D picture turns slowly until the user takes it in hand.
  useEffect(() => {
    if (mode !== "3d" || !spin || hovered) return;
    let frame = 0;
    const tick = () => {
      setRot((r) => ({ ...r, yaw: r.yaw + 0.004 }));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [mode, spin, hovered]);
  // Playing the period: its end moves on month by month.
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => {
      setRange(([from, to]) => {
        if (to >= months.length - 1) {
          setPlaying(false);
          return [from, to];
        }
        return [from, to + 1];
      });
    }, 700);
    return () => clearInterval(timer);
  }, [playing, months.length]);

  function chooseMode(next: Mode) {
    setMode(next);
    setView({ k: 1, x: 0, y: 0 });
    try {
      window.localStorage.setItem(MODE_KEY, next);
    } catch {
      /* private mode: the choice lasts for this view only */
    }
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void box.current?.requestFullscreen?.();
  }

  function toggle(set: Set<string>, value: string): Set<string> {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    return next;
  }

  // What is drawn: the legend's filters, then the crowded nodes' leaves grouped.
  const matches = useMemo(() => findNodes(allNodes, query), [allNodes, query]);
  const display = useMemo(() => {
    const keep = new Set<string>([
      ...(focus ? [focus] : []),
      ...picked,
      ...pinned.keys(),
      ...(emphasis?.nodes ?? []),
      ...matches,
    ]);
    const nodes = allNodes.filter((n) => !hiddenTypes.has(n.type) || keep.has(n.id));
    const ids = new Set(nodes.map((n) => n.id));
    const edges = graph.edges.filter(
      (e) => !hiddenRels.has(e.type) && ids.has(e.a) && ids.has(e.b),
    );
    return clusterLeaves(nodes, edges, keep, openHubs);
  }, [
    allNodes,
    graph.edges,
    hiddenTypes,
    hiddenRels,
    openHubs,
    focus,
    picked,
    pinned,
    emphasis,
    matches,
  ]);
  const nodes = display.nodes;
  // A node that went away under the pointer (an opened group) is not hovered.
  const hover = hovered && nodes.some((n) => n.id === hovered) ? hovered : null;

  const timed = timeOn && months.length > 0;
  const from = months[Math.min(range[0], range[1])] ?? "";
  const to = months[Math.max(range[0], range[1])] ?? "";
  const edges = useMemo(
    () =>
      timed
        ? display.edges.filter((e) => edgeInPeriod(e, from, to, undated, relInfo(e.type).ongoing))
        : display.edges,
    [display.edges, timed, from, to, undated, relInfo],
  );
  const pairs = useMemo(() => {
    const out = new Map<string, GraphEdge[]>();
    for (const e of edges) out.set(pairKey(e), [...(out.get(pairKey(e)) ?? []), e]);
    return [...out.entries()];
  }, [edges]);
  const neighbours = useMemo(() => {
    const out = new Map<string, Set<string>>();
    for (const [, list] of pairs) {
      const { a, b } = list[0];
      out.set(a, (out.get(a) ?? new Set()).add(b));
      out.set(b, (out.get(b) ?? new Set()).add(a));
    }
    return out;
  }, [pairs]);

  // The layout uses every drawn edge, so the picture holds still while the period
  // moves; it starts from the earlier positions when most nodes had one.
  const nodesKey = nodes.map((n) => n.id).join("|");
  const edgesKey = display.edges.map((e) => e.id).join("|");
  const layout2d = useMemo(() => {
    const ids = nodes.map((n) => n.id);
    const known = ids.filter((id) => placed.current.has(id)).length;
    const warm = ids.length > 0 && known >= 0.3 * ids.length;
    return layoutGraph(ids, display.edges, focusRef.current, 260, {
      seed: warm ? placed.current : undefined,
      fixed: pinnedRef.current,
    });
    // by content: a new array with the same nodes and edges keeps the picture
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodesKey, edgesKey]);
  useEffect(() => {
    placed.current = new Map([...placed.current, ...layout2d]);
  }, [layout2d]);
  const layout3d = useMemo(
    () =>
      mode === "3d"
        ? layoutGraph3d(
            nodes.map((n) => n.id),
            display.edges,
            null,
          )
        : null,
    // by content, as the 2D layout
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mode, nodesKey, edgesKey],
  );

  const maxMentions = Math.max(1, ...nodes.map((n) => n.mentions));
  const fitted = useRef<{ key: string; frame: Frame3 } | null>(null);
  const frameKey = `${mode}|${nodesKey}`;
  const computedFrame = useMemo((): Frame3 => {
    if (layout3d) {
      const far = Math.max(
        60,
        ...[...layout3d.values()].map((p) => Math.sqrt(p.x * p.x + p.y * p.y + p.z * p.z)),
      );
      const pad = far + 60;
      return { minX: -pad, minY: -pad, w: 2 * pad, h: 2 * pad, distance: far * 3 };
    }
    const pts = [...layout2d.values()];
    const xs = pts.map((p) => p.x);
    const ys = pts.map((p) => p.y);
    const pad = 60;
    const minX = Math.min(...xs, 0) - pad;
    const minY = Math.min(...ys, 0) - pad;
    return {
      minX,
      minY,
      w: Math.max(...xs, 0) + pad - minX,
      h: Math.max(...ys, 0) + pad - minY,
      distance: 0,
    };
  }, [layout2d, layout3d]);
  // The picture keeps its frame while the same nodes move (a pinned node, a
  // settled layout); it is fitted again when nodes come or go.
  if (fitted.current?.key !== frameKey) fitted.current = { key: frameKey, frame: computedFrame };
  const frame = fitted.current.frame;
  // User units per CSS pixel at zoom 1 (the viewBox is fitted into the box), and
  // per screen pixel now: text, strokes and circles keep their size on screen.
  const unit = Math.max(frame.w / size.w, frame.h / size.h);
  const px = unit / view.k;
  const k = view.k;

  const points = useMemo(() => {
    const out = new Map<string, Pt>();
    for (const n of nodes) {
      const r = (8 + 7 * Math.sqrt(n.mentions / maxMentions)) * unit;
      if (layout3d) {
        const p = layout3d.get(n.id);
        if (!p) continue;
        const v = project(p, rot.yaw, rot.pitch, frame.distance);
        out.set(n.id, { x: v.x, y: v.y, r: r * v.scale, scale: v.scale, depth: v.depth });
      } else {
        const p = dragging?.id === n.id ? dragging.at : (pinned.get(n.id) ?? layout2d.get(n.id));
        if (p) out.set(n.id, { x: p.x, y: p.y, r, scale: 1, depth: 0 });
      }
    }
    return out;
  }, [nodes, layout2d, layout3d, rot, frame.distance, unit, maxMentions, pinned, dragging]);

  // Nearness 0..1 in 3D: the far side of the picture is drawn fainter.
  const depths = [...points.values()].map((p) => p.depth);
  const dMin = Math.min(0, ...depths);
  const dMax = Math.max(0, ...depths);
  const near = (p: Pt) => (layout3d && dMax > dMin ? (p.depth - dMin) / (dMax - dMin) : 1);

  const current = matches.length ? matches[match % matches.length] : null;
  const selectedPair = selectedEdge ? pairs.find(([key]) => key === selectedEdge)?.[1] : undefined;
  const highlight = useMemo(() => {
    if (hover) return new Set([hover, ...(neighbours.get(hover) ?? [])]);
    if (emphasis) return emphasis.nodes;
    if (selectedPair) return new Set([selectedPair[0].a, selectedPair[0].b]);
    if (query.trim()) return new Set(matches);
    return null;
  }, [hover, neighbours, emphasis, selectedPair, query, matches]);
  const live = useMemo(() => {
    if (!timed) return null;
    const out = new Set<string>(focus ? [focus] : []);
    for (const e of edges) out.add(e.a).add(e.b);
    return out;
  }, [timed, edges, focus]);
  const dim = (id: string) => (highlight && !highlight.has(id)) || (live && !live.has(id));
  const pairLit = (key: string, first: string, second: string) =>
    key === selectedEdge ||
    (hover !== null && (first === hover || second === hover)) ||
    (!hover && Boolean(emphasis?.pairs.has(key)));

  const clusterLabel = (id: string) => {
    const c = display.clusters.get(id);
    return c ? `${c.members.length} × ${subtypeLabel(c.subtype) || tm(TYPE_NAME[c.type])}` : "";
  };
  const nodeLabel = (n: GraphNode) => {
    const text = n.label || clusterLabel(n.id);
    return text.length > 28 ? `${text.slice(0, 27)}…` : text;
  };

  // Centre the found name in 2D.
  useEffect(() => {
    if (!current || layout3d) return;
    const p = points.get(current);
    if (!p) return;
    const cx = frame.minX + frame.w / 2;
    const cy = frame.minY + frame.h / 2;
    setView((v) => {
      const zoom = Math.max(v.k, 1.6);
      return { k: zoom, x: -zoom * (p.x - cx), y: -zoom * (p.y - cy) };
    });
    // when the found node or the layout under it changes, not on every redraw
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, layout3d, layout2d]);
  // A found node inside a group opens the group.
  useEffect(() => {
    const hubs = [...display.clusters.values()]
      .filter((c) => c.members.some((m) => matches.includes(m)))
      .map((c) => c.hub);
    if (hubs.length) setOpenHubs((open) => new Set([...open, ...hubs]));
  }, [matches, display.clusters]);

  const showEdgeLabels = pairs.length <= 40;
  // Edge labels: always for the selected pair and the lines of the node under the
  // pointer, for every pair only while the picture is small.
  const edgeLabels = useMemo(() => {
    const out = new Map<string, { x: number; y: number; text: string; lit: boolean }>();
    for (const [key, list] of pairs) {
      const [first, second] = key.split("|");
      const p = points.get(first);
      const q = points.get(second);
      if (!p || !q) continue;
      const lit = pairLit(key, first, second);
      if (!lit && (!showEdgeLabels || highlight)) continue;
      const counts = new Map<string, number>();
      for (const x of list) counts.set(x.type, (counts.get(x.type) ?? 0) + 1);
      const text =
        [...counts.entries()]
          .map(([type, n]) => (n > 1 ? `${relLabel(type)} ×${n}` : relLabel(type)))
          .join(", ") + (list.length === 1 && list[0].start ? ` · ${list[0].start}` : "");
      // Away from the focused node, where every line starts.
      const t = first === focus ? 0.62 : second === focus ? 0.38 : 0.5;
      out.set(key, { x: p.x + (q.x - p.x) * t, y: p.y + (q.y - p.y) * t - 3 * px, text, lit });
    }
    return out;
    // pairLit reads the same inputs
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    pairs,
    points,
    selectedEdge,
    hover,
    emphasis,
    showEdgeLabels,
    highlight,
    relLabel,
    focus,
    px,
  ]);

  const labelled = useMemo(() => {
    const out = [];
    for (const n of nodes) {
      const p = points.get(n.id);
      if (!p || dim(n.id)) continue;
      const text = nodeLabel(n);
      const cluster = display.clusters.has(n.id);
      out.push({
        id: n.id,
        x: p.x,
        y: p.y + p.r + 9 * px,
        w: (text.length * 5.6 + 6) * px,
        h: 13 * px,
        priority:
          (n.id === focus ? 1e7 : 0) +
          (highlight?.has(n.id) || picked.includes(n.id) ? 1e6 : 0) +
          (cluster ? 1e4 : 0) +
          n.mentions * 10 +
          near(p) * 100,
        force: n.id === focus || n.id === hover || n.id === current || picked.includes(n.id),
      });
    }
    for (const [key, label] of edgeLabels) {
      out.push({
        id: `edge:${key}`,
        x: label.x,
        y: label.y - 3 * px,
        w: (label.text.length * 5 + 4) * px,
        h: 11 * px,
        priority: key === selectedEdge ? 1e8 : label.lit ? 5e5 : 1,
        force: key === selectedEdge,
      });
    }
    const circles = nodes.flatMap((n) => {
      const p = points.get(n.id);
      return p && !dim(n.id) ? [{ x: p.x, y: p.y, w: 2 * p.r, h: 2 * p.r }] : [];
    });
    return placeLabels(out, circles);
    // dim/near/nodeLabel read the same memoised inputs
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, points, highlight, live, focus, hover, current, picked, px, edgeLabels, selectedEdge]);

  const legendTypes = useMemo(() => {
    const counts = new Map<GraphNode["type"], number>();
    for (const n of allNodes) counts.set(n.type, (counts.get(n.type) ?? 0) + 1);
    return [...counts.entries()];
  }, [allNodes]);
  const legendRels = useMemo(() => {
    const counts = new Map<string, number>();
    for (const e of graph.edges) counts.set(e.type, (counts.get(e.type) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [graph.edges]);
  const anyProtected = useMemo(
    () => graph.edges.some((e) => isProtectedEdge(graph.documents, e)),
    [graph],
  );

  const drawnPairs = layout3d
    ? [...pairs].sort(([, x], [, y]) => {
        const dx = (points.get(x[0].a)?.depth ?? 0) + (points.get(x[0].b)?.depth ?? 0);
        const dy = (points.get(y[0].a)?.depth ?? 0) + (points.get(y[0].b)?.depth ?? 0);
        return dx - dy;
      })
    : pairs;
  const drawnNodes = layout3d
    ? [...nodes].sort((x, y) => (points.get(x.id)?.depth ?? 0) - (points.get(y.id)?.depth ?? 0))
    : nodes;

  async function exportPicture(format: "png" | "svg") {
    const svg = svgRef.current;
    if (!svg || exporting) return;
    setExportOpen(false);
    setExporting(true);
    try {
      const mark = await onExport({ format, view: mode, nodes: nodes.length, edges: edges.length });
      if (!mark) return;
      const background =
        window.getComputedStyle(document.documentElement).getPropertyValue("--surface-2").trim() ||
        "#ffffff";
      const lines = [
        `${tm("Exported by")}: ${mark.user} · ${tm("Case reference")}: ${mark.case_ref || "—"} · ${mark.at}`,
        tm("ViVeSec AI Box relationship view. The export is in the query log."),
      ];
      const stamp = mark.at.replace(/[^0-9]/g, "").slice(0, 12);
      await downloadPicture(
        pictureSvg(svg, frame, unit, background, lines),
        format,
        `kapcsolatok_${stamp}`,
      );
    } finally {
      setExporting(false);
    }
  }

  // User units per screen pixel: the viewBox is fitted ("meet") into the box.
  const screenScale = () => px;
  const button =
    "flex h-7 w-7 items-center justify-center rounded-full border border-ink/10 text-ink/70 hover:bg-ink/10 hover:text-ink disabled:opacity-40";
  const panel = "rounded-lg border border-ink/10 px-3 py-2 text-[11px] shadow-sm";

  return (
    <div
      ref={box}
      className="relative h-full w-full"
      style={fullscreen ? { backgroundColor: "var(--surface-2)" } : undefined}
    >
      <svg
        ref={svgRef}
        className={`h-full w-full ${layout3d ? "cursor-move" : "cursor-grab active:cursor-grabbing"}`}
        viewBox={`${frame.minX} ${frame.minY} ${frame.w} ${frame.h}`}
        style={{ color: "var(--foreground)" }}
        onWheel={(e) => {
          const next = Math.min(6, Math.max(0.3, k * (e.deltaY < 0 ? 1.12 : 0.89)));
          setView((v) => ({ ...v, k: next }));
        }}
        onPointerDown={(e) => {
          moved.current = false;
          setSpin(false);
          drag.current = {
            x: e.clientX,
            y: e.clientY,
            vx: view.x,
            vy: view.y,
            yaw: rot.yaw,
            pitch: rot.pitch,
          };
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d) return;
          const ddx = e.clientX - d.x;
          const ddy = e.clientY - d.y;
          if (Math.abs(ddx) + Math.abs(ddy) > 4) moved.current = true;
          if (d.node && d.start && !layout3d) {
            if (!moved.current) return;
            const scale = screenScale();
            setDragging({
              id: d.node,
              at: { x: d.start.x + ddx * scale, y: d.start.y + ddy * scale },
            });
            return;
          }
          if (layout3d && !e.shiftKey) {
            setRot({
              yaw: d.yaw + ddx * 0.008,
              pitch: Math.max(-1.45, Math.min(1.45, d.pitch + ddy * 0.008)),
            });
            return;
          }
          const scale = screenScale();
          setView((v) => ({ ...v, x: d.vx + ddx * scale, y: d.vy + ddy * scale }));
        }}
        onPointerUp={() => {
          if (dragging) {
            // A dragged node stays where it was dropped.
            setPinned((pins) => new Map(pins).set(dragging.id, dragging.at));
            setDragging(null);
          }
          drag.current = null;
        }}
        onPointerLeave={() => {
          drag.current = null;
          setDragging(null);
        }}
      >
        <defs>
          {(["n", "a", "p"] as const).map((kind) => (
            <marker
              key={kind}
              id={`${uid}-arrow-${kind}`}
              viewBox="0 0 8 8"
              refX={8}
              refY={4}
              markerWidth={7 * unit}
              markerHeight={7 * unit}
              markerUnits="userSpaceOnUse"
              orient="auto-start-reverse"
            >
              <path
                d="M0,0 L8,4 L0,8 z"
                style={{ fill: kind === "a" ? ACCENT_TEXT : kind === "p" ? PROTECTED : inkA(0.5) }}
              />
            </marker>
          ))}
        </defs>
        <g
          transform={`translate(${view.x} ${view.y}) translate(${frame.minX + frame.w / 2} ${
            frame.minY + frame.h / 2
          }) scale(${k}) translate(${-(frame.minX + frame.w / 2)} ${-(frame.minY + frame.h / 2)})`}
        >
          {drawnPairs.map(([key, list]) => {
            const [first, second] = key.split("|");
            const p = points.get(first);
            const q = points.get(second);
            if (!p || !q) return null;
            const active = key === selectedEdge;
            const lit = pairLit(key, first, second);
            const faded = highlight !== null && !lit;
            const secret = list.some((e) => isProtectedEdge(graph.documents, e));
            const forward = list.some((e) => e.a === first && relInfo(e.type).directed);
            const backward = list.some((e) => e.a === second && relInfo(e.type).directed);
            const len = Math.hypot(q.x - p.x, q.y - p.y) || 1;
            const ux = (q.x - p.x) / len;
            const uy = (q.y - p.y) / len;
            const x1 = p.x + ux * p.r;
            const y1 = p.y + uy * p.r;
            const x2 = q.x - ux * q.r;
            const y2 = q.y - uy * q.r;
            const depthFade = layout3d ? 0.35 + 0.65 * ((near(p) + near(q)) / 2) : 1;
            const tone = lit ? "a" : secret ? "p" : "n";
            const marker = `url(#${uid}-arrow-${tone})`;
            const grouped = key.includes("cluster:");
            return (
              <g
                key={key}
                onClick={() => !moved.current && !grouped && onEdge(key)}
                className={grouped ? undefined : "cursor-pointer"}
                opacity={(faded ? FADED : 1) * depthFade}
              >
                <line
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke="transparent"
                  strokeWidth={10}
                  vectorEffect="non-scaling-stroke"
                />
                <line
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  style={{ stroke: lit ? ACCENT_TEXT : secret ? PROTECTED : inkA(0.32) }}
                  strokeWidth={
                    (active ? 2.4 : lit ? 1.8 : 1.2) + Math.min(list.length - 1, 4) * 0.5
                  }
                  strokeDasharray={DASH[edgeSource(list)]}
                  vectorEffect="non-scaling-stroke"
                  markerEnd={forward ? marker : undefined}
                  markerStart={backward ? marker : undefined}
                />
              </g>
            );
          })}
          {drawnNodes.map((n) => {
            const p = points.get(n.id);
            if (!p) return null;
            const cluster = display.clusters.get(n.id);
            const isFocus = n.id === focus;
            const isPicked = picked.includes(n.id);
            const faded = dim(n.id);
            const Icon = SUBTYPE_ICON[n.subtype] ?? TYPE_ICON[n.type];
            const icon = p.r * 1.15;
            const showIcon = p.r / px >= 7;
            const secret = !cluster && isProtectedNode(graph.documents, n);
            const pin = pinned.has(n.id);
            return (
              <g
                key={n.id}
                onPointerDown={(e) => {
                  if (layout3d || e.shiftKey || cluster) return;
                  e.stopPropagation();
                  moved.current = false;
                  setSpin(false);
                  drag.current = {
                    x: e.clientX,
                    y: e.clientY,
                    vx: view.x,
                    vy: view.y,
                    yaw: rot.yaw,
                    pitch: rot.pitch,
                    node: n.id,
                    start: { x: p.x, y: p.y },
                  };
                }}
                onClick={(e) => {
                  if (moved.current) return;
                  if (cluster) {
                    setHover(null);
                    setOpenHubs((open) => new Set(open).add(cluster.hub));
                  } else onNode(n.id, e.shiftKey);
                }}
                onDoubleClick={() => !cluster && onExpand(n.id)}
                onPointerEnter={() => setHover(n.id)}
                onPointerLeave={() => setHover((h) => (h === n.id ? null : h))}
                className="cursor-pointer"
                opacity={(faded ? FADED : 1) * (layout3d ? 0.45 + 0.55 * near(p) : 1)}
              >
                {(isPicked || n.id === current) && (
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={p.r + 4 * px}
                    fill="none"
                    style={{ stroke: ACCENT_TEXT }}
                    strokeWidth={2.5}
                    vectorEffect="non-scaling-stroke"
                  />
                )}
                {cluster && (
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={p.r + 3.5 * px}
                    fill="none"
                    stroke={TYPE_COLOR[n.type]}
                    strokeWidth={1.5}
                    strokeDasharray="3 2"
                    vectorEffect="non-scaling-stroke"
                  />
                )}
                <circle
                  cx={p.x}
                  cy={p.y}
                  r={p.r}
                  fill={TYPE_COLOR[n.type]}
                  fillOpacity={isFocus || highlight?.has(n.id) ? 1 : 0.88}
                  stroke="rgba(0,0,0,0.4)"
                  style={isFocus ? { stroke: "var(--ink)" } : undefined}
                  strokeWidth={isFocus ? 2.5 : 1}
                  vectorEffect="non-scaling-stroke"
                />
                {showIcon &&
                  (cluster ? (
                    <text
                      x={p.x}
                      y={p.y + 3.5 * px}
                      textAnchor="middle"
                      fontSize={10 * px}
                      fontWeight={700}
                      fill="rgba(0,0,0,0.7)"
                    >
                      {cluster.members.length}
                    </text>
                  ) : (
                    <Icon
                      x={p.x - icon / 2}
                      y={p.y - icon / 2}
                      width={icon}
                      height={icon}
                      color="rgba(0,0,0,0.62)"
                      strokeWidth={2.2}
                      absoluteStrokeWidth={false}
                      pointerEvents="none"
                    />
                  ))}
                {secret && (
                  <g pointerEvents="none">
                    <circle
                      cx={p.x + p.r * 0.75}
                      cy={p.y - p.r * 0.75}
                      r={6 * px}
                      fill={PROTECTED}
                    />
                    <Lock
                      x={p.x + p.r * 0.75 - 4 * px}
                      y={p.y - p.r * 0.75 - 4 * px}
                      width={8 * px}
                      height={8 * px}
                      color="#fff"
                      strokeWidth={2.6}
                    />
                  </g>
                )}
                {pin && (
                  <Pin
                    x={p.x - p.r - 6 * px}
                    y={p.y - p.r - 6 * px}
                    width={10 * px}
                    height={10 * px}
                    style={{ color: ACCENT_TEXT }}
                    strokeWidth={2.4}
                    pointerEvents="none"
                  />
                )}
                {labelled.has(n.id) && (
                  <text
                    x={p.x}
                    y={p.y + p.r + 11 * px}
                    textAnchor="middle"
                    fontSize={10 * px}
                    fontWeight={isFocus || hover === n.id || isPicked ? 600 : undefined}
                    style={{ fill: "var(--foreground)", ...halo(px) }}
                  >
                    {nodeLabel(n)}
                  </text>
                )}
                <title>
                  {cluster
                    ? `${clusterLabel(n.id)} — ${tm("Click to show them one by one")}`
                    : `${n.label} — ${subtypeLabel(n.subtype) || tm(TYPE_NAME[n.type])}`}
                </title>
              </g>
            );
          })}
          {[...edgeLabels].map(
            ([key, label]) =>
              labelled.has(`edge:${key}`) && (
                <text
                  key={key}
                  x={label.x}
                  y={label.y}
                  textAnchor="middle"
                  fontSize={9 * px}
                  className="pointer-events-none"
                  style={{ fill: label.lit ? ACCENT_TEXT : inkText(0.6), ...halo(px) }}
                >
                  {label.text}
                </text>
              ),
          )}
        </g>
      </svg>

      <div className="absolute right-3 top-3 flex flex-col items-end gap-1.5">
        <div className="flex items-center gap-1.5">
          {searchOpen && (
            <div
              className="flex items-center gap-1 rounded-full border border-ink/10 px-2 py-1 text-[11px]"
              style={{ backgroundColor: "var(--surface-3)" }}
            >
              <input
                autoFocus
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setMatch(0);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") setMatch((m) => m + 1);
                  if (e.key === "Escape") {
                    setQuery("");
                    setSearchOpen(false);
                  }
                }}
                placeholder={tm("Find in the picture")}
                className="w-40 bg-transparent outline-none placeholder:text-ink/40"
              />
              {query.trim() && (
                <span className="tabular-nums text-ink/55">
                  {matches.length ? (match % matches.length) + 1 : 0} / {matches.length}
                </span>
              )}
            </div>
          )}
          <button
            className={button}
            title={tm("Find in the picture")}
            aria-pressed={searchOpen}
            style={searchOpen ? { color: ACCENT_TEXT } : undefined}
            onClick={() => {
              setSearchOpen((on) => !on);
              setQuery("");
            }}
          >
            <Search className="h-3.5 w-3.5" />
          </button>
        </div>
        <button
          className={button}
          title={tm("Zoom in")}
          onClick={() => setView((v) => ({ ...v, k: Math.min(6, v.k * 1.25) }))}
        >
          <ZoomIn className="h-3.5 w-3.5" />
        </button>
        <button
          className={button}
          title={tm("Zoom out")}
          onClick={() => setView((v) => ({ ...v, k: Math.max(0.3, v.k / 1.25) }))}
        >
          <ZoomOut className="h-3.5 w-3.5" />
        </button>
        <button
          className={button}
          title={tm("Fit to view")}
          onClick={() => {
            fitted.current = null;
            setView({ k: 1, x: 0, y: 0 });
          }}
        >
          <Crosshair className="h-3.5 w-3.5" />
        </button>
        {canFullscreen && (
          <button
            className={button}
            title={tm(fullscreen ? "Exit full screen" : "Full screen")}
            onClick={toggleFullscreen}
          >
            {fullscreen ? (
              <Minimize className="h-3.5 w-3.5" />
            ) : (
              <Maximize className="h-3.5 w-3.5" />
            )}
          </button>
        )}
        <button
          className={button}
          title={tm(mode === "3d" ? "2D view" : "3D view")}
          onClick={() => chooseMode(mode === "3d" ? "2d" : "3d")}
        >
          {mode === "3d" ? <Network className="h-3.5 w-3.5" /> : <Box className="h-3.5 w-3.5" />}
        </button>
        {mode === "3d" && (
          <button
            className={button}
            title={tm(spin ? "Stop rotating" : "Rotate")}
            onClick={() => setSpin((s) => !s)}
          >
            {spin ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
          </button>
        )}
        {pinned.size > 0 && (
          <button
            className={button}
            title={tm("Release pinned nodes")}
            onClick={() => setPinned(new Map())}
          >
            <PinOff className="h-3.5 w-3.5" />
          </button>
        )}
        {months.length > 0 && (
          <button
            className={button}
            title={tm("Time filter")}
            aria-pressed={timeOn}
            style={timeOn ? { color: ACCENT_TEXT } : undefined}
            onClick={() => {
              setTimeOn((on) => !on);
              setPlaying(false);
            }}
          >
            <CalendarRange className="h-3.5 w-3.5" />
          </button>
        )}
        <div className="relative">
          <button
            className={button}
            title={tm("Save the picture")}
            disabled={exporting}
            onClick={() => setExportOpen((o) => !o)}
          >
            <Download className="h-3.5 w-3.5" />
          </button>
          {exportOpen && (
            <div
              className={`absolute right-9 top-0 w-56 ${panel}`}
              style={{ backgroundColor: "var(--surface-3)" }}
            >
              <div className="mb-1 text-ink/65">
                {tm(
                  "The picture carries your name and the case reference, and the export is logged.",
                )}
              </div>
              <div className="flex gap-1.5">
                {(["png", "svg"] as const).map((format) => (
                  <button
                    key={format}
                    onClick={() => void exportPicture(format)}
                    className="rounded-full border border-ink/10 px-3 py-1 uppercase hover:bg-ink/10"
                  >
                    {format}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
        <button
          className={button}
          title={tm("Legend")}
          aria-pressed={legend}
          style={legend ? { color: ACCENT_TEXT } : undefined}
          onClick={() =>
            setLegend((on) => {
              try {
                window.localStorage.setItem(LEGEND_KEY, on ? "0" : "1");
              } catch {
                /* private mode: for this view only */
              }
              return !on;
            })
          }
        >
          <Info className="h-3.5 w-3.5" />
        </button>
      </div>

      {mode === "3d" && (
        <div className="pointer-events-none absolute left-3 top-3 text-[11px] text-ink/45">
          {tm("Drag to rotate, scroll to zoom")}
        </div>
      )}

      {legend && (
        <div
          className={`absolute w-[250px] overflow-y-auto ${panel}`}
          style={{
            backgroundColor: "var(--surface-3)",
            right: 48,
            top: 48,
            maxHeight: "calc(100% - 6rem)",
          }}
        >
          <div className="mb-0.5 font-semibold text-ink/80">{tm("Legend")}</div>
          <div className="mb-1 text-[10px] text-ink/50">
            {tm("Click a row to hide or show it.")}
          </div>
          {legendTypes.map(([type, count]) => (
            <button
              key={type}
              onClick={() => setHiddenTypes((s) => toggle(s, type))}
              className={`flex w-full items-center gap-2 rounded px-1 text-left hover:bg-ink/5 ${
                hiddenTypes.has(type) ? "text-ink/35 line-through" : "text-ink/75"
              }`}
            >
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{
                  backgroundColor: TYPE_COLOR[type],
                  opacity: hiddenTypes.has(type) ? 0.3 : 1,
                }}
              />
              <span className="flex-1">{tm(TYPE_NAME[type])}</span>
              <span className="tabular-nums text-ink/50">{count}</span>
            </button>
          ))}
          <div className="mt-1.5 border-t border-ink/10 pt-1.5 font-semibold text-ink/70">
            {tm("Relation types")}
          </div>
          {legendRels.map(([type, count]) => (
            <button
              key={type}
              onClick={() => setHiddenRels((s) => toggle(s, type))}
              className={`flex w-full items-center gap-2 rounded px-1 text-left hover:bg-ink/5 ${
                hiddenRels.has(type) ? "text-ink/35 line-through" : "text-ink/75"
              }`}
            >
              <span className="flex-1 truncate">{relLabel(type)}</span>
              <span className="tabular-nums text-ink/50">{count}</span>
            </button>
          ))}
          <svg width="0" height="0" className="absolute">
            <defs>
              <marker
                id={`${uid}-legend-arrow`}
                viewBox="0 0 8 8"
                refX={8}
                refY={4}
                markerWidth={6}
                markerHeight={6}
                orient="auto"
              >
                <path d="M0,0 L8,4 L0,8 z" style={{ fill: inkA(0.6) }} />
              </marker>
            </defs>
          </svg>
          <div className="mt-1.5 space-y-0.5 border-t border-ink/10 pt-1.5 text-ink/70">
            {(
              [
                ["From a register (structured data)", {}],
                ["From the text of a document", { strokeDasharray: DASH.text }],
                ["Mentioned together only", { strokeDasharray: DASH.comention }],
                ["Directed relation", { markerEnd: `url(#${uid}-legend-arrow)` }],
                ["Thicker line: several relations", { strokeWidth: 3 }],
                ...(anyProtected
                  ? ([["Informant data (protected)", { stroke: PROTECTED }]] as const)
                  : []),
              ] as const
            ).map(([text, extra]) => (
              <div key={text} className="flex items-center gap-2">
                <svg width="26" height="8" className="shrink-0">
                  <line
                    x1="1"
                    y1="4"
                    x2="24"
                    y2="4"
                    strokeWidth={1.4}
                    style={{ stroke: "stroke" in extra ? extra.stroke : inkA(0.6) }}
                    {...extra}
                  />
                </svg>
                <span>{tm(text)}</span>
              </div>
            ))}
            <div className="flex items-center gap-2">
              <svg width="26" height="14" className="shrink-0">
                <circle cx="6" cy="7" r="3" style={{ fill: inkA(0.45) }} />
                <circle cx="19" cy="7" r="5.5" style={{ fill: inkA(0.45) }} />
              </svg>
              <span>{tm("Larger circle: mentioned more often")}</span>
            </div>
            <div className="flex items-center gap-2">
              <svg width="26" height="16" className="shrink-0">
                <circle cx="13" cy="8" r="5" style={{ fill: inkA(0.45) }} />
                <circle
                  cx="13"
                  cy="8"
                  r="7"
                  fill="none"
                  strokeDasharray="3 2"
                  style={{ stroke: inkA(0.6) }}
                />
              </svg>
              <span>{tm("Group: click to show them one by one")}</span>
            </div>
          </div>
          <div className="mt-1.5 border-t border-ink/10 pt-1.5 text-[10px] text-ink/50">
            {tm(
              "Double-click adds a node's relations; Shift+click picks two nodes; drag pins a node.",
            )}
          </div>
        </div>
      )}

      {timed && (
        <div
          className={`absolute bottom-8 left-3 w-[min(440px,calc(100%-290px))] min-w-[240px] ${panel}`}
          style={{ backgroundColor: "var(--surface-3)" }}
        >
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="flex items-center gap-2 font-semibold text-ink/80">
              {tm("Time filter")}
              <button
                className={button}
                title={tm(playing ? "Pause" : "Play the period")}
                onClick={() => {
                  if (!playing && range[1] >= months.length - 1) setRange(([f]) => [f, f]);
                  setPlaying((p) => !p);
                }}
              >
                {playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
              </button>
            </span>
            <span className="tabular-nums text-ink/70">
              {from} – {to}
            </span>
          </div>
          {(
            [
              ["Period start", 0],
              ["Period end", 1],
            ] as const
          ).map(([text, i]) => (
            <label key={text} className="flex items-center gap-2 text-ink/65">
              <span className="w-28 shrink-0">{tm(text)}</span>
              <input
                type="range"
                min={0}
                max={Math.max(0, months.length - 1)}
                value={range[i]}
                onChange={(e) => {
                  const value = Number(e.target.value);
                  setPlaying(false);
                  setRange((r) => (i === 0 ? [value, r[1]] : [r[0], value]));
                }}
                className="w-full"
                style={{ accentColor: ACCENT_TEXT }}
              />
            </label>
          ))}
          <div className="mt-1 flex items-center justify-between gap-2 text-ink/65">
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={undated}
                onChange={(e) => setUndated(e.target.checked)}
              />
              {tm("Include undated relations")}
            </label>
            <span className="tabular-nums">
              {tm("Relations shown")}: {edges.length} / {display.edges.length}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
