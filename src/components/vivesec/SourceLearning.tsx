// Learning structured sources (E28b, graph_service/source_learning.py).
//
// LearnSource: a new source from a sample file. The box reads the header and a
// few rows; the local model (or, without it, name rules) says what each column
// is; the administrator confirms or corrects that here and the box saves it as
// a new source in the next profile version. The draft never holds a cell value.
//
// AssignColumns: an export renamed a column of an existing source; the
// administrator picks the file's column for it once, and the box matches it by
// that name from then on (the source's column_aliases).

import { useEffect, useMemo, useState } from "react";
import { Loader2, Plus, Sparkles, X } from "lucide-react";

import {
  graphApi,
  SOURCE_ROLES,
  type GraphDraftColumn,
  type GraphDraftRelation,
  type GraphEntityType,
  type GraphImportFit,
  type GraphLabel,
  type GraphProfile,
  type GraphSampleFile,
  type GraphSourceDraft,
  type GraphSourceRole,
} from "@/lib/api/graph.functions";
import { AdapterGraphError } from "@/lib/rag/adapter-client";
import { ACCENT, ON_ACCENT } from "./brand";
import { useLang } from "./i18n";

const ROLE_TEXT: Record<GraphSourceRole, string> = {
  record_id: "Record id",
  person: "Person name",
  birth_date: "Birth date",
  birth_place: "Birth place",
  mother_name: "Mother's name",
  id_number: "Personal ID number",
  registry_number: "Registry number",
  phone: "Phone number",
  email: "E-mail address",
  address: "Postal address",
  start_date: "Start date",
  end_date: "End date",
  relation_label: "Nature of the relation",
  deleted: "Deleted mark",
  text: "Free text",
  ignore: "Not used",
};
const KIND_TEXT: Record<GraphDraftColumn["kind"], string> = {
  empty: "empty in the sample",
  date: "dates",
  number: "numbers",
  email: "e-mail addresses",
  phone: "phone numbers",
  text: "text",
};
const ATTRIBUTE_ROLES = new Set<GraphSourceRole>([
  "birth_date",
  "birth_place",
  "mother_name",
  "id_number",
  "registry_number",
]);
const OWNED: Partial<Record<GraphSourceRole, GraphEntityType>> = {
  phone: "identifier",
  email: "identifier",
  address: "place",
};
const GENERIC = new Set(["RELATED_TO", "CO_MENTIONED"]);

const input =
  "min-w-0 rounded-lg border border-ink/15 bg-shade/30 px-2 py-1 text-xs text-ink outline-none focus:border-ink/30 disabled:opacity-50";
const button =
  "inline-flex items-center gap-1.5 rounded-lg border border-ink/15 px-3 py-1 text-xs hover:bg-ink/10 disabled:opacity-50";

function errorText(err: unknown): string {
  return err instanceof AdapterGraphError || err instanceof Error ? err.message : String(err);
}

type Relation = GraphProfile["relation_types"][number];

function linkable(profile: GraphProfile): Relation[] {
  return profile.relation_types.filter(
    (r) => !GENERIC.has(r.id) && !(r as { protected?: boolean }).protected,
  );
}

function defaultLink(relations: Relation[], role: GraphSourceRole): string {
  const target = OWNED[role];
  if (!target) return "";
  const fits = relations.filter((r) => r.from.includes("person") && r.to.includes(target));
  const prefer = role === "address" ? "RESIDES_AT" : "USES";
  return (fits.find((r) => r.id === prefer) ?? fits[0])?.id ?? "";
}

export function LearnSource({
  drive,
  file,
  profile,
  code,
  onSaved,
  onCancel,
}: {
  drive: string;
  file: GraphSampleFile;
  profile: GraphProfile;
  code: keyof GraphLabel;
  onSaved: (source: string, version: number) => Promise<void>;
  onCancel: () => void;
}) {
  const { tm } = useLang();
  const [draft, setDraft] = useState<GraphSourceDraft | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<"learn" | "save" | null>("learn");
  const [error, setError] = useState("");
  const [readError, setReadError] = useState("");

  const label = (l: GraphLabel | undefined) => (l ? (l[code] ?? l.en ?? "") : "");
  const relations = useMemo(() => linkable(profile), [profile]);
  const personRoles = profile.entity_subtypes.filter((s) => s.type === "person");

  // One model call per file: not repeated on a language switch.
  useEffect(() => {
    let live = true;
    setBusy("learn");
    setReadError("");
    graphApi
      .importLearn(drive, file)
      .then((res) => {
        if (!live) return;
        setDraft(res);
        setName(res.name);
      })
      .catch((err) => live && setReadError(errorText(err)))
      .finally(() => live && setBusy(null));
    return () => {
      live = false;
    };
  }, [drive, file]);

  if (!draft) {
    return (
      <div className="mt-2 space-y-2 rounded-xl border border-ink/10 p-2.5 text-xs">
        {busy === "learn" && (
          <div className="flex items-center gap-2 text-ink/60">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {tm("Reading the columns. The local model may take a minute…")}
          </div>
        )}
        {readError && (
          <div className="text-[11px] text-amber-700 dark:text-amber-300">
            {`${tm("The file could not be read.")} ${readError}`}
          </div>
        )}
        <button onClick={onCancel} className={button}>
          {tm("Cancel")}
        </button>
      </div>
    );
  }

  const persons = draft.columns.filter((c) => c.role === "person").map((c) => c.column);
  const entityColumns = draft.columns.filter((c) => c.role === "person" || OWNED[c.role]);
  const typeOf = (column: string): GraphEntityType | null => {
    const c = draft.columns.find((x) => x.column === column);
    return !c ? null : c.role === "person" ? "person" : (OWNED[c.role] ?? null);
  };
  const columnsOf = (role: GraphSourceRole) =>
    draft.columns.filter((c) => c.role === role).map((c) => c.column);

  function setColumn(column: string, change: Partial<GraphDraftColumn>) {
    setDraft((d) => {
      if (!d) return d;
      let columns = d.columns.map((c) => (c.column === column ? { ...c, ...change } : c));
      const before = d.columns.find((c) => c.column === column);
      const after = columns.find((c) => c.column === column)!;
      let rels = d.relations;
      if (change.role && before && change.role !== before.role) {
        const people = columns.filter((c) => c.role === "person").map((c) => c.column);
        const entity = after.role === "person" || !!OWNED[after.role];
        Object.assign(after, {
          subtype: "",
          of: ATTRIBUTE_ROLES.has(after.role) || OWNED[after.role] ? (people[0] ?? "") : "",
          relation: OWNED[after.role] ? defaultLink(relations, after.role) : undefined,
        });
        // What pointed at the column in its old role no longer can.
        if (before.role === "person") {
          columns = columns.map((c) =>
            c.of === column ? { ...c, of: people.length === 1 ? people[0] : "" } : c,
          );
        }
        rels = rels
          .filter((r) => entity || (r.from !== column && r.to !== column))
          .map((r) => ({
            ...r,
            start: r.start === column ? "" : r.start,
            end: r.end === column ? "" : r.end,
            label: r.label === column ? "" : r.label,
          }));
      }
      return { ...d, columns, relations: rels };
    });
  }

  function setRelation(n: number, change: Partial<GraphDraftRelation>) {
    setDraft((d) =>
      d ? { ...d, relations: d.relations.map((r, i) => (i === n ? { ...r, ...change } : r)) } : d,
    );
  }

  function addRelation() {
    const from = entityColumns[0]?.column ?? "";
    const to = entityColumns.find((c) => c.column !== from)?.column ?? "";
    const ta = typeOf(from);
    const tb = typeOf(to);
    const type =
      relations.find((r) => ta && tb && r.from.includes(ta) && r.to.includes(tb))?.id ??
      relations[0]?.id ??
      "";
    setDraft((d) =>
      d
        ? {
            ...d,
            relations: [...d.relations, { type, from, to, start: "", end: "", label: "" }],
          }
        : d,
    );
  }

  async function save() {
    if (!draft) return;
    if (!name.trim()) {
      setError(tm("Give the source a name."));
      return;
    }
    if (!draft.columns.some((c) => c.role === "record_id")) {
      setError(tm("Choose at least one record id column."));
      return;
    }
    setBusy("save");
    setError("");
    try {
      const applied = await graphApi.importLearnApply(drive, draft, name.trim());
      const saved = await graphApi.saveProfile(drive, applied.profile);
      await onSaved(applied.source.id, saved.profile.version);
    } catch (err) {
      setError(`${tm("The source was not saved.")} ${errorText(err)}`);
    } finally {
      setBusy(null);
    }
  }

  const modelNote =
    draft.model === "used"
      ? "The local model suggested these roles. Check every column before saving."
      : "The local model was not available: the roles come from the column names. Check every column before saving.";
  const options = (values: string[], empty: string) => (
    <>
      <option value="" className="bg-surface-2">
        {empty}
      </option>
      {values.map((v) => (
        <option key={v} value={v} className="bg-surface-2">
          {v}
        </option>
      ))}
    </>
  );

  return (
    <div className="mt-2 space-y-3 rounded-xl border border-ink/10 p-2.5 text-xs">
      <div className="flex items-start gap-2">
        <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink/50" />
        <div className="space-y-0.5">
          <div className="font-medium text-ink">{tm("New source from this file")}</div>
          <div className="text-[11px] text-ink/55">{tm(modelNote)}</div>
          <div className="text-[11px] text-ink/45">
            {tm("{rows} rows; the sample stays on the AI Box.").replace(
              "{rows}",
              String(draft.rows),
            )}
            {draft.tables.length > 1 && ` ${tm("Table")}: ${draft.sheet}`}
          </div>
        </div>
      </div>
      <label className="flex flex-wrap items-center gap-2">
        <span className="w-28 shrink-0 text-ink/60">{tm("Source name")}</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={60}
          disabled={!!busy}
          className={`${input} flex-1`}
        />
      </label>
      <div className="space-y-1">
        <div className="text-[11px] uppercase tracking-wide text-ink/45">{tm("Columns")}</div>
        {draft.columns.map((c) => {
          const owned = OWNED[c.role];
          const linkFits = owned
            ? relations.filter((r) => r.from.includes("person") && r.to.includes(owned))
            : [];
          return (
            <div
              key={c.column}
              className="flex flex-wrap items-center gap-1.5 border-b border-ink/5 pb-1"
            >
              <div className="w-44 min-w-0 shrink-0">
                <div className="truncate font-medium text-ink" title={c.column}>
                  {c.column}
                </div>
                <div className="text-[10.5px] text-ink/45">{tm(KIND_TEXT[c.kind])}</div>
              </div>
              <select
                value={c.role}
                onChange={(e) =>
                  setColumn(c.column, { role: e.target.value as GraphSourceRole, source: "rule" })
                }
                disabled={!!busy}
                className={input}
                aria-label={tm("Role")}
              >
                {SOURCE_ROLES.map((r) => (
                  <option key={r} value={r} className="bg-surface-2">
                    {tm(ROLE_TEXT[r])}
                  </option>
                ))}
              </select>
              {c.role === "person" && personRoles.length > 0 && (
                <select
                  value={c.subtype}
                  onChange={(e) => setColumn(c.column, { subtype: e.target.value })}
                  disabled={!!busy}
                  className={input}
                  aria-label={tm("Person's role")}
                >
                  <option value="" className="bg-surface-2">
                    {tm("No particular role")}
                  </option>
                  {personRoles.map((s) => (
                    <option key={s.id} value={s.id} className="bg-surface-2">
                      {label(s.label)}
                    </option>
                  ))}
                </select>
              )}
              {(ATTRIBUTE_ROLES.has(c.role) || owned) && persons.length > 1 && (
                <label className="flex items-center gap-1 text-ink/60">
                  {tm("Belongs to")}
                  <select
                    value={c.of}
                    onChange={(e) => setColumn(c.column, { of: e.target.value })}
                    disabled={!!busy}
                    className={input}
                  >
                    {options(persons, tm("Choose a person"))}
                  </select>
                </label>
              )}
              {owned && persons.length > 0 && (
                <select
                  value={c.relation ?? ""}
                  onChange={(e) => setColumn(c.column, { relation: e.target.value })}
                  disabled={!!busy}
                  className={input}
                  aria-label={tm("Linked to the person as")}
                >
                  <option value="" className="bg-surface-2">
                    {tm("Not linked to the person")}
                  </option>
                  {linkFits.map((r) => (
                    <option key={r.id} value={r.id} className="bg-surface-2">
                      {label(r.label)}
                    </option>
                  ))}
                </select>
              )}
            </div>
          );
        })}
      </div>
      <div className="space-y-1.5">
        <div className="text-[11px] uppercase tracking-wide text-ink/45">
          {tm("Relations in every row")}
        </div>
        {draft.relations.length === 0 && (
          <div className="text-[11px] text-ink/50">
            {tm("None. Dates and relation texts no relation reads stay in the record's text.")}
          </div>
        )}
        {draft.relations.map((r, n) => (
          <div key={n} className="flex flex-wrap items-center gap-1.5 border-b border-ink/5 pb-1.5">
            <select
              value={r.from}
              onChange={(e) => setRelation(n, { from: e.target.value })}
              disabled={!!busy}
              className={input}
              aria-label={tm("From")}
            >
              {options(
                entityColumns.map((c) => c.column),
                "—",
              )}
            </select>
            <select
              value={r.type}
              onChange={(e) => setRelation(n, { type: e.target.value })}
              disabled={!!busy}
              className={input}
              aria-label={tm("Relation")}
            >
              {relations.map((x) => (
                <option key={x.id} value={x.id} className="bg-surface-2">
                  {label(x.label)}
                </option>
              ))}
            </select>
            <select
              value={r.to}
              onChange={(e) => setRelation(n, { to: e.target.value })}
              disabled={!!busy}
              className={input}
              aria-label={tm("To")}
            >
              {options(
                entityColumns.map((c) => c.column),
                "—",
              )}
            </select>
            {(
              [
                ["start", "start_date", "Start date"],
                ["end", "end_date", "End date"],
                ["label", "relation_label", "Nature of the relation"],
              ] as const
            ).map(
              ([key, role, title]) =>
                columnsOf(role).length > 0 && (
                  <label key={key} className="flex items-center gap-1 text-ink/60">
                    {tm(title)}
                    <select
                      value={r[key]}
                      onChange={(e) => setRelation(n, { [key]: e.target.value })}
                      disabled={!!busy}
                      className={input}
                    >
                      {options(columnsOf(role), "—")}
                    </select>
                  </label>
                ),
            )}
            <button
              onClick={() =>
                setDraft((d) =>
                  d ? { ...d, relations: d.relations.filter((_, i) => i !== n) } : d,
                )
              }
              disabled={!!busy}
              className="rounded p-1 text-ink/50 hover:bg-ink/10 hover:text-ink"
              aria-label={tm("Remove")}
              title={tm("Remove")}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
        {entityColumns.length >= 2 && relations.length > 0 && (
          <button onClick={addRelation} disabled={!!busy} className={button}>
            <Plus className="h-3.5 w-3.5" />
            {tm("Add relation")}
          </button>
        )}
      </div>
      {draft.columns.some((c) => c.role === "text") && (
        <label className="flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            checked={!!draft.rag}
            onChange={(e) => setDraft((d) => (d ? { ...d, rag: e.target.checked } : d))}
            disabled={!!busy}
          />
          <span>{tm("Also put the free text into the search")}</span>
        </label>
      )}
      {error && <div className="text-[11px] text-amber-700 dark:text-amber-300">{error}</div>}
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => void save()}
          disabled={!!busy}
          className="rounded-lg px-3 py-1 text-xs font-medium disabled:opacity-50"
          style={{ backgroundColor: ACCENT, color: ON_ACCENT }}
        >
          {tm("Save as a new source")}
        </button>
        <button onClick={onCancel} disabled={busy === "save"} className={button}>
          {tm("Cancel")}
        </button>
        {busy === "save" && <Loader2 className="h-3.5 w-3.5 animate-spin text-ink/60" />}
      </div>
      <div className="text-[11px] text-ink/45">
        {tm(
          "The box checks the new profile version before saving it. Documents need not be processed again.",
        )}
      </div>
    </div>
  );
}

export function AssignColumns({
  drive,
  fit,
  sourceName,
  onSaved,
  onCancel,
}: {
  drive: string;
  fit: GraphImportFit;
  sourceName: string;
  onSaved: (version: number) => Promise<void>;
  onCancel: () => void;
}) {
  const { tm } = useLang();
  const missing = [...fit.missing_required, ...fit.missing_optional];
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const taken = new Set(Object.values(chosen).filter(Boolean));

  async function save() {
    const aliases = Object.fromEntries(Object.entries(chosen).filter(([, v]) => v));
    if (!Object.keys(aliases).length) {
      setError(tm("Choose the file's column for at least one missing column."));
      return;
    }
    setBusy(true);
    setError("");
    try {
      const applied = await graphApi.importAliasesApply(drive, fit.id, aliases);
      const saved = await graphApi.saveProfile(drive, applied.profile);
      await onSaved(saved.profile.version);
    } catch (err) {
      setError(`${tm("The column names were not saved.")} ${errorText(err)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 space-y-2 rounded-xl border border-ink/10 p-2.5 text-xs">
      <div className="font-medium text-ink">
        {tm("Match the columns of {source}").replace("{source}", sourceName)}
      </div>
      <div className="text-[11px] text-ink/55">
        {tm(
          "Pick the file's column for each missing one. The box remembers the new name and matches it by itself from then on.",
        )}
      </div>
      {missing.map((column) => (
        <label key={column} className="flex flex-wrap items-center gap-2">
          <span className="w-48 min-w-0 shrink-0 truncate" title={column}>
            {column}
            {fit.missing_required.includes(column) && (
              <span className="text-amber-700 dark:text-amber-300"> *</span>
            )}
          </span>
          <select
            value={chosen[column] ?? ""}
            onChange={(e) => setChosen((c) => ({ ...c, [column]: e.target.value }))}
            disabled={busy}
            className={`${input} flex-1`}
          >
            <option value="" className="bg-surface-2">
              {tm("Not in this file")}
            </option>
            {fit.unused
              .filter((h) => h === chosen[column] || !taken.has(h))
              .map((h) => (
                <option key={h} value={h} className="bg-surface-2">
                  {h}
                </option>
              ))}
          </select>
        </label>
      ))}
      {fit.missing_required.length > 0 && (
        <div className="text-[11px] text-ink/45">{`* ${tm("The import needs this column.")}`}</div>
      )}
      {error && <div className="text-[11px] text-amber-700 dark:text-amber-300">{error}</div>}
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => void save()}
          disabled={busy}
          className="rounded-lg px-3 py-1 text-xs font-medium disabled:opacity-50"
          style={{ backgroundColor: ACCENT, color: ON_ACCENT }}
        >
          {tm("Save the column names")}
        </button>
        <button onClick={onCancel} disabled={busy} className={button}>
          {tm("Cancel")}
        </button>
        {busy && <Loader2 className="h-3.5 w-3.5 animate-spin text-ink/60" />}
      </div>
    </div>
  );
}
