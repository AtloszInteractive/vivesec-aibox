// Administration page of a standalone box (E27 S7): users, AD group mapping,
// rooms and permissions, two-factor resets, the clock and virus definitions,
// and the event logs. Only for users with the admin role; the login gate and
// both backends check that again on every call.

import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, DoorOpen, Gauge, Loader2, Network, ScrollText, Users } from "lucide-react";

import {
  listRooms,
  listUsers,
  readAdapterSystem,
  readAuthSystem,
  readDirectory,
  type AdapterSystem,
  type AdminUser,
  type AuthSystemReading,
  type DirectoryInfo,
  type Room,
} from "@/lib/auth/admin-client";
import { fetchGateSession, gatePath } from "@/lib/auth/client";
import { ADMIN_ROLE, LOGIN_PATH } from "@/lib/auth/protocol";
import { GateLogo, LanguagePicker } from "../AuthPages";
import { ACCENT_TEXT, NAVY } from "../brand";
import { LanguageProvider, useLang } from "../i18n";
import { DirectoryTab } from "./DirectoryTab";
import { EventsTab } from "./EventsTab";
import { OverviewTab } from "./OverviewTab";
import { RoomsTab } from "./RoomsTab";
import { needsAttention } from "./status";
import { UsersTab } from "./UsersTab";
import { errorText } from "./ui";

type Tab = "overview" | "users" | "rooms" | "directory" | "events";

type Gate = { state: "loading" } | { state: "not_standalone" } | { state: "ready"; me: string };

function AdminScreen() {
  const { tm } = useLang();
  const [gate, setGate] = useState<Gate>({ state: "loading" });
  const [tab, setTab] = useState<Tab>("overview");

  const [authSystem, setAuthSystem] = useState<AuthSystemReading | null>(null);
  const [adapterSystem, setAdapterSystem] = useState<AdapterSystem | null>(null);
  const [authError, setAuthError] = useState("");
  const [adapterError, setAdapterError] = useState("");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [usersError, setUsersError] = useState("");
  const [rooms, setRooms] = useState<Room[]>([]);
  const [roomsError, setRoomsError] = useState("");
  const [directory, setDirectory] = useState<DirectoryInfo | null>(null);
  const [directoryError, setDirectoryError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void fetchGateSession().then((s) => {
      if (!s.ok) return;
      if (!s.standalone) setGate({ state: "not_standalone" });
      else if (!s.authenticated) window.location.assign(gatePath(LOGIN_PATH));
      else if (!s.user.roles.includes(ADMIN_ROLE)) window.location.assign(gatePath("/"));
      else setGate({ state: "ready", me: s.user.username });
    });
  }, []);

  const loadSystem = useCallback(async () => {
    setBusy(true);
    await Promise.all([
      readAuthSystem()
        .then((r) => {
          setAuthSystem(r);
          setAuthError("");
        })
        .catch((err) => setAuthError(errorText(err, tm))),
      readAdapterSystem()
        .then((r) => {
          setAdapterSystem(r);
          setAdapterError("");
        })
        .catch((err) => setAdapterError(errorText(err, tm))),
    ]);
    setBusy(false);
  }, [tm]);

  const loadUsers = useCallback(async () => {
    try {
      setUsers(await listUsers());
      setUsersError("");
    } catch (err) {
      setUsersError(errorText(err, tm));
    }
  }, [tm]);

  const loadRooms = useCallback(async () => {
    try {
      setRooms((await listRooms()).rooms);
      setRoomsError("");
    } catch (err) {
      setRoomsError(errorText(err, tm));
    }
  }, [tm]);

  const loadDirectory = useCallback(async () => {
    try {
      setDirectory(await readDirectory());
      setDirectoryError("");
    } catch (err) {
      setDirectoryError(errorText(err, tm));
    }
  }, [tm]);

  const reloadAll = useCallback(async () => {
    setBusy(true);
    await Promise.all([loadUsers(), loadRooms(), loadDirectory()]);
    setBusy(false);
  }, [loadUsers, loadRooms, loadDirectory]);

  useEffect(() => {
    if (gate.state !== "ready") return;
    void loadSystem();
    void reloadAll();
  }, [gate.state, loadSystem, reloadAll]);

  if (gate.state === "loading") {
    return (
      <div
        className="flex min-h-screen items-center justify-center"
        style={{ backgroundColor: NAVY }}
      >
        <Loader2 className="h-6 w-6 animate-spin text-white/60" />
      </div>
    );
  }

  const attention = needsAttention(authSystem, adapterSystem);
  const tabs: { id: Tab; label: string; icon: React.ReactNode; dot?: boolean }[] = [
    { id: "overview", label: tm("Overview"), icon: <Gauge className="h-4 w-4" />, dot: attention },
    { id: "users", label: tm("Users"), icon: <Users className="h-4 w-4" /> },
    { id: "rooms", label: tm("Rooms and permissions"), icon: <DoorOpen className="h-4 w-4" /> },
    { id: "directory", label: tm("AD groups"), icon: <Network className="h-4 w-4" /> },
    { id: "events", label: tm("Event log"), icon: <ScrollText className="h-4 w-4" /> },
  ];

  return (
    <div
      className="min-h-screen text-white"
      style={{
        backgroundColor: NAVY,
        fontFamily: "Arial, 'Liberation Sans', 'Nimbus Sans', Helvetica, sans-serif",
      }}
    >
      <header className="border-b border-white/10">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-3 px-4 py-3">
          <GateLogo />
          <h1 className="text-base font-semibold">{tm("Administration")}</h1>
          <div className="ml-auto flex items-center gap-3">
            <a
              href={gatePath("/")}
              className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-white/80 transition hover:bg-white/10"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              {tm("Back to the AI Box")}
            </a>
            <LanguagePicker />
          </div>
        </div>
      </header>
      {gate.state === "not_standalone" ? (
        <main className="mx-auto max-w-6xl px-4 py-10 text-sm text-white/70">
          {tm(
            "This AI Box is administered by the ViVeSecBox; the administration page is only used in standalone mode.",
          )}
        </main>
      ) : (
        <main className="mx-auto max-w-6xl px-4 py-5">
          <nav className="mb-4 flex flex-wrap gap-1.5" aria-label={tm("Administration")}>
            {tabs.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                aria-current={tab === t.id ? "page" : undefined}
                className={`relative inline-flex items-center gap-2 rounded-xl border px-3 py-2 text-xs transition ${
                  tab === t.id
                    ? "border-[#8C49E9] bg-[#8C49E9]/15 text-white"
                    : "border-white/10 bg-white/[0.03] text-white/70 hover:bg-white/[0.06]"
                }`}
              >
                <span style={{ color: ACCENT_TEXT }}>{t.icon}</span>
                {t.label}
                {t.dot && (
                  <span
                    className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full bg-amber-400"
                    aria-label={tm("Needs attention")}
                  />
                )}
              </button>
            ))}
          </nav>
          {tab === "overview" && (
            <OverviewTab
              auth={authSystem}
              adapter={adapterSystem}
              authError={authError}
              adapterError={adapterError}
              busy={busy}
              onRefresh={() => void loadSystem()}
            />
          )}
          {tab === "users" && (
            <UsersTab
              users={users}
              rooms={rooms}
              me={gate.me}
              busy={busy}
              error={usersError}
              onReload={() => void loadUsers()}
            />
          )}
          {tab === "rooms" && (
            <RoomsTab
              rooms={rooms}
              users={users}
              groupMap={directory?.map ?? null}
              busy={busy}
              error={roomsError}
              onReload={() => void Promise.all([loadRooms(), loadUsers()])}
            />
          )}
          {tab === "directory" && (
            <DirectoryTab
              info={directory}
              rooms={rooms}
              busy={busy}
              error={directoryError}
              onReload={() => void Promise.all([loadDirectory(), loadRooms()])}
              onSaved={setDirectory}
            />
          )}
          {tab === "events" && <EventsTab />}
        </main>
      )}
    </div>
  );
}

export function AdminPage() {
  return (
    <LanguageProvider>
      <AdminScreen />
    </LanguageProvider>
  );
}
