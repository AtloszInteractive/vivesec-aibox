// Updates of a Matrix box (E07): what the host updater offers, the
// administrator's approval and the way back. The adapter only passes the
// updater's state on and writes the request (adapter/updates.py); installing
// happens on the host, and the services restart meanwhile, so this page keeps
// polling and treats a lost connection during an installation as expected.

import { useCallback, useEffect, useRef, useState } from "react";
import { History, RefreshCw, RotateCcw, ShieldAlert, Download } from "lucide-react";

import {
  AdminError,
  installUpdate,
  readUpdate,
  rollbackUpdate,
  type UpdateState,
  type UpdateStep,
} from "@/lib/auth/admin-client";
import { useLang, type Lang } from "../i18n";
import { Badge, Button, Card, Checkbox, Dialog, Notice, errorText, fill, formatTime } from "./ui";

const POLL_MS = 5_000;
const ACTIVE = new Set(["downloading", "installing", "rolling_back"]);

const STEP_TEXT: Record<UpdateStep, string> = {
  verify: "Checking the downloaded files",
  snapshot: "Copying the data aside",
  load: "Loading the new version",
  kit: "Unpacking the installer",
  install: "Starting the services",
  data: "Putting the data copy back",
};

const NOTE_LANG: Record<Lang, "en" | "hu" | "da" | "de"> = {
  EN: "en",
  HU: "hu",
  DA: "da",
  DE: "de",
};

const seconds = (iso: string | null | undefined) => (iso ? Date.parse(iso) / 1000 : null);

export function UpdatesTab() {
  const { tm, lang } = useLang();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [state, setState] = useState<UpdateState | null>(null);
  const [error, setError] = useState("");
  const [restarting, setRestarting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<"install" | "rollback" | null>(null);
  const [restoreData, setRestoreData] = useState(false);
  const last = useRef<UpdateState | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await readUpdate();
      setEnabled(res.enabled);
      setState(res.update);
      last.current = res.update;
      setError("");
      setRestarting(false);
    } catch (err) {
      // While the new version starts, the adapter itself is replaced.
      const during = last.current && (ACTIVE.has(last.current.status) || last.current.pending);
      if (during && err instanceof AdminError && (err.code === "network" || err.status >= 500)) {
        setRestarting(true);
      } else {
        setError(errorText(err, tm));
      }
    }
  }, [tm]);

  useEffect(() => {
    void load();
  }, [load]);

  const polling =
    restarting || (state !== null && (ACTIVE.has(state.status) || state.pending !== null));
  useEffect(() => {
    if (!polling) return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [polling, load]);

  const act = async (action: () => Promise<{ update: UpdateState }>) => {
    setBusy(true);
    setError("");
    try {
      const res = await action();
      setState(res.update);
      last.current = res.update;
      setConfirm(null);
    } catch (err) {
      setError(errorText(err, tm));
    } finally {
      setBusy(false);
    }
  };

  const available = state?.available ?? null;
  const notes = available
    ? (available.notes[NOTE_LANG[lang]] ??
      available.notes.en ??
      Object.values(available.notes)[0] ??
      "")
    : "";
  const sizeMb = available?.download_bytes
    ? Math.max(1, Math.round(available.download_bytes / 1e6))
    : null;
  const canInstall =
    state !== null &&
    available !== null &&
    (state.status === "ready" || state.status === "failed") &&
    !state.interrupted &&
    !state.pending;
  // After a failed installation the previous version runs again: going back once
  // more is for the data copy only (or a box that is broken or was interrupted).
  const onlyData =
    state !== null &&
    state.last !== null &&
    state.installed === state.last.previous &&
    state.status !== "broken" &&
    !state.interrupted;
  const canRollBack =
    state !== null &&
    state.last !== null &&
    (state.status === "current" || state.status === "failed" || state.status === "broken") &&
    !state.pending &&
    (!onlyData || state.last.data_copy);

  return (
    <div className="space-y-4">
      <Card
        title={tm("Updates")}
        icon={<Download className="h-4 w-4" />}
        actions={
          <Button busy={busy} onClick={() => void load()}>
            <RefreshCw className="h-3.5 w-3.5" />
            {tm("Refresh")}
          </Button>
        }
      >
        {error && <Notice tone="error">{error}</Notice>}
        {restarting && (
          <Notice tone="warn">
            {tm("The AI Box is restarting with the new version. This page refreshes by itself.")}
          </Notice>
        )}
        {enabled === false && (
          <p className="text-xs text-ink/60">{tm("Updates are not set up on this AI Box.")}</p>
        )}
        {state && (
          <div className="space-y-3 text-xs">
            <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
              <dt className="text-ink/50">{tm("Installed version")}</dt>
              <dd className="font-mono text-ink/90">{state.installed ?? "—"}</dd>
              <dt className="text-ink/50">{tm("Channel")}</dt>
              <dd className="text-ink/80">{state.channel ?? "—"}</dd>
              <dt className="text-ink/50">{tm("Last check")}</dt>
              <dd className="text-ink/80">{formatTime(seconds(state.checked_at), lang)}</dd>
            </dl>

            {state.status === "current" && !state.pending && (
              <Notice tone="ok">{tm("Up to date.")}</Notice>
            )}
            {(state.status === "off" || state.status === "never") && (
              <Notice tone="warn">{tm("Update checks are off on this AI Box.")}</Notice>
            )}
            {state.status === "error" && (
              <Notice tone="warn">
                {tm("The last update check failed. It is retried automatically.")}
              </Notice>
            )}
            {state.status === "downloading" && available && (
              <Notice tone="ok">
                {fill(tm("Downloading version {v}…"), { v: available.version })}
              </Notice>
            )}
            {state.status === "blocked" && available && (
              <Notice tone="warn">
                {fill(tm("Version {v} needs version {min} installed first."), {
                  v: available.version,
                  min: available.min_from ?? "?",
                })}
              </Notice>
            )}
            {state.status === "installing" && (
              <Notice tone="warn">
                {fill(tm("Installing: {step}"), { step: tm(STEP_TEXT[state.step ?? "verify"]) })}
              </Notice>
            )}
            {state.status === "rolling_back" && (
              <Notice tone="warn">
                {fill(tm("Going back to the previous version: {step}"), {
                  step: tm(STEP_TEXT[state.step ?? "install"]),
                })}
              </Notice>
            )}
            {state.status === "failed" && !state.interrupted && (
              <Notice tone="error">
                {tm("The update failed; the previous version runs again.")}
              </Notice>
            )}
            {state.interrupted && (
              <Notice tone="error">
                {tm(
                  "The last installation was interrupted. Go back to the previous version or install again.",
                )}
              </Notice>
            )}
            {state.status === "broken" && (
              <Notice tone="error">
                {tm(
                  "The AI Box needs attention: the previous version did not start either. Contact support.",
                )}
              </Notice>
            )}
            {state.pending && (
              <Notice tone={state.pending.stuck ? "error" : "warn"}>
                {state.pending.stuck
                  ? tm(
                      "The updater has not picked up the request. Check that the update service runs on the AI Box.",
                    )
                  : tm("Waiting for the updater…")}
              </Notice>
            )}
            {state.error &&
              (state.status === "failed" ||
                state.status === "broken" ||
                state.status === "error") && (
                <p className="break-all font-mono text-[11px] text-ink/45">{state.error}</p>
              )}

            {available && (state.status === "ready" || state.status === "failed") && (
              <div className="space-y-2 rounded-xl border border-ink/10 bg-ink/[0.03] p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold">
                    {fill(tm("Version {v} is ready to install."), { v: available.version })}
                  </span>
                  {available.security && (
                    <Badge tone="bad">
                      <ShieldAlert className="mr-1 h-3 w-3" />
                      {tm("Security update")}
                    </Badge>
                  )}
                </div>
                {notes && (
                  <div>
                    <div className="mb-1 text-[11px] text-ink/50">{tm("Release notes")}</div>
                    <p className="whitespace-pre-line text-ink/80">{notes}</p>
                  </div>
                )}
                {sizeMb !== null && (
                  <p className="text-ink/50">{fill(tm("Download size: {n} MB"), { n: sizeMb })}</p>
                )}
                {available.data_copy && (
                  <Notice tone="warn">
                    {tm(
                      "Before installing, the data is copied aside, because this version changes how it is stored.",
                    )}
                  </Notice>
                )}
                {available.reindex && (
                  <Notice tone="warn">
                    {tm(
                      "The search index is rebuilt after installing; until then answers may be incomplete.",
                    )}
                  </Notice>
                )}
                <Button kind="primary" disabled={!canInstall} onClick={() => setConfirm("install")}>
                  {tm("Install now")}
                </Button>
              </div>
            )}

            {canRollBack && state.last && (
              <div className="flex flex-wrap items-center gap-2 border-t border-ink/10 pt-3">
                <Button kind="danger" onClick={() => setConfirm("rollback")}>
                  <RotateCcw className="h-3.5 w-3.5" />
                  {tm("Back to the previous version")}
                </Button>
                <span className="text-ink/50">
                  {fill(tm("Last update: {v}, before it: {prev}"), {
                    v: state.last.version,
                    prev: state.last.previous ?? "?",
                  })}
                </span>
              </div>
            )}
          </div>
        )}
      </Card>

      {state && state.history.length > 0 && (
        <Card title={tm("History")} icon={<History className="h-4 w-4" />}>
          <table className="w-full text-left text-xs">
            <tbody>
              {state.history.map((item, index) => (
                <tr key={`${item.at}-${index}`} className="border-b border-ink/5">
                  <td className="whitespace-nowrap py-1.5 pr-3 text-ink/60">
                    {formatTime(seconds(item.at), lang)}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-ink/85">{item.version ?? "—"}</td>
                  <td className="py-1.5 text-ink/80">
                    {item.result === "installed"
                      ? tm("Installed")
                      : item.data_restored
                        ? tm("Rolled back, data restored")
                        : tm("Rolled back")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {confirm === "install" && available && (
        <Dialog
          title={fill(tm("Install version {v}?"), { v: available.version })}
          onClose={() => setConfirm(null)}
          footer={
            <>
              <Button onClick={() => setConfirm(null)}>{tm("Cancel")}</Button>
              <Button
                kind="primary"
                busy={busy}
                onClick={() => void act(() => installUpdate(available.version))}
              >
                {tm("Install now")}
              </Button>
            </>
          }
        >
          <p className="text-xs text-ink/80">
            {tm(
              "The AI Box is unavailable for a few minutes while the new version starts. If it does not start, the previous version comes back automatically.",
            )}
          </p>
        </Dialog>
      )}

      {confirm === "rollback" && state?.last && (
        <Dialog
          title={fill(tm("Go back to version {v}?"), { v: state.last.previous ?? "?" })}
          onClose={() => setConfirm(null)}
          footer={
            <>
              <Button onClick={() => setConfirm(null)}>{tm("Cancel")}</Button>
              <Button
                kind="danger"
                busy={busy}
                disabled={onlyData && !restoreData}
                onClick={() => void act(() => rollbackUpdate(restoreData))}
              >
                {tm("Go back")}
              </Button>
            </>
          }
        >
          <p className="text-xs text-ink/80">
            {tm("The AI Box is unavailable for a few minutes while the previous version starts.")}
          </p>
          {onlyData && (
            <p className="text-xs text-ink/60">
              {tm("The previous version runs already; going back now only restores the data copy.")}
            </p>
          )}
          {state.last.data_copy && (
            <Checkbox
              checked={restoreData}
              onChange={setRestoreData}
              label={tm(
                "Also restore the data as it was before the update (everything saved since then is lost)",
              )}
            />
          )}
        </Dialog>
      )}
    </div>
  );
}
