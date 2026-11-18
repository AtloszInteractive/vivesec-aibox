// Profile suggestions (E26e, graph_service/profile_suggest.py): what the
// extraction could only label as a generic link, and identifiers no pattern
// knows, counted over the documents this administrator may see. Each item is
// accepted, edited or dropped here; applying makes the next profile version,
// which is saved like an edited profile. Items carry labels and counts only.

import { useState } from "react";
import { Check, Loader2, Sparkles } from "lucide-react";

import {
  graphApi,
  type GraphEntityType,
  type GraphLabel,
  type GraphProfile,
  type GraphProfileChange,
  type GraphProfileSuggestions,
  type GraphSuggestion,
} from "@/lib/api/graph.functions";
import { AdapterGraphError } from "@/lib/rag/adapter-client";
import { ACCENT } from "./brand";
import { TYPE_NAME } from "./graph-style";
import { useLang } from "./i18n";

const LANGS = ["en", "hu", "da", "de"] as const;
const GENERIC = new Set(["RELATED_TO", "CO_MENTIONED"]);

type Row = GraphSuggestion & { accepted: boolean };
type Candidate = { profile: GraphProfile; changes: GraphProfileChange[] };

const input =
  "w-full rounded-md border border-ink/15 bg-ink/5 px-2 py-1 text-[11px] text-ink outline-none focus:border-brand-soft";
const button =
  "inline-flex items-center gap-1.5 rounded-full border border-ink/15 px-3 py-1 text-xs hover:bg-ink/10 disabled:opacity-40";

function fill(text: string, values: Record<string, string | number>): string {
  return text.replace(/\{(\w+)\}/g, (all, key: string) =>
    key in values ? String(values[key]) : all,
  );
}

export function ProfileSuggestions({
  drive,
  profile,
  code,
  onSaved,
}: {
  drive: string;
  profile: GraphProfile;
  code: keyof GraphLabel;
  onSaved: (message: string) => Promise<void>;
}) {
  const { tm } = useLang();
  const [data, setData] = useState<GraphProfileSuggestions | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [busy, setBusy] = useState<"load" | "apply" | "save" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const label = (l: GraphLabel | undefined) => (l ? (l[code] ?? l.en ?? "") : "");
  const relations = profile.relation_types.filter((r) => !GENERIC.has(r.id));
  const relationName = (id?: string) => {
    const found = relations.find((r) => r.id === id);
    return found ? label(found.label) : (id ?? "");
  };
  const identifierKinds = profile.entity_subtypes.filter((s) => s.type === "identifier");
  const kinds = (types?: GraphEntityType[]) => (types ?? []).map((t) => tm(TYPE_NAME[t])).join("/");

  function failed(err: unknown) {
    setError(
      err instanceof AdapterGraphError && err.status === 400
        ? `${tm("The profile would not be valid:")} ${err.message}`
        : tm("The request failed."),
    );
  }

  async function load() {
    setBusy("load");
    setError(null);
    setCandidate(null);
    try {
      const res = await graphApi.profileSuggest(drive);
      setData(res);
      setRows(res.items.map((item) => ({ ...item, accepted: item.action !== "ignore" })));
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
    }
  }

  async function apply() {
    const accepted = rows
      .filter((r) => r.accepted && r.action !== "ignore")
      .map(({ accepted: _accepted, ...item }) => item);
    if (!accepted.length) {
      setError(tm("Accept at least one suggestion."));
      return;
    }
    setBusy("apply");
    setError(null);
    try {
      setCandidate(await graphApi.profileSuggestApply(drive, accepted));
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    if (!candidate) return;
    setBusy("save");
    setError(null);
    try {
      await graphApi.saveProfile(drive, candidate.profile);
      setCandidate(null);
      setData(null);
      setRows([]);
      await onSaved(
        tm(
          "Saved as a new version. New documents are read with it at once; the others when they are processed again (Processing tab).",
        ),
      );
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
    }
  }

  const update = (key: string, change: Partial<Row>) =>
    setRows((current) => current.map((r) => (r.key === key ? { ...r, ...change } : r)));
  const setLabel = (row: Row, lang: (typeof LANGS)[number], value: string) =>
    update(row.key, { labels: { ...(row.labels ?? { en: "" }), [lang]: value } as GraphLabel });

  function changeText(c: GraphProfileChange): string {
    if (c.change === "relation") return fill(tm("New relation type: {id}"), { id: c.relation });
    if (c.change === "synonyms")
      return fill(tm("{relation}: new words {words}"), {
        relation: relationName(c.relation),
        words: c.added.join(", ") || "—",
      });
    return fill(
      tm(c.new_subtype ? "New identifier kind with a pattern: {id}" : "New pattern for {id}"),
      {
        id: c.subtype,
      },
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-[11px] text-ink/60">
        {tm(
          "Links the extraction could only label because no relation type fitted, and identifiers no pattern knows, counted over the documents you may see. Accept what fits; nothing changes until you save the new version.",
        )}
      </p>
      <button className={button} disabled={busy !== null} onClick={() => void load()}>
        {busy === "load" ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Sparkles className="h-3.5 w-3.5" />
        )}
        {busy === "load"
          ? tm("Looking for suggestions, this can take a few minutes…")
          : tm("Look for suggestions")}
      </button>
      {error && (
        <div className="rounded-lg border border-rose-400/40 bg-rose-400/10 px-3 py-2 text-xs text-rose-800 dark:text-rose-100">
          {error}
        </div>
      )}
      {data && !candidate && (
        <div className="space-y-2">
          {(data.model === "failed" || data.model === "off") && rows.length > 0 && (
            <div className="text-[11px] text-amber-700 dark:text-amber-200">
              {tm("The language model was not available: name the new types yourself.")}
            </div>
          )}
          {data.scanned.cut && (
            <div className="text-[11px] text-ink/50">
              {tm("Only the first part of the graph was examined; run it again after saving.")}
            </div>
          )}
          {data.known.length > 0 && (
            <div className="rounded-lg border border-ink/10 bg-ink/5 px-3 py-2 text-[11px] text-ink/70">
              <div className="mb-1 font-medium">
                {tm(
                  "Already in the profile: these links come from documents read with an older profile, and reprocessing them types the links.",
                )}
              </div>
              {data.known.map((k) => (
                <div key={k.key}>
                  “{k.label}” → {relationName(k.relation)} ·{" "}
                  {fill(tm("{n} links, {m} documents to reprocess"), {
                    n: k.support.relations ?? 0,
                    m: k.support.stale_documents ?? 0,
                  })}
                </div>
              ))}
            </div>
          )}
          {rows.length === 0 && (
            <div className="text-[11px] text-ink/60">
              {tm("No suggestions: the profile covers what the extraction found.")}
            </div>
          )}
          {rows.map((row) => (
            <div
              key={row.key}
              className={`rounded-lg border px-3 py-2 text-xs ${row.accepted ? "border-ink/15" : "border-ink/5 opacity-60"}`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="checkbox"
                  checked={row.accepted}
                  aria-label={tm("Accept")}
                  onChange={(e) => update(row.key, { accepted: e.target.checked })}
                  className="h-3.5 w-3.5 accent-[#8C49E9]"
                />
                <span className="font-medium">
                  {row.kind === "relation" ? `“${row.label}”` : row.shape}
                </span>
                <span className="text-[11px] text-ink/50">
                  {row.kind === "relation"
                    ? fill(tm("{n} links in {m} documents"), {
                        n: row.support.relations ?? 0,
                        m: row.support.documents,
                      })
                    : fill(tm("{n} values in {m} documents"), {
                        n: row.support.values ?? 0,
                        m: row.support.documents,
                      })}
                </span>
                {row.kind === "relation" && row.ends?.length ? (
                  <span className="text-[11px] text-ink/50">
                    {row.ends
                      .slice(0, 2)
                      .map((e) => `${tm(TYPE_NAME[e.from])} → ${tm(TYPE_NAME[e.to])}`)
                      .join(", ")}
                  </span>
                ) : null}
                <span className="ml-auto text-[10px] text-ink/40">
                  {row.source === "model" ? tm("suggested by the model") : tm("to be named")}
                </span>
              </div>
              {row.accepted && (
                <div className="mt-2 grid gap-2 md:grid-cols-2">
                  <label className="block">
                    <span className="mb-0.5 block text-[10px] text-ink/50">{tm("What to do")}</span>
                    <select
                      value={row.action}
                      onChange={(e) => {
                        const action = e.target.value as Row["action"];
                        const fallback =
                          row.kind === "relation"
                            ? { relation: row.relation ?? relations[0]?.id }
                            : { subtype: row.subtype ?? identifierKinds[0]?.id };
                        update(row.key, { action, ...fallback });
                      }}
                      className={input}
                    >
                      {row.kind === "relation" ? (
                        <>
                          <option value="new">{tm("New relation type")}</option>
                          <option value="synonym">{tm("Word for an existing type")}</option>
                          <option value="ignore">{tm("Not a relation type")}</option>
                        </>
                      ) : (
                        <>
                          <option value="new">{tm("New identifier kind")}</option>
                          <option value="existing">{tm("Pattern of an existing kind")}</option>
                          <option value="ignore">{tm("Not an identifier")}</option>
                        </>
                      )}
                    </select>
                  </label>
                  {row.action === "synonym" && (
                    <label className="block">
                      <span className="mb-0.5 block text-[10px] text-ink/50">
                        {tm("Relation type")}
                      </span>
                      <select
                        value={row.relation ?? ""}
                        onChange={(e) => update(row.key, { relation: e.target.value })}
                        className={input}
                      >
                        {relations.map((r) => (
                          <option key={r.id} value={r.id}>
                            {label(r.label)} ({kinds(r.from)} → {kinds(r.to)})
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {row.action === "existing" && (
                    <label className="block">
                      <span className="mb-0.5 block text-[10px] text-ink/50">
                        {tm("Identifier kind")}
                      </span>
                      <select
                        value={row.subtype ?? ""}
                        onChange={(e) => update(row.key, { subtype: e.target.value })}
                        className={input}
                      >
                        {identifierKinds.map((s) => (
                          <option key={s.id} value={s.id}>
                            {label(s.label)}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {row.action === "new" && (
                    <label className="block">
                      <span className="mb-0.5 block text-[10px] text-ink/50">{tm("Id")}</span>
                      <input
                        value={row.id ?? ""}
                        onChange={(e) =>
                          update(row.key, {
                            id:
                              row.kind === "relation"
                                ? e.target.value.toUpperCase()
                                : e.target.value.toLowerCase(),
                          })
                        }
                        className={`${input} font-mono`}
                      />
                    </label>
                  )}
                  {row.action === "new" && (
                    <div className="grid grid-cols-2 gap-1 md:col-span-2 md:grid-cols-4">
                      {LANGS.map((lang) => (
                        <label key={lang} className="block">
                          <span className="mb-0.5 block text-[10px] uppercase text-ink/50">
                            {lang}
                          </span>
                          <input
                            value={row.labels?.[lang] ?? ""}
                            onChange={(e) => setLabel(row, lang, e.target.value)}
                            className={input}
                          />
                        </label>
                      ))}
                    </div>
                  )}
                  {row.kind === "identifier" && row.action !== "ignore" && (
                    <label className="block md:col-span-2">
                      <span className="mb-0.5 block text-[10px] text-ink/50">
                        {tm("Pattern (regular expression)")}
                      </span>
                      <input
                        value={row.regex ?? ""}
                        onChange={(e) => update(row.key, { regex: e.target.value })}
                        className={`${input} font-mono`}
                      />
                    </label>
                  )}
                  {row.kind === "relation" && row.action === "new" && (
                    <div className="text-[11px] text-ink/50 md:col-span-2">
                      {kinds(row.from)} → {kinds(row.to)}
                      {row.variants?.length ? ` · ${tm("Words")}: ${row.variants.join(", ")}` : ""}
                    </div>
                  )}
                  {row.kind === "relation" &&
                    row.action === "synonym" &&
                    row.fit !== undefined &&
                    row.fit < 0.5 && (
                      <div className="text-[11px] text-amber-700 dark:text-amber-200 md:col-span-2">
                        {tm(
                          "Most of these links join kinds this type does not allow; they would stay generic.",
                        )}
                      </div>
                    )}
                </div>
              )}
            </div>
          ))}
          {rows.length > 0 && (
            <button
              className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs text-white disabled:opacity-40"
              style={{ backgroundColor: ACCENT }}
              disabled={busy !== null}
              onClick={() => void apply()}
            >
              {busy === "apply" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Check className="h-3.5 w-3.5" />
              )}
              {tm("Apply the accepted ones")}
            </button>
          )}
        </div>
      )}
      {candidate && (
        <div className="space-y-2 rounded-lg border border-ink/15 px-3 py-2 text-xs">
          <div className="font-medium">
            {fill(tm("Version {v} would change:"), { v: candidate.profile.version })}
          </div>
          <ul className="list-disc space-y-0.5 pl-5 text-[11px] text-ink/70">
            {candidate.changes.map((c, i) => (
              <li key={i}>{changeText(c)}</li>
            ))}
          </ul>
          <div className="flex gap-2">
            <button
              className="rounded-full px-3 py-1 text-xs text-white disabled:opacity-40"
              style={{ backgroundColor: ACCENT }}
              disabled={busy !== null}
              onClick={() => void save()}
            >
              {tm("Save as new version")}
            </button>
            <button className={button} disabled={busy !== null} onClick={() => setCandidate(null)}>
              {tm("Back")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
