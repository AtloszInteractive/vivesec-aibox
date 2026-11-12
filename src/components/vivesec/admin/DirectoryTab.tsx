import { useEffect, useState } from "react";
import { Network, Plus, RefreshCw, Save, Search, Trash2 } from "lucide-react";

import {
  checkDirectory,
  saveGroupMap,
  type DirectoryCheck,
  type DirectoryInfo,
  type GroupMap,
  type Room,
} from "@/lib/auth/admin-client";
import { useLang } from "../i18n";
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Notice,
  TextField,
  errorText,
  fill,
  roomName,
  splitList,
  withSlash,
} from "./ui";

type GroupRow = { id: number; dn: string; name: string; roles: string };
type RoleRow = { id: number; role: string; drives: string[] };

let nextId = 1;

function toRows(map: GroupMap | null): { groups: GroupRow[]; roles: RoleRow[] } {
  return {
    groups: (map?.groups ?? []).map((g) => ({
      id: nextId++,
      dn: g.dn,
      name: g.name,
      roles: g.roles.join(", "),
    })),
    roles: Object.entries(map?.roles ?? {}).map(([role, drives]) => ({
      id: nextId++,
      role,
      drives: drives.map(withSlash),
    })),
  };
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-white/5 py-1.5 text-xs last:border-0">
      <span className="text-white/55">{label}</span>
      <span className="break-all text-right text-white/90">{children}</span>
    </div>
  );
}

export function DirectoryTab({
  info,
  rooms,
  busy,
  error,
  onReload,
  onSaved,
}: {
  info: DirectoryInfo | null;
  rooms: Room[];
  busy: boolean;
  error: string;
  onReload: () => void;
  onSaved: (info: DirectoryInfo) => void;
}) {
  const { tm } = useLang();
  const [groups, setGroups] = useState<GroupRow[]>([]);
  const [roles, setRoles] = useState<RoleRow[]>([]);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [message, setMessage] = useState("");
  const [checkName, setCheckName] = useState("");
  const [checking, setChecking] = useState(false);
  const [check, setCheck] = useState<DirectoryCheck | null>(null);
  const [checkError, setCheckError] = useState("");

  useEffect(() => {
    const rows = toRows(info?.map ?? null);
    setGroups(rows.groups);
    setRoles(rows.roles);
    setDirty(false);
  }, [info]);

  // Every role a group grants needs a row for its rooms.
  const referenced = new Set(groups.flatMap((g) => splitList(g.roles).map((r) => r.toLowerCase())));
  const missingRoles = [...referenced].filter((r) => !roles.some((row) => row.role === r));

  async function save() {
    if (!info) return;
    setSaving(true);
    setSaveError("");
    setMessage("");
    const map: GroupMap = {
      groups: groups.map((g) => ({
        dn: g.dn.trim(),
        name: g.name.trim(),
        roles: splitList(g.roles).map((r) => r.toLowerCase()),
      })),
      roles: Object.fromEntries([
        ...roles.map((r) => [r.role.trim().toLowerCase(), r.drives] as const),
        ...missingRoles.map((r) => [r, []] as const),
      ]),
    };
    // An empty name means "the CN of the DN": the service fills it in.
    for (const group of map.groups) {
      if (!group.name) delete (group as Partial<typeof group>).name;
    }
    try {
      const saved = await saveGroupMap(map, info.map_version);
      onSaved(saved);
      setMessage(
        tm(
          "The group mapping was saved. Signed-in directory users get the new roles and rooms within a few minutes.",
        ),
      );
    } catch (err) {
      setSaveError(errorText(err, tm));
    } finally {
      setSaving(false);
    }
  }

  async function runCheck() {
    setChecking(true);
    setCheck(null);
    setCheckError("");
    try {
      setCheck(await checkDirectory(checkName.trim() || undefined));
    } catch (err) {
      setCheckError(errorText(err, tm));
    } finally {
      setChecking(false);
    }
  }

  const stateText = info
    ? {
        off: tm("not configured: local users only"),
        ready: tm("configured"),
        invalid: tm("settings not usable: directory users cannot sign in"),
      }[info.state]
    : "—";

  return (
    <div className="space-y-4">
      <Card
        title="Active Directory"
        icon={<Network className="h-4 w-4" />}
        actions={
          <Button busy={busy} onClick={onReload}>
            <RefreshCw className="h-3.5 w-3.5" />
            {tm("Refresh")}
          </Button>
        }
      >
        {error && <Notice tone="error">{error}</Notice>}
        {info && (
          <>
            <Row label={tm("State")}>{stateText}</Row>
            {info.problem && <Row label={tm("Problem")}>{info.problem}</Row>}
            <Row label={tm("Domain controllers")}>{info.urls.join(", ") || "—"}</Row>
            <Row label={tm("Search base")}>{info.base_dn || "—"}</Row>
            <Row label={tm("Service account (read only)")}>{info.bind_dn || "—"}</Row>
            <Row label={tm("Sign-in name")}>{info.user_attribute}</Row>
            <Row label={tm("Certificate check")}>
              {[
                info.pins ? fill(tm("{n} pinned certificate(s)"), { n: info.pins }) : "",
                info.ca_file ? tm("CA file") : "",
              ]
                .filter(Boolean)
                .join(" + ") || "—"}
            </Row>
            <Row label={tm("Re-check of signed-in users")}>
              {fill(tm("every {n} minutes"), { n: Math.round(info.refresh_seconds / 60) })}
            </Row>
            <Row label={tm("Directory users on the box")}>{info.users}</Row>
            <p className="mt-2 text-[11px] text-white/50">
              {tm(
                "The connection settings and the service account password are set on the box when the AI Box is installed (auth_service README).",
              )}
            </p>
          </>
        )}
        {info?.state === "ready" && (
          <div className="mt-3 flex flex-wrap items-end gap-2 border-t border-white/10 pt-3">
            <div className="w-56">
              <TextField
                label={tm("Test a user (optional)")}
                placeholder="anna"
                value={checkName}
                onChange={(e) => setCheckName(e.target.value)}
              />
            </div>
            <Button busy={checking} onClick={() => void runCheck()}>
              <Search className="h-3.5 w-3.5" />
              {tm("Test the connection")}
            </Button>
          </div>
        )}
        {checkError && (
          <div className="mt-2">
            <Notice tone="error">{checkError}</Notice>
          </div>
        )}
        {check && (
          <div className="mt-3 space-y-1.5 text-xs">
            {check.error ? (
              <Notice tone="error">
                {tm("The directory cannot be reached.")} ({check.error})
              </Notice>
            ) : (
              <Notice tone="ok">{tm("The service account signed in.")}</Notice>
            )}
            {check.groups?.map((g) => (
              <div key={g.dn} className="flex items-center justify-between gap-2">
                <span className="break-all text-white/80">{g.dn}</span>
                <Badge tone={g.found ? "ok" : "bad"}>
                  {g.found ? tm("found") : tm("not found")}
                </Badge>
              </div>
            ))}
            {check.user && (
              <div className="rounded-lg border border-white/10 p-2 text-white/80">
                <b>{check.user.username}</b>:{" "}
                {check.user.refused
                  ? `${tm("cannot sign in")} (${check.user.refused})`
                  : check.user.may_sign_in
                    ? fill(tm("may sign in; roles: {roles}; rooms: {rooms}"), {
                        roles: (check.user.roles ?? []).join(", ") || "—",
                        rooms: (check.user.drives ?? []).map(roomName).join(", ") || "—",
                      })
                    : tm("is in none of the mapped groups and cannot sign in")}
              </div>
            )}
          </div>
        )}
      </Card>

      <Card
        title={tm("Group mapping")}
        icon={<Network className="h-4 w-4" />}
        actions={
          <Button
            kind="primary"
            busy={saving}
            disabled={!dirty || !info}
            onClick={() => void save()}
          >
            <Save className="h-3.5 w-3.5" />
            {tm("Save")}
          </Button>
        }
      >
        <p className="mb-3 text-[11px] leading-relaxed text-white/50">
          {tm(
            "An AD group gives roles; a role gives rooms. Nested groups count. A user in none of these groups cannot sign in. The group name is what the permission rules use as group:<name>.",
          )}
        </p>
        <div className="space-y-2">
          {saveError && <Notice tone="error">{saveError}</Notice>}
          {message && <Notice tone="ok">{message}</Notice>}
          {info?.map_error && (
            <Notice tone="error">
              {tm("The saved group mapping is not valid; directory users cannot sign in.")}{" "}
              {info.map_error}
            </Notice>
          )}
          {dirty && <Notice tone="warn">{tm("There are unsaved changes.")}</Notice>}
        </div>
        <h3 className="mb-2 mt-3 text-xs font-semibold text-white/80">{tm("AD groups")}</h3>
        <div className="space-y-2">
          {groups.map((group) => (
            <div
              key={group.id}
              className="grid gap-2 rounded-xl border border-white/10 bg-white/[0.03] p-2 md:grid-cols-[2fr_1fr_1fr_auto]"
            >
              <TextField
                label={tm("Group (distinguished name)")}
                placeholder="CN=AIBox-Analysts,OU=Groups,DC=bv,DC=local"
                value={group.dn}
                onChange={(e) => {
                  const dn = e.target.value;
                  setGroups((c) => c.map((g) => (g.id === group.id ? { ...g, dn } : g)));
                  setDirty(true);
                }}
              />
              <TextField
                label={tm("Name in the rules")}
                placeholder={tm("the CN")}
                value={group.name}
                onChange={(e) => {
                  const name = e.target.value;
                  setGroups((c) => c.map((g) => (g.id === group.id ? { ...g, name } : g)));
                  setDirty(true);
                }}
              />
              <TextField
                label={tm("Roles")}
                placeholder="analyst"
                value={group.roles}
                onChange={(e) => {
                  const value = e.target.value;
                  setGroups((c) => c.map((g) => (g.id === group.id ? { ...g, roles: value } : g)));
                  setDirty(true);
                }}
              />
              <div className="flex items-end">
                <Button
                  kind="danger"
                  title={tm("Remove")}
                  onClick={() => {
                    setGroups((c) => c.filter((g) => g.id !== group.id));
                    setDirty(true);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          ))}
          <Button
            onClick={() => {
              setGroups((c) => [...c, { id: nextId++, dn: "", name: "", roles: "" }]);
              setDirty(true);
            }}
          >
            <Plus className="h-3.5 w-3.5" />
            {tm("Add group")}
          </Button>
        </div>

        <h3 className="mb-2 mt-4 text-xs font-semibold text-white/80">
          {tm("Rooms of the roles")}
        </h3>
        <div className="space-y-2">
          {[
            ...roles,
            ...missingRoles.map((role) => ({ id: -1, role, drives: [] as string[] })),
          ].map((row) => (
            <div
              key={`${row.id}-${row.role}`}
              className="rounded-xl border border-white/10 bg-white/[0.03] p-2"
            >
              <div className="mb-1.5 flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-white">
                  {row.role}
                  {row.role === "admin" && (
                    <span className="ml-2 text-[11px] font-normal text-amber-100/80">
                      {tm("(administrators of the AI Box)")}
                    </span>
                  )}
                </span>
                {row.id !== -1 && !referenced.has(row.role) && (
                  <Button
                    kind="danger"
                    title={tm("Remove")}
                    onClick={() => {
                      setRoles((c) => c.filter((r) => r.id !== row.id));
                      setDirty(true);
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
              <div className="grid grid-cols-2 gap-1.5 md:grid-cols-4">
                {rooms.map((room) => {
                  const slashed = withSlash(room.path);
                  return (
                    <Checkbox
                      key={room.path}
                      label={room.name}
                      checked={row.drives.includes(slashed)}
                      onChange={(on) => {
                        setRoles((current) => {
                          const existing = current.find((r) => r.role === row.role);
                          const drives = on
                            ? [...(existing?.drives ?? []), slashed]
                            : (existing?.drives ?? []).filter((d) => d !== slashed);
                          if (existing) {
                            return current.map((r) => (r.role === row.role ? { ...r, drives } : r));
                          }
                          return [...current, { id: nextId++, role: row.role, drives }];
                        });
                        setDirty(true);
                      }}
                    />
                  );
                })}
                {row.drives
                  .filter((d) => !rooms.some((room) => withSlash(room.path) === d))
                  .map((d) => (
                    <Checkbox
                      key={d}
                      label={`${roomName(d)} (${tm("missing")})`}
                      checked
                      onChange={() => {
                        setRoles((c) =>
                          c.map((r) =>
                            r.id === row.id ? { ...r, drives: r.drives.filter((x) => x !== d) } : r,
                          ),
                        );
                        setDirty(true);
                      }}
                    />
                  ))}
              </div>
            </div>
          ))}
          {roles.length === 0 && missingRoles.length === 0 && (
            <p className="text-xs text-white/50">{tm("Add a group and its roles first.")}</p>
          )}
        </div>
      </Card>
    </div>
  );
}
