import { useEffect, useMemo, useState } from "react";
import { DoorOpen, Plus, RefreshCw, Save, ShieldCheck, Trash2 } from "lucide-react";

import {
  createRoom,
  deleteRoom,
  readAcl,
  saveAcl,
  type AclRules,
  type AclState,
  type AdminUser,
  type GroupMap,
  type Room,
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
  levelLabel,
  splitList,
  withSlash,
} from "./ui";

type RuleRow = {
  id: number;
  path: string;
  restricted: boolean;
  allow: string;
  deny: string;
  classification: string;
  inherit: boolean;
};

type ClearanceRow = { id: number; principal: string; level: string };

let nextId = 1;

function toRows(rules: AclRules | null | undefined): {
  rows: RuleRow[];
  clearance: ClearanceRow[];
  defaultClassification: string;
} {
  const rows = Object.entries(rules?.paths ?? {}).map(([path, node]) => ({
    id: nextId++,
    path,
    restricted: Array.isArray(node.allow),
    allow: (node.allow ?? []).join(", "),
    deny: (node.deny ?? []).join(", "),
    classification: node.classification ?? "",
    inherit: node.inherit !== false,
  }));
  const clearance = Object.entries(rules?.clearance ?? {}).map(([principal, level]) => ({
    id: nextId++,
    principal,
    level,
  }));
  return { rows, clearance, defaultClassification: rules?.default_classification ?? "" };
}

function toRules(
  rows: RuleRow[],
  clearance: ClearanceRow[],
  defaultClassification: string,
): AclRules {
  const paths: AclRules["paths"] = {};
  for (const row of rows) {
    const node: AclRules["paths"][string] = {};
    if (row.restricted) node.allow = splitList(row.allow);
    const deny = splitList(row.deny);
    if (deny.length) node.deny = deny;
    if (!row.inherit) node.inherit = false;
    if (row.classification) node.classification = row.classification;
    paths[row.path] = node;
  }
  const rules: AclRules = { paths };
  const entries = clearance.filter((c) => c.principal.trim());
  if (entries.length) {
    rules.clearance = Object.fromEntries(entries.map((c) => [c.principal.trim(), c.level]));
  }
  if (defaultClassification) rules.default_classification = defaultClassification;
  return rules;
}

const under = (path: string, root: string) => path === root || path.startsWith(`${root}/`);

function RuleEditor({
  row,
  room,
  levels,
  onChange,
  onRemove,
}: {
  row: RuleRow;
  room: string;
  levels: string[];
  onChange: (row: RuleRow) => void;
  onRemove: () => void;
}) {
  const { tm } = useLang();
  const relative = row.path === room ? tm("The whole room") : row.path.slice(room.length + 1);
  return (
    <div className="space-y-2 rounded-xl border border-white/10 bg-white/[0.03] p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="break-all text-xs font-medium text-white">{relative}</span>
        <Button kind="danger" title={tm("Remove the rule")} onClick={onRemove}>
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>
      <Checkbox
        label={tm("Only these may see it (allow list)")}
        checked={row.restricted}
        onChange={(restricted) => onChange({ ...row, restricted })}
      />
      {row.restricted && (
        <TextField
          label={tm("Allowed")}
          hint={tm("For example role:analyst, group:Nyomozok, user:anna. Empty: nobody.")}
          value={row.allow}
          onChange={(e) => onChange({ ...row, allow: e.target.value })}
        />
      )}
      <TextField
        label={tm("Denied (always wins)")}
        value={row.deny}
        onChange={(e) => onChange({ ...row, deny: e.target.value })}
      />
      <div className="grid gap-2 sm:grid-cols-2">
        <SelectField
          label={tm("Classification")}
          value={row.classification}
          onChange={(classification) => onChange({ ...row, classification })}
          options={[
            { value: "", label: tm("Not set (inherited)") },
            ...levels.map((level) => ({ value: level, label: levelLabel(level, tm) })),
          ]}
        />
        <div className="flex items-end pb-1.5">
          <Checkbox
            label={tm("Inherit the allow list of the parent")}
            checked={row.inherit}
            onChange={(inherit) => onChange({ ...row, inherit })}
          />
        </div>
      </div>
    </div>
  );
}

export function RoomsTab({
  rooms,
  users,
  groupMap,
  busy,
  error,
  onReload,
}: {
  rooms: Room[];
  users: AdminUser[];
  groupMap: GroupMap | null;
  busy: boolean;
  error: string;
  onReload: () => void;
}) {
  const { tm } = useLang();
  const [selected, setSelected] = useState<string>("");
  const [newRoom, setNewRoom] = useState("");
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Room | null>(null);
  const [message, setMessage] = useState("");
  const [actionError, setActionError] = useState("");

  const [acl, setAcl] = useState<AclState | null>(null);
  const [aclError, setAclError] = useState("");
  const [rows, setRows] = useState<RuleRow[]>([]);
  const [clearance, setClearance] = useState<ClearanceRow[]>([]);
  const [defaultClassification, setDefaultClassification] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [subfolder, setSubfolder] = useState("");

  async function loadAcl() {
    setAclError("");
    try {
      const state = await readAcl();
      setAcl(state);
      const converted = toRows(state.rules);
      setRows(converted.rows);
      setClearance(converted.clearance);
      setDefaultClassification(converted.defaultClassification);
      setDirty(false);
    } catch (err) {
      setAclError(errorText(err, tm));
    }
  }

  useEffect(() => {
    void loadAcl();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selected && rooms.length) setSelected(rooms[0].path);
    if (selected && rooms.length && !rooms.some((r) => r.path === selected)) {
      setSelected(rooms[0].path);
    }
  }, [rooms, selected]);

  const room = rooms.find((r) => r.path === selected) ?? null;
  const levels = acl?.levels ?? [];
  const editable = Boolean(acl?.configured) && acl?.rules !== null;

  const access = useMemo(() => {
    if (!room) return { users: [] as string[], roles: [] as string[] };
    const slashed = withSlash(room.path);
    const roomUsers = users
      .filter((u) => u.drives.some((d) => withSlash(d) === slashed))
      .map((u) => u.username);
    const roles = Object.entries(groupMap?.roles ?? {})
      .filter(([, drives]) => drives.some((d) => withSlash(d) === slashed))
      .map(([role]) => role);
    return { users: roomUsers, roles };
  }, [room, users, groupMap]);

  function change(updater: (rows: RuleRow[]) => RuleRow[]) {
    setRows(updater);
    setDirty(true);
  }

  function addRule(path: string) {
    if (rows.some((r) => r.path === path)) return;
    change((current) => [
      ...current,
      {
        id: nextId++,
        path,
        restricted: false,
        allow: "",
        deny: "",
        classification: "",
        inherit: true,
      },
    ]);
  }

  async function save() {
    if (!acl?.version) return;
    setSaving(true);
    setAclError("");
    setMessage("");
    try {
      await saveAcl(toRules(rows, clearance, defaultClassification), acl.version);
      await loadAcl();
      setMessage(tm("The permission rules were saved. They apply at once."));
    } catch (err) {
      setAclError(errorText(err, tm));
    } finally {
      setSaving(false);
    }
  }

  async function addRoom() {
    setCreating(true);
    setActionError("");
    setMessage("");
    try {
      const created = await createRoom(newRoom.trim());
      setNewRoom("");
      setSelected(created.path);
      setMessage(
        fill(tm("The room {name} was created. Give users access to it under Users or AD groups."), {
          name: created.name,
        }),
      );
      onReload();
    } catch (err) {
      setActionError(errorText(err, tm));
    } finally {
      setCreating(false);
    }
  }

  async function removeRoom(target: Room) {
    setActionError("");
    setMessage("");
    try {
      await deleteRoom(target.path);
      setDeleting(null);
      setMessage(fill(tm("The room {name} was deleted."), { name: target.name }));
      onReload();
      void loadAcl();
    } catch (err) {
      setDeleting(null);
      setActionError(errorText(err, tm));
    }
  }

  const roomRows = room ? rows.filter((r) => under(r.path, room.path)) : [];

  return (
    <div className="space-y-4">
      <Card
        title={tm("Rooms")}
        icon={<DoorOpen className="h-4 w-4" />}
        actions={
          <Button busy={busy} onClick={onReload}>
            <RefreshCw className="h-3.5 w-3.5" />
            {tm("Refresh")}
          </Button>
        }
      >
        <div className="space-y-2">
          {error && <Notice tone="error">{error}</Notice>}
          {actionError && <Notice tone="error">{actionError}</Notice>}
          {message && <Notice tone="ok">{message}</Notice>}
        </div>
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <div className="w-64">
            <TextField
              label={tm("New room")}
              value={newRoom}
              placeholder={tm("Room name")}
              onChange={(e) => setNewRoom(e.target.value)}
            />
          </div>
          <Button
            kind="primary"
            busy={creating}
            disabled={!newRoom.trim()}
            onClick={() => void addRoom()}
          >
            <Plus className="h-3.5 w-3.5" />
            {tm("Create")}
          </Button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {rooms.length === 0 && (
            <p className="text-xs text-white/50">{tm("There are no rooms yet.")}</p>
          )}
          {rooms.map((r) => (
            <button
              key={r.path}
              type="button"
              onClick={() => setSelected(r.path)}
              className={`rounded-xl border px-3 py-2 text-left text-xs transition ${
                r.path === selected
                  ? "border-[#8C49E9] bg-[#8C49E9]/15 text-white"
                  : "border-white/10 bg-white/[0.03] text-white/80 hover:bg-white/[0.06]"
              }`}
            >
              <div className="font-medium">{r.name}</div>
              <div className="mt-0.5 text-[11px] text-white/50">
                {fill(tm("{n} file(s)"), { n: r.files })}
                {!r.stored && ` · ${tm("index only")}`}
              </div>
            </button>
          ))}
        </div>
      </Card>

      {room && (
        <Card
          title={fill(tm("Access to {name}"), { name: room.name })}
          icon={<ShieldCheck className="h-4 w-4" />}
          actions={
            <>
              <Button
                kind="danger"
                disabled={room.files > 0}
                title={room.files > 0 ? tm("Only an empty room can be deleted.") : undefined}
                onClick={() => setDeleting(room)}
              >
                <Trash2 className="h-3.5 w-3.5" />
                {tm("Delete room")}
              </Button>
              <Button
                kind="primary"
                busy={saving}
                disabled={!dirty || !editable}
                onClick={() => void save()}
              >
                <Save className="h-3.5 w-3.5" />
                {tm("Save rules")}
              </Button>
            </>
          }
        >
          <div className="space-y-3">
            <div className="grid gap-2 text-xs sm:grid-cols-2">
              <div>
                <span className="text-white/55">{tm("Users with this room")}: </span>
                <span className="text-white/90">{access.users.join(", ") || "—"}</span>
              </div>
              <div>
                <span className="text-white/55">{tm("AD roles with this room")}: </span>
                <span className="text-white/90">{access.roles.join(", ") || "—"}</span>
              </div>
            </div>
            <p className="text-[11px] leading-relaxed text-white/50">
              {tm(
                "A user sees a room only if it is assigned to them (Users, or an AD role). Inside the room the rules below narrow it further: a deny always wins, an allow list limits the room or folder to the listed users, groups and roles, and the classification must not exceed the user's clearance.",
              )}
            </p>
            {aclError && <Notice tone="error">{aclError}</Notice>}
            {acl && !acl.configured && (
              <Notice tone="warn">
                {tm("No permission rules file is configured on this AI Box (ADAPTER_ACL_RULES).")}
              </Notice>
            )}
            {acl?.configured && acl.rules === null && (
              <Notice tone="error">
                {tm(
                  "The permission rules file is not valid; every document is hidden until it is fixed.",
                )}{" "}
                {acl.error}
              </Notice>
            )}
            {dirty && <Notice tone="warn">{tm("There are unsaved changes.")}</Notice>}
            {editable && (
              <>
                {roomRows.length === 0 && (
                  <p className="text-xs text-white/60">
                    {tm("No rules: everyone who has the room sees every document in it.")}
                  </p>
                )}
                {roomRows.map((row) => (
                  <RuleEditor
                    key={row.id}
                    row={row}
                    room={room.path}
                    levels={levels}
                    onChange={(updated) =>
                      change((current) => current.map((r) => (r.id === updated.id ? updated : r)))
                    }
                    onRemove={() => change((current) => current.filter((r) => r.id !== row.id))}
                  />
                ))}
                <div className="flex flex-wrap items-end gap-2">
                  {!rows.some((r) => r.path === room.path) && (
                    <Button onClick={() => addRule(room.path)}>
                      <Plus className="h-3.5 w-3.5" />
                      {tm("Rule for the whole room")}
                    </Button>
                  )}
                  <div className="w-64">
                    <TextField
                      label={tm("Folder in the room")}
                      placeholder="2026/Ügyek"
                      value={subfolder}
                      onChange={(e) => setSubfolder(e.target.value)}
                    />
                  </div>
                  <Button
                    disabled={!subfolder.trim().replace(/^\/+|\/+$/g, "")}
                    onClick={() => {
                      const relative = subfolder.trim().replace(/^\/+|\/+$/g, "");
                      addRule(`${room.path}/${relative.normalize("NFC")}`);
                      setSubfolder("");
                    }}
                  >
                    <Plus className="h-3.5 w-3.5" />
                    {tm("Rule for a folder")}
                  </Button>
                </div>
              </>
            )}
          </div>
        </Card>
      )}

      {editable && (
        <Card
          title={tm("Clearance and default classification")}
          icon={<ShieldCheck className="h-4 w-4" />}
        >
          <p className="mb-3 text-[11px] leading-relaxed text-white/50">
            {fill(
              tm(
                "A document without a classification counts as the default level. A user sees documents up to their clearance: the highest level that matches them below, else “default” (the box setting is {level}).",
              ),
              { level: levelLabel(acl?.settings.default_clearance, tm) },
            )}
          </p>
          <div className="mb-3 w-72">
            <SelectField
              label={tm("Default classification")}
              value={defaultClassification}
              onChange={(value) => {
                setDefaultClassification(value);
                setDirty(true);
              }}
              options={[
                {
                  value: "",
                  label: fill(tm("Box setting ({level})"), {
                    level: levelLabel(acl?.settings.box_default_classification, tm),
                  }),
                },
                ...levels.map((level) => ({ value: level, label: levelLabel(level, tm) })),
              ]}
            />
          </div>
          <div className="space-y-2">
            {clearance.map((row) => (
              <div key={row.id} className="flex flex-wrap items-end gap-2">
                <div className="w-64">
                  <TextField
                    label={tm("Who")}
                    placeholder="role:analyst"
                    value={row.principal}
                    onChange={(e) => {
                      const principal = e.target.value;
                      setClearance((current) =>
                        current.map((c) => (c.id === row.id ? { ...c, principal } : c)),
                      );
                      setDirty(true);
                    }}
                  />
                </div>
                <div className="w-56">
                  <SelectField
                    label={tm("Clearance")}
                    value={row.level}
                    onChange={(level) => {
                      setClearance((current) =>
                        current.map((c) => (c.id === row.id ? { ...c, level } : c)),
                      );
                      setDirty(true);
                    }}
                    options={levels.map((level) => ({
                      value: level,
                      label: levelLabel(level, tm),
                    }))}
                  />
                </div>
                <Button
                  kind="danger"
                  title={tm("Remove")}
                  onClick={() => {
                    setClearance((current) => current.filter((c) => c.id !== row.id));
                    setDirty(true);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            ))}
            <Button
              onClick={() => {
                setClearance((current) => [
                  ...current,
                  { id: nextId++, principal: "", level: levels[1] ?? "internal" },
                ]);
                setDirty(true);
              }}
            >
              <Plus className="h-3.5 w-3.5" />
              {tm("Add clearance")}
            </Button>
          </div>
          <div className="mt-3 flex justify-end">
            <Button kind="primary" busy={saving} disabled={!dirty} onClick={() => void save()}>
              <Save className="h-3.5 w-3.5" />
              {tm("Save rules")}
            </Button>
          </div>
        </Card>
      )}

      {deleting && (
        <Dialog
          title={fill(tm("Delete the room {name}?"), { name: deleting.name })}
          onClose={() => setDeleting(null)}
          footer={
            <>
              <Button onClick={() => setDeleting(null)}>{tm("Cancel")}</Button>
              <Button kind="danger" onClick={() => void removeRoom(deleting)}>
                {tm("Delete room")}
              </Button>
            </>
          }
        >
          <p className="text-xs leading-relaxed text-white/80">
            {tm(
              "Only an empty room can be deleted. Its permission rules are removed too. Users and AD roles keep the room in their list until you remove it there.",
            )}
          </p>
          {(access.users.length > 0 || access.roles.length > 0) && (
            <Badge tone="warn">
              {fill(tm("Still assigned to: {list}"), {
                list: [...access.users, ...access.roles.map((r) => `role:${r}`)].join(", "),
              })}
            </Badge>
          )}
        </Dialog>
      )}
    </div>
  );
}
