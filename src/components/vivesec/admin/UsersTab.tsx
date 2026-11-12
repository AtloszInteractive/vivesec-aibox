import { useMemo, useState } from "react";
import { KeyRound, Lock, Pencil, Plus, RefreshCw, Smartphone, Trash2, UserX } from "lucide-react";

import {
  createUser,
  deleteUser,
  resetPassword,
  resetTotp,
  unlockUser,
  updateUser,
  type AdminUser,
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
  TextField,
  errorText,
  fill,
  formatTime,
  generatePassword,
  roomName,
  splitList,
  withSlash,
} from "./ui";

type Editing =
  | { kind: "create" }
  | { kind: "edit"; user: AdminUser }
  | { kind: "password"; user: AdminUser }
  | { kind: "confirm"; user: AdminUser; action: "delete" | "totp" | "suspend" };

function RoomPicker({
  rooms,
  selected,
  onChange,
}: {
  rooms: Room[];
  selected: string[];
  onChange: (drives: string[]) => void;
}) {
  const { tm } = useLang();
  const known = new Set(rooms.map((r) => withSlash(r.path)));
  const missing = selected.filter((d) => !known.has(withSlash(d)));
  const toggle = (path: string, on: boolean) => {
    const slashed = withSlash(path);
    const rest = selected.filter((d) => withSlash(d) !== slashed);
    onChange(on ? [...rest, slashed] : rest);
  };
  return (
    <div>
      <span className="mb-1 block text-[11px] font-medium text-white/65">{tm("Rooms")}</span>
      {rooms.length === 0 && missing.length === 0 && (
        <p className="text-[11px] text-white/50">
          {tm("There are no rooms yet. Create one under Rooms and permissions.")}
        </p>
      )}
      <div className="grid max-h-48 grid-cols-2 gap-1.5 overflow-y-auto rounded-lg border border-white/10 bg-white/[0.03] p-2">
        {rooms.map((room) => (
          <Checkbox
            key={room.path}
            label={room.name}
            checked={selected.some((d) => withSlash(d) === withSlash(room.path))}
            onChange={(on) => toggle(room.path, on)}
          />
        ))}
        {missing.map((path) => (
          <Checkbox
            key={path}
            label={`${roomName(path)} (${tm("missing")})`}
            checked
            onChange={(on) => toggle(path, on)}
          />
        ))}
      </div>
    </div>
  );
}

function UserDialog({
  editing,
  rooms,
  onClose,
  onDone,
}: {
  editing: Extract<Editing, { kind: "create" | "edit" }>;
  rooms: Room[];
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const { tm } = useLang();
  const user = editing.kind === "edit" ? editing.user : null;
  const managed = user?.source === "ldap";
  const [username, setUsername] = useState(user?.username ?? "");
  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [roles, setRoles] = useState((user?.roles ?? []).join(", "));
  const [groups, setGroups] = useState((user?.groups ?? []).join(", "));
  const [drives, setDrives] = useState<string[]>(user?.drives ?? []);
  const [password, setPassword] = useState(() => (user ? "" : generatePassword()));
  const [temporary, setTemporary] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setBusy(true);
    setError("");
    try {
      const fields = {
        display_name: displayName,
        roles: splitList(roles).map((r) => r.toLowerCase()),
        groups: splitList(groups),
        drives,
      };
      if (user) {
        await updateUser(user.username, fields);
        onDone(fill(tm("{name} was saved."), { name: user.username }));
      } else {
        const created = await createUser({ ...fields, username, password, temporary });
        onDone(
          fill(
            tm(
              "{name} was created. Give the user the password; at the first sign-in they register their authenticator app.",
            ),
            { name: created.username },
          ),
        );
      }
    } catch (err) {
      setError(errorText(err, tm));
      setBusy(false);
    }
  }

  return (
    <Dialog
      wide
      title={user ? fill(tm("Edit {name}"), { name: user.username }) : tm("New user")}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{tm("Cancel")}</Button>
          {!managed && (
            <Button kind="primary" busy={busy} onClick={() => void save()}>
              {tm("Save")}
            </Button>
          )}
        </>
      }
    >
      {error && <Notice tone="error">{error}</Notice>}
      {managed && (
        <Notice tone="warn">
          {tm(
            "This user signs in with Active Directory: name, roles, groups and rooms come from AD and the group mapping.",
          )}
        </Notice>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label={tm("Username")}
          value={username}
          disabled={Boolean(user)}
          autoComplete="off"
          onChange={(e) => setUsername(e.target.value.toLowerCase())}
        />
        <TextField
          label={tm("Display name")}
          value={displayName}
          disabled={managed}
          onChange={(e) => setDisplayName(e.target.value)}
        />
        <TextField
          label={tm("Roles")}
          hint={tm("Separated by commas. “admin” makes the user an administrator.")}
          value={roles}
          disabled={managed}
          onChange={(e) => setRoles(e.target.value)}
        />
        <TextField
          label={tm("Groups")}
          hint={tm("Separated by commas; used in the permission rules as group:<name>.")}
          value={groups}
          disabled={managed}
          onChange={(e) => setGroups(e.target.value)}
        />
      </div>
      {!managed && <RoomPicker rooms={rooms} selected={drives} onChange={setDrives} />}
      {managed && (
        <p className="text-xs text-white/70">
          {tm("Rooms")}: {drives.map(roomName).join(", ") || "—"}
        </p>
      )}
      {!user && (
        <div className="space-y-2">
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <TextField
                label={tm("Initial password")}
                value={password}
                autoComplete="new-password"
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            <Button onClick={() => setPassword(generatePassword())}>{tm("Generate")}</Button>
          </div>
          <Checkbox
            label={tm("The user must change it at the first sign-in")}
            checked={temporary}
            onChange={setTemporary}
          />
        </div>
      )}
    </Dialog>
  );
}

function PasswordDialog({
  user,
  onClose,
  onDone,
}: {
  user: AdminUser;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const { tm } = useLang();
  const [password, setPassword] = useState(generatePassword);
  const [temporary, setTemporary] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setBusy(true);
    setError("");
    try {
      await resetPassword(user.username, password, temporary);
      onDone(
        fill(tm("The password of {name} was reset; every session of the user has ended."), {
          name: user.username,
        }),
      );
    } catch (err) {
      setError(errorText(err, tm));
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={fill(tm("New password for {name}"), { name: user.username })}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{tm("Cancel")}</Button>
          <Button kind="primary" busy={busy} onClick={() => void save()}>
            {tm("Set password")}
          </Button>
        </>
      }
    >
      {error && <Notice tone="error">{error}</Notice>}
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <TextField
            label={tm("New password")}
            value={password}
            autoComplete="new-password"
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <Button onClick={() => setPassword(generatePassword())}>{tm("Generate")}</Button>
      </div>
      <Checkbox
        label={tm("The user must change it at the first sign-in")}
        checked={temporary}
        onChange={setTemporary}
      />
    </Dialog>
  );
}

function ConfirmDialog({
  editing,
  onClose,
  onDone,
}: {
  editing: Extract<Editing, { kind: "confirm" }>;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const { tm } = useLang();
  const { user, action } = editing;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const texts = {
    delete: {
      title: tm("Delete user"),
      body: tm(
        "The user and every session of the user are deleted. Their conversations stay on the AI Box. A directory user is created again at the next sign-in if AD still allows it.",
      ),
      button: tm("Delete"),
      done: tm("{name} was deleted."),
    },
    totp: {
      title: tm("Reset two-factor authentication"),
      body: tm(
        "The registered authenticator app stops working and every session of the user ends. At the next sign-in the user registers the app again (for example on a new phone).",
      ),
      button: tm("Reset"),
      done: tm("The two-factor authentication of {name} was reset."),
    },
    suspend: {
      title: tm("Suspend user"),
      body: tm(
        "The user cannot sign in until reactivated, and every session of the user ends now.",
      ),
      button: tm("Suspend"),
      done: tm("{name} was suspended."),
    },
  }[action];

  async function run() {
    setBusy(true);
    setError("");
    try {
      if (action === "delete") await deleteUser(user.username);
      else if (action === "totp") await resetTotp(user.username);
      else await updateUser(user.username, { status: "suspended" });
      onDone(fill(texts.done, { name: user.username }));
    } catch (err) {
      setError(errorText(err, tm));
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={`${texts.title}: ${user.username}`}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{tm("Cancel")}</Button>
          <Button kind="danger" busy={busy} onClick={() => void run()}>
            {texts.button}
          </Button>
        </>
      }
    >
      {error && <Notice tone="error">{error}</Notice>}
      <p className="text-xs leading-relaxed text-white/80">{texts.body}</p>
    </Dialog>
  );
}

export function UsersTab({
  users,
  rooms,
  me,
  busy,
  error,
  onReload,
}: {
  users: AdminUser[];
  rooms: Room[];
  me: string;
  busy: boolean;
  error: string;
  onReload: () => void;
}) {
  const { tm, lang } = useLang();
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<Editing | null>(null);
  const [message, setMessage] = useState("");
  const [actionError, setActionError] = useState("");

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return users;
    return users.filter(
      (u) =>
        u.username.includes(needle) ||
        u.display_name.toLowerCase().includes(needle) ||
        u.roles.some((r) => r.includes(needle)),
    );
  }, [users, filter]);

  function done(text: string) {
    setEditing(null);
    setActionError("");
    setMessage(text);
    onReload();
  }

  async function quick(action: () => Promise<unknown>, text: string) {
    setMessage("");
    setActionError("");
    try {
      await action();
      done(text);
    } catch (err) {
      setActionError(errorText(err, tm));
    }
  }

  return (
    <Card
      title={fill(tm("Users ({n})"), { n: users.length })}
      actions={
        <>
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={tm("Filter")}
            className="w-40 rounded-lg border border-white/15 bg-white/5 px-2.5 py-1.5 text-xs text-white outline-none placeholder:text-white/30 focus:border-[#B48CF5]"
          />
          <Button busy={busy} onClick={onReload}>
            <RefreshCw className="h-3.5 w-3.5" />
            {tm("Refresh")}
          </Button>
          <Button
            kind="primary"
            onClick={() => {
              setMessage("");
              setEditing({ kind: "create" });
            }}
          >
            <Plus className="h-3.5 w-3.5" />
            {tm("New user")}
          </Button>
        </>
      }
    >
      <div className="space-y-2">
        {error && <Notice tone="error">{error}</Notice>}
        {actionError && <Notice tone="error">{actionError}</Notice>}
        {message && <Notice tone="ok">{message}</Notice>}
      </div>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[760px] text-left text-xs">
          <thead className="text-[11px] text-white/50">
            <tr className="border-b border-white/10">
              <th className="py-2 pr-2 font-medium">{tm("User")}</th>
              <th className="py-2 pr-2 font-medium">{tm("Roles")}</th>
              <th className="py-2 pr-2 font-medium">{tm("Rooms")}</th>
              <th className="py-2 pr-2 font-medium">{tm("State")}</th>
              <th className="py-2 pr-2 font-medium">{tm("Last sign-in")}</th>
              <th className="py-2 text-right font-medium">{tm("Actions")}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((user) => (
              <tr key={user.username} className="border-b border-white/5 align-top">
                <td className="py-2 pr-2">
                  <div className="flex items-center gap-1.5 text-white">
                    {user.username}
                    {user.username === me && <Badge tone="accent">{tm("you")}</Badge>}
                  </div>
                  {user.display_name && (
                    <div className="text-[11px] text-white/50">{user.display_name}</div>
                  )}
                  <div className="mt-1">
                    <Badge tone={user.source === "ldap" ? "accent" : "neutral"}>
                      {user.source === "ldap" ? "Active Directory" : tm("Local")}
                    </Badge>
                  </div>
                </td>
                <td className="py-2 pr-2 text-white/80">{user.roles.join(", ") || "—"}</td>
                <td className="max-w-[14rem] py-2 pr-2 text-white/80">
                  {user.drives.map(roomName).join(", ") || "—"}
                </td>
                <td className="py-2 pr-2">
                  <div className="flex flex-wrap gap-1">
                    {user.status === "suspended" ? (
                      <Badge tone="bad">{tm("Suspended")}</Badge>
                    ) : (
                      <Badge tone="ok">{tm("Active")}</Badge>
                    )}
                    {user.locked && <Badge tone="bad">{tm("Locked")}</Badge>}
                    {!user.totp_enrolled && <Badge tone="warn">{tm("2FA not registered")}</Badge>}
                    {user.must_change_password && (
                      <Badge tone="warn">{tm("Temporary password")}</Badge>
                    )}
                    {user.sessions > 0 && (
                      <Badge>{fill(tm("{n} session(s)"), { n: user.sessions })}</Badge>
                    )}
                  </div>
                </td>
                <td className="py-2 pr-2 text-white/70">{formatTime(user.last_login_at, lang)}</td>
                <td className="py-2">
                  <div className="flex flex-wrap justify-end gap-1">
                    <Button
                      title={tm("Edit")}
                      onClick={() => {
                        setMessage("");
                        setEditing({ kind: "edit", user });
                      }}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    {user.source === "local" && (
                      <Button
                        title={tm("New password")}
                        onClick={() => setEditing({ kind: "password", user })}
                      >
                        <KeyRound className="h-3.5 w-3.5" />
                      </Button>
                    )}
                    {user.totp_enrolled && (
                      <Button
                        title={tm("Reset two-factor authentication")}
                        onClick={() => setEditing({ kind: "confirm", user, action: "totp" })}
                      >
                        <Smartphone className="h-3.5 w-3.5" />
                      </Button>
                    )}
                    {user.locked && (
                      <Button
                        title={tm("Unlock")}
                        onClick={() =>
                          void quick(
                            () => unlockUser(user.username),
                            fill(tm("{name} was unlocked."), { name: user.username }),
                          )
                        }
                      >
                        <Lock className="h-3.5 w-3.5" />
                      </Button>
                    )}
                    {user.status === "active" ? (
                      <Button
                        title={tm("Suspend")}
                        disabled={user.username === me}
                        onClick={() => setEditing({ kind: "confirm", user, action: "suspend" })}
                      >
                        <UserX className="h-3.5 w-3.5" />
                      </Button>
                    ) : (
                      <Button
                        onClick={() =>
                          void quick(
                            () => updateUser(user.username, { status: "active" }),
                            fill(tm("{name} was reactivated."), { name: user.username }),
                          )
                        }
                      >
                        {tm("Reactivate")}
                      </Button>
                    )}
                    <Button
                      kind="danger"
                      title={tm("Delete")}
                      disabled={user.username === me}
                      onClick={() => setEditing({ kind: "confirm", user, action: "delete" })}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(editing?.kind === "create" || editing?.kind === "edit") && (
        <UserDialog
          editing={editing}
          rooms={rooms}
          onClose={() => setEditing(null)}
          onDone={done}
        />
      )}
      {editing?.kind === "password" && (
        <PasswordDialog user={editing.user} onClose={() => setEditing(null)} onDone={done} />
      )}
      {editing?.kind === "confirm" && (
        <ConfirmDialog editing={editing} onClose={() => setEditing(null)} onDone={done} />
      )}
    </Card>
  );
}
