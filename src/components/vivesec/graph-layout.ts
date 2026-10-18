// Small deterministic force layout for the relationship view (E26). The graph
// shown at once is a neighbourhood (at most a few hundred nodes), so a plain
// O(n^2) simulation run to rest before drawing is fast enough and needs no
// visualisation dependency in the embedded bundle.

export type LayoutNode = { id: string; x: number; y: number };

export function layoutGraph(
  ids: string[],
  links: { a: string; b: string }[],
  center: string | null,
  iterations = 260,
): Map<string, LayoutNode> {
  const n = ids.length;
  const nodes = new Map<string, LayoutNode>();
  // Seeded start on a spiral: the same graph always lands in the same picture.
  ids.forEach((id, i) => {
    const angle = i * 2.399963;
    const radius = 40 * Math.sqrt(i + 1);
    nodes.set(id, { id, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius });
  });
  if (center && nodes.has(center)) Object.assign(nodes.get(center)!, { x: 0, y: 0 });
  const list = [...nodes.values()];
  const edges = links
    .map((l) => [nodes.get(l.a), nodes.get(l.b)] as const)
    .filter((p): p is readonly [LayoutNode, LayoutNode] => Boolean(p[0] && p[1]));
  const spring = n > 80 ? 70 : 110;
  const repulse = n > 80 ? 2600 : 5200;
  for (let step = 0; step < iterations; step++) {
    const cool = 1 - step / iterations;
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
      if (p.id === center) continue;
      dx[i] -= p.x * 0.01;
      dy[i] -= p.y * 0.01;
      const len = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]);
      const max = 30 * cool + 1;
      const k = len > max ? max / len : 1;
      p.x += dx[i] * k;
      p.y += dy[i] * k;
    }
  }
  return nodes;
}
