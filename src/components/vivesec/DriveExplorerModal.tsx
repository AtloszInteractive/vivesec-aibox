import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Check,
  ChevronDown,
  ChevronRight,
  ClipboardPaste,
  Copy,
  FileBarChart,
  FileCode,
  FileSpreadsheet,
  FileText,
  Folder,
  FolderInput,
  FolderPlus,
  Grid2x2,
  HardDrive,
  Info,
  LayoutList,
  ListFilter,
  Loader2,
  Lock,
  Menu,
  Pencil,
  RefreshCw,
  Rows3,
  Search,
  Scissors,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useLang } from "./i18n";

const LIME = "#C6F24E";
const PANEL = "#23272B";
const BG = "#1B1F22";
const HEAD = "#15181B";

export type ExplorerFile = {
  id: string;
  name: string;
  type: "pdf" | "docx" | "xlsx" | "csv" | "md" | "txt";
  size: string;
  modified: string;
  folder?: string;
};

export type ExplorerFolder = { path: string; name: string };

type SortKey = "name" | "modified" | "type" | "size";
type ViewMode = "details" | "compact" | "grid";

function sizeToBytes(value: string) {
  const amount = Number.parseFloat(value) || 0;
  const unit = value.toUpperCase();
  if (unit.includes("GB")) return amount * 1024 * 1024 * 1024;
  if (unit.includes("MB")) return amount * 1024 * 1024;
  if (unit.includes("KB")) return amount * 1024;
  return amount;
}

function iconFor(type: ExplorerFile["type"]) {
  if (type === "xlsx" || type === "csv") return FileSpreadsheet;
  if (type === "md") return FileCode;
  if (type === "txt") return FileBarChart;
  return FileText;
}

function tintFor(type: ExplorerFile["type"]) {
  if (type === "xlsx" || type === "csv") return "bg-emerald-400/10 text-emerald-300";
  if (type === "docx") return "bg-sky-400/10 text-sky-300";
  if (type === "md") return "bg-violet-400/10 text-violet-300";
  if (type === "txt") return "bg-amber-400/10 text-amber-300";
  return "bg-rose-400/10 text-rose-300";
}

export function DriveExplorerModal({
  files,
  folders,
  drive,
  driveName,
  cwd,
  search,
  searching,
  searchTruncated,
  loading,
  error,
  onSearch,
  onNavigate,
  onRefresh,
  onOpen,
  onClose,
}: {
  files: ExplorerFile[];
  folders: ExplorerFolder[];
  drive: string;
  driveName: string;
  cwd: string;
  search: string;
  searching: boolean;
  searchTruncated: boolean;
  loading: boolean;
  error: string;
  onSearch: (value: string) => void;
  onNavigate: (path: string) => void;
  onRefresh: () => void;
  onOpen: (file: ExplorerFile) => void;
  onClose: () => void;
}) {
  const { tm } = useLang();
  const [mounted, setMounted] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [view, setView] = useState<ViewMode>("details");
  const [sort, setSort] = useState<SortKey>("name");
  const [ascending, setAscending] = useState(true);
  const [sortOpen, setSortOpen] = useState(false);
  const [viewOpen, setViewOpen] = useState(false);
  const [treeOpen, setTreeOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(true);
  const [history, setHistory] = useState(() => [cwd || drive]);
  const [historyIndex, setHistoryIndex] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setMounted(true);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  useEffect(() => {
    setSelected((current) => current.filter((id) => files.some((file) => file.id === id)));
  }, [files]);

  const sortedFiles = useMemo(() => {
    const direction = ascending ? 1 : -1;
    return [...files].sort((left, right) => {
      if (sort === "size") return direction * (sizeToBytes(left.size) - sizeToBytes(right.size));
      return direction * String(left[sort]).localeCompare(String(right[sort]));
    });
  }, [files, sort, ascending]);

  const activeFile = selected.length === 1 ? files.find((file) => file.id === selected[0]) : undefined;
  const driveRoot = drive.replace(/\/+$/, "");
  const currentPath = cwd || driveRoot;
  const relativePath = currentPath.startsWith(driveRoot) ? currentPath.slice(driveRoot.length) : "";
  const crumbs = relativePath.split("/").filter(Boolean);
  const parent = crumbs.length ? currentPath.slice(0, currentPath.lastIndexOf("/")) : "";

  const navigate = (path: string) => {
    const nextHistory = [...history.slice(0, historyIndex + 1), path];
    setHistory(nextHistory);
    setHistoryIndex(nextHistory.length - 1);
    setSelected([]);
    setTreeOpen(false);
    onSearch("");
    onNavigate(path);
  };

  const travel = (index: number) => {
    const path = history[index];
    if (!path) return;
    setHistoryIndex(index);
    setSelected([]);
    onSearch("");
    onNavigate(path);
  };

  const toggleFile = (id: string, additive: boolean) => {
    setSelected((current) => {
      if (!additive) return [id];
      return current.includes(id) ? current.filter((item) => item !== id) : [...current, id];
    });
  };

  const allSelected = sortedFiles.length > 0 && sortedFiles.every((file) => selected.includes(file.id));
  const navButton = "grid h-8 w-8 shrink-0 place-items-center rounded-md text-white/70 transition hover:bg-white/10 disabled:opacity-30";
  const toolbarButton = "inline-flex shrink-0 items-center gap-1.5 rounded-md border border-white/10 px-2.5 py-1.5 text-[11.5px] text-white/75 transition hover:bg-white/[0.07] disabled:cursor-not-allowed disabled:text-white/30 disabled:hover:bg-transparent";
  const unavailableTitle = tm("Requires file-management support from the AI Box");

  if (!mounted) return null;

  return createPortal(
    <div
      className="fixed inset-0 flex items-stretch justify-center sm:items-center sm:p-4"
      style={{ zIndex: 210 }}
    >
      <button className="absolute inset-0 bg-black/75 backdrop-blur-sm" onClick={onClose} aria-label={tm("Close explorer")} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={tm("ViVeSecBox File Explorer")}
        className="relative flex h-full w-full flex-col overflow-hidden border border-white/10 shadow-2xl sm:h-[92vh] sm:max-w-6xl sm:rounded-lg"
        style={{ backgroundColor: BG, paddingBottom: "env(safe-area-inset-bottom)" }}
      >
        <div className="flex items-center gap-2 border-b border-white/10 px-3 py-2.5" style={{ backgroundColor: HEAD }}>
          <button onClick={() => setTreeOpen((open) => !open)} className={`${navButton} lg:hidden`} aria-label={tm("Toggle navigation pane")}>
            <Menu className="h-4 w-4" />
          </button>
          <HardDrive className="h-4 w-4" style={{ color: LIME }} />
          <div className="min-w-0 flex-1 truncate text-[13px] font-semibold">{tm("ViVeSecBox File Explorer")}</div>
          <span className="hidden items-center gap-1 rounded-full border border-white/10 px-2 py-0.5 text-[10px] text-white/50 sm:inline-flex">
            <Lock className="h-3 w-3" /> {tm("Air-gapped")}
          </span>
          <button onClick={onClose} className={navButton} aria-label={tm("Close explorer")}><X className="h-4 w-4" /></button>
        </div>

        <div className="flex items-center gap-1.5 border-b border-white/[0.07] px-3 py-2">
          <button className={navButton} disabled={historyIndex === 0} onClick={() => travel(historyIndex - 1)} aria-label={tm("Back")}><ArrowLeft className="h-4 w-4" /></button>
          <button className={navButton} disabled={historyIndex >= history.length - 1} onClick={() => travel(historyIndex + 1)} aria-label={tm("Forward")}><ArrowRight className="h-4 w-4" /></button>
          <button className={navButton} disabled={!parent} onClick={() => parent && navigate(parent)} aria-label={tm("Up one level")}><ArrowUp className="h-4 w-4" /></button>
          <button className={navButton} disabled={loading} onClick={onRefresh} aria-label={tm("Refresh")}><RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} /></button>

          <div className="ml-1 flex min-w-0 flex-1 items-center overflow-x-auto rounded-md border border-white/10 px-2 py-1.5" style={{ backgroundColor: PANEL }}>
            <button onClick={() => navigate(driveRoot)} className="shrink-0 rounded px-1.5 py-0.5 text-[11.5px] text-white/75 hover:bg-white/10">{driveName || tm("DriveRoot")}</button>
            {crumbs.map((crumb, index) => (
              <span key={`${crumb}-${index}`} className="flex shrink-0 items-center">
                <ChevronRight className="h-3 w-3 text-white/30" />
                <button onClick={() => navigate(`${driveRoot}/${crumbs.slice(0, index + 1).join("/")}`)} className="rounded px-1.5 py-0.5 text-[11.5px] text-white/75 hover:bg-white/10">{crumb}</button>
              </span>
            ))}
          </div>

          <div className="flex w-[40%] max-w-67.5 items-center gap-1.5 rounded-md border border-white/10 px-2 py-1.5" style={{ backgroundColor: PANEL }}>
            <Search className="h-3.5 w-3.5 shrink-0 text-white/40" />
            <input ref={searchRef} value={search} onChange={(event) => onSearch(event.target.value)} placeholder={tm("Search this drive")} className="w-full bg-transparent text-[12px] outline-none placeholder:text-white/35" />
            {search && <button onClick={() => onSearch("")} aria-label={tm("Clear search")}><X className="h-3.5 w-3.5 text-white/45" /></button>}
          </div>
        </div>

        <div className="flex items-center gap-1.5 overflow-x-auto border-b border-white/[0.07] px-3 py-2">
          <button className={toolbarButton} disabled title={unavailableTitle}>
            <FolderPlus className="h-3.5 w-3.5" /> {tm("New folder")}
          </button>
          <button className={toolbarButton} disabled title={unavailableTitle}>
            <Upload className="h-3.5 w-3.5" /> {tm("Upload")}
          </button>
          <span className="h-5 w-px shrink-0 bg-white/10" />
          <button className={toolbarButton} disabled title={unavailableTitle}>
            <Scissors className="h-3.5 w-3.5" /> {tm("Cut")}
          </button>
          <button className={toolbarButton} disabled title={unavailableTitle}>
            <Copy className="h-3.5 w-3.5" /> {tm("Copy")}
          </button>
          <button className={toolbarButton} disabled title={unavailableTitle}>
            <ClipboardPaste className="h-3.5 w-3.5" /> {tm("Paste")}
          </button>
          <button className={toolbarButton} disabled title={unavailableTitle}>
            <FolderInput className="h-3.5 w-3.5" /> {tm("Move to")}
          </button>
          <button className={toolbarButton} disabled title={unavailableTitle}>
            <Pencil className="h-3.5 w-3.5" /> {tm("Rename")}
          </button>
          <button className={toolbarButton} disabled title={unavailableTitle}>
            <Trash2 className="h-3.5 w-3.5" /> {tm("Delete")}
          </button>
          <span className="h-5 w-px shrink-0 bg-white/10" />
          <button className={toolbarButton} onClick={() => setSelected(allSelected ? [] : sortedFiles.map((file) => file.id))} disabled={!sortedFiles.length}>
            <Check className="h-3.5 w-3.5" /> {allSelected ? tm("Clear selection") : tm("Select all")}
          </button>
          <span className="h-5 w-px shrink-0 bg-white/10" />
          <div className="relative shrink-0">
            <button className={toolbarButton} onClick={() => { setSortOpen((open) => !open); setViewOpen(false); }}><ListFilter className="h-3.5 w-3.5" /> {tm("Sort")} <ChevronDown className="h-3 w-3" /></button>
            {sortOpen && (
              <div className="absolute left-0 top-full z-30 mt-1 w-44 overflow-hidden rounded-md border border-white/10 shadow-2xl" style={{ backgroundColor: PANEL }}>
                {([['name', tm('Name')], ['modified', tm('Date modified')], ['type', tm('Type')], ['size', tm('Size')]] as [SortKey, string][]).map(([key, label]) => (
                  <button key={key} onClick={() => { if (sort === key) setAscending((value) => !value); else { setSort(key); setAscending(true); } setSortOpen(false); }} className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] text-white/80 hover:bg-white/[0.07]">
                    {sort === key ? <Check className="h-3.5 w-3.5" style={{ color: LIME }} /> : <span className="w-3.5" />} {label}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="relative shrink-0">
            <button className={toolbarButton} onClick={() => { setViewOpen((open) => !open); setSortOpen(false); }}>{view === 'details' ? <LayoutList className="h-3.5 w-3.5" /> : view === 'compact' ? <Rows3 className="h-3.5 w-3.5" /> : <Grid2x2 className="h-3.5 w-3.5" />} {tm("View")} <ChevronDown className="h-3 w-3" /></button>
            {viewOpen && (
              <div className="absolute left-0 top-full z-30 mt-1 w-40 overflow-hidden rounded-md border border-white/10 shadow-2xl" style={{ backgroundColor: PANEL }}>
                {([['details', LayoutList, tm('Details')], ['compact', Rows3, tm('Compact')], ['grid', Grid2x2, tm('Grid')]] as const).map(([mode, Icon, label]) => (
                  <button key={mode} onClick={() => { setView(mode); setViewOpen(false); }} className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] text-white/80 hover:bg-white/[0.07]"><Icon className="h-3.5 w-3.5" /> {label}</button>
                ))}
              </div>
            )}
          </div>
          <button className={`${toolbarButton} ml-auto`} onClick={() => setDetailsOpen((open) => !open)}><Info className="h-3.5 w-3.5" /> {tm("Details pane")}</button>
        </div>

        <div className="flex min-h-0 flex-1">
          <aside className={`${treeOpen ? "absolute inset-y-11 left-0 z-40 flex w-64" : "hidden"} shrink-0 flex-col border-r border-white/[0.07] p-2 lg:static lg:flex lg:w-52`} style={{ backgroundColor: HEAD }}>
            <div className="px-2 pb-2 pt-1 text-[10px] uppercase text-white/35">{tm("Locations")}</div>
            <button onClick={() => navigate(driveRoot)} className="flex items-center gap-2 rounded-md bg-white/8 px-2.5 py-2 text-left text-[12px] text-white">
              <HardDrive className="h-3.5 w-3.5" style={{ color: LIME }} /><span className="truncate">{driveName || tm("DriveRoot")}</span>
            </button>
            <div className="mt-auto flex items-center gap-2 border-t border-white/[0.07] px-2 pt-3 text-[10px] leading-relaxed text-white/40"><ShieldCheck className="h-4 w-4 shrink-0" style={{ color: LIME }} />{tm("Searched locally on ViVeSecBox")}</div>
          </aside>

          <main className="min-w-0 flex-1 overflow-y-auto p-2 sm:p-3">
            {searching && <div className="mb-2 px-1 text-[11px] text-white/45">{files.length} {tm("matching files in this drive")}{searchTruncated ? ` · ${tm("first results only")}` : ""}</div>}
            {loading && !files.length && <div className="grid h-full place-items-center text-xs text-white/45"><div className="text-center"><Loader2 className="mx-auto mb-2 h-6 w-6 animate-spin" />{tm("Loading files from the box…")}</div></div>}
            {!loading && error && <div className="grid h-full place-items-center px-4 text-center text-xs text-rose-200/80"><div>{tm("Could not read the drive from the box.")}<div className="mt-1 text-[11px] text-white/40">{error}</div><button onClick={onRefresh} className="mt-3 rounded-md border border-white/15 px-3 py-1.5 text-white/80">{tm("Retry")}</button></div></div>}
            {!loading && !error && !searching && folders.length === 0 && files.length === 0 && <div className="grid h-full place-items-center text-xs text-white/40">{tm("This folder is empty.")}</div>}
            {!loading && !error && searching && files.length === 0 && <div className="grid h-full place-items-center text-xs text-white/40">{tm("No files match your search.")}</div>}

            {!searching && folders.length > 0 && (
              <div className="mb-3 grid grid-cols-1 gap-1 sm:grid-cols-2 xl:grid-cols-3">
                {folders.map((folder) => <button key={folder.path} onDoubleClick={() => navigate(folder.path)} onClick={() => navigate(folder.path)} className="flex items-center gap-3 rounded-md px-2.5 py-2 text-left hover:bg-white/5"><span className="grid h-9 w-9 place-items-center rounded-md bg-sky-400/10"><Folder className="h-4 w-4 text-sky-300" /></span><span className="min-w-0 flex-1 truncate text-[12.5px]">{folder.name}</span><ChevronRight className="h-3.5 w-3.5 text-white/25" /></button>)}
              </div>
            )}

            {view === "details" && sortedFiles.length > 0 && (
              <div className="min-w-140">
                <div className="grid grid-cols-[32px_minmax(220px,1fr)_130px_105px_150px] border-b border-white/[0.07] px-2 py-1.5 text-[10px] uppercase text-white/35"><span /><span>{tm("Name")}</span><span>{tm("Type")}</span><span>{tm("Size")}</span><span>{tm("Date modified")}</span></div>
                {sortedFiles.map((file) => {
                  const Icon = iconFor(file.type);
                  const isSelected = selected.includes(file.id);
                  return <button key={file.id} onClick={(event) => toggleFile(file.id, event.ctrlKey || event.metaKey)} onDoubleClick={() => onOpen(file)} className={`grid w-full grid-cols-[32px_minmax(220px,1fr)_130px_105px_150px] items-center rounded-md px-2 py-2 text-left text-[12px] ${isSelected ? "bg-lime-300/10 ring-1 ring-inset ring-lime-300/30" : "hover:bg-white/4"}`}><span className={`grid h-4 w-4 place-items-center rounded border ${isSelected ? "border-lime-300 bg-lime-300 text-black" : "border-white/20"}`}>{isSelected && <Check className="h-3 w-3" />}</span><span className="flex min-w-0 items-center gap-2"><span className={`grid h-8 w-8 shrink-0 place-items-center rounded-md ${tintFor(file.type)}`}><Icon className="h-4 w-4" /></span><span className="truncate font-medium">{file.name}</span></span><span className="text-white/50">{file.type.toUpperCase()}</span><span className="text-white/50">{file.size}</span><span className="text-white/50">{file.modified}</span></button>;
                })}
              </div>
            )}

            {view === "compact" && <div className="grid grid-cols-1 gap-1 sm:grid-cols-2 xl:grid-cols-3">{sortedFiles.map((file) => { const Icon = iconFor(file.type); const isSelected = selected.includes(file.id); return <button key={file.id} onClick={(event) => toggleFile(file.id, event.ctrlKey || event.metaKey)} onDoubleClick={() => onOpen(file)} className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-left ${isSelected ? "bg-lime-300/10 ring-1 ring-inset ring-lime-300/30" : "hover:bg-white/4"}`}><Icon className={`h-4 w-4 shrink-0 ${tintFor(file.type).split(' ').pop()}`} /><span className="truncate text-[12px]">{file.name}</span></button>; })}</div>}

            {view === "grid" && <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">{sortedFiles.map((file) => { const Icon = iconFor(file.type); const isSelected = selected.includes(file.id); return <button key={file.id} onClick={(event) => toggleFile(file.id, event.ctrlKey || event.metaKey)} onDoubleClick={() => onOpen(file)} className={`relative min-h-32 rounded-md border p-3 text-left ${isSelected ? "border-lime-300/50 bg-lime-300/10" : "border-white/10 bg-white/2.5 hover:bg-white/6"}`}><span className={`mb-3 grid h-10 w-10 place-items-center rounded-md ${tintFor(file.type)}`}><Icon className="h-5 w-5" /></span><div className="wrap-break-word text-[12px] font-medium leading-snug">{file.name}</div><div className="mt-1 text-[10.5px] text-white/40">{file.size}</div></button>; })}</div>}
          </main>

          {detailsOpen && (
            <aside className="hidden w-60 shrink-0 border-l border-white/[0.07] p-4 md:block" style={{ backgroundColor: HEAD }}>
              <div className="mb-4 text-[10px] uppercase text-white/35">{tm("Details")}</div>
              {activeFile ? (() => { const Icon = iconFor(activeFile.type); return <div><span className={`mx-auto mb-3 grid h-14 w-14 place-items-center rounded-md ${tintFor(activeFile.type)}`}><Icon className="h-7 w-7" /></span><div className="wrap-break-word text-center text-[12.5px] font-medium">{activeFile.name}</div><dl className="mt-5 space-y-3 text-[11px]"><div><dt className="text-white/35">{tm("Location")}</dt><dd className="mt-0.5 wrap-break-word text-white/70">{activeFile.folder || driveName}</dd></div><div><dt className="text-white/35">{tm("Type")}</dt><dd className="mt-0.5 text-white/70">{activeFile.type.toUpperCase()}</dd></div><div><dt className="text-white/35">{tm("Size")}</dt><dd className="mt-0.5 text-white/70">{activeFile.size}</dd></div><div><dt className="text-white/35">{tm("Modified")}</dt><dd className="mt-0.5 text-white/70">{activeFile.modified}</dd></div></dl></div>; })() : <div className="text-[11.5px] leading-relaxed text-white/40">{selected.length > 1 ? `${selected.length} ${tm("items selected")}` : tm("Select a file to view its details.")}</div>}
            </aside>
          )}
        </div>

        <div className="flex min-h-11 items-center gap-3 border-t border-white/[0.07] px-3 py-2" style={{ backgroundColor: HEAD }}>
          <span className="text-[11px] text-white/40">{folders.length} {tm("folders")} · {files.length} {tm("files")}</span>
          {selected.length > 0 && <span className="text-[11px] text-white/55">{selected.length} {tm("selected")}</span>}
          <button onClick={onClose} className="ml-auto rounded-md border border-white/10 px-3 py-1.5 text-[11.5px] text-white/75 hover:bg-white/10">{tm("Cancel")}</button>
          <button onClick={() => activeFile && onOpen(activeFile)} disabled={!activeFile} className="rounded-md px-3 py-1.5 text-[11.5px] font-semibold disabled:opacity-35" style={{ backgroundColor: LIME, color: HEAD }}>{tm("Open")}</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}