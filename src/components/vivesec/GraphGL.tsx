// The WebGL relationship picture (E26f): three.js through 3d-force-graph, loaded
// only in the browser and only when the picture is opened (its own chunk). The
// same component draws 3D (a turning scene) and 2D (a flat layout seen from
// above), so a large picture never falls back to the CPU-drawn SVG; the SVG
// view stays for browsers without WebGL. What is drawn, filtered, grouped and
// highlighted is decided by GraphCanvas; this file only renders it.
//
// 3d-force-graph runs the layout, the camera, picking and dragging; the picture
// itself is drawn with a few instanced meshes (spheres, icons, arrows), point
// sprites (particles) and line batches, so thousands of nodes cost tens of
// draw calls instead of thousands -- an office laptop's integrated GPU keeps up.
// No post-processing: the scene renders straight to the screen.

import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import ForceGraph3D, { type ForceGraph3DInstance } from "3d-force-graph";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import { LineSegments2 } from "three/examples/jsm/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/examples/jsm/lines/LineSegmentsGeometry.js";
import { Lock, Pin, type LucideIcon } from "lucide-react";
import type { GraphEntityType } from "@/lib/api/graph.functions";
import { placeLabels, type LabelCandidate } from "./graph-layout";
import type { EdgeSource } from "./graph-model";
import { FADED, PROTECTED, SUBTYPE_ICON, TYPE_COLOR, TYPE_ICON } from "./graph-style";

export type GlNode = {
  id: string;
  type: GraphEntityType;
  subtype: string;
  /** The short label under the node. */
  label: string;
  /** The tooltip. */
  title: string;
  /** 0..1: how often it is mentioned, relative to the picture. */
  weight: number;
  /** Members of a group of leaves, 0 for a plain node. */
  cluster: number;
  secret: boolean;
};

export type GlPair = {
  key: string;
  a: string;
  b: string;
  count: number;
  dash: EdgeSource;
  secret: boolean;
  /** A directed relation from `a` to `b` / from `b` to `a`. */
  forward: boolean;
  backward: boolean;
  /** A group's line: not clickable. */
  grouped: boolean;
  label: string;
};

export type GlView = {
  focus: string | null;
  hover: string | null;
  current: string | null;
  picked: string[];
  highlight: Set<string> | null;
  live: Set<string> | null;
  selectedEdge: string | null;
  emphasisPairs: Set<string> | null;
  /** The pairs the time filter keeps (null: all). */
  shown: Set<string> | null;
  /** Every pair's label while the picture is small. */
  edgeLabels: boolean;
};

/** A node on the screen, in CSS pixels (the SVG picture's point). */
export type GlPoint = { x: number; y: number; r: number; scale: number; depth: number };
export type GlSnapshot = { points: Map<string, GlPoint>; w: number; h: number };

export type GlHandle = {
  zoom: (factor: number) => void;
  fit: () => void;
  flyTo: (id: string) => void;
  snapshot: () => GlSnapshot;
  releasePins: () => void;
};

type Props = {
  dims: 2 | 3;
  nodes: GlNode[];
  pairs: GlPair[];
  view: GlView;
  spin: boolean;
  onNode: (id: string, shift: boolean) => void;
  onExpand: (id: string) => void;
  onEdge: (key: string) => void;
  onCluster: (id: string) => void;
  onHover: (id: string | null) => void;
  onPins: (ids: string[]) => void;
  onInteract: () => void;
  onFail: () => void;
};

type N = {
  id: string;
  x?: number;
  y?: number;
  z?: number;
  fx?: number;
  fy?: number;
  fz?: number;
  vx?: number;
  vy?: number;
  vz?: number;
  __d: GlNode;
  __r: number;
  __born: number;
  /** Index in the instanced meshes. */
  __i: number;
  /** Size now (grows in, swells under the pointer) and the size it heads to. */
  __grow: number;
  __target: number;
  /** Relations, relative to the busiest node of the picture (0..1). */
  __deg: number;
};

type L = { id: string; source: string | N; target: string | N; __p: GlPair };

type Tone = "n" | "p" | "a";

type Batch = {
  line: LineSegments2;
  ends: N[];
  positions: Float32Array;
  dashed: boolean;
};

type Arrow = { from: N; to: N };

type Particle = { from: N; to: N; t: number; speed: number; size: number; color: THREE.Color };

/** The picture's colours, from the theme of the page (light or dark). */
type Palette = {
  key: string;
  /** Background (--surface-2), lines (--ink), text (--foreground), accent (--brand-soft). */
  bg: string;
  ink: string;
  fg: string;
  accent: string;
  /** "r,g,b" of the text and of the background, for the overlay. */
  fgRgb: string;
  bgRgb: string;
};

const ICON_INK = "#10131d";
const PIN = "#8C49E9";
/** Above this many nodes the spheres carry no icon. */
const ICONS_UP_TO = 1500;
const LABELS_MAX = 220;
const ARROW = 3.5;
const PARTICLES = 800;
/** Settled layouts kept for coming back (one step and back, Back). */
const LAYOUTS_MAX = 8;
/** Last positions of single nodes kept (start points of new pictures). */
const MEMORY_MAX = 20000;
const ICONS: [string, LucideIcon][] = [
  ...Object.entries(SUBTYPE_ICON),
  ...Object.entries(TYPE_ICON).map(([k, v]) => [`type:${k}`, v] as [string, LucideIcon]),
  ["lock", Lock],
  ["pin", Pin],
];

const radiusOf = (d: GlNode) => (d.cluster ? 5.5 : 3 + 4.2 * d.weight);
/** The depth of a node; NaN or missing (a 2D layout) is the plane. */
const zOf = (n: { z?: number }) => (Number.isFinite(n.z) ? (n.z as number) : 0);
/** One picture: the view (2D/3D), its nodes and its relations. */
const pictureKey = (dims: number, nodes: string, links: string) => `${dims}#${nodes}#${links}`;
/** 0..1 from a node id: the same offsets every time a picture is built. */
function hash01(id: string, salt: number): number {
  let h = 2166136261 ^ salt;
  for (let i = 0; i < id.length; i++) h = Math.imul(h ^ id.charCodeAt(i), 16777619);
  return ((h >>> 0) % 100000) / 100000;
}
const iconKey = (d: GlNode) => (SUBTYPE_ICON[d.subtype] ? d.subtype : `type:${d.type}`);

/** A CSS colour as the browser resolves it ("#rrggbb" or "rgb(...)"). */
function cssColor(value: string, fallback: string): string {
  const ctx = document.createElement("canvas").getContext("2d");
  if (!ctx || !value) return fallback;
  ctx.fillStyle = fallback;
  ctx.fillStyle = value;
  return ctx.fillStyle;
}

function rgbOf(color: string): string {
  const hex = new THREE.Color(color).getHex();
  return `${(hex >> 16) & 255},${(hex >> 8) & 255},${hex & 255}`;
}

function readPalette(el: Element): Palette {
  const style = window.getComputedStyle(el);
  const pick = (name: string, fallback: string) =>
    cssColor(style.getPropertyValue(name).trim(), fallback);
  const bg = pick("--surface-2", "#152027");
  const ink = pick("--ink", "#ffffff");
  const fg = pick("--foreground", "#eceff1");
  const accent = pick("--brand-soft", "#b48cf5");
  return {
    key: [bg, ink, fg, accent].join("|"),
    bg,
    ink,
    fg,
    accent,
    fgRgb: rgbOf(fg),
    bgRgb: rgbOf(bg),
  };
}

function glowTexture(): THREE.Texture {
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.35, "rgba(255,255,255,0.9)");
  g.addColorStop(0.6, "rgba(255,255,255,0.25)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  return new THREE.CanvasTexture(canvas);
}

function light(color: string, intensity: number, x: number, y: number, z: number) {
  const out = new THREE.DirectionalLight(color, intensity);
  out.position.set(x, y, z);
  return out;
}

/** Point sprites with a size and a tint each (the particles). */
/** One opacity per instance (1: shown, FADED: out of the period/highlight). */
function opacityAttribute(count: number) {
  return new THREE.InstancedBufferAttribute(new Float32Array(count).fill(1), 1);
}

/** A built-in material that reads `instanceOpacity` and multiplies its alpha. */
function withInstanceOpacity<M extends THREE.Material>(material: M): M {
  material.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace(
        "#include <common>",
        "#include <common>\nattribute float instanceOpacity;\nvarying float vInstanceOpacity;",
      )
      .replace(
        "#include <begin_vertex>",
        "#include <begin_vertex>\nvInstanceOpacity = instanceOpacity;",
      );
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nvarying float vInstanceOpacity;")
      .replace(
        "#include <dithering_fragment>",
        "#include <dithering_fragment>\ngl_FragColor.a *= vInstanceOpacity;",
      );
  };
  material.customProgramCacheKey = () => "instance-opacity";
  return material;
}

function spriteMaterial(map: THREE.Texture) {
  return new THREE.ShaderMaterial({
    uniforms: { map: { value: map }, scale: { value: 1 } },
    vertexShader: `
      attribute float size;
      attribute vec3 tint;
      uniform float scale;
      varying vec3 vTint;
      void main() {
        vTint = tint;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = size * scale / max(0.001, -mv.z);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: `
      uniform sampler2D map;
      varying vec3 vTint;
      void main() {
        float a = texture2D(map, gl_PointCoord).a;
        gl_FragColor = vec4(vTint, a);
        #include <colorspace_fragment>
      }`,
    transparent: true,
    depthWrite: false,
  });
}

function spritePoints(capacity: number, map: THREE.Texture) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(capacity * 3), 3));
  geometry.setAttribute("tint", new THREE.BufferAttribute(new Float32Array(capacity * 3), 3));
  geometry.setAttribute("size", new THREE.BufferAttribute(new Float32Array(capacity), 1));
  const points = new THREE.Points(geometry, spriteMaterial(map));
  points.frustumCulled = false;
  points.raycast = () => {};
  return points;
}

export const GraphGL = forwardRef<GlHandle, Props>(function GraphGL(props, ref) {
  const host = useRef<HTMLDivElement>(null);
  const mount = useRef<HTMLDivElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const iconHost = useRef<HTMLDivElement>(null);
  const fgRef = useRef<ForceGraph3DInstance | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;
  const nodesRef = useRef(new Map<string, N>());
  const orderRef = useRef<N[]>([]);
  const linksRef = useRef<L[]>([]);
  const keysRef = useRef({ nodes: "", links: "", dims: 3 as number });
  const dirty = useRef(true);
  const fitPending = useRef(false);
  const lastClick = useRef({ id: "", at: 0 });
  const started = useRef(false);
  const interacted = useRef(false);
  const rankedRef = useRef<string[]>([]);
  const memoryRef = useRef(new Map<string, { x: number; y: number; z: number }>());
  /** Whole, settled layouts of the last pictures, by view, nodes and relations. */
  const layoutsRef = useRef(new Map<string, Map<string, { x: number; y: number; z: number }>>());
  /** The layout on screen has come to rest (the simulation stopped). */
  const settledRef = useRef(false);

  function saveLayout(
    keys: { nodes: string; links: string; dims: number },
    layout: Map<string, { x: number; y: number; z: number }>,
  ) {
    const layouts = layoutsRef.current;
    const key = pictureKey(keys.dims, keys.nodes, keys.links);
    layouts.delete(key);
    layouts.set(key, layout);
    while (layouts.size > LAYOUTS_MAX) layouts.delete(layouts.keys().next().value!);
  }
  const focused = useRef<string | null>(null);
  const tmp = useRef({
    v: new THREE.Vector3(),
    w: new THREE.Vector3(),
    q: new THREE.Quaternion(),
    m: new THREE.Matrix4(),
    s: new THREE.Vector3(),
    c: new THREE.Color(),
    z: new THREE.Vector3(0, 0, 1),
    bg: new THREE.Color("#152027"),
  });
  const palRef = useRef<Palette | null>(null);
  const pal = (): Palette =>
    palRef.current ?? {
      key: "",
      bg: "#152027",
      ink: "#ffffff",
      fg: "#eceff1",
      accent: "#b48cf5",
      fgRgb: "236,239,241",
      bgRgb: "21,32,39",
    };
  const toneColor = (tone: Tone) =>
    tone === "a" ? pal().accent : tone === "p" ? PROTECTED : pal().ink;

  /** The theme's colours now: on a change the scene and the highlight follow. */
  function syncPalette() {
    const el = host.current;
    const fg = fgRef.current;
    if (!el || !fg) return;
    const next = readPalette(el);
    if (next.key === palRef.current?.key) return;
    palRef.current = next;
    tmp.current.bg.set(next.bg);
    const scene = fg.scene();
    if (scene.background instanceof THREE.Color) scene.background.set(next.bg);
    else scene.background = new THREE.Color(next.bg);
    if (scene.fog instanceof THREE.FogExp2) scene.fog.color.set(next.bg);
    dirty.current = true;
  }
  const gfx = useRef<{
    group: THREE.Group;
    glowMap?: THREE.Texture;
    icons: Map<string, { canvas: HTMLCanvasElement; texture: THREE.CanvasTexture }>;
    pickGeo: THREE.SphereGeometry;
    pickMat: THREE.MeshBasicMaterial;
    sphere?: THREE.SphereGeometry;
    cores?: THREE.InstancedMesh;
    iconMeshes: Map<string, { mesh: THREE.InstancedMesh; nodes: N[] }>;
    plane: THREE.PlaneGeometry;
    batches: Batch[];
    arrowGeo: THREE.ConeGeometry;
    arrows?: THREE.InstancedMesh;
    arrowList: Arrow[];
    particles?: THREE.Points;
    live: Particle[];
    lit: Set<string>;
    radius: number;
    frames: number;
    size: { w: number; h: number };
  }>({
    group: new THREE.Group(),
    icons: new Map(),
    pickGeo: new THREE.SphereGeometry(1, 8, 6),
    pickMat: new THREE.MeshBasicMaterial({ visible: false }),
    iconMeshes: new Map(),
    plane: new THREE.PlaneGeometry(1, 1),
    batches: [],
    arrowGeo: (() => {
      const cone = new THREE.ConeGeometry(ARROW * 0.25, ARROW, 10);
      cone.translate(0, ARROW / 2, 0);
      cone.rotateX(Math.PI / 2);
      return cone;
    })(),
    arrowList: [],
    live: [],
    lit: new Set(),
    radius: 100,
    frames: 0,
    size: { w: 800, h: 600 },
  });

  function isLit(p: GlPair, v: GlView) {
    return (
      p.key === v.selectedEdge ||
      (v.hover !== null && (p.a === v.hover || p.b === v.hover)) ||
      (!v.hover && Boolean(v.emphasisPairs?.has(p.key)))
    );
  }

  function dimmed(id: string, v: GlView) {
    return Boolean((v.highlight && !v.highlight.has(id)) || (v.live && !v.live.has(id)));
  }

  function disposeMesh(
    obj?: THREE.Object3D & {
      geometry?: THREE.BufferGeometry;
      material?: THREE.Material | THREE.Material[];
    },
    keepGeometry = false,
  ) {
    if (!obj) return;
    obj.parent?.remove(obj);
    if (!keepGeometry) obj.geometry?.dispose();
    const mats = Array.isArray(obj.material) ? obj.material : obj.material ? [obj.material] : [];
    for (const m of mats) m.dispose();
  }

  /** The per-node meshes for the nodes now in the picture. */
  function buildNodes() {
    const g = gfx.current;
    const order = orderRef.current;
    const count = order.length;
    disposeMesh(g.cores, true);
    for (const { mesh } of g.iconMeshes.values()) disposeMesh(mesh);
    g.iconMeshes.clear();
    const segments = count > 2000 ? 10 : count > 500 ? 14 : 24;
    if (!g.sphere || g.sphere.parameters.widthSegments !== segments) {
      g.sphere?.dispose();
      g.sphere = new THREE.SphereGeometry(1, segments, Math.max(8, segments - 4));
    }
    const cores = new THREE.InstancedMesh(
      g.sphere,
      withInstanceOpacity(
        new THREE.MeshPhongMaterial({
          color: "#ffffff",
          shininess: 80,
          specular: "#555a6e",
          transparent: true,
        }),
      ),
      Math.max(1, count),
    );
    g.sphere.setAttribute("instanceOpacity", opacityAttribute(Math.max(1, count)));
    cores.count = count;
    cores.frustumCulled = false;
    cores.raycast = () => {};
    for (let i = 0; i < count; i++)
      cores.setColorAt(i, tmp.current.c.set(TYPE_COLOR[order[i].__d.type]));
    g.cores = cores;
    g.group.add(cores);
    if (count <= ICONS_UP_TO) {
      const byKey = new Map<string, N[]>();
      for (const n of order) {
        if (n.__d.cluster) continue;
        const key = iconKey(n.__d);
        byKey.set(key, [...(byKey.get(key) ?? []), n]);
      }
      for (const [key, list] of byKey) {
        // Its own quad: the per-instance opacity lives on the geometry.
        const plane = g.plane.clone();
        plane.setAttribute("instanceOpacity", opacityAttribute(list.length));
        const mesh = new THREE.InstancedMesh(
          plane,
          withInstanceOpacity(
            new THREE.MeshBasicMaterial({
              map: g.icons.get(key)?.texture,
              color: ICON_INK,
              transparent: true,
              depthWrite: false,
            }),
          ),
          list.length,
        );
        mesh.frustumCulled = false;
        mesh.raycast = () => {};
        g.iconMeshes.set(key, { mesh, nodes: list });
        g.group.add(mesh);
      }
    }
  }

  /** Lines, arrows and colours for the current highlight and time filter. */
  function applyView() {
    const g = gfx.current;
    const t = tmp.current;
    const v = propsRef.current.view;
    const nodes = nodesRef.current;
    const order = orderRef.current;
    const cores = g.cores;
    if (cores) {
      const opacity = cores.geometry.getAttribute("instanceOpacity") as THREE.BufferAttribute;
      for (const n of order) {
        const dim = dimmed(n.id, v);
        const lit = n.id === v.focus || n.id === v.hover || Boolean(v.highlight?.has(n.id));
        t.c.set(TYPE_COLOR[n.__d.type]);
        if (lit) t.c.lerp(new THREE.Color("#ffffff"), 0.18);
        cores.setColorAt(n.__i, t.c);
        // Out of the period or the highlight: nearly gone, as in the SVG view.
        opacity.setX(n.__i, dim ? FADED : 1);
        n.__target =
          n.id === v.hover ? 1.4 : n.id === v.focus ? 1.3 : v.picked.includes(n.id) ? 1.2 : 1;
      }
      opacity.needsUpdate = true;
      if (cores.instanceColor) cores.instanceColor.needsUpdate = true;
    }
    for (const { mesh, nodes: list } of g.iconMeshes.values()) {
      const opacity = mesh.geometry.getAttribute("instanceOpacity") as THREE.BufferAttribute;
      list.forEach((n, i) => opacity.setX(i, dimmed(n.id, v) ? FADED : 1));
      opacity.needsUpdate = true;
    }

    // Line batches: by line style and width (lit and multiple relations thicker).
    for (const b of g.batches) disposeMesh(b.line);
    g.batches = [];
    g.lit = new Set();
    const groups = new Map<
      string,
      { dash: EdgeSource; thick: boolean; pairs: { p: GlPair; color: THREE.Color }[] }
    >();
    const arrows: Arrow[] = [];
    const arrowColors: THREE.Color[] = [];
    for (const l of linksRef.current) {
      const p = l.__p;
      if (v.shown && !v.shown.has(p.key)) continue;
      const a = nodes.get(p.a);
      const b = nodes.get(p.b);
      if (!a || !b) continue;
      const lit = isLit(p, v);
      if (lit) g.lit.add(p.key);
      const faded = v.highlight !== null && !lit;
      const tone: Tone = lit ? "a" : p.secret ? "p" : "n";
      const strength = tone === "a" ? 0.95 : tone === "p" ? 0.85 : 0.38;
      const color = new THREE.Color().lerpColors(
        t.bg,
        new THREE.Color(toneColor(tone)),
        faded ? Math.max(0.035, strength * FADED) : strength,
      );
      const thick = lit || p.count > 1;
      const key = `${p.dash}|${thick ? 1 : 0}`;
      const group = groups.get(key) ?? { dash: p.dash, thick, pairs: [] };
      group.pairs.push({ p, color });
      groups.set(key, group);
      const arrowColor = new THREE.Color().lerpColors(
        t.bg,
        new THREE.Color(toneColor(tone)),
        faded ? 0.08 : 0.9,
      );
      if (p.forward) {
        arrows.push({ from: a, to: b });
        arrowColors.push(arrowColor);
      }
      if (p.backward) {
        arrows.push({ from: b, to: a });
        arrowColors.push(arrowColor);
      }
    }
    const { w, h } = g.size;
    for (const group of groups.values()) {
      const m = group.pairs.length;
      const positions = new Float32Array(m * 6);
      const colors = new Float32Array(m * 6);
      const ends: N[] = [];
      group.pairs.forEach(({ p, color }, i) => {
        ends.push(nodes.get(p.a)!, nodes.get(p.b)!);
        colors.set([color.r, color.g, color.b, color.r, color.g, color.b], i * 6);
      });
      const geometry = new LineSegmentsGeometry();
      geometry.setPositions(positions);
      geometry.setColors(colors);
      const dashed = group.dash !== "register";
      const material = new LineMaterial({
        color: 0xffffff,
        vertexColors: true,
        linewidth: group.thick ? 2.4 : 1.1,
        dashed,
        dashSize: group.dash === "text" ? 3.2 : 1,
        gapSize: group.dash === "text" ? 1.9 : 1.5,
        transparent: true,
        depthWrite: false,
      });
      material.resolution.set(w, h);
      const line = new LineSegments2(geometry, material);
      line.frustumCulled = false;
      line.raycast = () => {};
      line.renderOrder = 1;
      if (dashed) line.computeLineDistances();
      const batch: Batch = { line, ends, positions, dashed };
      writeBatch(batch);
      g.batches.push(batch);
      g.group.add(line);
    }
    disposeMesh(g.arrows, true);
    g.arrows = undefined;
    g.arrowList = arrows;
    if (arrows.length) {
      const mesh = new THREE.InstancedMesh(
        g.arrowGeo,
        new THREE.MeshBasicMaterial(),
        arrows.length,
      );
      arrowColors.forEach((c, i) => mesh.setColorAt(i, c));
      mesh.frustumCulled = false;
      mesh.raycast = () => {};
      g.arrows = mesh;
      g.group.add(mesh);
    }
  }

  /** The line batch at the nodes' positions now (and its dash lengths). */
  function writeBatch(b: Batch) {
    const { positions, ends } = b;
    for (let i = 0; i < ends.length; i++) {
      const n = ends[i];
      positions[i * 3] = n.x ?? 0;
      positions[i * 3 + 1] = n.y ?? 0;
      positions[i * 3 + 2] = zOf(n);
    }
    const geometry = b.line.geometry;
    const start = geometry.getAttribute("instanceStart") as THREE.InterleavedBufferAttribute;
    start.data.needsUpdate = true;
    if (b.dashed) {
      const dist = geometry.getAttribute("instanceDistanceStart") as
        | THREE.InterleavedBufferAttribute
        | undefined;
      if (dist) {
        const arr = dist.data.array as Float32Array;
        for (let i = 0; i < ends.length / 2; i++) {
          const o = i * 6;
          arr[i * 2] = 0;
          arr[i * 2 + 1] = Math.hypot(
            positions[o + 3] - positions[o],
            positions[o + 4] - positions[o + 1],
            positions[o + 5] - positions[o + 2],
          );
        }
        dist.data.needsUpdate = true;
      }
    }
  }

  // The scene, once.
  useEffect(() => {
    const el = mount.current;
    if (!el) return;
    let fg: ForceGraph3DInstance;
    try {
      fg = new ForceGraph3D(el, { controlType: "orbit" });
    } catch {
      propsRef.current.onFail();
      return;
    }
    fgRef.current = fg;
    const g = gfx.current;
    g.glowMap = glowTexture();

    // Icons: lucide's SVG rendered (hidden) below, drawn once into canvases.
    const serializer = new XMLSerializer();
    for (const svg of iconHost.current?.querySelectorAll<SVGSVGElement>("svg[data-icon]") ?? []) {
      const key = svg.dataset.icon!;
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 128;
      const texture = new THREE.CanvasTexture(canvas);
      texture.colorSpace = THREE.SRGBColorSpace;
      const image = new Image();
      image.onload = () => {
        canvas.getContext("2d")?.drawImage(image, 16, 16, 96, 96);
        texture.needsUpdate = true;
      };
      image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(serializer.serializeToString(svg))}`;
      g.icons.set(key, { canvas, texture });
    }

    const renderer = fg.renderer();
    const lost = (e: Event) => {
      e.preventDefault();
      propsRef.current.onFail();
    };
    renderer.domElement.addEventListener("webglcontextlost", lost);

    fg.backgroundColor(pal().bg)
      .showNavInfo(false)
      .nodeId("id")
      .nodeRelSize(1)
      .nodeVal((n: object) => (n as N).__r ** 3)
      .nodeLabel((n: object) => {
        const tip = document.createElement("div");
        tip.textContent = (n as N).__d.title;
        return tip;
      })
      // Only for picking and dragging: the drawing is ours (instanced).
      .nodeThreeObject((obj: object) => {
        const n = obj as N;
        const pick = new THREE.Mesh(g.pickGeo, g.pickMat);
        pick.scale.setScalar(n.__r * 1.3);
        return pick;
      })
      .linkSource("source")
      .linkTarget("target")
      .linkVisibility(false)
      .enableNodeDrag(true)
      .cooldownTime(8000)
      // A restored picture holds still until a node is dragged.
      .onNodeDrag(() => {
        settledRef.current = false;
        if (fg.cooldownTicks() === 0) fg.cooldownTicks(Infinity);
      })
      .onNodeClick((obj: object, event: MouseEvent) => {
        const n = obj as N;
        const p = propsRef.current;
        p.onInteract();
        if (n.__d.cluster) {
          p.onCluster(n.id);
          return;
        }
        const now = performance.now();
        if (lastClick.current.id === n.id && now - lastClick.current.at < 350) {
          lastClick.current = { id: "", at: 0 };
          p.onExpand(n.id);
          return;
        }
        lastClick.current = { id: n.id, at: now };
        p.onNode(n.id, event.shiftKey);
      })
      .onNodeHover((obj: object | null) => propsRef.current.onHover(obj ? (obj as N).id : null))
      .onNodeDragEnd((obj: object) => {
        // A dropped node stays where it was put.
        const n = obj as N;
        n.fx = n.x;
        n.fy = n.y;
        n.fz = zOf(n);
        propsRef.current.onInteract();
        propsRef.current.onPins(
          [...nodesRef.current.values()].filter((m) => m.fx !== undefined).map((m) => m.id),
        );
      })
      // Relations are drawn as batches: a click near a line opens it.
      .onBackgroundClick((event: MouseEvent) => {
        propsRef.current.onInteract();
        const key = pairAt(event);
        if (key) propsRef.current.onEdge(key);
      })
      .onEngineStop(() => {
        settledRef.current = true;
        if (fitPending.current && !interacted.current) fg.zoomToFit(900, 40);
        fitPending.current = false;
      });

    fg.lights([
      new THREE.AmbientLight("#b8c4ff", 1.1),
      light("#ffffff", 2.4, 1, 1.5, 2),
      light("#8C49E9", 1.3, -2, -1, -1.5),
    ]);

    const scene = fg.scene();
    syncPalette();
    scene.add(g.group);
    g.particles = spritePoints(PARTICLES, g.glowMap);
    g.particles.geometry.setDrawRange(0, 0);
    g.group.add(g.particles);

    const observer = new ResizeObserver(([entry]) => {
      const w = Math.max(1, Math.round(entry.contentRect.width));
      const h = Math.max(1, Math.round(entry.contentRect.height));
      fg.width(w).height(h);
      g.size = { w, h };
      for (const b of g.batches) (b.line.material as LineMaterial).resolution.set(w, h);
      const canvas = overlay.current;
      if (canvas) {
        const dpr = window.devicePixelRatio || 1;
        canvas.width = w * dpr;
        canvas.height = h * dpr;
        canvas.style.width = `${w}px`;
        canvas.style.height = `${h}px`;
      }
    });
    observer.observe(host.current!);

    let frame = 0;
    let failedOnce = false;
    let lastLit = 0;
    let lastAmbient = 0;
    const loop = (now: number) => {
      frame = requestAnimationFrame(loop);
      try {
        if (now - lastLit > 350) {
          lastLit = now;
          emitLit();
        }
        if (now - lastAmbient > 260) {
          lastAmbient = now;
          emitAmbient();
        }
        tick(now);
      } catch (err) {
        // A frame that failed is drawn again next time; say why once.
        if (!failedOnce) {
          failedOnce = true;
          console.error("[graph] frame", err);
        }
      }
    };
    frame = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      renderer.domElement.removeEventListener("webglcontextlost", lost);
      fg._destructor();
      fgRef.current = null;
      disposeMesh(g.cores, true);
      disposeMesh(g.particles);
      disposeMesh(g.arrows, true);
      for (const b of g.batches) disposeMesh(b.line);
      for (const { mesh } of g.iconMeshes.values()) disposeMesh(mesh);
      g.plane.dispose();
      for (const { texture } of g.icons.values()) texture.dispose();
      g.batches = [];
      g.iconMeshes.clear();
      g.icons.clear();
      g.cores = g.particles = g.arrows = undefined;
      g.sphere?.dispose();
      g.sphere = undefined;
      g.glowMap?.dispose();
      g.live = [];
      nodesRef.current = new Map();
      orderRef.current = [];
      linksRef.current = [];
      keysRef.current = { nodes: "", links: "", dims: 3 };
      started.current = false;
    };
    // the scene is built once; props are read through propsRef
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The data: rebuilt (and laid out again from the earlier positions) only when
  // nodes or pairs come or go.
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg) return;
    const nodeKey = props.nodes.map((n) => n.id).join("|");
    const linkKey = props.pairs.map((p) => p.key).join("|");
    const prev = nodesRef.current;
    if (nodeKey === keysRef.current.nodes && linkKey === keysRef.current.links) {
      for (const d of props.nodes) {
        const n = prev.get(d.id);
        if (n) n.__d = d;
      }
      const byKey = new Map(props.pairs.map((p) => [p.key, p]));
      for (const l of linksRef.current) l.__p = byKey.get(l.id) ?? l.__p;
      dirty.current = true;
      return;
    }
    const leaving = keysRef.current;
    keysRef.current = { nodes: nodeKey, links: linkKey, dims: props.dims };
    const now = performance.now();
    // Where every node stood when last shown: new nodes start there and the
    // layout goes on from it (a half-settled picture too).
    const memory = memoryRef.current;
    const layout = new Map<string, { x: number; y: number; z: number }>();
    for (const n of prev.values())
      if (Number.isFinite(n.x)) {
        const at = { x: n.x!, y: n.y ?? 0, z: zOf(n) };
        memory.delete(n.id);
        memory.set(n.id, at);
        layout.set(n.id, at);
      }
    while (memory.size > MEMORY_MAX) memory.delete(memory.keys().next().value!);
    // A settled picture is kept whole: the same view of the same nodes and
    // relations (one step and back, Back) comes back exactly, without a new run.
    if (leaving.nodes && layout.size && settledRef.current) saveLayout(leaving, layout);
    const restored = layoutsRef.current.get(pictureKey(props.dims, nodeKey, linkKey));
    const known = new Map<string, N>();
    for (const d of props.nodes) {
      const n = prev.get(d.id);
      if (n) {
        n.__d = d;
        n.__r = radiusOf(d);
        known.set(d.id, n);
      }
    }
    const placedNeighbour = new Map<string, N>();
    const degree = new Map<string, number>();
    for (const p of props.pairs) {
      degree.set(p.a, (degree.get(p.a) ?? 0) + 1);
      degree.set(p.b, (degree.get(p.b) ?? 0) + 1);
      const [a, b] = [known.get(p.a), known.get(p.b)];
      if (a && !b && !placedNeighbour.has(p.b)) placedNeighbour.set(p.b, a);
      if (b && !a && !placedNeighbour.has(p.a)) placedNeighbour.set(p.a, b);
    }
    const maxDegree = Math.max(1, ...degree.values());
    const next = new Map<string, N>();
    const spread = props.dims === 3 ? 1 : 0;
    const order: N[] = [];
    for (const d of props.nodes) {
      let n = known.get(d.id);
      if (!n) {
        // The first picture pops in node by node; later ones grow where they land.
        const born = started.current ? now : now + hash01(d.id, 4) * 900;
        n = {
          id: d.id,
          __d: d,
          __r: radiusOf(d),
          __born: born,
          __i: 0,
          __grow: 0,
          __target: 1,
          __deg: 0,
        };
        const seen = memory.get(d.id);
        // A new node starts where it stood before, else beside a neighbour that
        // already has its place (the same offset every time).
        const at = placedNeighbour.get(d.id);
        if (seen) {
          n.x = seen.x;
          n.y = seen.y;
          n.z = seen.z * spread;
        } else if (at?.x !== undefined) {
          n.x = at.x + (hash01(d.id, 1) - 0.5) * 24;
          n.y = (at.y ?? 0) + (hash01(d.id, 2) - 0.5) * 24;
          n.z = zOf(at) + (hash01(d.id, 3) - 0.5) * 24 * spread;
        }
      }
      n.__deg = (degree.get(d.id) ?? 0) / maxDegree;
      n.__i = order.length;
      order.push(n);
      next.set(d.id, n);
    }
    if (restored)
      for (const n of order) {
        const at = restored.get(n.id);
        if (!at) continue;
        n.x = at.x;
        n.y = at.y;
        n.z = at.z * spread;
        n.vx = n.vy = n.vz = 0;
      }
    // A settled picture shown again comes back exactly: no new simulation run.
    const whole = Boolean(restored) && order.every((n) => restored!.has(n.id));
    settledRef.current = whole;
    fg.cooldownTicks(whole ? 0 : Infinity);
    nodesRef.current = next;
    orderRef.current = order;
    gfx.current.live = gfx.current.live.filter((pt) => next.has(pt.from.id) && next.has(pt.to.id));
    const links: L[] = props.pairs.map((p) => ({ id: p.key, source: p.a, target: p.b, __p: p }));
    linksRef.current = links;
    buildNodes();
    const big = props.nodes.length > 500;
    const charge = fg.d3Force("charge") as unknown as {
      strength: (s: number) => unknown;
      distanceMax: (d: number) => unknown;
    } | null;
    charge?.strength(big ? -28 : -60);
    charge?.distanceMax(big ? 420 : 900);
    const link = fg.d3Force("link") as unknown as { distance: (d: number) => unknown } | null;
    link?.distance(props.dims === 3 ? (big ? 26 : 38) : big ? 22 : 32);
    fg.graphData({ nodes: order, links });
    dirty.current = true;
    fitPending.current = true;
    interacted.current = false;
    if (!started.current && props.nodes.length) {
      started.current = true;
      // The opening: from far away into the picture.
      fg.cameraPosition({ x: 0, y: 0, z: 2800 });
      setTimeout(() => fgRef.current?.zoomToFit(1800, 40), 600);
    } else {
      setTimeout(() => {
        if (!interacted.current) fgRef.current?.zoomToFit(800, 40);
      }, 900);
    }
    // the nodes and pairs by content (keys above)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.nodes, props.pairs]);

  // 2D: a flat layout seen from above; 3D: a turning space. A settled picture
  // is kept for the view it was laid out in, and comes back when switched back.
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg) return;
    const keys = keysRef.current;
    if (keys.nodes && keys.dims !== props.dims && settledRef.current) {
      const layout = new Map<string, { x: number; y: number; z: number }>();
      for (const n of orderRef.current)
        if (Number.isFinite(n.x)) layout.set(n.id, { x: n.x!, y: n.y ?? 0, z: zOf(n) });
      saveLayout(keys, layout);
    }
    keysRef.current = { ...keys, dims: props.dims };
    const restored = layoutsRef.current.get(pictureKey(props.dims, keys.nodes, keys.links));
    const whole = Boolean(restored) && orderRef.current.every((n) => restored!.has(n.id));
    for (const n of nodesRef.current.values()) {
      const at = whole ? restored!.get(n.id)! : null;
      if (at) {
        n.x = at.x;
        n.y = at.y;
        n.z = props.dims === 2 ? 0 : at.z;
        n.vx = n.vy = n.vz = 0;
      } else if (props.dims === 2) {
        n.z = 0;
        if (n.fz !== undefined) n.fz = 0;
      } else if (!zOf(n)) n.z = (hash01(n.id, 5) - 0.5) * 60;
    }
    settledRef.current = whole;
    fg.cooldownTicks(whole ? 0 : Infinity);
    fg.numDimensions(props.dims);
    const controls = fg.controls() as OrbitControls;
    controls.enableRotate = props.dims === 3;
    controls.screenSpacePanning = true;
    controls.mouseButtons = {
      LEFT: props.dims === 3 ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN,
      MIDDLE: THREE.MOUSE.DOLLY,
      RIGHT: THREE.MOUSE.PAN,
    };
    controls.touches = {
      ONE: props.dims === 3 ? THREE.TOUCH.ROTATE : THREE.TOUCH.PAN,
      TWO: THREE.TOUCH.DOLLY_PAN,
    };
    if (props.dims === 2) {
      const at = fg.cameraPosition();
      fg.cameraPosition({ x: 0, y: 0, z: Math.hypot(at.x, at.y, at.z) }, { x: 0, y: 0, z: 0 }, 600);
    }
    fitPending.current = true;
  }, [props.dims]);

  useEffect(() => {
    dirty.current = true;
  }, [props.view]);

  // Which labels to try first: the most mentioned and the most connected nodes
  // (after the data effect above has counted the relations); ties by id, so the
  // same picture labels the same nodes.
  useEffect(() => {
    const deg = (id: string) => nodesRef.current.get(id)?.__deg ?? 0;
    const score = (n: GlNode) => n.weight + deg(n.id) + (n.cluster ? 0.5 : 0);
    rankedRef.current = [...props.nodes]
      .sort((a, b) => score(b) - score(a) || (a.id < b.id ? -1 : 1))
      .slice(0, 400)
      .map((n) => n.id);
  }, [props.nodes, props.pairs]);

  // Follow the focused node: a short flight to it.
  useEffect(() => {
    const id = props.view.focus;
    if (!id || focused.current === id) return;
    const first = focused.current === null;
    focused.current = id;
    if (!first) flyTo(id);
  }, [props.view.focus]);

  function flyTo(id: string) {
    const fg = fgRef.current;
    const n = nodesRef.current.get(id);
    if (!fg || !n || n.x === undefined) return;
    interacted.current = true;
    const target = { x: n.x, y: n.y ?? 0, z: zOf(n) };
    const cam = fg.cameraPosition();
    if (propsRef.current.dims === 2) {
      fg.cameraPosition(
        { x: target.x, y: target.y, z: Math.max(120, cam.z) },
        { ...target, z: 0 },
        900,
      );
      return;
    }
    const dir = new THREE.Vector3(cam.x - target.x, cam.y - target.y, cam.z - target.z);
    const dist = Math.min(260, Math.max(90, dir.length()));
    dir.normalize().multiplyScalar(dist);
    fg.cameraPosition(
      { x: target.x + dir.x, y: target.y + dir.y, z: target.z + dir.z },
      target,
      1000,
    );
  }

  /** The relation drawn within a few pixels of a click, if any. */
  function pairAt(event: MouseEvent): string | null {
    const fg = fgRef.current;
    if (!fg) return null;
    const camera = fg.camera() as THREE.PerspectiveCamera;
    const rect = fg.renderer().domElement.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    const v = propsRef.current.view;
    const t = tmp.current;
    const screen = (n: N) => {
      t.v.set(n.x ?? 0, n.y ?? 0, zOf(n)).project(camera);
      return t.v.z > 1
        ? null
        : { x: ((t.v.x + 1) / 2) * rect.width, y: ((1 - t.v.y) / 2) * rect.height };
    };
    let best: string | null = null;
    let bestDist = 6;
    for (const l of linksRef.current) {
      const p = l.__p;
      if (p.grouped || (v.shown && !v.shown.has(p.key))) continue;
      const a = nodesRef.current.get(p.a);
      const b = nodesRef.current.get(p.b);
      const sa = a && screen(a);
      const sb = b && screen(b);
      if (!sa || !sb) continue;
      const dx = sb.x - sa.x;
      const dy = sb.y - sa.y;
      const len2 = dx * dx + dy * dy || 1;
      const k = Math.max(0, Math.min(1, ((mx - sa.x) * dx + (my - sa.y) * dy) / len2));
      const d = Math.hypot(sa.x + k * dx - mx, sa.y + k * dy - my);
      if (d < bestDist) {
        bestDist = d;
        best = p.key;
      }
    }
    return best;
  }

  /** Particles: along the lit relations, and a quiet flow elsewhere. */
  function emit(l: L, lit: boolean) {
    const g = gfx.current;
    if (g.live.length >= PARTICLES) return;
    const p = l.__p;
    const a = nodesRef.current.get(p.a);
    const b = nodesRef.current.get(p.b);
    if (!a || !b) return;
    const reverse = p.backward && !p.forward ? true : p.forward ? false : Math.random() < 0.5;
    g.live.push({
      from: reverse ? b : a,
      to: reverse ? a : b,
      t: 0,
      speed: lit ? 0.02 : 0.009,
      size: lit ? 4.2 : 3,
      color: new THREE.Color().lerpColors(
        tmp.current.bg,
        new THREE.Color(p.secret && !lit ? PROTECTED : pal().accent),
        lit ? 1 : 0.75,
      ),
    });
  }

  function emitLit() {
    const lit = gfx.current.lit;
    if (!lit.size) return;
    let sent = 0;
    for (const l of linksRef.current) {
      if (!lit.has(l.id)) continue;
      emit(l, true);
      if (++sent >= 120) break;
    }
  }

  function emitAmbient() {
    const p = propsRef.current;
    if (p.view.highlight) return;
    const links = linksRef.current;
    if (!links.length) return;
    const count = Math.min(4, Math.ceil(links.length / 150));
    for (let i = 0; i < count; i++) {
      const l = links[Math.floor(Math.random() * links.length)];
      if (p.view.shown && !p.view.shown.has(l.id)) continue;
      emit(l, false);
    }
  }

  /** Per frame: the instanced picture at the layout's positions, the camera's
   *  turn, the fog, and the 2D overlay (labels, rings, badges). */
  function tick(now: number) {
    const fg = fgRef.current;
    const canvas = overlay.current;
    if (!fg || !canvas) return;
    const p = propsRef.current;
    const g = gfx.current;
    const t = tmp.current;
    if (dirty.current) {
      dirty.current = false;
      applyView();
    }
    const camera = fg.camera() as THREE.PerspectiveCamera;
    const controls = fg.controls() as OrbitControls;
    controls.autoRotate = p.dims === 3 && p.spin && !p.view.hover;
    controls.autoRotateSpeed = 0.55;
    const order = orderRef.current;
    const nodes = nodesRef.current;

    // Centre and size of the picture now and then (fog); the theme's colours.
    if (g.frames++ % 30 === 0) {
      let far = 1;
      for (const n of order) {
        const d = Math.hypot(n.x ?? 0, n.y ?? 0, zOf(n));
        if (d > far) far = d;
      }
      g.radius = far;
      syncPalette();
    }
    const camDist = camera.position.length();
    const scene = fg.scene();
    if (p.dims === 3) {
      const density = 0.95 / (camDist + g.radius);
      if (scene.fog instanceof THREE.FogExp2) scene.fog.density = density;
      else scene.fog = new THREE.FogExp2(pal().bg, density);
    } else if (scene.fog) scene.fog = null;

    // Nodes: grow in, swell under the pointer; icons with them.
    const cores = g.cores;
    for (const n of order) {
      const born = Math.max(0, Math.min(1, (now - n.__born) / 700));
      const want = n.__target * (1 - Math.pow(1 - born, 3));
      n.__grow += (want - n.__grow) * 0.18;
      const r = n.__r * Math.max(0.001, n.__grow);
      t.v.set(n.x ?? 0, n.y ?? 0, zOf(n));
      if (cores) {
        t.m.compose(t.v, t.q.identity(), t.s.setScalar(r));
        cores.setMatrixAt(n.__i, t.m);
      }
    }
    if (cores) cores.instanceMatrix.needsUpdate = true;
    const pointScale =
      (fg.renderer().domElement.height || 1) / (2 * Math.tan((camera.fov * Math.PI) / 360));
    for (const { mesh, nodes: list } of g.iconMeshes.values()) {
      list.forEach((n, i) => {
        const r = n.__r * Math.max(0.001, n.__grow);
        t.v.set(n.x ?? 0, n.y ?? 0, zOf(n));
        t.w
          .copy(camera.position)
          .sub(t.v)
          .normalize()
          .multiplyScalar(r * 1.02);
        t.v.add(t.w);
        t.m.compose(t.v, camera.quaternion, t.s.setScalar(r * 1.35));
        mesh.setMatrixAt(i, t.m);
      });
      mesh.instanceMatrix.needsUpdate = true;
    }
    for (const b of g.batches) writeBatch(b);
    if (g.arrows) {
      g.arrowList.forEach(({ from, to }, i) => {
        t.v.set(to.x ?? 0, to.y ?? 0, zOf(to));
        t.w.set(from.x ?? 0, from.y ?? 0, zOf(from));
        const dir = t.w.sub(t.v).multiplyScalar(-1);
        const len = dir.length() || 1;
        dir.divideScalar(len);
        t.q.setFromUnitVectors(t.z, dir);
        t.v.addScaledVector(dir, -(to.__r * Math.max(0.001, to.__grow) + ARROW));
        t.m.compose(t.v, t.q, t.s.setScalar(1));
        g.arrows!.setMatrixAt(i, t.m);
      });
      g.arrows.instanceMatrix.needsUpdate = true;
      if (g.arrows.instanceColor) g.arrows.instanceColor.needsUpdate = true;
    }
    // Particles travel and leave at the end of their relation.
    if (g.particles) {
      const geo = g.particles.geometry;
      const pos = geo.getAttribute("position") as THREE.BufferAttribute;
      const tint = geo.getAttribute("tint") as THREE.BufferAttribute;
      const size = geo.getAttribute("size") as THREE.BufferAttribute;
      g.live = g.live.filter((pt) => (pt.t += pt.speed) < 1);
      g.live.forEach((pt, i) => {
        const k = pt.t;
        pos.setXYZ(
          i,
          (pt.from.x ?? 0) + ((pt.to.x ?? 0) - (pt.from.x ?? 0)) * k,
          (pt.from.y ?? 0) + ((pt.to.y ?? 0) - (pt.from.y ?? 0)) * k,
          zOf(pt.from) + (zOf(pt.to) - zOf(pt.from)) * k,
        );
        tint.setXYZ(i, pt.color.r, pt.color.g, pt.color.b);
        size.setX(i, pt.size);
      });
      geo.setDrawRange(0, g.live.length);
      pos.needsUpdate = tint.needsUpdate = size.needsUpdate = true;
      (g.particles.material as THREE.ShaderMaterial).uniforms.scale.value = pointScale;
    }

    // The overlay.
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    const dpr = canvas.width / Math.max(1, w);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const fov = (camera.fov * Math.PI) / 180;
    const pxPerUnit = (dist: number) => h / (2 * Math.max(1e-3, dist) * Math.tan(fov / 2));
    const fogOf = (dist: number) => {
      if (!(scene.fog instanceof THREE.FogExp2)) return 0;
      const f = scene.fog.density * dist;
      return 1 - Math.exp(-f * f);
    };
    const screen = new Map<string, { x: number; y: number; r: number; alpha: number }>();
    const project = (n: N) => {
      const hit = screen.get(n.id);
      if (hit) return hit;
      if (n.x === undefined) return null;
      const at = t.v.set(n.x, n.y ?? 0, zOf(n));
      const dist = at.distanceTo(camera.position);
      at.project(camera);
      if (at.z > 1 || at.z < -1) return null;
      const sx = ((at.x + 1) / 2) * w;
      const sy = ((1 - at.y) / 2) * h;
      if (sx < -60 || sy < -60 || sx > w + 60 || sy > h + 60) return null;
      const r = n.__r * Math.max(0.001, n.__grow) * pxPerUnit(dist);
      const out = { x: sx, y: sy, r, alpha: Math.max(0.3, 1 - fogOf(dist) * 0.85) };
      screen.set(n.id, out);
      return out;
    };

    const v = p.view;
    const forced = new Set<string>([
      ...(v.focus ? [v.focus] : []),
      ...(v.hover ? [v.hover] : []),
      ...(v.current ? [v.current] : []),
      ...v.picked,
    ]);
    const candidates: LabelCandidate[] = [];
    const add = (n: N, extra: number) => {
      if (dimmed(n.id, v) && !forced.has(n.id)) return;
      const s = project(n);
      if (!s) return;
      const text = n.__d.label;
      if (!text) return;
      candidates.push({
        id: n.id,
        x: s.x,
        y: s.y + s.r + 10,
        w: text.length * 6.2 + 6,
        h: 14,
        priority:
          extra + n.__d.weight * 60 + n.__deg * 60 + s.alpha * 50 + (n.__d.cluster ? 1e4 : 0),
        force: forced.has(n.id),
      });
    };
    for (const id of forced) {
      const n = nodes.get(id);
      if (n) add(n, id === v.focus ? 1e7 : 1e6);
    }
    if (v.highlight)
      for (const id of [...v.highlight].slice(0, 80)) {
        const n = nodes.get(id);
        if (n && !forced.has(id)) add(n, 1e5);
      }
    const ranked = rankedRef.current;
    for (let i = 0; i < ranked.length && candidates.length < LABELS_MAX; i++) {
      const n = nodes.get(ranked[i]);
      if (n && !forced.has(n.id) && !v.highlight?.has(n.id)) add(n, 0);
    }
    const edgeTexts = new Map<string, { x: number; y: number; text: string; lit: boolean }>();
    for (const l of linksRef.current) {
      const pair = l.__p;
      const lit = g.lit.has(pair.key);
      if (!lit && (!v.edgeLabels || v.highlight)) continue;
      if (v.shown && !v.shown.has(pair.key)) continue;
      const a = nodes.get(pair.a);
      const b = nodes.get(pair.b);
      const sa = a && project(a);
      const sb = b && project(b);
      if (!sa || !sb) continue;
      const k = pair.a === v.focus ? 0.62 : pair.b === v.focus ? 0.38 : 0.5;
      edgeTexts.set(`edge:${pair.key}`, {
        x: sa.x + (sb.x - sa.x) * k,
        y: sa.y + (sb.y - sa.y) * k - 4,
        text: pair.label,
        lit,
      });
    }
    for (const [id, e] of edgeTexts)
      candidates.push({
        id,
        x: e.x,
        y: e.y - 3,
        w: e.text.length * 5.4 + 4,
        h: 12,
        priority: id === `edge:${v.selectedEdge}` ? 1e8 : e.lit ? 5e5 : 1,
        force: id === `edge:${v.selectedEdge}`,
      });
    const obstacles = [...screen.values()].map((s) => ({ x: s.x, y: s.y, w: 2 * s.r, h: 2 * s.r }));
    const shown = placeLabels(candidates, obstacles);

    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.lineJoin = "round";
    // Rings: picked and found nodes, the focus (pulsing), groups (dashed);
    // badges: informant data, pinned nodes.
    const colors = pal();
    const halo = `rgba(${colors.bgRgb},0.9)`;
    const pulse = 0.5 + 0.5 * Math.sin(now / 320);
    for (const n of order) {
      const d = n.__d;
      const isFocus = n.id === v.focus;
      const ring = isFocus || n.id === v.current || v.picked.includes(n.id);
      const pinned = n.fx !== undefined;
      if (!ring && !d.cluster && !d.secret && !pinned) continue;
      const s = project(n);
      if (!s) continue;
      const dim = dimmed(n.id, v) ? FADED * 2 : 1;
      ctx.globalAlpha = s.alpha * dim;
      if (ring) {
        ctx.beginPath();
        ctx.setLineDash([]);
        ctx.lineWidth = isFocus ? 2 : 2.5;
        ctx.strokeStyle = colors.accent;
        ctx.arc(s.x, s.y, s.r + (isFocus ? 6 + 5 * pulse : 5), 0, Math.PI * 2);
        if (isFocus) ctx.globalAlpha = s.alpha * (0.45 + 0.55 * (1 - pulse));
        ctx.stroke();
        ctx.globalAlpha = s.alpha * dim;
      }
      if (d.cluster) {
        ctx.beginPath();
        ctx.setLineDash([4, 3]);
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = TYPE_COLOR[d.type];
        ctx.arc(s.x, s.y, s.r + 4, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
        if (s.r >= 6) {
          ctx.font = `700 ${Math.max(9, Math.min(14, s.r))}px system-ui, sans-serif`;
          ctx.fillStyle = "rgba(10,12,20,0.85)";
          ctx.fillText(String(d.cluster), s.x, s.y + 0.5);
        }
      }
      if (d.secret) {
        const bx = s.x + s.r * 0.75;
        const by = s.y - s.r * 0.75;
        ctx.beginPath();
        ctx.fillStyle = PROTECTED;
        ctx.arc(bx, by, 6, 0, Math.PI * 2);
        ctx.fill();
        const lock = g.icons.get("lock")?.canvas;
        if (lock) ctx.drawImage(lock, bx - 5, by - 5, 10, 10);
      }
      if (pinned) {
        const pin = g.icons.get("pin")?.canvas;
        if (pin) ctx.drawImage(pin, s.x - s.r - 12, s.y - s.r - 12, 12, 12);
      }
    }
    ctx.globalAlpha = 1;
    // Labels with a halo in the background colour.
    for (const c of candidates) {
      if (!shown.has(c.id)) continue;
      const e = edgeTexts.get(c.id);
      if (e) {
        ctx.font = "10px system-ui, sans-serif";
        ctx.lineWidth = 3;
        ctx.strokeStyle = halo;
        ctx.strokeText(e.text, e.x, e.y);
        ctx.fillStyle = e.lit ? colors.accent : `rgba(${colors.fgRgb},0.7)`;
        ctx.fillText(e.text, e.x, e.y);
        continue;
      }
      const n = nodes.get(c.id);
      const s = screen.get(c.id);
      if (!n || !s) continue;
      const strong = forced.has(c.id);
      ctx.font = `${strong ? 600 : 500} 11px system-ui, sans-serif`;
      ctx.lineWidth = 3.2;
      ctx.strokeStyle = halo;
      const y = s.y + s.r + 11;
      ctx.strokeText(n.__d.label, s.x, y);
      ctx.fillStyle = `rgba(${colors.fgRgb},${strong ? 1 : s.alpha.toFixed(2)})`;
      ctx.fillText(n.__d.label, s.x, y);
    }
  }

  useImperativeHandle(ref, () => ({
    zoom(factor: number) {
      const fg = fgRef.current;
      if (!fg) return;
      interacted.current = true;
      const controls = fg.controls() as OrbitControls;
      const t = controls.target;
      const cam = fg.cameraPosition();
      fg.cameraPosition(
        {
          x: t.x + (cam.x - t.x) / factor,
          y: t.y + (cam.y - t.y) / factor,
          z: t.z + (cam.z - t.z) / factor,
        },
        { x: t.x, y: t.y, z: t.z },
        250,
      );
    },
    fit() {
      interacted.current = true;
      fgRef.current?.zoomToFit(700, 40);
    },
    flyTo,
    snapshot() {
      const fg = fgRef.current;
      const el = host.current;
      const points = new Map<string, GlPoint>();
      if (!fg || !el) return { points, w: 800, h: 600 };
      const camera = fg.camera() as THREE.PerspectiveCamera;
      const w = el.clientWidth;
      const h = el.clientHeight;
      const fov = (camera.fov * Math.PI) / 180;
      for (const n of orderRef.current) {
        if (n.x === undefined) continue;
        const at = new THREE.Vector3(n.x, n.y ?? 0, zOf(n));
        const dist = at.distanceTo(camera.position);
        at.project(camera);
        if (at.z > 1 || at.z < -1) continue;
        const r = (n.__r * h) / (2 * dist * Math.tan(fov / 2));
        points.set(n.id, {
          x: ((at.x + 1) / 2) * w,
          y: ((1 - at.y) / 2) * h,
          r: Math.max(2, r),
          scale: 1,
          depth: -dist,
        });
      }
      return { points, w, h };
    },
    releasePins() {
      for (const n of nodesRef.current.values()) {
        delete n.fx;
        delete n.fy;
        delete n.fz;
      }
      settledRef.current = false;
      fgRef.current?.cooldownTicks(Infinity).d3ReheatSimulation();
      propsRef.current.onPins([]);
    },
  }));

  return (
    <div
      ref={host}
      className="absolute inset-0 overflow-hidden"
      style={{ backgroundColor: "var(--surface-2)" }}
      onPointerDown={() => {
        interacted.current = true;
        propsRef.current.onInteract();
      }}
      onWheel={() => {
        interacted.current = true;
      }}
    >
      <div ref={mount} className="absolute inset-0" />
      <canvas ref={overlay} className="pointer-events-none absolute inset-0" />
      <div ref={iconHost} hidden aria-hidden>
        {ICONS.map(([key, Icon]) => (
          <Icon
            key={key}
            data-icon={key}
            width={96}
            height={96}
            color={key === "pin" ? PIN : "#ffffff"}
            strokeWidth={2.2}
          />
        ))}
      </div>
    </div>
  );
});

export default GraphGL;
