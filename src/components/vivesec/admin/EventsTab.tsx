import { useCallback, useEffect, useState } from "react";
import { RefreshCw, ScrollText } from "lucide-react";

import {
  listAuthEvents,
  listStoreEvents,
  type AuthEvent,
  type StoreEvent,
} from "@/lib/auth/admin-client";
import { useLang } from "../i18n";
import { Button, Card, Notice, errorText, formatTime } from "./ui";

const PAGE = 100;

// Event names of the authentication service and the file store, mapped to the
// English source text of the i18n table. Unknown names are shown as they are.
const EVENT_TEXT: Record<string, string> = {
  login_ok: "Signed in",
  login_failed: "Sign-in failed",
  login_password_ok: "Password accepted, code requested",
  logout: "Signed out",
  account_locked: "Account locked",
  account_unlocked: "Account unlocked",
  client_throttled: "Too many attempts from this computer",
  totp_enrolled: "Authenticator app registered",
  totp_reset: "Two-factor authentication reset",
  password_changed: "Password changed",
  password_reset: "Password reset",
  user_created: "User created",
  user_updated: "User changed",
  user_deleted: "User deleted",
  directory_user_created: "Directory user first signed in",
  directory_user_updated: "Directory roles changed",
  directory_access_ended: "Directory access ended",
  directory_unavailable: "Directory unreachable",
  directory_checked: "Directory connection tested",
  group_map_updated: "Group mapping saved",
  upload: "Upload received",
  scan: "Virus scan",
  store: "Stored",
  index: "Indexed",
  mkdir: "Folder created",
  delete: "Deleted",
  cancel: "Upload cancelled",
  room_create: "Room created",
  room_delete: "Room deleted",
  acl_update: "Permission rules saved",
};

const FAILURES = new Set([
  "login_failed",
  "account_locked",
  "client_throttled",
  "directory_access_ended",
  "directory_unavailable",
]);

function storeDetail(event: StoreEvent): string {
  const skip = new Set(["ts", "action", "user", "path"]);
  return Object.entries(event)
    .filter(([key, value]) => !skip.has(key) && value !== null && value !== undefined)
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.length : String(value)}`)
    .join(" ");
}

export function EventsTab() {
  const { tm, lang } = useLang();
  const [view, setView] = useState<"auth" | "files">("auth");
  const [username, setUsername] = useState("");
  const [authEvents, setAuthEvents] = useState<AuthEvent[]>([]);
  const [authMore, setAuthMore] = useState(false);
  const [storeEvents, setStoreEvents] = useState<StoreEvent[]>([]);
  const [storeLimit, setStoreLimit] = useState(PAGE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const label = (name: string) => (EVENT_TEXT[name] ? tm(EVENT_TEXT[name]) : name);

  const loadAuth = useCallback(
    async (before?: number) => {
      setBusy(true);
      setError("");
      try {
        const page = await listAuthEvents({
          limit: PAGE,
          username: username.trim() || undefined,
          before,
        });
        setAuthEvents((current) => (before ? [...current, ...page] : page));
        setAuthMore(page.length === PAGE);
      } catch (err) {
        setError(errorText(err, tm));
      } finally {
        setBusy(false);
      }
    },
    [username, tm],
  );

  const loadStore = useCallback(
    async (limit: number) => {
      setBusy(true);
      setError("");
      try {
        setStoreEvents(await listStoreEvents(limit));
        setStoreLimit(limit);
      } catch (err) {
        setError(errorText(err, tm));
      } finally {
        setBusy(false);
      }
    },
    [tm],
  );

  useEffect(() => {
    if (view === "auth") void loadAuth();
    else void loadStore(PAGE);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-xs transition ${active ? "bg-[#8C49E9]/20 text-white" : "text-white/60 hover:bg-white/5"}`;

  return (
    <Card
      title={tm("Event log")}
      icon={<ScrollText className="h-4 w-4" />}
      actions={
        <>
          <button
            type="button"
            className={tabClass(view === "auth")}
            onClick={() => setView("auth")}
          >
            {tm("Sign-in and administration")}
          </button>
          <button
            type="button"
            className={tabClass(view === "files")}
            onClick={() => setView("files")}
          >
            {tm("Files and permissions")}
          </button>
        </>
      }
    >
      {view === "auth" && (
        <form
          className="mb-3 flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void loadAuth();
          }}
        >
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder={tm("Username")}
            className="w-48 rounded-lg border border-white/15 bg-white/5 px-2.5 py-1.5 text-xs text-white outline-none placeholder:text-white/30 focus:border-[#B48CF5]"
          />
          <Button type="submit" busy={busy}>
            <RefreshCw className="h-3.5 w-3.5" />
            {tm("Refresh")}
          </Button>
        </form>
      )}
      {view === "files" && (
        <div className="mb-3">
          <Button busy={busy} onClick={() => void loadStore(storeLimit)}>
            <RefreshCw className="h-3.5 w-3.5" />
            {tm("Refresh")}
          </Button>
        </div>
      )}
      {error && <Notice tone="error">{error}</Notice>}
      <div className="overflow-x-auto">
        {view === "auth" ? (
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead className="text-[11px] text-white/50">
              <tr className="border-b border-white/10">
                <th className="py-2 pr-2 font-medium">{tm("Time")}</th>
                <th className="py-2 pr-2 font-medium">{tm("Event")}</th>
                <th className="py-2 pr-2 font-medium">{tm("User")}</th>
                <th className="py-2 pr-2 font-medium">{tm("By")}</th>
                <th className="py-2 pr-2 font-medium">{tm("Computer")}</th>
                <th className="py-2 font-medium">{tm("Details")}</th>
              </tr>
            </thead>
            <tbody>
              {authEvents.map((event) => (
                <tr key={event.id} className="border-b border-white/5 align-top">
                  <td className="whitespace-nowrap py-1.5 pr-2 text-white/60">
                    {formatTime(event.at, lang)}
                  </td>
                  <td
                    className={`py-1.5 pr-2 ${FAILURES.has(event.event) ? "text-rose-200" : "text-white/90"}`}
                  >
                    {label(event.event)}
                  </td>
                  <td className="py-1.5 pr-2 text-white/80">{event.username || "—"}</td>
                  <td className="py-1.5 pr-2 text-white/60">{event.actor || "—"}</td>
                  <td className="py-1.5 pr-2 text-white/60">{event.client || "—"}</td>
                  <td className="break-all py-1.5 text-white/50">{event.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead className="text-[11px] text-white/50">
              <tr className="border-b border-white/10">
                <th className="py-2 pr-2 font-medium">{tm("Time")}</th>
                <th className="py-2 pr-2 font-medium">{tm("Event")}</th>
                <th className="py-2 pr-2 font-medium">{tm("User")}</th>
                <th className="py-2 pr-2 font-medium">{tm("Path")}</th>
                <th className="py-2 font-medium">{tm("Details")}</th>
              </tr>
            </thead>
            <tbody>
              {storeEvents.map((event, index) => (
                <tr key={`${event.ts}-${index}`} className="border-b border-white/5 align-top">
                  <td className="whitespace-nowrap py-1.5 pr-2 text-white/60">
                    {formatTime(event.ts, lang)}
                  </td>
                  <td
                    className={`py-1.5 pr-2 ${event.result === "infected" ? "text-rose-200" : "text-white/90"}`}
                  >
                    {label(event.action)}
                  </td>
                  <td className="py-1.5 pr-2 text-white/80">{event.user || "—"}</td>
                  <td className="break-all py-1.5 pr-2 text-white/70">{event.path}</td>
                  <td className="break-all py-1.5 text-white/50">{storeDetail(event)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {((view === "auth" && authMore) ||
        (view === "files" && storeEvents.length === storeLimit && storeLimit < 1000)) && (
        <div className="mt-3 flex justify-center">
          <Button
            busy={busy}
            onClick={() =>
              view === "auth"
                ? void loadAuth(authEvents[authEvents.length - 1]?.id)
                : void loadStore(Math.min(1000, storeLimit + PAGE * 2))
            }
          >
            {tm("Load more")}
          </Button>
        </div>
      )}
      {!busy && !error && (view === "auth" ? authEvents : storeEvents).length === 0 && (
        <p className="py-4 text-center text-xs text-white/50">{tm("No events.")}</p>
      )}
    </Card>
  );
}
