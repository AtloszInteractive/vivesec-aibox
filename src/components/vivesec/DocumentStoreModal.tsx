import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle,
  ChevronRight,
  Download,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  FolderUp,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from "lucide-react";

import {
  adapterDriveFile,
  STORE_FINAL_STATES,
  StoreError,
  storeDelete,
  storeDismiss,
  storeInfo,
  storeList,
  storeMkdir,
  storeUpload,
  storeUploads,
  type StoreEntry,
  type StoreInfo,
  type StoreUpload,
} from "@/lib/rag/adapter-client";
import { ACCENT, ACCENT_TEXT, ON_ACCENT } from "./brand";
import { useLang } from "./i18n";

const PANEL = "#10243A";
const BG = "#0A1B2D";
const HEAD = "#071625";
const POLL_MS = 3000;

type Pending = { file: File; rel: string };

function formatSize(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

function baseName(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

/** Error code of a failed store call -> a sentence for the user. */
function errorText(code: string, tm: (s: string) => string): string {
  const known: Record<string, string> = {
    not_allowed: "You are not allowed to change files here.",
    invalid_path: "This name is not allowed (no leading dot, no control characters).",
    type_not_allowed: "This file type cannot be uploaded.",
    too_large: "The file is larger than the upload limit.",
    exists: "A file with this name already exists.",
    pending: "This file is already being uploaded.",
    parent_is_file: "A file with the name of the folder is in the way.",
    is_folder: "A folder with this name already exists.",
    room_root: "A room cannot be deleted here.",
    "not-found": "Not found. It may have been deleted meanwhile.",
    store_disabled: "The file store is not available on this AI Box.",
    store_unavailable: "The file store is not available on this AI Box.",
    "storage locked": "The storage is locked.",
    busy: "The file is being indexed; try again in a moment.",
    network: "The AI Box could not be reached.",
    aborted: "The upload was cancelled.",
    deleted: "Deleted before it was indexed.",
    lost: "The upload was lost; upload it again.",
  };
  return tm(known[code] ?? "The operation failed.");
}

function statusText(upload: StoreUpload, tm: (s: string) => string): string {
  switch (upload.status) {
    case "queued":
      return tm("Waiting for the virus scan");
    case "waiting_scanner":
      return tm("Virus scanner unavailable, kept in quarantine");
    case "indexing":
      return tm("Indexing…");
    case "index_pending":
      return tm("Stored; indexing will be retried");
    case "indexed":
      return tm("Indexed, searchable");
    case "stored":
      return upload.reason === "too_large"
        ? tm("Stored, but too large to index")
        : tm("Stored, but not searchable");
    case "infected":
      return tm("Virus found, the file was deleted");
    case "rejected":
      return tm("Could not be scanned, the file was rejected");
    case "cancelled":
      return tm("Cancelled");
    default:
      return errorText(upload.reason ?? "", tm);
  }
}

function statusTone(status: StoreUpload["status"]): string {
  if (status === "indexed") return "text-emerald-300";
  if (status === "infected" || status === "rejected" || status === "failed") return "text-rose-300";
  if (status === "stored" || status === "waiting_scanner" || status === "index_pending")
    return "text-amber-200";
  return "text-white/60";
}

function visibleUpload(rel: string): boolean {
  return rel.split("/").every((part) => part && !part.startsWith("."));
}

/**
 * The file store of a standalone AI Box (E27 S5): browse a room, upload files
 * and folders, create folders, delete, download. Uploads are virus-scanned in
 * quarantine and indexed on the box; their state is shown until dismissed.
 */
export function DocumentStoreModal({ drive, onClose }: { drive: string; onClose: () => void }) {
  const { tm } = useLang();
  const [mounted, setMounted] = useState(false);
  const [info, setInfo] = useState<StoreInfo | null | undefined>(undefined);
  const [room, setRoom] = useState("");
  const [cwd, setCwd] = useState("");
  const [entries, setEntries] = useState<StoreEntry[]>([]);
  const [listing, setListing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [uploads, setUploads] = useState<StoreUpload[]>([]);
  const [active, setActive] = useState<{ name: string; sent: number; total: number } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<StoreEntry | null>(null);
  const [confirmOverwrite, setConfirmOverwrite] = useState<{
    files: Pending[];
    conflicts: string[];
  } | null>(null);
  const [folderName, setFolderName] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const abort = useRef<AbortController | null>(null);
  const finished = useRef(new Set<string>());

  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    setMounted(true);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeRef.current();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      abort.current?.abort();
    };
  }, []);

  // Folder inputs need the non-standard attribute, which React does not type.
  useEffect(() => {
    folderInput.current?.setAttribute("webkitdirectory", "");
  }, [mounted]);

  const load = useCallback(
    async (path: string) => {
      setListing(true);
      setError("");
      try {
        const res = await storeList(drive, path);
        setEntries(res.entries);
        setCwd(res.path);
      } catch (err) {
        setEntries([]);
        setError(errorText(err instanceof StoreError ? err.code : "", tm));
      } finally {
        setListing(false);
      }
    },
    [drive, tm],
  );

  const refreshUploads = useCallback(async () => {
    try {
      const list = await storeUploads(drive);
      setUploads(list);
      return list;
    } catch {
      return null;
    }
  }, [drive]);

  useEffect(() => {
    let cancelled = false;
    void storeInfo(drive)
      .then((next) => {
        if (cancelled) return;
        setInfo(next);
        if (!next || !next.rooms.length) return;
        const own = drive.replace(/\/+$/, "");
        const first = next.rooms.includes(own) ? own : next.rooms[0];
        setRoom(first);
        void load(first);
      })
      .catch(() => {
        if (cancelled) return;
        setInfo(null);
        setError(errorText("network", tm));
      });
    void refreshUploads().then((list) => {
      for (const u of list ?? []) if (STORE_FINAL_STATES.has(u.status)) finished.current.add(u.id);
    });
    return () => {
      cancelled = true;
    };
  }, [drive, load, refreshUploads, tm]);

  // While an upload is still on its way through scan and index, keep asking;
  // when one lands, the folder on screen may have changed.
  const unfinished = uploads.some((u) => !STORE_FINAL_STATES.has(u.status));
  useEffect(() => {
    if (!unfinished) return;
    const id = window.setInterval(() => {
      void refreshUploads().then((list) => {
        let landed = false;
        for (const u of list ?? []) {
          if (STORE_FINAL_STATES.has(u.status) && !finished.current.has(u.id)) {
            finished.current.add(u.id);
            landed = true;
          }
        }
        if (landed && cwd) void load(cwd);
      });
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [unfinished, refreshUploads, load, cwd]);

  const writable = Boolean(info?.writable);
  const crumbs = useMemo(() => {
    if (!room || !cwd.startsWith(room)) return [] as string[];
    return cwd.slice(room.length).split("/").filter(Boolean);
  }, [room, cwd]);

  function accepts(file: File, rel: string): string | null {
    if (!info) return "network";
    if (info.extensions && !info.extensions.includes(extensionOf(rel))) return "type_not_allowed";
    if (file.size > info.maxBytes) return "too_large";
    return null;
  }

  async function runUploads(files: Pending[], overwrite: string[]) {
    const replace = new Set(overwrite);
    for (const { file, rel } of files) {
      const path = `${cwd}/${rel}`;
      setActive({ name: rel, sent: 0, total: file.size });
      abort.current = new AbortController();
      try {
        await storeUpload({
          drive,
          path,
          file,
          overwrite: replace.has(rel),
          signal: abort.current.signal,
          onProgress: (sent, total) => setActive({ name: rel, sent, total }),
        });
      } catch (err) {
        const code = err instanceof StoreError ? err.code : "";
        setNotice(`${rel}: ${errorText(code, tm)}`);
        if (code === "aborted") break;
      }
      await refreshUploads();
    }
    setActive(null);
    abort.current = null;
  }

  function startUploads(picked: Pending[]) {
    setNotice("");
    const usable: Pending[] = [];
    let skipped = 0;
    for (const item of picked) {
      if (!visibleUpload(item.rel)) continue;
      if (accepts(item.file, item.rel)) {
        skipped += 1;
        continue;
      }
      usable.push(item);
    }
    if (skipped) {
      setNotice(
        tm("{n} file(s) skipped: type not allowed or larger than the limit.").replace(
          "{n}",
          String(skipped),
        ),
      );
    }
    if (!usable.length) return;
    const existing = new Set(entries.filter((e) => e.file && e.stored).map((e) => e.name));
    const conflicts = usable.filter((p) => !p.rel.includes("/") && existing.has(p.rel));
    if (conflicts.length) {
      setConfirmOverwrite({ files: usable, conflicts: conflicts.map((p) => p.rel) });
      return;
    }
    void runUploads(usable, []);
  }

  function pickFiles(list: FileList | null, folder: boolean) {
    if (!list) return;
    const picked = Array.from(list).map((file) => ({
      file,
      rel: folder && file.webkitRelativePath ? file.webkitRelativePath : file.name,
    }));
    startUploads(picked);
  }

  function onDrop(event: React.DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (!writable || active) return;
    const files: Pending[] = [];
    let folders = 0;
    const items = Array.from(event.dataTransfer.items ?? []);
    items.forEach((item, index) => {
      const entry = item.webkitGetAsEntry?.();
      if (entry?.isDirectory) {
        folders += 1;
        return;
      }
      const file = item.getAsFile() ?? event.dataTransfer.files[index];
      if (file) files.push({ file, rel: file.name });
    });
    startUploads(files);
    if (folders) setNotice(tm("Folders cannot be dropped here; use Upload folder."));
  }

  async function createFolder() {
    const name = (folderName ?? "").trim();
    if (!name) return;
    try {
      await storeMkdir(drive, `${cwd}/${name}`);
      setFolderName(null);
      await load(cwd);
    } catch (err) {
      setNotice(errorText(err instanceof StoreError ? err.code : "", tm));
    }
  }

  async function remove(entry: StoreEntry) {
    setConfirmDelete(null);
    try {
      await storeDelete(drive, entry.path);
      await load(cwd);
    } catch (err) {
      setNotice(errorText(err instanceof StoreError ? err.code : "", tm));
    }
  }

  async function download(entry: StoreEntry) {
    const res = await adapterDriveFile({ path: entry.path, drive });
    if (!res.ok || !res.blob) {
      setNotice(errorText("not-found", tm));
      return;
    }
    const url = URL.createObjectURL(res.blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = entry.name;
    a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }

  async function dismiss(upload: StoreUpload) {
    try {
      await storeDismiss(drive, upload.id);
    } catch (err) {
      setNotice(errorText(err instanceof StoreError ? err.code : "", tm));
    }
    await refreshUploads();
  }

  const button =
    "inline-flex shrink-0 items-center gap-1.5 rounded-md border border-white/10 px-2.5 py-1.5 text-[11.5px] text-white/80 transition hover:bg-white/[0.07] disabled:cursor-not-allowed disabled:text-white/30 disabled:hover:bg-transparent";
  const iconButton =
    "grid h-7 w-7 shrink-0 place-items-center rounded-md text-white/60 transition hover:bg-white/10 hover:text-white disabled:opacity-30";

  if (!mounted) return null;

  return createPortal(
    <div
      className="fixed inset-0 flex items-stretch justify-center sm:items-center sm:p-4"
      style={{ zIndex: 210 }}
    >
      <button
        className="absolute inset-0 bg-black/75 backdrop-blur-sm"
        onClick={onClose}
        aria-label={tm("Close")}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={tm("Documents")}
        className="relative flex h-full w-full flex-col overflow-hidden border border-white/10 text-[#E6EAEE] shadow-2xl sm:h-[88vh] sm:max-w-5xl sm:rounded-lg"
        style={{ backgroundColor: BG }}
        onDragOver={(event) => {
          if (!writable) return;
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <div
          className="flex items-center gap-2 border-b border-white/10 px-3 py-2.5"
          style={{ backgroundColor: HEAD }}
        >
          <FolderOpen className="h-4 w-4" style={{ color: ACCENT_TEXT }} />
          <div className="min-w-0 flex-1 truncate text-[13px] font-semibold">{tm("Documents")}</div>
          {info && info.scan !== "off" && (
            <span className="hidden items-center gap-1 rounded-full border border-white/10 px-2 py-0.5 text-[10px] text-white/55 sm:inline-flex">
              <ShieldCheck className="h-3 w-3" /> {tm("Virus-scanned uploads")}
            </span>
          )}
          <button onClick={onClose} className={iconButton} aria-label={tm("Close")}>
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-1.5 border-b border-white/[0.07] px-3 py-2">
          {info && info.rooms.length > 1 && (
            <select
              value={room}
              onChange={(event) => {
                setRoom(event.target.value);
                void load(event.target.value);
              }}
              className="rounded-md border border-white/10 px-2 py-1.5 text-[12px] text-white/85 outline-none"
              style={{ backgroundColor: PANEL }}
              aria-label={tm("Room")}
            >
              {info.rooms.map((r) => (
                <option key={r} value={r}>
                  {baseName(r)}
                </option>
              ))}
            </select>
          )}
          <div
            className="flex min-w-0 flex-1 items-center overflow-x-auto rounded-md border border-white/10 px-2 py-1.5"
            style={{ backgroundColor: PANEL }}
          >
            <button
              onClick={() => void load(room)}
              className="shrink-0 rounded px-1.5 py-0.5 text-[11.5px] text-white/80 hover:bg-white/10"
            >
              {baseName(room) || tm("Room")}
            </button>
            {crumbs.map((crumb, index) => (
              <span key={`${crumb}-${index}`} className="flex shrink-0 items-center">
                <ChevronRight className="h-3 w-3 text-white/30" />
                <button
                  onClick={() => void load(`${room}/${crumbs.slice(0, index + 1).join("/")}`)}
                  className="rounded px-1.5 py-0.5 text-[11.5px] text-white/80 hover:bg-white/10"
                >
                  {crumb}
                </button>
              </span>
            ))}
          </div>
          <button
            className={iconButton}
            disabled={listing || !cwd}
            onClick={() => void load(cwd)}
            aria-label={tm("Refresh")}
          >
            <RefreshCw className={`h-4 w-4 ${listing ? "animate-spin" : ""}`} />
          </button>
          {writable && (
            <>
              <button className={button} disabled={!cwd} onClick={() => setFolderName("")}>
                <FolderPlus className="h-3.5 w-3.5" /> {tm("New folder")}
              </button>
              <button
                className={button}
                disabled={!cwd || Boolean(active)}
                onClick={() => fileInput.current?.click()}
              >
                <Upload className="h-3.5 w-3.5" /> {tm("Upload files")}
              </button>
              <button
                className={button}
                disabled={!cwd || Boolean(active)}
                onClick={() => folderInput.current?.click()}
              >
                <FolderUp className="h-3.5 w-3.5" /> {tm("Upload folder")}
              </button>
              <input
                ref={fileInput}
                type="file"
                multiple
                hidden
                accept={info?.extensions?.join(",")}
                onChange={(event) => {
                  pickFiles(event.target.files, false);
                  event.target.value = "";
                }}
              />
              <input
                ref={folderInput}
                type="file"
                multiple
                hidden
                onChange={(event) => {
                  pickFiles(event.target.files, true);
                  event.target.value = "";
                }}
              />
            </>
          )}
        </div>

        {folderName !== null && (
          <form
            className="flex items-center gap-2 border-b border-white/[0.07] px-3 py-2"
            onSubmit={(event) => {
              event.preventDefault();
              void createFolder();
            }}
          >
            <FolderPlus className="h-4 w-4 text-white/50" />
            <input
              autoFocus
              value={folderName}
              onChange={(event) => setFolderName(event.target.value)}
              placeholder={tm("Folder name")}
              maxLength={200}
              className="min-w-0 flex-1 rounded-md border border-white/10 bg-transparent px-2 py-1 text-[12.5px] outline-none"
            />
            <button
              type="submit"
              className="rounded-md px-3 py-1 text-[12px]"
              style={{ backgroundColor: ACCENT, color: ON_ACCENT }}
            >
              {tm("Create")}
            </button>
            <button type="button" className={button} onClick={() => setFolderName(null)}>
              {tm("Cancel")}
            </button>
          </form>
        )}

        {(notice || (info?.scanner && !info.scanner.ok)) && (
          <div className="space-y-1 border-b border-amber-300/20 bg-amber-300/[0.06] px-3 py-2 text-[12px] text-amber-100">
            {info?.scanner && !info.scanner.ok && (
              <div className="flex items-center gap-2">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                {tm("The virus scanner is not available: uploads wait in quarantine until it is.")}
              </div>
            )}
            {notice && (
              <div className="flex items-center gap-2">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                <span className="min-w-0 flex-1 break-words">{notice}</span>
                <button
                  className={iconButton}
                  onClick={() => setNotice("")}
                  aria-label={tm("Close")}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            )}
          </div>
        )}

        <div className="relative min-h-0 flex-1 overflow-y-auto">
          {dragging && (
            <div
              className="pointer-events-none absolute inset-2 z-10 grid place-items-center rounded-lg border-2 border-dashed text-[13px]"
              style={{ borderColor: ACCENT_TEXT, backgroundColor: `${ACCENT}22` }}
            >
              {tm("Drop files to upload them here")}
            </div>
          )}
          {info === undefined ? (
            <div className="grid place-items-center p-6">
              <Loader2 className="h-5 w-5 animate-spin text-white/50" />
            </div>
          ) : info === null && !error ? (
            <p className="p-6 text-center text-[12.5px] text-white/55">
              {tm("The file store is not available on this AI Box.")}
            </p>
          ) : error ? (
            <p className="p-6 text-center text-[12.5px] text-rose-200">{error}</p>
          ) : (
            <table className="w-full text-left text-[12.5px]">
              <thead
                className="sticky top-0 text-[10.5px] uppercase text-white/40"
                style={{ backgroundColor: BG }}
              >
                <tr>
                  <th className="px-3 py-2 font-normal">{tm("Name")}</th>
                  <th className="hidden px-3 py-2 font-normal sm:table-cell">{tm("Size")}</th>
                  <th className="hidden px-3 py-2 font-normal md:table-cell">
                    {tm("Date modified")}
                  </th>
                  <th className="px-3 py-2 font-normal">{tm("State")}</th>
                  <th className="w-20 px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <tr
                    key={entry.path}
                    className="border-t border-white/[0.05] hover:bg-white/[0.03]"
                  >
                    <td className="max-w-0 px-3 py-2">
                      {entry.file ? (
                        <span className="flex min-w-0 items-center gap-2">
                          <FileText className="h-4 w-4 shrink-0 text-white/45" />
                          <span className="truncate">{entry.name}</span>
                        </span>
                      ) : (
                        <button
                          onClick={() => void load(entry.path)}
                          className="flex min-w-0 items-center gap-2 text-left hover:text-brand-soft"
                        >
                          <Folder className="h-4 w-4 shrink-0" style={{ color: ACCENT_TEXT }} />
                          <span className="truncate">{entry.name}</span>
                        </button>
                      )}
                    </td>
                    <td className="hidden whitespace-nowrap px-3 py-2 text-white/60 sm:table-cell">
                      {entry.file ? formatSize(entry.size) : ""}
                    </td>
                    <td className="hidden whitespace-nowrap px-3 py-2 text-white/60 md:table-cell">
                      {entry.modified ? new Date(entry.modified).toLocaleString() : ""}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-[11.5px]">
                      {entry.file &&
                        (!entry.stored ? (
                          <span className="text-white/50">
                            {tm("Indexed only, original not on the box")}
                          </span>
                        ) : entry.indexed ? (
                          <span className="text-emerald-300">{tm("Searchable")}</span>
                        ) : (
                          <span className="text-amber-200">{tm("Not searchable yet")}</span>
                        ))}
                    </td>
                    <td className="px-2 py-1.5">
                      <div className="flex justify-end gap-0.5">
                        {entry.file && entry.stored && (
                          <button
                            className={iconButton}
                            onClick={() => void download(entry)}
                            aria-label={tm("Download")}
                            title={tm("Download")}
                          >
                            <Download className="h-3.5 w-3.5" />
                          </button>
                        )}
                        {writable && (
                          <button
                            className={iconButton}
                            onClick={() => setConfirmDelete(entry)}
                            aria-label={tm("Delete")}
                            title={tm("Delete")}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
                {!entries.length && !listing && (
                  <tr>
                    <td colSpan={5} className="px-3 py-8 text-center text-white/45">
                      {writable
                        ? tm("This folder is empty. Upload files or drop them here.")
                        : tm("This folder is empty.")}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>

        {(active || uploads.length > 0) && (
          <div
            className="max-h-[32%] shrink-0 overflow-y-auto border-t border-white/10 px-3 py-2"
            style={{ backgroundColor: HEAD }}
          >
            <div className="mb-1 text-[10.5px] uppercase text-white/40">{tm("Uploads")}</div>
            {active && (
              <div className="flex items-center gap-2 py-1 text-[12px]">
                <Loader2
                  className="h-3.5 w-3.5 shrink-0 animate-spin"
                  style={{ color: ACCENT_TEXT }}
                />
                <span className="min-w-0 flex-1 truncate">{active.name}</span>
                <span className="whitespace-nowrap text-white/55">
                  {tm("Sending {n}%").replace(
                    "{n}",
                    String(active.total ? Math.round((active.sent / active.total) * 100) : 0),
                  )}
                </span>
                <button
                  className={iconButton}
                  onClick={() => abort.current?.abort()}
                  aria-label={tm("Cancel")}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            )}
            {uploads.map((upload) => (
              <div key={upload.id} className="flex items-center gap-2 py-1 text-[12px]">
                {STORE_FINAL_STATES.has(upload.status) ? (
                  <span className="h-3.5 w-3.5 shrink-0" />
                ) : (
                  <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-white/50" />
                )}
                <span className="min-w-0 flex-1 truncate" title={upload.path}>
                  {baseName(upload.path)}
                </span>
                <span className={`whitespace-nowrap ${statusTone(upload.status)}`}>
                  {statusText(upload, tm)}
                  {upload.signature ? ` (${upload.signature})` : ""}
                </span>
                {upload.status !== "indexing" && upload.status !== "index_pending" && (
                  <button
                    className={iconButton}
                    onClick={() => void dismiss(upload)}
                    aria-label={
                      STORE_FINAL_STATES.has(upload.status)
                        ? tm("Remove from the list")
                        : tm("Cancel")
                    }
                    title={
                      STORE_FINAL_STATES.has(upload.status)
                        ? tm("Remove from the list")
                        : tm("Cancel")
                    }
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {(confirmDelete || confirmOverwrite) && (
          <div className="absolute inset-0 z-20 grid place-items-center bg-black/60 p-4">
            <div
              role="alertdialog"
              className="w-full max-w-md rounded-lg border border-white/10 p-4 shadow-2xl"
              style={{ backgroundColor: PANEL }}
            >
              {confirmDelete ? (
                <>
                  <p className="text-[13px] text-white">
                    {(confirmDelete.file
                      ? tm("Delete the file “{name}”?")
                      : tm("Delete the folder “{name}” and everything in it?")
                    ).replace("{name}", confirmDelete.name)}
                  </p>
                  <p className="mt-1.5 text-[12px] text-white/55">
                    {tm(
                      "It is removed from the AI Box and from the search index. This cannot be undone.",
                    )}
                  </p>
                </>
              ) : (
                <>
                  <p className="text-[13px] text-white">
                    {tm("{n} file(s) already exist in this folder. Replace them?").replace(
                      "{n}",
                      String(confirmOverwrite?.conflicts.length ?? 0),
                    )}
                  </p>
                  <p className="mt-1.5 break-words text-[12px] text-white/55">
                    {confirmOverwrite?.conflicts.slice(0, 5).join(", ")}
                    {(confirmOverwrite?.conflicts.length ?? 0) > 5 ? ", …" : ""}
                  </p>
                </>
              )}
              <div className="mt-4 flex justify-end gap-2">
                {confirmOverwrite && (
                  <button
                    className={button}
                    onClick={() => {
                      const pending = confirmOverwrite;
                      setConfirmOverwrite(null);
                      const conflicts = new Set(pending.conflicts);
                      void runUploads(
                        pending.files.filter((p) => !conflicts.has(p.rel)),
                        [],
                      );
                    }}
                  >
                    {tm("Skip them")}
                  </button>
                )}
                <button
                  className={button}
                  onClick={() => {
                    setConfirmDelete(null);
                    setConfirmOverwrite(null);
                  }}
                >
                  {tm("Cancel")}
                </button>
                <button
                  className="rounded-md px-3 py-1.5 text-[12px]"
                  style={
                    confirmDelete
                      ? { backgroundColor: "#BE123C", color: "#FFFFFF" }
                      : { backgroundColor: ACCENT, color: ON_ACCENT }
                  }
                  onClick={() => {
                    if (confirmDelete) {
                      void remove(confirmDelete);
                      return;
                    }
                    const pending = confirmOverwrite;
                    setConfirmOverwrite(null);
                    if (pending) void runUploads(pending.files, pending.conflicts);
                  }}
                >
                  {confirmDelete ? tm("Delete") : tm("Replace")}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
