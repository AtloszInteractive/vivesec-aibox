// Organisation templates on the administration page (E18, adapter templates.py):
// the Word template every generated DOCX is framed in and the column layout of
// the investigation spreadsheet. The adapter validates everything again; this
// page only shapes the calls and shows what it answered.

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  CheckCircle2,
  Download,
  FileSpreadsheet,
  FileText,
  Plus,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";

import {
  activateWordTemplate,
  previewWordTemplate,
  readSampleSheet,
  readTemplates,
  saveLayout,
  uploadWordTemplate,
  type FrameSummary,
  type LayoutColumn,
  type TemplatesState,
} from "@/lib/auth/admin-client";
import { useLang } from "../i18n";
import { Badge, Button, Card, Notice, errorText, fill, formatTime, type Tm } from "./ui";

type Busy = string | null;
type Feedback = { tone: "ok" | "error"; text: string } | null;

function downloadFile(name: string, contentType: string, base64: string) {
  const bytes = Uint8Array.from(atob(base64), (ch) => ch.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: contentType }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function FilePicker({
  accept,
  busy,
  disabled,
  onFile,
  children,
}: {
  accept: string;
  busy: boolean;
  disabled?: boolean;
  onFile: (file: File) => void;
  children: ReactNode;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) onFile(file);
        }}
      />
      <Button kind="primary" busy={busy} disabled={disabled} onClick={() => input.current?.click()}>
        <Upload className="h-3.5 w-3.5" />
        {children}
      </Button>
    </>
  );
}

function frameText(summary: FrameSummary, tm: Tm): string[] {
  const lines = [
    fill(tm("Page: {w} × {h} mm, {orientation}"), {
      w: summary.page.width_mm,
      h: summary.page.height_mm,
      orientation: summary.page.landscape ? tm("landscape") : tm("portrait"),
    }),
    fill(tm("{h} header(s), {f} footer(s), {p} picture(s)"), {
      h: summary.headers,
      f: summary.footers,
      p: summary.pictures,
    }),
  ];
  if (summary.fonts.headings || summary.fonts.body) {
    lines.push(
      fill(tm("Fonts: headings {h}, body text {b}"), {
        h: summary.fonts.headings ?? "—",
        b: summary.fonts.body ?? "—",
      }),
    );
  }
  if (summary.added_styles.length) {
    lines.push(tm("The template had no heading styles; the built-in ones were added."));
  }
  if (summary.bullets === "symbol") {
    lines.push(tm("Lists use a bullet character (the template has no list bullet style)."));
  }
  return lines;
}

/* ------------------------------------------------------------------ Word template */

function WordTemplateCard({
  state,
  onState,
}: {
  state: TemplatesState;
  onState: (state: TemplatesState) => void;
}) {
  const { tm, lang } = useLang();
  const [busy, setBusy] = useState<Busy>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const { active, versions } = state.docx;

  async function act(key: string, work: () => Promise<void>) {
    setBusy(key);
    setFeedback(null);
    try {
      await work();
    } catch (err) {
      setFeedback({ tone: "error", text: errorText(err, tm) });
    } finally {
      setBusy(null);
    }
  }

  const sample = () =>
    [
      `# ${tm("Sample report")}`,
      "",
      `## ${tm("Summary")}`,
      "",
      tm("This paragraph shows the body text in the template's style."),
      `- ${tm("First point")}`,
      `- ${tm("Second point")}`,
      "",
      `## ${tm("Details")}`,
      "",
      tm("Headings, lists, the page setup, the header and the footer come from the template."),
    ].join("\n");

  return (
    <Card
      title={tm("Word template")}
      icon={<FileText className="h-4 w-4" />}
      actions={
        <>
          {active && (
            <Button
              busy={busy === "builtin"}
              onClick={() =>
                void act("builtin", async () => {
                  onState(await activateWordTemplate(null));
                  setFeedback({ tone: "ok", text: tm("The built-in look is used again.") });
                })
              }
            >
              {tm("Use the built-in look")}
            </Button>
          )}
          <FilePicker
            accept=".docx,.dotx"
            busy={busy === "upload"}
            disabled={!state.configured}
            onFile={(file) =>
              void act("upload", async () => {
                onState(await uploadWordTemplate(file));
                setFeedback({
                  tone: "ok",
                  text: tm("Uploaded. Download the sample to check it, then activate it."),
                });
              })
            }
          >
            {tm("Upload a Word template")}
          </FilePicker>
        </>
      }
    >
      <p className="mb-3 text-xs text-ink/65">
        {tm(
          "A Word document or template (.docx, .dotx) gives its styles, fonts, page setup, headers and footers (with the logo) to every Word file the AI Box writes. Its text is not used. Files with macros, embedded objects or linked content are refused.",
        )}
      </p>
      {feedback && <Notice tone={feedback.tone}>{feedback.text}</Notice>}
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        <span className="text-ink/55">{tm("In use")}:</span>
        {active ? (
          <Badge tone="accent">{versions.find((v) => v.id === active)?.name ?? active}</Badge>
        ) : (
          <Badge>{tm("Built-in look")}</Badge>
        )}
        <Button
          busy={busy === "sample"}
          onClick={() =>
            void act("sample", async () => {
              const file = await previewWordTemplate(null, sample(), tm("Sample report"));
              downloadFile(file.name, file.content_type, file.content_b64);
            })
          }
        >
          <Download className="h-3.5 w-3.5" />
          {tm("Sample of the current look")}
        </Button>
      </div>
      {versions.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-xs">
            <thead className="text-[11px] text-ink/50">
              <tr className="border-b border-ink/10">
                <th className="py-2 pr-2 font-medium">{tm("File")}</th>
                <th className="py-2 pr-2 font-medium">{tm("Uploaded")}</th>
                <th className="py-2 pr-2 font-medium">{tm("What it gives")}</th>
                <th className="py-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              {versions.map((version) => (
                <tr key={version.id} className="border-b border-ink/5 align-top">
                  <td className="break-all py-1.5 pr-2 text-ink/90">{version.name}</td>
                  <td className="whitespace-nowrap py-1.5 pr-2 text-ink/60">
                    {formatTime(version.uploaded_at, lang)}
                    <br />
                    {version.uploaded_by}
                  </td>
                  <td className="py-1.5 pr-2 text-ink/70">
                    {frameText(version.summary, tm).map((line) => (
                      <div key={line}>{line}</div>
                    ))}
                  </td>
                  <td className="py-1.5">
                    <div className="flex flex-wrap justify-end gap-1.5">
                      <Button
                        busy={busy === `sample-${version.id}`}
                        onClick={() =>
                          void act(`sample-${version.id}`, async () => {
                            const file = await previewWordTemplate(
                              version.id,
                              sample(),
                              tm("Sample report"),
                            );
                            downloadFile(file.name, file.content_type, file.content_b64);
                          })
                        }
                      >
                        <Download className="h-3.5 w-3.5" />
                        {tm("Sample")}
                      </Button>
                      {version.id === active ? (
                        <Badge tone="ok">
                          <CheckCircle2 className="mr-1 h-3 w-3" />
                          {tm("Active")}
                        </Badge>
                      ) : (
                        <Button
                          kind="primary"
                          busy={busy === `activate-${version.id}`}
                          onClick={() =>
                            void act(`activate-${version.id}`, async () => {
                              onState(await activateWordTemplate(version.id));
                              setFeedback({
                                tone: "ok",
                                text: tm("Activated: every new Word file uses this template."),
                              });
                            })
                          }
                        >
                          {tm("Activate")}
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ spreadsheet columns */

function SpreadsheetCard({
  state,
  onState,
}: {
  state: TemplatesState;
  onState: (state: TemplatesState) => void;
}) {
  const { tm, lang } = useLang();
  const [busy, setBusy] = useState<Busy>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [draft, setDraft] = useState<LayoutColumn[] | null>(null);
  const code = lang.toLowerCase() as "en" | "hu" | "da" | "de";
  const columnName = (id: string | null) => {
    const found = state.columns.find((c) => c.id === id);
    return found ? (found.labels[code] ?? found.labels.en) : tm("(empty column)");
  };
  const current = state.xlsx_layout?.value ?? null;

  async function act(key: string, work: () => Promise<void>) {
    setBusy(key);
    setFeedback(null);
    try {
      await work();
    } catch (err) {
      setFeedback({ tone: "error", text: errorText(err, tm) });
    } finally {
      setBusy(null);
    }
  }

  const update = (index: number, change: Partial<LayoutColumn>) =>
    setDraft((rows) => rows && rows.map((row, i) => (i === index ? { ...row, ...change } : row)));
  const used = new Set((draft ?? []).map((row) => row.column).filter(Boolean));

  return (
    <Card
      title={tm("Spreadsheet columns")}
      icon={<FileSpreadsheet className="h-4 w-4" />}
      actions={
        !draft && (
          <>
            {current && (
              <Button
                busy={busy === "reset"}
                onClick={() =>
                  void act("reset", async () => {
                    onState(await saveLayout(null));
                    setFeedback({ tone: "ok", text: tm("The default columns are used again.") });
                  })
                }
              >
                {tm("Use the default columns")}
              </Button>
            )}
            <Button
              disabled={!state.configured}
              onClick={() =>
                setDraft(
                  current ?? state.columns.map((c) => ({ column: c.id, label: null, width: null })),
                )
              }
            >
              {tm("Edit")}
            </Button>
            <FilePicker
              accept=".xlsx"
              busy={busy === "read"}
              disabled={!state.configured}
              onFile={(file) =>
                void act("read", async () => {
                  setDraft(await readSampleSheet(file));
                  setFeedback({
                    tone: "ok",
                    text: tm("Check the matched columns, then save."),
                  });
                })
              }
            >
              {tm("Read a sample spreadsheet")}
            </FilePicker>
          </>
        )
      }
    >
      <p className="mb-3 text-xs text-ink/65">
        {tm(
          "The investigation table (XLSX) can follow your own column order and header texts. From a sample spreadsheet, the first row with text is read as the header and each header is matched to a column. An empty column stays empty for entry by hand. Which personal data a row shows does not change.",
        )}
      </p>
      {feedback && <Notice tone={feedback.tone}>{feedback.text}</Notice>}
      {!draft && (
        <div className="mt-3 text-xs">
          {current ? (
            <>
              <div className="mb-2 text-ink/55">
                {fill(tm("Set by {user}, {time}"), {
                  user: state.xlsx_layout?.updated_by ?? "—",
                  time: formatTime(state.xlsx_layout?.updated_at, lang),
                })}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {current.map((row, index) => (
                  <Badge key={index} tone={row.column ? "neutral" : "warn"}>
                    {row.label ?? columnName(row.column)}
                    {row.label && row.column && row.label !== columnName(row.column)
                      ? ` → ${columnName(row.column)}`
                      : ""}
                  </Badge>
                ))}
              </div>
            </>
          ) : (
            <span className="text-ink/55">{tm("The default columns are used.")}</span>
          )}
        </div>
      )}
      {draft && (
        <div className="mt-3 space-y-2">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-xs">
              <thead className="text-[11px] text-ink/50">
                <tr className="border-b border-ink/10">
                  <th className="py-2 pr-2 font-medium">{tm("Header text")}</th>
                  <th className="py-2 pr-2 font-medium">{tm("Content")}</th>
                  <th className="w-24 py-2 pr-2 font-medium">{tm("Width")}</th>
                  <th className="w-10 py-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                {draft.map((row, index) => (
                  <tr key={index} className="border-b border-ink/5">
                    <td className="py-1 pr-2">
                      <input
                        value={row.label ?? ""}
                        maxLength={state.limits.max_label}
                        placeholder={row.column ? columnName(row.column) : ""}
                        aria-label={tm("Header text")}
                        onChange={(e) => update(index, { label: e.target.value || null })}
                        className="w-full rounded-lg border border-ink/15 bg-ink/5 px-2 py-1 text-xs text-ink outline-none placeholder:text-ink/30 focus:border-brand-soft"
                      />
                    </td>
                    <td className="py-1 pr-2">
                      <select
                        value={row.column ?? ""}
                        aria-label={tm("Content")}
                        onChange={(e) => update(index, { column: e.target.value || null })}
                        className="w-full rounded-lg border border-ink/15 bg-surface-1 px-2 py-1 text-xs text-ink outline-none focus:border-brand-soft"
                      >
                        <option value="">{tm("(empty column)")}</option>
                        {state.columns.map((c) => (
                          <option
                            key={c.id}
                            value={c.id}
                            disabled={c.id !== row.column && used.has(c.id)}
                          >
                            {columnName(c.id)}
                            {state.required.includes(c.id) ? ` (${tm("required")})` : ""}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="py-1 pr-2">
                      <input
                        type="number"
                        min={3}
                        max={100}
                        value={row.width ?? ""}
                        aria-label={tm("Width")}
                        onChange={(e) =>
                          update(index, { width: e.target.value ? Number(e.target.value) : null })
                        }
                        className="w-full rounded-lg border border-ink/15 bg-ink/5 px-2 py-1 text-xs text-ink outline-none focus:border-brand-soft"
                      />
                    </td>
                    <td className="py-1 text-right">
                      <button
                        type="button"
                        title={tm("Remove")}
                        aria-label={tm("Remove")}
                        onClick={() => setDraft(draft.filter((_, i) => i !== index))}
                        className="rounded-md p-1 text-ink/50 hover:bg-ink/10 hover:text-ink"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button
              disabled={draft.length >= state.limits.max_columns}
              onClick={() => setDraft([...draft, { column: null, label: "", width: null }])}
            >
              <Plus className="h-3.5 w-3.5" />
              {tm("Add column")}
            </Button>
            <div className="flex gap-2">
              <Button onClick={() => setDraft(null)}>{tm("Cancel")}</Button>
              <Button
                kind="primary"
                busy={busy === "save"}
                onClick={() =>
                  void act("save", async () => {
                    onState(await saveLayout(draft));
                    setDraft(null);
                    setFeedback({ tone: "ok", text: tm("Saved.") });
                  })
                }
              >
                {tm("Save")}
              </Button>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ the tab */

export function TemplatesTab() {
  const { tm } = useLang();
  const [state, setState] = useState<TemplatesState | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setState(await readTemplates());
      setError("");
    } catch (err) {
      setError(errorText(err, tm));
    } finally {
      setBusy(false);
    }
  }, [tm]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button busy={busy} onClick={() => void load()}>
          <RefreshCw className="h-3.5 w-3.5" />
          {tm("Refresh")}
        </Button>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {state && !state.configured && (
        <Notice tone="warn">
          {tm(
            "No template folder is configured on this AI Box (ADAPTER_TEMPLATES_DIR): every file has the built-in look.",
          )}
        </Notice>
      )}
      {state?.error && (
        <Notice tone="error">
          {tm("The template settings cannot be read; the built-in look is used.")}
        </Notice>
      )}
      {state && (
        <>
          <WordTemplateCard state={state} onState={setState} />
          <SpreadsheetCard state={state} onState={setState} />
        </>
      )}
    </div>
  );
}
