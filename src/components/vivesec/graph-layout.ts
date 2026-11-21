// Small deterministic force layout for the relationship view (E26). The graph
// shown at once is a neighbourhood (at most a few hundred nodes), so a plain
// O(n^2) simulation run to rest before drawing is fast enough and needs no
// visualisation dependency in the embedded bundle.

export type LayoutNode = { id: string; x: number; y: number };

export type Point = { x: number; y: number };

/** `seed`: earlier positions to start from (a warm start: the picture keeps its
 *  shape when nodes are added, new ones start next to a placed neighbour);
 *  `fixed`: positions that do not move (nodes the user pinned). */
export function layoutGraph(
  ids: string[],
  links: { a: string; b: string }[],
  center: string | null,
  iterations = 260,
  options: { seed?: Map<string, Point>; fixed?: Map<string, Point> } = {},
): Map<string, LayoutNode> {
  const n = ids.length;
  const nodes = new Map<string, LayoutNode>();
  const seed = options.seed ?? new Map<string, Point>();
  const fixed = options.fixed ?? new Map<string, Point>();
  const warm = ids.some((id) => seed.has(id));
  // Seeded start on a spiral: the same graph always lands in the same picture.
  ids.forEach((id, i) => {
    const at = fixed.get(id) ?? seed.get(id);
    if (at) {
      nodes.set(id, { id, x: at.x, y: at.y });
      return;
    }
    const angle = i * 2.399963;
    const radius = 40 * Math.sqrt(i + 1);
    nodes.set(id, { id, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius });
  });
  if (warm) {
    // A new node starts beside a neighbour that already has its place.
    const around = new Map<string, Point>();
    for (const l of links) {
      for (const [mine, other] of [
        [l.a, l.b],
        [l.b, l.a],
      ]) {
        const at = fixed.get(other) ?? seed.get(other);
        if (!seed.has(mine) && !fixed.has(mine) && at && !around.has(mine)) around.set(mine, at);
      }
    }
    let k = 0;
    for (const [id, at] of around) {
      const angle = k++ * 2.399963;
      Object.assign(nodes.get(id) ?? {}, {
        x: at.x + Math.cos(angle) * 45,
        y: at.y + Math.sin(angle) * 45,
      });
    }
  } else if (center && nodes.has(center)) Object.assign(nodes.get(center)!, { x: 0, y: 0 });
  const list = [...nodes.values()];
  const edges = links
    .map((l) => [nodes.get(l.a), nodes.get(l.b)] as const)
    .filter((p): p is readonly [LayoutNode, LayoutNode] => Boolean(p[0] && p[1]));
  const spring = n > 80 ? 70 : 110;
  const repulse = n > 80 ? 2600 : 5200;
  const steps = warm ? Math.round(iterations / 2) : iterations;
  for (let step = 0; step < steps; step++) {
    const cool = 1 - step / steps;
    const dx = new Float64Array(n);
    const dy = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let ddx = list[i].x - list[j].x;
        let ddy = list[i].y - list[j].y;
        let d2 = ddx * ddx + ddy * ddy;
        if (d2 < 0.01) {
          ddx = (i % 7) - 3 + 0.1;
          ddy = (j % 5) - 2 + 0.1;
          d2 = ddx * ddx + ddy * ddy;
        }
        const f = repulse / d2;
        const d = Math.sqrt(d2);
        dx[i] += (ddx / d) * f;
        dy[i] += (ddy / d) * f;
        dx[j] -= (ddx / d) * f;
        dy[j] -= (ddy / d) * f;
      }
    }
    const index = new Map(list.map((p, i) => [p, i]));
    for (const [p, q] of edges) {
      const ddx = q.x - p.x;
      const ddy = q.y - p.y;
      const d = Math.sqrt(ddx * ddx + ddy * ddy) || 1;
      const f = (d - spring) * 0.08;
      const i = index.get(p)!;
      const j = index.get(q)!;
      dx[i] += (ddx / d) * f;
      dy[i] += (ddy / d) * f;
      dx[j] -= (ddx / d) * f;
      dy[j] -= (ddy / d) * f;
    }
    for (let i = 0; i < n; i++) {
      const p = list[i];
      if ((!warm && p.id === center) || fixed.has(p.id)) continue;
      dx[i] -= p.x * 0.01;
      dy[i] -= p.y * 0.01;
      const len = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]);
      // A warm start only settles: placed nodes move little, new ones find room.
      const max = warm ? (seed.has(p.id) ? 1.5 * cool + 0.15 : 20 * cool + 1) : 30 * cool + 1;
      const k = len > max ? max / len : 1;
      p.x += dx[i] * k;
      p.y += dy[i] * k;
    }
  }
  return nodes;
}

export type LayoutNode3 = { id: string; x: number; y: number; z: number };

/** The same force layout in three dimensions (the optional 3D view), seeded on a
 *  Fibonacci sphere so the same graph always gives the same picture. */
export function layoutGraph3d(
  ids: string[],
  links: { a: string; b: string }[],
  center: string | null,
  iterations = 220,
): Map<string, LayoutNode3> {
  const n = ids.length;
  const nodes = new Map<string, LayoutNode3>();
  ids.forEach((id, i) => {
    const y = 1 - (2 * (i + 0.5)) / Math.max(n, 1);
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const angle = i * 2.399963;
    const radius = 40 * Math.cbrt(i + 1) + 20;
    nodes.set(id, {
      id,
      x: Math.cos(angle) * r * radius,
      y: y * radius,
      z: Math.sin(angle) * r * radius,
    });
  });
  if (center && nodes.has(center)) Object.assign(nodes.get(center)!, { x: 0, y: 0, z: 0 });
  const list = [...nodes.values()];
  const index = new Map(list.map((p, i) => [p.id, i]));
  const edges = links
    .map((l) => [index.get(l.a), index.get(l.b)] as const)
    .filter((p): p is readonly [number, number] => p[0] !== undefined && p[1] !== undefined);
  const spring = n > 80 ? 80 : 120;
  const repulse = n > 80 ? 60000 : 120000;
  for (let step = 0; step < iterations; step++) {
    const cool = 1 - step / iterations;
    const f = new Float64Array(n * 3);
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = list[i].x - list[j].x;
        let dy = list[i].y - list[j].y;
        let dz = list[i].z - list[j].z;
        let d2 = dx * dx + dy * dy + dz * dz;
        if (d2 < 0.01) {
          dx = (i % 7) - 3 + 0.1;
          dy = (j % 5) - 2 + 0.1;
          dz = ((i + j) % 3) - 1 + 0.1;
          d2 = dx * dx + dy * dy + dz * dz;
        }
        const d = Math.sqrt(d2);
        const push = repulse / (d2 * d);
        f[i * 3] += dx * push;
        f[i * 3 + 1] += dy * push;
        f[i * 3 + 2] += dz * push;
        f[j * 3] -= dx * push;
        f[j * 3 + 1] -= dy * push;
        f[j * 3 + 2] -= dz * push;
      }
    }
    for (const [i, j] of edges) {
      const dx = list[j].x - list[i].x;
      const dy = list[j].y - list[i].y;
      const dz = list[j].z - list[i].z;
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
      const pull = ((d - spring) * 0.08) / d;
      f[i * 3] += dx * pull;
      f[i * 3 + 1] += dy * pull;
      f[i * 3 + 2] += dz * pull;
      f[j * 3] -= dx * pull;
      f[j * 3 + 1] -= dy * pull;
      f[j * 3 + 2] -= dz * pull;
    }
    for (let i = 0; i < n; i++) {
      const p = list[i];
      if (p.id === center) continue;
      const fx = f[i * 3] - p.x * 0.01;
      const fy = f[i * 3 + 1] - p.y * 0.01;
      const fz = f[i * 3 + 2] - p.z * 0.01;
      const len = Math.sqrt(fx * fx + fy * fy + fz * fz);
      const max = 30 * cool + 1;
      const k = len > max ? max / len : 1;
      p.x += fx * k;
      p.y += fy * k;
      p.z += fz * k;
    }
  }
  return nodes;
}

/** A point of the 3D layout seen from the camera: turned by yaw (around the
 *  vertical axis) and pitch, then in perspective. `scale` < 1 is farther away;
 *  `depth` grows towards the viewer. */
export function project(
  p: { x: number; y: number; z: number },
  yaw: number,
  pitch: number,
  distance: number,
): { x: number; y: number; scale: number; depth: number } {
  const cy = Math.cos(yaw);
  const sy = Math.sin(yaw);
  const x1 = p.x * cy + p.z * sy;
  const z1 = -p.x * sy + p.z * cy;
  const cp = Math.cos(pitch);
  const sp = Math.sin(pitch);
  const y2 = p.y * cp - z1 * sp;
  const z2 = p.y * sp + z1 * cp;
  // No camera distance (no layout yet): a flat view instead of 0/0.
  if (!(distance > 0)) return { x: x1, y: y2, scale: 1, depth: -z2 };
  const scale = distance / Math.max(distance * 0.15, distance + z2);
  return { x: x1 * scale, y: y2 * scale, scale, depth: -z2 };
}

/** "YYYY-MM" of a date string ("2026-03-05", "2026-03", "2026"), or null. */
export function monthOf(date: string | null | undefined): string | null {
  const m = /^(\d{4})(?:-(\d{2}))?/.exec(date ?? "");
  return m ? `${m[1]}-${m[2] ?? "01"}` : null;
}

/** Every month from the earliest to the latest date of the edges, in order. */
export function monthsOf(edges: { start: string | null; end: string | null }[]): string[] {
  const seen = edges.flatMap((e) => [monthOf(e.start), monthOf(e.end)]).filter(Boolean) as string[];
  if (!seen.length) return [];
  seen.sort();
  const out: string[] = [];
  let [y, m] = seen[0].split("-").map(Number);
  const last = seen[seen.length - 1];
  for (let guard = 0; guard < 1200; guard++) {
    const cur = `${y}-${String(m).padStart(2, "0")}`;
    out.push(cur);
    if (cur >= last) break;
    m += 1;
    if (m > 12) {
      m = 1;
      y += 1;
    }
  }
  return out;
}

/** Does the edge's time span touch the months from..to? Without an end date an
 *  `ongoing` relation (a placement still in force) runs on, any other is the
 *  one month of its date; an undated edge counts only when `undated` is set. */
export function edgeInPeriod(
  e: { start: string | null; end: string | null },
  from: string,
  to: string,
  undated: boolean,
  ongoing = false,
): boolean {
  const start = monthOf(e.start);
  const end = monthOf(e.end);
  if (!start && !end) return undated;
  const first = start ?? end!;
  const last = end ?? (ongoing ? "9999-12" : first);
  return first <= to && last >= from;
}

export type LabelCandidate = {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  priority: number;
  /** Shown even where it overlaps (the focused node, the selected relation). */
  force?: boolean;
};

/** The labels that fit without overlapping each other or the `obstacles` (the
 *  circles), most important first (greedy). */
export function placeLabels(
  candidates: LabelCandidate[],
  obstacles: Omit<LabelCandidate, "id" | "priority">[] = [],
): Set<string> {
  const placed: Omit<LabelCandidate, "id" | "priority">[] = [...obstacles];
  const out = new Set<string>();
  for (const c of [...candidates].sort((p, q) => q.priority - p.priority)) {
    const clash = placed.some(
      (p) => Math.abs(p.x - c.x) * 2 < p.w + c.w && Math.abs(p.y - c.y) * 2 < p.h + c.h,
    );
    if (!clash || c.force) {
      placed.push(c);
      out.add(c.id);
    }
  }
  return out;
}
