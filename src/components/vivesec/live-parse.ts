/* ============================================================================
 * LIVE ANSWER PARSING
 *
 * Every answer in the app comes from the AI Box adapter (there is no mock
 * data). The adapter's quick actions (adapter/llm.py TASK_INSTRUCTIONS) ask the
 * local model for a fixed section structure, so the UI can lift that structure
 * back out and render the real cards instead of a wall of text.
 *
 * Parsing is best-effort by design: the model is instructed to omit sections it
 * has no evidence for, so every parser may return null and the caller then
 * falls back to the plain answer card. Nothing here invents content.
 * ==========================================================================*/

export type ParsedAction = { text: string; assignee: string };
export type ParsedTask = { title: string; owner: string; status: string };
export type ParsedChartPoint = { label: string; value: number; display: string };
export type ParsedChart = { unit?: string; points: ParsedChartPoint[] };
export type ParsedSlide = {
  title: string;
  subtitle?: string;
  bullets: string[];
  chart?: ParsedChart;
};

/** C6 — the adapter appends the mandatory "Adatkontroll & Audit Info" footer to
 *  every displayed answer. Split it off so it can be rendered as a real footer
 *  block instead of raw markdown inside the answer body. */
export function splitAuditFooter(answer: string): { body: string; audit: string[] } {
  const i = answer.indexOf("\n---\n");
  if (i < 0) return { body: answer, audit: [] };
  const tail = answer.slice(i + 5).trim();
  if (!tail.startsWith("**Adatkontroll")) return { body: answer, audit: [] };
  return {
    body: answer.slice(0, i).trimEnd(),
    audit: tail
      .split("\n")
      .slice(1)
      .map((l) => l.replace(/^\*\s*/, "").trim())
      .filter(Boolean),
  };
}

/** Strip markdown chrome the model tends to add around a heading or bullet. */
function clean(line: string): string {
  // Bold markers come off first: on "**1. Key achievements**" the numbering
  // would otherwise survive and the section heading would not be recognised.
  return line
    .replace(/\*\*/g, "")
    .replace(/^\s*[#>]+\s*/, "")
    .replace(/^\s*[-*•]\s+/, "")
    .replace(/^\s*\d+[.)]\s+/, "")
    .trim();
}

function isBullet(line: string): boolean {
  return /^\s*([-*•]\s+|\d+[.)]\s+)/.test(line);
}

/**
 * Split an answer into the numbered sections the task instruction asked for.
 * A section starts at a line whose cleaned text begins with one of `titles`
 * (case-insensitive prefix match). The task instructions number their sections
 * ("1. Meeting details"), and models routinely put the first content on the
 * same line ("1. **Project health status**: On Track — ..."), so the heading
 * remainder is kept as the section's first content line.
 */
function sections(answer: string, titles: string[]): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  let current: string | null = null;
  for (const raw of answer.split("\n")) {
    const cleaned = clean(raw);
    const lower = cleaned.toLowerCase();
    const hit = titles.find((t) => lower.startsWith(t.toLowerCase()));
    if (hit) {
      current = hit;
      out[hit] = [];
      const rest = cleaned.slice(hit.length).replace(/^[\s:.\-–—]+/, "").trim();
      if (rest) out[hit].push(rest);
      continue;
    }
    if (current) out[current].push(raw);
  }
  return out;
}

function bulletsOf(lines: string[] | undefined, limit = 8): string[] {
  if (!lines) return [];
  return lines
    .filter((l) => isBullet(l) && clean(l).length > 1)
    .map((l) => clean(l))
    // Drop pure sub-headers ("Pending:", "Achieved:") that carry no content.
    .filter((l) => !/^[\w\s]{1,20}:$/.test(l))
    .slice(0, limit);
}

function textOf(lines: string[] | undefined): string {
  return (lines ?? []).join("\n").trim();
}

/** Pipe-separated rows ("Task | Owner | Deadline") inside a section. */
function pipeRows(lines: string[] | undefined): string[][] {
  if (!lines) return [];
  return lines
    .map((l) => clean(l))
    .filter((l) => l.includes("|"))
    .map((l) => l.split("|").map((c) => c.trim()))
    .filter((cells) => cells.length >= 2 && cells[0].length > 0)
    // Drop markdown table rulers ("--- | --- | ---") and header rows.
    .filter((cells) => !cells.every((c) => /^:?-{2,}:?$/.test(c)));
}

/* ---------------------------------------------------------------- summary --*/
export type ParsedSummary = {
  bullets: string[];
  actions: ParsedAction[];
  email: string;
};

export function parseSummary(answer: string): ParsedSummary | null {
  const s = sections(answer, [
    "meeting details",
    "executive summary",
    "action items",
    "follow-up email",
  ]);
  const bullets = bulletsOf(s["executive summary"]);
  const actions: ParsedAction[] = pipeRows(s["action items"]).map((cells) => ({
    text: cells[0],
    assignee: cells[1] || "?",
  }));
  const email = textOf(s["follow-up email"]);
  if (!bullets.length && !actions.length && !email) return null;
  return { bullets, actions, email };
}

/* ----------------------------------------------------------------- report --*/
export type ParsedReport = {
  metrics: { label: string; value: string }[];
  tasks: ParsedTask[];
  /** Sections that did not fit the card, rendered as the report narrative. */
  narrative: string[];
};

export function parseReport(answer: string): ParsedReport | null {
  const s = sections(answer, [
    "weekly progress overview",
    "key achievements",
    "bottlenecks",
    "strategic next-step",
    "key events",
  ]);
  const achievements = bulletsOf(s["key achievements"]);
  const blockers = bulletsOf(s["bottlenecks"]);
  const next = bulletsOf(s["strategic next-step"]);
  const events = bulletsOf(s["key events"]);
  if (!achievements.length && !blockers.length && !next.length) return null;
  return {
    metrics: [],
    tasks: [
      ...achievements.map((t) => ({ title: t, owner: "✓", status: "Done" })),
      ...blockers.map((t) => ({ title: t, owner: "!", status: "At Risk" })),
      ...next.map((t) => ({ title: t, owner: "→", status: "On Track" })),
    ],
    narrative: [textOf(s["weekly progress overview"]), ...events].filter(Boolean),
  };
}

/* --------------------------------------------------------------- tracking --*/
export type ParsedTracking = {
  health: "On Track" | "At Risk" | "Delayed" | null;
  healthNote: string;
  milestones: string[];
  tasks: ParsedTask[];
};

export function parseTracking(answer: string): ParsedTracking | null {
  const s = sections(answer, [
    "project health status",
    "milestones",
    "active task tracker",
  ]);
  const healthText = textOf(s["project health status"]);
  const health =
    /delayed/i.test(healthText) ? "Delayed"
      : /at risk/i.test(healthText) ? "At Risk"
        : /on track/i.test(healthText) ? "On Track"
          : null;
  const tasks: ParsedTask[] = pipeRows(s["active task tracker"]).map((cells) => ({
    title: cells[0],
    owner: cells[1] || "?",
    status: cells[2] || "Pending",
  }));
  const milestones = bulletsOf(s["milestones"], 10);
  if (!health && !tasks.length && !milestones.length) return null;
  return { health, healthNote: healthText, milestones, tasks };
}

/* ----------------------------------------------------------- presentation --*/
/**
 * "Slide N: <headline>" blocks with labelled lines ("Core message: …") below.
 * Everything after the title becomes the slide's bullet list, so nothing the
 * model grounded in CONTEXT is dropped.
 */
/** The model labels each line ("**Core Message:** …"); the card shows the value. */
const SLIDE_LABEL =
  /^\*{0,2}(title|subtitle|core message|key message|message|suggested visuals?(?:\s*[/&]\s*data points?)?|visuals?(?:\s*[/&]\s*data points?)?|data points?)\*{0,2}\s*[:\-–]\s*/i;
/** Placeholder values the task instruction tells the model to write. */
const SLIDE_EMPTY = /^(none|none specified|n\/a|not specified|\?|-)\.?$/i;
/** A bare column name from the instruction, echoed instead of real content. */
const SLIDE_TEMPLATE_CELL =
  /^(?:slide\s*)?(?:title|subtitle|core message|key message|message|suggested visuals?(?:\s*[/&]\s*data points?)?|visuals?(?:\s*[/&]\s*data points?)?|data points?)$/i;
/** The optional plottable data line: "Chart: Q1 = 13.2 | Q2 = 14.7 | unit: EUR million". */
const CHART_LINE = /^chart\s*[:\-–]\s*(.+)$/i;
/** The model likes to announce the data line with an empty label line first. */
const CHART_HEADER = /^chart\s*[:\-–]?\s*$/i;
const CHART_KIND_CELL = /^(bar|column|bar chart|column chart|chart)$/i;

/** First number in a cell ("EUR 14.7 million" -> 14.7), EU and US decimals. */
function chartValue(text: string): number | null {
  const m = /-?\d[\d\s.,]*/.exec(text);
  if (!m) return null;
  let raw = m[0].replace(/\s/g, "").replace(/[.,]+$/, "");
  // "1.234,5" is EU grouping, "1,234.5" and "14.7" are not.
  if (/,\d{1,2}$/.test(raw)) raw = raw.replace(/\./g, "").replace(",", ".");
  else raw = raw.replace(/,/g, "");
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

/**
 * The model only emits this line when CONTEXT holds comparable figures, and
 * the adapter has already checked every digit against CONTEXT — so a chart is
 * plotted from grounded numbers or not at all.
 */
export function parseChart(line: string): ParsedChart | null {
  const m = CHART_LINE.exec(line.replace(/\*\*/g, "").trim());
  if (!m) return null;
  let unit: string | undefined;
  const points: ParsedChartPoint[] = [];
  for (const cell of m[1].split("|").map((c) => c.trim())) {
    if (!cell || CHART_KIND_CELL.test(cell)) continue;
    const u = /^unit\s*[:=]\s*(.+)$/i.exec(cell);
    if (u) {
      unit = u[1].trim();
      continue;
    }
    const kv = /^(.+?)\s*[=:]\s*(.+)$/.exec(cell);
    if (!kv) continue;
    const display = kv[2].trim();
    // "Q4 2025 = 99." — the generation cap cut the number in half; 99 would
    // be a plausible but wrong bar.
    if (/\d[.,]\s*$/.test(display)) continue;
    const value = chartValue(display);
    if (value === null) continue;
    points.push({ label: kv[1].trim(), value, display });
  }
  return points.length >= 2 ? { unit, points: points.slice(0, 8) } : null;
}

export function parseSlides(answer: string): ParsedSlide[] | null {
  const slides: ParsedSlide[] = [];
  const re = /^\s*(?:[#*\-\s]*)slide\s*(\d+)\s*[:.\-–]\s*(.+)$/i;
  const lines = answer.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const m = re.exec(clean(lines[i]).length ? lines[i] : "");
    if (!m) continue;
    const cells = m[2].split("|").map((c) => c.replace(/\*\*/g, "").trim());
    let title = cells.shift() ?? "";
    const raw = cells.filter(Boolean);
    // A slide block may continue on the following bullet lines.
    for (let j = i + 1; j < lines.length && raw.length < 8; j++) {
      if (re.test(lines[j])) break;
      if (!isBullet(lines[j])) {
        const plain = clean(lines[j]);
        if (plain === "") continue;
        // The model does not always bullet the labelled lines it was asked for.
        if (!SLIDE_LABEL.test(plain) && !CHART_LINE.test(plain)) break;
        raw.push(plain);
        continue;
      }
      raw.push(clean(lines[j]));
    }
    let subtitle: string | undefined;
    let labelledTitle: string | undefined;
    let chart: ParsedChart | undefined;
    const bullets: string[] = [];
    for (const line of raw) {
      if (CHART_HEADER.test(line)) continue;
      if (!chart) {
        const plot = parseChart(line);
        if (plot) {
          chart = plot;
          continue;
        }
      }
      const label = SLIDE_LABEL.exec(line);
      const value = label ? line.slice(label[0].length).trim() : line;
      if (!value || SLIDE_EMPTY.test(value)) continue;
      if (!label && SLIDE_TEMPLATE_CELL.test(value)) continue;
      const kind = label?.[1].toLowerCase();
      if (kind === "title" && !labelledTitle) {
        labelledTitle = value;
        continue;
      }
      if (kind === "subtitle" && !subtitle) {
        subtitle = value;
        continue;
      }
      bullets.push(value);
    }
    if (!title || SLIDE_TEMPLATE_CELL.test(title)) {
      // The model often echoes the instruction's column name ("Slide 1: Title")
      // and puts the real headline on the "Title:" line below it.
      title = labelledTitle ?? bullets.shift() ?? "";
    } else if (labelledTitle && !subtitle && labelledTitle.toLowerCase() !== title.toLowerCase()) {
      subtitle = labelledTitle;
    }
    if (title) slides.push({ title, subtitle, bullets: bullets.slice(0, 6), chart });
  }
  const merged = mergeContinuedSlides(slides);
  return merged.length >= 2 ? merged : null;
}

/** "… (continued)" repeats of the previous headline, each re-charting the same
 *  series with one more point. Asking the model not to do it does not stick, so
 *  the repeats are folded back into one slide with the fullest chart. */
const CONTINUED_TITLE =
  /\s*[([]?\s*(continued|cont\.?|folytatás|part\s*\d+|\d+\s*\/\s*\d+)\s*[)\]]?\s*$/i;

function mergeContinuedSlides(slides: ParsedSlide[]): ParsedSlide[] {
  const base = (t: string) => t.replace(CONTINUED_TITLE, "").trim();
  const merged: ParsedSlide[] = [];
  for (const slide of slides) {
    const prev = merged[merged.length - 1];
    if (prev && base(prev.title).toLowerCase() === base(slide.title).toLowerCase()) {
      prev.title = base(prev.title);
      prev.subtitle = prev.subtitle ?? slide.subtitle;
      for (const b of slide.bullets) {
        if (prev.bullets.length < 6 && !prev.bullets.includes(b)) prev.bullets.push(b);
      }
      if (slide.chart && (!prev.chart || slide.chart.points.length > prev.chart.points.length)) {
        prev.chart = slide.chart;
      }
      continue;
    }
    merged.push(slide);
  }
  return merged;
}

/* ------------------------------------------------------------------- memo --*/
/** The memo card renders the body verbatim; only the title is lifted out. */
export function parseMemoTitle(answer: string): string | null {
  for (const raw of answer.split("\n")) {
    const line = clean(raw);
    if (!line) continue;
    if (/^(background|the issue)/i.test(line)) return null;
    return line.length > 90 ? null : line;
  }
  return null;
}
