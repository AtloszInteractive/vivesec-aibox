// Release identity (E01). The UI is versioned with the same calendar version
// (YY.MM.N) and source commit as the box; vite.config.ts injects the record at
// build time and also ships it as version.json next to the bundle.

export type BuildInfo = {
  version: string | null;
  commit: string | null;
  dirty: boolean | null;
  tagged: boolean;
  label: string;
  built_at: string | null;
};

/** What the box reports in /api/v1/status `version` (adapter/service.py). */
export type BoxVersion = {
  release: string;
  version: string | null;
  commit: string | null;
  consistent: boolean;
  components: { adapter: BuildInfo | null; rag: BuildInfo | null };
};

export const UI_BUILD: BuildInfo = __AIBOX_BUILD__;

function readBuild(raw: unknown): BuildInfo | null {
  if (!raw || typeof raw !== "object") return null;
  const b = raw as Record<string, unknown>;
  const str = (v: unknown) => (typeof v === "string" && v ? v : null);
  return {
    version: str(b.version),
    commit: str(b.commit),
    dirty: typeof b.dirty === "boolean" ? b.dirty : null,
    tagged: b.tagged === true,
    label: str(b.label) ?? "unknown",
    built_at: str(b.built_at),
  };
}

/** Parses the status `version` block; null for adapters older than E01. */
export function readBoxVersion(raw: unknown): BoxVersion | null {
  if (!raw || typeof raw !== "object") return null;
  const v = raw as Record<string, unknown>;
  const components = (v.components ?? {}) as Record<string, unknown>;
  return {
    release: typeof v.release === "string" && v.release ? v.release : "unknown",
    version: typeof v.version === "string" && v.version ? v.version : null,
    commit: typeof v.commit === "string" && v.commit ? v.commit : null,
    consistent: v.consistent === true,
    components: { adapter: readBuild(components.adapter), rag: readBuild(components.rag) },
  };
}

/**
 * True when the UI and the box were not built from the same release, or the
 * box itself runs mixed builds. Informational only: the embedding host may
 * legitimately serve a newer UI than the box runs (the /api/v1 contract is
 * versioned), so this never blocks anything. A box that predates version
 * reporting cannot be compared and counts as differing.
 */
export function versionDiffers(ui: BuildInfo, box: BoxVersion | null): boolean {
  if (!box) return true;
  if (!box.consistent) return true;
  return Boolean(ui.version && box.version && ui.version !== box.version);
}
