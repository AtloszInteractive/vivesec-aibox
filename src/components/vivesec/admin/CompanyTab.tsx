// The company profile on the administration page (E12, adapter
// company_profile.py): the organisation's facts by group, proposals read from
// documents for review, approval, expiry, history and restore. Facts in effect
// are background for the assistant and the agents, up to each user's
// clearance. The adapter validates everything again; this page only shapes
// the calls and shows what it answered.

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BookOpenText,
  Building2,
  Check,
  FileSearch,
  History,
  Pencil,
  Plus,
  RefreshCw,
  Square,
  Trash2,
} from "lucide-react";

import {
  AdminError,
  cancelExtraction,
  deleteFact,
  listProfileDocuments,
  readCompany,
  readFactHistory,
  restoreFact,
  saveFact,
  setFactState,
  startExtraction,
  type CompanyFact,
  type CompanyState,
  type Extraction,
  type FactHistoryEntry,
  type FactInput,
  type FactState,
  type ProfileDocument,
  type ProfileGroup,
} from "@/lib/auth/admin-client";
import { useLang } from "../i18n";
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Dialog,
  Notice,
  SelectField,
  TextField,
  errorText,
  fill,
  formatTime,
  levelLabel,
  type Tm,
} from "./ui";

type Feedback = { tone: "ok" | "error"; text: string } | null;

const STATE_TEXT: Record<FactState, string> = {
  draft: "Draft",
  effective: "In effect",
  expired: "Expired",
  archived: "Archived",
};

const STATE_TONE: Record<FactState, "neutral" | "accent" | "ok" | "warn"> = {
  draft: "warn",
  effective: "ok",
  expired: "neutral",
  archived: "neutral",
};

const ACTION_TEXT: Record<FactHistoryEntry["action"], string> = {
  create: "Created",
  edit: "Edited",
  state: "State changed",
  delete: "Deleted",
  propose: "Proposed from a document",
  import: "Taken over from the organisation card",
};

const READING = new Set<Extraction["state"]>(["queued", "running"]);

const TEXTAREA =
  "w-full rounded-lg border border-ink/15 bg-ink/5 px-2.5 py-1.5 text-sm text-ink outline-none transition placeholder:text-ink/30 focus:border-brand-soft focus:ring-2 focus:ring-[#8C49E9]/40 disabled:opacity-60";

function fieldOf(schema: ProfileGroup[], fact: { group: string; field: string }) {
  return schema.find((g) => g.id === fact.group)?.fields.find((f) => f.id === fact.field);
}

function validity(fact: CompanyFact, tm: Tm): string | null {
  if (fact.valid_from && fact.valid_to) {
    return fill(tm("Valid {from} – {to}"), { from: fact.valid_from, to: fact.valid_to });
  }
  if (fact.valid_from) return fill(tm("Valid from {date}"), { date: fact.valid_from });
  if (fact.valid_to) return fill(tm("Valid until {date}"), { date: fact.valid_to });
  return null;
}

function SourceLine({ fact }: { fact: CompanyFact }) {
  const { tm } = useLang();
  if (!fact.source) return null;
  const where =
    fact.source.page != null
      ? fill(tm("From {path}, page {page}"), { path: fact.source.path, page: fact.source.page })
      : fill(tm("From {path}"), { path: fact.source.path });
  return (
    <div className="mt-1.5 rounded-lg border-l-2 border-[#8C49E9]/50 bg-ink/[0.03] px-2.5 py-1.5 text-[11px] text-ink/65">
      {fact.source.quote && <q className="italic">{fact.source.quote}</q>}
      <span className="mt-0.5 block break-all text-ink/45">{where}</span>
    </div>
  );
}

/* ------------------------------------------------------------------ one fact */

function FactRow({
  fact,
  state,
  busy,
  onAct,
  onEdit,
  onHistory,
  onDelete,
}: {
  fact: CompanyFact;
  state: CompanyState;
  busy: string | null;
  onAct: (key: string, work: (base: number) => Promise<CompanyState>, done: string) => void;
  onEdit: (fact: CompanyFact) => void;
  onHistory: (fact: CompanyFact) => void;
  onDelete: (fact: CompanyFact) => void;
}) {
  const { tm, lang } = useLang();
  const field = fieldOf(state.schema, fact);
  const name = field ? tm(field.label) : `${fact.group}/${fact.field}`;
  const until = validity(fact, tm);
  const move = (to: FactState, done: string) =>
    onAct(`${fact.id}:${to}`, (base) => setFactState(base, fact.id, to), done);
  return (
    <li className="rounded-xl border border-ink/10 bg-ink/[0.02] px-3 py-2.5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="font-semibold text-ink">
              {name}
              {fact.label && `: ${fact.label}`}
            </span>
            <Badge tone={STATE_TONE[fact.state]}>{tm(STATE_TEXT[fact.state])}</Badge>
            <Badge>{levelLabel(fact.classification, tm)}</Badge>
            {until && <Badge>{until}</Badge>}
            {fact.replaces && <Badge tone="accent">{tm("Replaces an earlier value")}</Badge>}
          </div>
          <p className="mt-1 whitespace-pre-wrap break-words text-sm text-ink/85">{fact.value}</p>
          <SourceLine fact={fact} />
          <span className="mt-1 block text-[10.5px] text-ink/45">
            {fill(tm("Set by {user}, {time}"), {
              user: fact.updated_by,
              time: formatTime(fact.updated_at, lang),
            })}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {fact.state === "draft" && (
            <Button
              kind="primary"
              busy={busy === `${fact.id}:effective`}
              disabled={!!busy}
              onClick={() => move("effective", tm("Approved: the assistant uses it from now on."))}
            >
              <Check className="h-3.5 w-3.5" />
              {tm("Approve")}
            </Button>
          )}
          {fact.state === "effective" && (
            <Button
              busy={busy === `${fact.id}:expired`}
              disabled={!!busy}
              onClick={() => move("expired", tm("Expired: the assistant no longer uses it."))}
            >
              {tm("Expire")}
            </Button>
          )}
          {(fact.state === "effective" || fact.state === "expired") && (
            <Button
              busy={busy === `${fact.id}:archived`}
              disabled={!!busy}
              onClick={() => move("archived", tm("Archived."))}
            >
              {tm("Archive")}
            </Button>
          )}
          {(fact.state === "expired" || fact.state === "archived") && (
            <Button
              busy={busy === `${fact.id}:draft`}
              disabled={!!busy}
              onClick={() => move("draft", tm("Reopened as a draft."))}
            >
              {tm("Reopen as draft")}
            </Button>
          )}
          <Button disabled={!!busy} onClick={() => onEdit(fact)} title={tm("Edit")}>
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button disabled={!!busy} onClick={() => onHistory(fact)} title={tm("History")}>
            <History className="h-3.5 w-3.5" />
          </Button>
          {fact.state !== "effective" && (
            <Button
              kind="danger"
              disabled={!!busy}
              onClick={() => onDelete(fact)}
              title={tm("Delete")}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>
    </li>
  );
}

/* ------------------------------------------------------------------ editing */

type EditTarget = { fact: FactInput; current: CompanyFact | null };

function FactDialog({
  target,
  state,
  onClose,
  onSave,
}: {
  target: EditTarget;
  state: CompanyState;
  onClose: () => void;
  onSave: (fact: FactInput) => Promise<boolean>;
}) {
  const { tm } = useLang();
  const [fact, setFact] = useState<FactInput>(target.fact);
  const [busy, setBusy] = useState(false);
  const group = state.schema.find((g) => g.id === fact.group);
  const field = fieldOf(state.schema, fact);
  const set = (change: Partial<FactInput>) => setFact({ ...fact, ...change });
  const ready =
    fact.value.trim() !== "" &&
    (!field?.named || fact.label.trim() !== "") &&
    (!fact.valid_from || !fact.valid_to || fact.valid_from <= fact.valid_to);

  async function save() {
    setBusy(true);
    const ok = await onSave({ ...fact, label: field?.repeatable ? fact.label : "" });
    setBusy(false);
    if (ok) onClose();
  }

  return (
    <Dialog
      title={target.current ? tm("Edit fact") : tm("New fact")}
      wide
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{tm("Cancel")}</Button>
          <Button kind="primary" busy={busy} disabled={!ready} onClick={() => void save()}>
            {tm("Save")}
          </Button>
        </>
      }
    >
      {target.current?.state === "effective" && (
        <Notice tone="warn">
          {tm(
            "This fact is in effect. Saving makes a draft; the current value stays in use until you approve the draft.",
          )}
        </Notice>
      )}
      {!target.current && group && (
        <SelectField
          label={tm("Field")}
          value={fact.field}
          options={group.fields.map((f) => ({ value: f.id, label: tm(f.label) }))}
          onChange={(value) => set({ field: value })}
        />
      )}
      {field?.repeatable && (
        <TextField
          label={field.named ? tm("Name") : tm("Name (optional)")}
          hint={tm("For example the name of the unit, the role, the policy or the term.")}
          value={fact.label}
          maxLength={state.limits.max_label}
          onChange={(e) => set({ label: e.target.value })}
        />
      )}
      <label className="block">
        <span className="mb-1 block text-[11px] font-medium text-ink/65">{tm("Value")}</span>
        <textarea
          value={fact.value}
          maxLength={state.limits.max_value}
          rows={4}
          onChange={(e) => set({ value: e.target.value })}
          className={TEXTAREA}
        />
        <span className="mt-1 block text-[11px] text-ink/45">
          {fill(tm("{n} of {m} characters"), {
            n: fact.value.length,
            m: state.limits.max_value,
          })}
        </span>
      </label>
      <SelectField
        label={tm("Classification")}
        value={fact.classification}
        options={state.levels.map((level) => ({ value: level, label: levelLabel(level, tm) }))}
        onChange={(value) => set({ classification: value })}
      />
      <p className="text-[11px] text-ink/45">
        {tm("A user gets the fact only when their clearance reaches this level.")}
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label={tm("Valid from")}
          type="date"
          value={fact.valid_from ?? ""}
          onChange={(e) => set({ valid_from: e.target.value || null })}
        />
        <TextField
          label={tm("Valid until")}
          type="date"
          value={fact.valid_to ?? ""}
          onChange={(e) => set({ valid_to: e.target.value || null })}
        />
      </div>
    </Dialog>
  );
}

function HistoryDialog({
  fact,
  state,
  onClose,
  onRestore,
}: {
  fact: CompanyFact;
  state: CompanyState;
  onClose: () => void;
  onRestore: (entry: FactHistoryEntry) => Promise<boolean>;
}) {
  const { tm, lang } = useLang();
  const [entries, setEntries] = useState<FactHistoryEntry[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<number | null>(null);

  useEffect(() => {
    readFactHistory(fact.id)
      .then(setEntries)
      .catch((err) => setError(errorText(err, tm)));
  }, [fact.id, tm]);

  const field = fieldOf(state.schema, fact);
  return (
    <Dialog
      title={`${tm("History of the fact")}: ${field ? tm(field.label) : fact.field}${fact.label ? ` – ${fact.label}` : ""}`}
      wide
      onClose={onClose}
      footer={<Button onClick={onClose}>{tm("Close")}</Button>}
    >
      {error && <Notice tone="error">{error}</Notice>}
      {entries && entries.length === 0 && (
        <p className="text-xs text-ink/60">{tm("No history.")}</p>
      )}
      <ol className="space-y-2">
        {entries?.map((entry) => {
          const content = entry.before ?? entry.after;
          return (
            <li key={entry.seq} className="rounded-lg border border-ink/10 px-3 py-2 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium text-ink">
                  {tm(ACTION_TEXT[entry.action] ?? entry.action)}
                  {entry.action === "state" && entry.after && (
                    <>
                      {" → "}
                      {tm(STATE_TEXT[entry.after.state])}
                    </>
                  )}
                </span>
                <span className="text-ink/50">
                  {entry.user}, {formatTime(entry.ts, lang)}
                </span>
              </div>
              {entry.action === "edit" && entry.before && entry.after ? (
                <div className="mt-1 space-y-0.5 text-ink/75">
                  <p className="line-through decoration-ink/40">{entry.before.value}</p>
                  <p>{entry.after.value}</p>
                </div>
              ) : (
                content && <p className="mt-1 text-ink/75">{content.value}</p>
              )}
              {content && entry.action !== "state" && (
                <div className="mt-1.5">
                  <Button
                    busy={busy === entry.seq}
                    disabled={busy !== null}
                    title={tm("Makes a draft with this content.")}
                    onClick={() => {
                      setBusy(entry.seq);
                      void onRestore(entry).then((ok) => {
                        setBusy(null);
                        if (ok) onClose();
                      });
                    }}
                  >
                    {tm("Restore")}
                  </Button>
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ reading documents */

function ReadingCard({
  state,
  onState,
  onError,
}: {
  state: CompanyState;
  onState: (state: CompanyState) => void;
  onError: (err: unknown) => void;
}) {
  const { tm } = useLang();
  const [query, setQuery] = useState("");
  const [documents, setDocuments] = useState<ProfileDocument[] | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const reading = state.extraction;
  const running = READING.has(reading.state);
  const max = state.limits.max_documents;

  useEffect(() => {
    const timer = setTimeout(() => {
      listProfileDocuments(query)
        .then((docs) => {
          setDocuments(docs);
          setError("");
        })
        .catch((err) => setError(errorText(err, tm)));
    }, 300);
    return () => clearTimeout(timer);
  }, [query, tm]);

  async function start() {
    setBusy("start");
    try {
      onState(await startExtraction(picked));
      setPicked([]);
    } catch (err) {
      if (err instanceof AdminError && err.code === "busy") {
        setError(tm("A reading is already in progress."));
      } else {
        onError(err);
      }
    } finally {
      setBusy(null);
    }
  }

  async function stop() {
    setBusy("stop");
    try {
      onState(await cancelExtraction());
    } catch (err) {
      onError(err);
    } finally {
      setBusy(null);
    }
  }

  const toggle = (path: string, on: boolean) =>
    setPicked(on ? [...picked, path] : picked.filter((p) => p !== path));

  let status: string | null = null;
  if (reading.state === "queued") status = tm("Waiting for the model…");
  else if (reading.state === "running")
    status = fill(tm("Reading: {done} of {n} documents, {p} proposals so far"), {
      done: reading.done ?? 0,
      n: reading.documents ?? 0,
      p: reading.proposals ?? 0,
    });
  else if (reading.state === "done")
    status = fill(tm("Finished: {p} new proposals from {n} documents."), {
      p: reading.proposals ?? 0,
      n: reading.documents ?? 0,
    });
  else if (reading.state === "cancelled")
    status = fill(tm("Stopped: {p} new proposals."), { p: reading.proposals ?? 0 });
  else if (reading.state === "failed") status = tm("The reading failed.");

  return (
    <Card
      title={tm("Read documents")}
      icon={<FileSearch className="h-4 w-4" />}
      actions={
        running && (
          <Button kind="danger" busy={busy === "stop"} onClick={() => void stop()}>
            <Square className="h-3.5 w-3.5" />
            {tm("Stop")}
          </Button>
        )
      }
    >
      <p className="mb-3 text-xs text-ink/65">
        {tm(
          "Pick documents the assistant should read, for example the rules of organisation and operation, a policy or a glossary. What it finds becomes drafts you review; nothing is used until you approve it.",
        )}
      </p>
      {status && (
        <Notice tone={reading.state === "failed" ? "error" : running ? "warn" : "ok"}>
          {status}
          {!!reading.failed?.length &&
            ` ${fill(tm("Could not be read: {list}"), { list: reading.failed.join(", ") })}`}
        </Notice>
      )}
      {error && <Notice tone="error">{error}</Notice>}
      <div className="mt-3 space-y-2">
        <TextField
          label={tm("Search documents")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="max-h-56 space-y-1 overflow-y-auto rounded-lg border border-ink/10 p-2">
          {documents?.length === 0 && (
            <p className="text-xs text-ink/50">{tm("No documents found.")}</p>
          )}
          {documents?.map((doc) => (
            <Checkbox
              key={doc.path}
              checked={picked.includes(doc.path)}
              disabled={!picked.includes(doc.path) && picked.length >= max}
              onChange={(on) => toggle(doc.path, on)}
              label={
                <>
                  {doc.name} <span className="text-ink/45">({doc.room})</span>
                </>
              }
            />
          ))}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[11px] text-ink/45">
            {fill(tm("{n} of {m} documents selected"), { n: picked.length, m: max })}
          </span>
          <Button
            kind="primary"
            busy={busy === "start"}
            disabled={!picked.length || running || !state.configured}
            onClick={() => void start()}
          >
            <BookOpenText className="h-3.5 w-3.5" />
            {tm("Read the selected documents")}
          </Button>
        </div>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ the tab */

export function CompanyTab() {
  const { tm } = useLang();
  const [state, setState] = useState<CompanyState | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [showAll, setShowAll] = useState(false);
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [history, setHistory] = useState<CompanyFact | null>(null);
  const [deleting, setDeleting] = useState<CompanyFact | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setState(await readCompany());
      setError("");
    } catch (err) {
      setError(errorText(err, tm));
    } finally {
      setLoading(false);
    }
  }, [tm]);

  useEffect(() => {
    void load();
  }, [load]);

  const running = !!state && READING.has(state.extraction.state);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      readCompany()
        .then(setState)
        .catch(() => undefined);
    }, 3000);
    return () => clearInterval(timer);
  }, [running]);

  /** A change made on the revision read; a conflict reloads the profile. */
  const change = useCallback(
    async (
      key: string,
      work: (base: number) => Promise<CompanyState>,
      done: string,
    ): Promise<boolean> => {
      if (!state) return false;
      setBusy(key);
      setFeedback(null);
      try {
        setState(await work(state.revision));
        setFeedback({ tone: "ok", text: done });
        return true;
      } catch (err) {
        setFeedback({ tone: "error", text: errorText(err, tm) });
        if (err instanceof AdminError && err.code === "profile_changed") void load();
        return false;
      } finally {
        setBusy(null);
      }
    },
    [state, tm, load],
  );

  const facts = useMemo(
    () =>
      (state?.facts ?? []).filter((f) => showAll || f.state === "draft" || f.state === "effective"),
    [state, showAll],
  );

  if (!state) {
    return (
      <div className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}
        <Button busy={loading} onClick={() => void load()}>
          <RefreshCw className="h-3.5 w-3.5" />
          {tm("Refresh")}
        </Button>
      </div>
    );
  }

  const effective = state.facts.filter((f) => f.state === "effective").length;
  const drafts = state.facts.filter((f) => f.state === "draft");
  const fallbackLevel = state.default_classification ?? state.levels[1] ?? "internal";
  const newFact = (group: ProfileGroup): EditTarget => ({
    current: null,
    fact: {
      group: group.id,
      field: group.fields[0].id,
      label: "",
      value: "",
      classification: fallbackLevel,
      valid_from: null,
      valid_to: null,
    },
  });
  const editFact = (fact: CompanyFact): EditTarget => ({
    current: fact,
    fact: {
      id: fact.id,
      group: fact.group,
      field: fact.field,
      label: fact.label,
      value: fact.value,
      classification: fact.classification,
      valid_from: fact.valid_from,
      valid_to: fact.valid_to,
    },
  });
  const row = (fact: CompanyFact) => (
    <FactRow
      key={fact.id}
      fact={fact}
      state={state}
      busy={busy}
      onAct={(key, work, done) => void change(key, work, done)}
      onEdit={(f) => setEditing(editFact(f))}
      onHistory={setHistory}
      onDelete={setDeleting}
    />
  );

  return (
    <div className="space-y-4">
      <Card
        title={tm("Company profile")}
        icon={<Building2 className="h-4 w-4" />}
        actions={
          <Button busy={loading} onClick={() => void load()}>
            <RefreshCw className="h-3.5 w-3.5" />
            {tm("Refresh")}
          </Button>
        }
      >
        <p className="text-xs text-ink/65">
          {tm(
            "The company profile tells the assistant and the agents who your organisation is: its details, units, roles, policies, style and terms. It is background only: answers still cite your documents alone. Only facts in effect are used, and each user gets them up to their clearance.",
          )}
        </p>
        <p className="mt-2 text-xs text-ink/80">
          {fill(tm("{n} in effect, {d} waiting for approval"), { n: effective, d: drafts.length })}
        </p>
      </Card>
      {!state.configured && (
        <Notice tone="warn">
          {tm(
            "No template folder is configured on this AI Box (ADAPTER_TEMPLATES_DIR), so the company profile cannot be kept.",
          )}
        </Notice>
      )}
      {state.error && (
        <Notice tone="error">
          {tm(
            "The company profile cannot be read; the assistant gets no company background until it is fixed.",
          )}
        </Notice>
      )}
      {feedback && <Notice tone={feedback.tone}>{feedback.text}</Notice>}
      <ReadingCard
        state={state}
        onState={setState}
        onError={(err) => setFeedback({ tone: "error", text: errorText(err, tm) })}
      />
      {drafts.length > 0 && (
        <Card title={tm("Waiting for approval")} icon={<Check className="h-4 w-4" />}>
          <ul className="space-y-2">{drafts.map(row)}</ul>
        </Card>
      )}
      <div className="flex justify-end">
        <Checkbox
          label={tm("Show expired and archived facts too")}
          checked={showAll}
          onChange={setShowAll}
        />
      </div>
      {state.schema.map((group) => {
        const items = facts.filter((f) => f.group === group.id && f.state !== "draft");
        return (
          <Card
            key={group.id}
            title={tm(group.label)}
            actions={
              <Button
                disabled={!state.configured || !!busy}
                onClick={() => setEditing(newFact(group))}
              >
                <Plus className="h-3.5 w-3.5" />
                {tm("Add")}
              </Button>
            }
          >
            {items.length ? (
              <ul className="space-y-2">{items.map(row)}</ul>
            ) : (
              <p className="text-xs text-ink/45">{tm("No facts yet.")}</p>
            )}
          </Card>
        );
      })}
      {editing && (
        <FactDialog
          target={editing}
          state={state}
          onClose={() => setEditing(null)}
          onSave={(fact) =>
            change(
              "save",
              (base) => saveFact(base, fact),
              tm("Saved as a draft. Approve it to use it."),
            )
          }
        />
      )}
      {history && (
        <HistoryDialog
          fact={history}
          state={state}
          onClose={() => setHistory(null)}
          onRestore={(entry) =>
            change(
              "restore",
              (base) => restoreFact(base, entry.fact, entry.seq),
              tm("Restored as a draft. Approve it to use it."),
            )
          }
        />
      )}
      {deleting && (
        <Dialog
          title={tm("Delete this fact?")}
          onClose={() => setDeleting(null)}
          footer={
            <>
              <Button onClick={() => setDeleting(null)}>{tm("Cancel")}</Button>
              <Button
                kind="danger"
                busy={busy === "delete"}
                onClick={() =>
                  void change(
                    "delete",
                    (base) => deleteFact(base, deleting.id),
                    tm("Deleted."),
                  ).then(() => setDeleting(null))
                }
              >
                {tm("Delete")}
              </Button>
            </>
          }
        >
          <p className="text-sm text-ink/80">{deleting.value}</p>
          <p className="text-xs text-ink/55">
            {tm("The deletion is recorded in the history of the profile.")}
          </p>
        </Dialog>
      )}
    </div>
  );
}
