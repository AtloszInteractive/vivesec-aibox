import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronRight, Folder, FolderOpen, Loader2 } from "lucide-react";
import { useLang } from "./i18n";
import { listDriveChildren, listScope, type ScopeDrive } from "@/lib/api/rag.functions";
import { ACCENT, ACCENT_TEXT, ON_ACCENT } from "./brand";

/** Where a path sits, for people: "Room › folder › folder". */
function folderLabel(path: string, rooms: ScopeDrive[]): string {
  const clean = path.replace(/\/+$/, "");
  const room = rooms.find((r) => clean === r.path || clean.startsWith(r.path + "/"));
  if (!room) return clean.split("/").filter(Boolean).slice(-2).join(" › ");
  const rest = clean.slice(room.path.length).split("/").filter(Boolean);
  return [room.name, ...rest].join(" › ");
}

/** Choose a folder of the caller's scope by browsing it: a room (drive), then
 *  its subfolders. With `allowAll` an empty value means "every folder". */
export function FolderPicker({
  drive,
  value,
  onChange,
  allowAll = false,
  disabled = false,
}: {
  drive: string;
  value: string;
  onChange: (path: string) => void;
  allowAll?: boolean;
  disabled?: boolean;
}) {
  const { tm } = useLang();
  const [rooms, setRooms] = useState<ScopeDrive[]>([]);
  const [open, setOpen] = useState(false);
  const [room, setRoom] = useState("");
  const [current, setCurrent] = useState("");
  const [folders, setFolders] = useState<{ path: string; name: string }[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void listScope(drive).then((scope) => {
      if (!cancelled) setRooms(scope.drives);
    });
    return () => {
      cancelled = true;
    };
  }, [drive]);

  // Only the latest listing may land: a slower answer for a folder already
  // left must not show its subfolders under the current one.
  const request = useRef(0);
  const browse = useCallback(async (roomPath: string, path: string) => {
    const id = ++request.current;
    setCurrent(path);
    setFolders(null);
    setFailed(false);
    const res = await listDriveChildren({ data: { drive: `${roomPath}/`, path } });
    if (id !== request.current) return;
    if (!res.ok) setFailed(true);
    setFolders(
      res.entries
        .filter((e) => !e.file && !e.name.startsWith("~"))
        .map((e) => ({ path: e.path, name: e.name }))
        .sort((a, b) => a.name.localeCompare(b.name)),
    );
  }, []);

  function start() {
    const clean = value.replace(/\/+$/, "");
    const active = drive.replace(/\/+$/, "");
    const inRoom =
      rooms.find((r) => clean && (clean === r.path || clean.startsWith(r.path + "/"))) ??
      rooms.find((r) => r.path === active) ??
      rooms[0];
    if (!inRoom) return;
    setRoom(inRoom.path);
    setOpen(true);
    void browse(inRoom.path, clean.startsWith(inRoom.path) ? clean : inRoom.path);
  }

  const crumbs = current
    ? current
        .slice(room.length)
        .split("/")
        .filter(Boolean)
        .map((name, i, all) => ({ name, path: `${room}/${all.slice(0, i + 1).join("/")}` }))
    : [];
  const roomName = rooms.find((r) => r.path === room)?.name ?? room;
  const button =
    "rounded-lg border border-ink/15 px-2.5 py-1 text-xs transition hover:bg-ink/10 disabled:opacity-50";

  return (
    <div className="min-w-0 flex-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex min-w-0 items-center gap-1.5 text-xs text-ink">
          <Folder className="h-3.5 w-3.5 shrink-0" style={{ color: ACCENT_TEXT }} />
          <span className="truncate">
            {value ? folderLabel(value, rooms) : tm(allowAll ? "Every folder" : "Not chosen")}
          </span>
        </span>
        {!open && (
          <button
            onClick={start}
            disabled={disabled || rooms.length === 0}
            className={button}
            type="button"
          >
            {tm("Change")}
          </button>
        )}
        {allowAll && value && !open && (
          <button onClick={() => onChange("")} disabled={disabled} className={button} type="button">
            {tm("Every folder")}
          </button>
        )}
      </div>
      {open && (
        <div className="mt-2 rounded-lg border border-ink/10 bg-shade/20 p-2">
          {rooms.length > 1 && (
            <select
              value={room}
              onChange={(e) => {
                setRoom(e.target.value);
                void browse(e.target.value, e.target.value);
              }}
              className="mb-2 w-full rounded-lg border border-ink/15 bg-shade/30 px-2 py-1 text-xs text-ink outline-none"
            >
              {rooms.map((r) => (
                <option key={r.path} value={r.path} className="bg-surface-2">
                  {r.name}
                </option>
              ))}
            </select>
          )}
          <div className="mb-1 flex flex-wrap items-center gap-1 text-[11px]">
            <button
              type="button"
              onClick={() => void browse(room, room)}
              className="hover:underline"
              style={{ color: ACCENT_TEXT }}
            >
              {roomName}
            </button>
            {crumbs.map((c) => (
              <span key={c.path} className="inline-flex items-center gap-1">
                <ChevronRight className="h-3 w-3 text-ink/40" />
                <button
                  type="button"
                  onClick={() => void browse(room, c.path)}
                  className="hover:underline"
                  style={{ color: ACCENT_TEXT }}
                >
                  {c.name}
                </button>
              </span>
            ))}
          </div>
          <div className="max-h-44 space-y-0.5 overflow-y-auto pr-1">
            {folders === null && <Loader2 className="h-3.5 w-3.5 animate-spin text-ink/50" />}
            {failed && (
              <div className="text-[11px] text-amber-700 dark:text-amber-300">
                {tm("The folders could not be listed.")}
              </div>
            )}
            {folders?.length === 0 && !failed && (
              <div className="text-[11px] text-ink/50">{tm("No subfolders.")}</div>
            )}
            {folders?.map((f) => (
              <button
                key={f.path}
                type="button"
                onClick={() => void browse(room, f.path)}
                className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-xs hover:bg-ink/10"
              >
                <FolderOpen className="h-3.5 w-3.5 shrink-0 text-ink/50" />
                <span className="truncate">{f.name}</span>
              </button>
            ))}
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => {
                onChange(current);
                setOpen(false);
              }}
              disabled={!current}
              className="rounded-lg px-3 py-1 text-xs font-medium disabled:opacity-50"
              style={{ backgroundColor: ACCENT, color: ON_ACCENT }}
            >
              {tm("Choose this folder")}
            </button>
            <button type="button" onClick={() => setOpen(false)} className={button}>
              {tm("Cancel")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
