import { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  Box,
  CalendarRange,
  Crosshair,
  Info,
  Maximize,
  Minimize,
  Network,
  Pause,
  Play,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import type { GraphDocuments, GraphEdge, GraphNode } from "@/lib/api/graph.functions";
import { useLang } from "./i18n";
import { ACCENT_TEXT, inkA, inkText } from "./brand";
import {
  edgeInPeriod,
  layoutGraph,
  layoutGraph3d,
  monthsOf,
  placeLabels,
  project,
} from "./graph-layout";
import { pairKey, TYPE_COLOR, TYPE_NAME, type RelationInfo } from "./graph-style";

type Graph = { nodes: GraphNode[]; edges: GraphEdge[]; documents: GraphDocuments };
type Mode = "2d" | "3d";
type Pt = { x: number; y: number; r: number; scale: number; depth: number };

const MODE_KEY = "vvs.graph.view";
/** A band in the background colour behind text, `px` user units per screen pixel. */
const halo = (px: number) =>
  ({ stroke: "var(--surface-2)", strokeWidth: 3 * px, paintOrder: "stroke" }) as const;
const FADED = 0.13;

function storedMode(): Mode {
  try {
    return window.localStorage.getItem(MODE_KEY) === "3d" ? "3d" : "2d";
  } catch {
    return "2d";
  }
}

/** The relationship picture (E26): 2D, or 3D to turn around; zoom, fit, full
 *  screen, a legend, arrows for directed relations, a time filter, and the
 *  neighbours of the node under the pointer brought forward. */
export function GraphCanvas({
  graph,
  focus,
  selectedEdge,
  relLabel,
  relInfo,
  onNode,
  onEdge,
}: {
  graph: Graph;
  focus: string | null;
  selectedEdge: string | null;
  relLabel: (type: string) => string;
  relInfo: (type: string) => RelationInfo;
  onNode: (id: string) => void;
  onEdge: (id: string) => void;
}) {
  const { tm } = useLang();
  const uid = useId().replace(/:/g, "");
  const box = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<Mode>("2d");
  const [view, setView] = useState({ k: 1, x: 0, y: 0 });
  const [rot, setRot] = useState({ yaw: 0.5, pitch: -0.35 });
  const [spin, setSpin] = useState(true);
  const [hover, setHover] = useState<string | null>(null);
  const [legend, setLegend] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [canFullscreen, setCanFullscreen] = useState(false);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const drag = useRef<{
    x: number;
    y: number;
    vx: number;
    vy: number;
    yaw: number;
    pitch: number;
  } | null>(null);
  const moved = useRef(false);

  const nodes = useMemo(() => graph.nodes.filter(Boolean), [graph]);
  const months = useMemo(() => monthsOf(graph.edges), [graph.edges]);
  const [timeOn, setTimeOn] = useState(false);
  const [range, setRange] = useState<[number, number]>([0, 0]);
  const [undated, setUndated] = useState(true);

  useEffect(() => setMode(storedMode()), []);
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
    setView({ k: 1, x: 0, y: 0 });
    setRange([0, Math.max(0, months.length - 1)]);
    setHover(null);
  }, [graph, months.length]);
  useEffect(() => {
    setCanFullscreen(Boolean(document.fullscreenEnabled));
    const sync = () => setFullscreen(document.fullscreenElement === box.current);
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);
  // The 3D picture turns slowly until the user takes it in hand.
  useEffect(() => {
    if (mode !== "3d" || !spin || hover) return;
    let frame = 0;
    const tick = () => {
      setRot((r) => ({ ...r, yaw: r.yaw + 0.004 }));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [mode, spin, hover]);

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

  const timed = timeOn && months.length > 0;
  const from = months[Math.min(range[0], range[1])] ?? "";
  const to = months[Math.max(range[0], range[1])] ?? "";
  const edges = useMemo(
    () =>
      timed
        ? graph.edges.filter((e) => edgeInPeriod(e, from, to, undated, relInfo(e.type).ongoing))
        : graph.edges,
    [graph.edges, timed, from, to, undated, relInfo],
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

  // The layout uses every edge, so the picture holds still while the period moves.
  const layout2d = useMemo(
    () =>
      layoutGraph(
        nodes.map((n) => n.id),
        graph.edges,
        focus,
      ),
    [nodes, graph.edges, focus],
  );
  const layout3d = useMemo(
    () =>
      mode === "3d"
        ? layoutGraph3d(
            nodes.map((n) => n.id),
            graph.edges,
            focus,
          )
        : null,
    [mode, nodes, graph.edges, focus],
  );

  const maxMentions = Math.max(1, ...nodes.map((n) => n.mentions));
  const radius = (n: GraphNode) => 6 + 8 * Math.sqrt(n.mentions / maxMentions);

  const frame = useMemo(() => {
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

  // User units per CSS pixel at zoom 1 (the viewBox is fitted into the box), and
  // per screen pixel now: text, strokes and circles keep their size on screen.
  const unit = Math.max(frame.w / size.w, frame.h / size.h);
  const px = unit / view.k;

  const points = useMemo(() => {
    const out = new Map<string, Pt>();
    for (const n of nodes) {
      const r = radius(n) * unit;
      if (layout3d) {
        const p = layout3d.get(n.id);
        if (!p) continue;
        const v = project(p, rot.yaw, rot.pitch, frame.distance);
        out.set(n.id, { x: v.x, y: v.y, r: r * v.scale, scale: v.scale, depth: v.depth });
      } else {
        const p = layout2d.get(n.id);
        if (p) out.set(n.id, { x: p.x, y: p.y, r, scale: 1, depth: 0 });
      }
    }
    return out;
    // radius depends on nodes only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, layout2d, layout3d, rot, frame.distance, unit]);

  // Nearness 0..1 in 3D: the far side of the picture is drawn fainter.
  const depths = [...points.values()].map((p) => p.depth);
  const dMin = Math.min(0, ...depths);
  const dMax = Math.max(0, ...depths);
  const near = (p: Pt) => (layout3d && dMax > dMin ? (p.depth - dMin) / (dMax - dMin) : 1);

  const selectedPair = selectedEdge ? pairs.find(([key]) => key === selectedEdge)?.[1] : undefined;
  const highlight = useMemo(() => {
    if (hover) return new Set([hover, ...(neighbours.get(hover) ?? [])]);
    if (selectedPair) return new Set([selectedPair[0].a, selectedPair[0].b]);
    return null;
  }, [hover, neighbours, selectedPair]);
  const live = useMemo(() => {
    if (!timed) return null;
    const out = new Set<string>(focus ? [focus] : []);
    for (const e of edges) out.add(e.a).add(e.b);
    return out;
  }, [timed, edges, focus]);
  const dim = (id: string) => (highlight && !highlight.has(id)) || (live && !live.has(id));

  const k = view.k;
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
      const lit = key === selectedEdge || (hover !== null && (first === hover || second === hover));
      if (!lit && (!showEdgeLabels || highlight)) continue;
      const counts = new Map<string, number>();
      for (const x of list) counts.set(x.type, (counts.get(x.type) ?? 0) + 1);
      const text =
        [...counts.entries()]
          .map(([type, n]) => (n > 1 ? `${relLabel(type)} ×${n}` : relLabel(type)))
          .join(", ") + (list.length === 1 && list[0].start ? ` · ${list[0].start}` : "");
      // Away from the focused node, where every line starts.
      const t = first === focus ? 0.62 : second === focus ? 0.38 : 0.5;
      out.set(key, {
        x: p.x + (q.x - p.x) * t,
        y: p.y + (q.y - p.y) * t - 3 * px,
        text,
        lit,
      });
    }
    return out;
  }, [pairs, points, selectedEdge, hover, showEdgeLabels, highlight, relLabel, focus, px]);

  const labelled = useMemo(() => {
    const out = [];
    for (const n of nodes) {
      const p = points.get(n.id);
      if (!p || dim(n.id)) continue;
      const text = n.label.length > 28 ? `${n.label.slice(0, 27)}…` : n.label;
      out.push({
        id: n.id,
        x: p.x,
        y: p.y + p.r + 9 * px,
        w: (text.length * 5.6 + 6) * px,
        h: 13 * px,
        priority:
          (n.id === focus ? 1e7 : 0) +
          (highlight?.has(n.id) ? 1e6 : 0) +
          n.mentions * 10 +
          near(p) * 100,
        force: n.id === focus || n.id === hover,
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
    // dim/near read the same memoised inputs
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, points, highlight, live, focus, hover, px, edgeLabels, selectedEdge]);
  const types = useMemo(() => {
    const counts = new Map<GraphNode["type"], number>();
    for (const n of nodes) counts.set(n.type, (counts.get(n.type) ?? 0) + 1);
    return [...counts.entries()];
  }, [nodes]);

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

  const button =
    "flex h-7 w-7 items-center justify-center rounded-full border border-ink/10 text-ink/70 hover:bg-ink/10 hover:text-ink";
  const panel = "rounded-lg border border-ink/10 px-3 py-2 text-[11px] shadow-sm";

  return (
    <div
      ref={box}
      className="relative h-full w-full"
      style={fullscreen ? { backgroundColor: "var(--surface-2)" } : undefined}
    >
      <svg
        className={`h-full w-full ${layout3d ? "cursor-move" : "cursor-grab active:cursor-grabbing"}`}
        viewBox={`${frame.minX} ${frame.minY} ${frame.w} ${frame.h}`}
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
          if (layout3d && !e.shiftKey) {
            setRot({
              yaw: d.yaw + ddx * 0.008,
              pitch: Math.max(-1.45, Math.min(1.45, d.pitch + ddy * 0.008)),
            });
            return;
          }
          const scale = frame.w / (e.currentTarget.clientWidth || 1) / k;
          setView((v) => ({ ...v, x: d.vx + ddx * scale, y: d.vy + ddy * scale }));
        }}
        onPointerUp={() => (drag.current = null)}
        onPointerLeave={() => (drag.current = null)}
      >
        <defs>
          {(["n", "a"] as const).map((kind) => (
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
              <path d="M0,0 L8,4 L0,8 z" style={{ fill: kind === "a" ? ACCENT_TEXT : inkA(0.5) }} />
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
            const lit = active || (hover !== null && (first === hover || second === hover));
            const faded = highlight !== null && !lit;
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
            const marker = `url(#${uid}-arrow-${active || lit ? "a" : "n"})`;
            return (
              <g
                key={key}
                onClick={() => !moved.current && onEdge(key)}
                className="cursor-pointer"
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
                  style={{ stroke: active || lit ? ACCENT_TEXT : inkA(0.32) }}
                  strokeWidth={
                    (active ? 2.4 : lit ? 1.8 : 1.2) + Math.min(list.length - 1, 4) * 0.5
                  }
                  strokeDasharray={list.every((x) => x.weak) ? "4 4" : undefined}
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
            const isFocus = n.id === focus;
            const faded = dim(n.id);
            return (
              <g
                key={n.id}
                onClick={() => !moved.current && onNode(n.id)}
                onPointerEnter={() => setHover(n.id)}
                onPointerLeave={() => setHover((h) => (h === n.id ? null : h))}
                className="cursor-pointer"
                opacity={(faded ? FADED : 1) * (layout3d ? 0.45 + 0.55 * near(p) : 1)}
              >
                <circle
                  cx={p.x}
                  cy={p.y}
                  r={p.r}
                  fill={TYPE_COLOR[n.type]}
                  fillOpacity={isFocus || highlight?.has(n.id) ? 1 : 0.85}
                  stroke="rgba(0,0,0,0.4)"
                  style={isFocus ? { stroke: "var(--ink)" } : undefined}
                  strokeWidth={isFocus ? 2.5 : 1}
                  vectorEffect="non-scaling-stroke"
                />
                {labelled.has(n.id) && (
                  <text
                    x={p.x}
                    y={p.y + p.r + 11 * px}
                    textAnchor="middle"
                    fontSize={10 * px}
                    fontWeight={isFocus || hover === n.id ? 600 : undefined}
                    style={{ fill: "var(--foreground)", ...halo(px) }}
                  >
                    {n.label.length > 28 ? `${n.label.slice(0, 27)}…` : n.label}
                  </text>
                )}
                <title>{`${n.label} — ${tm(TYPE_NAME[n.type])}`}</title>
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

      <div className="absolute right-3 top-3 flex flex-col gap-1.5">
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
          onClick={() => setView({ k: 1, x: 0, y: 0 })}
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
        {months.length > 0 && (
          <button
            className={button}
            title={tm("Time filter")}
            aria-pressed={timeOn}
            style={timeOn ? { color: ACCENT_TEXT } : undefined}
            onClick={() => setTimeOn((on) => !on)}
          >
            <CalendarRange className="h-3.5 w-3.5" />
          </button>
        )}
        <button
          className={button}
          title={tm("Legend")}
          aria-pressed={legend}
          style={legend ? { color: ACCENT_TEXT } : undefined}
          onClick={() => setLegend((on) => !on)}
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
          className={`absolute bottom-8 right-3 max-w-[240px] ${panel}`}
          style={{ backgroundColor: "var(--surface-3)" }}
        >
          <div className="mb-1 font-semibold text-ink/80">{tm("Legend")}</div>
          {types.map(([type, count]) => (
            <div key={type} className="flex items-center gap-2 text-ink/75">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: TYPE_COLOR[type] }}
              />
              <span className="flex-1">{tm(TYPE_NAME[type])}</span>
              <span className="tabular-nums text-ink/50">{count}</span>
            </div>
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
                ["Directed relation", { markerEnd: `url(#${uid}-legend-arrow)` }],
                ["Mutual relation", {}],
                ["Mentioned together only", { strokeDasharray: "4 3" }],
                ["Thicker line: several relations", { strokeWidth: 3 }],
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
                    style={{ stroke: inkA(0.6) }}
                    {...extra}
                  />
                </svg>
                <span>{tm(text)}</span>
              </div>
            ))}
            <div className="flex items-center gap-2">
              <svg width="26" height="12" className="shrink-0">
                <circle cx="6" cy="6" r="3" style={{ fill: inkA(0.45) }} />
                <circle cx="19" cy="6" r="5.5" style={{ fill: inkA(0.45) }} />
              </svg>
              <span>{tm("Larger circle: mentioned more often")}</span>
            </div>
          </div>
        </div>
      )}

      {timed && (
        <div
          className={`absolute bottom-8 left-3 w-[min(420px,calc(100%-280px))] min-w-[240px] ${panel}`}
          style={{ backgroundColor: "var(--surface-3)" }}
        >
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="font-semibold text-ink/80">{tm("Time filter")}</span>
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
              {tm("Relations shown")}: {edges.length} / {graph.edges.length}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
