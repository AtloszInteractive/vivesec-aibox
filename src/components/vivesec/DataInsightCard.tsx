import { Database, HardDrive, Cpu, ShieldCheck, FileText, Lock, AlertTriangle } from "lucide-react";
import { useLang } from "./i18n";
import { getDataInsightCopy } from "./data-insight";
import type { PlatformInsight, InsightValue } from "@/lib/api/rag.functions";

const LIME = "#C6F24E";
const PANEL = "#2A2F35";
const PANEL_HI = "#333941";

function fmtNumber(n: number): string {
  return n.toLocaleString("en-US").replace(/,/g, " ");
}

function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

const num = (v: InsightValue): string | null =>
  v === null || v === undefined ? null : typeof v === "number" ? fmtNumber(v) : String(v);

function KpiCard({ icon: Icon, label, value, note }: {
  icon: typeof Database;
  label: string;
  value: string | null;
  note?: string;
}) {
  const { lang } = useLang();
  const c = getDataInsightCopy(lang);
  const missing = value === null;
  return (
    <div
      className="flex min-w-0 flex-col justify-between rounded-xl border border-white/[0.07] p-3"
      style={{ backgroundColor: PANEL_HI }}
    >
      <div className="flex items-center gap-1.5 text-[11px] leading-tight text-white/55">
        <Icon className="h-3.5 w-3.5 shrink-0" style={{ color: missing ? "rgba(255,255,255,0.3)" : LIME }} />
        <span className="break-words">{label}</span>
      </div>
      <div className={`mt-2 break-words text-lg font-bold leading-none ${missing ? "italic text-white/30" : "text-white"}`}>
        {missing ? c.noData : value}
      </div>
      {note && !missing && <div className="mt-1 text-[9px] leading-tight text-white/40">{note}</div>}
    </div>
  );
}

/** One label/value line. A missing value is reported, never filled in. */
function Row({ label, value }: { label: string; value: string | null }) {
  const { lang } = useLang();
  const c = getDataInsightCopy(lang);
  return (
    <div className="flex items-start justify-between gap-3 py-1 text-[12px]">
      <span className="min-w-0 text-white/60">{label}</span>
      {value === null ? (
        <span className="shrink-0 italic text-white/30" title={c.noDataHint}>{c.noData}</span>
      ) : (
        <span className="shrink-0 font-semibold text-white tabular-nums">{value}</span>
      )}
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <div className="mt-3 mb-1 text-[11px] font-semibold uppercase tracking-wide text-white/40">{children}</div>;
}

export function DataInsightCard({
  insight,
  loading,
  error,
}: {
  insight: PlatformInsight | null;
  loading?: boolean;
  error?: string;
}) {
  const { lang } = useLang();
  const c = getDataInsightCopy(lang);

  if (loading || !insight) {
    return (
      <div className="rounded-2xl border border-white/[0.07] p-6 text-center text-[12px] text-white/50" style={{ backgroundColor: PANEL }}>
        {error ? (
          <span className="text-rose-200/80">
            <AlertTriangle className="mr-1 inline h-4 w-4" />
            {c.loadFailed} {error}
          </span>
        ) : (
          c.loading
        )}
      </div>
    );
  }

  const { box, ai } = insight;
  const unit = c.filesUnit ? ` ${c.filesUnit}` : "";

  return (
    <div className="overflow-hidden rounded-2xl border border-white/[0.07]" style={{ backgroundColor: PANEL }}>
      {/* Header */}
      <div className="border-b border-white/[0.07] px-4 py-3">
        <div className="flex items-center gap-2">
          <Database className="h-4 w-4" style={{ color: LIME }} />
          <span className="text-[13px] font-bold leading-tight text-white">{c.cardTitle}</span>
        </div>
        <div className="mt-2 space-y-0.5 text-[11px] text-white/50">
          <div>
            <span className="text-white/70">{c.classification}:</span> {c.classificationValue}
          </div>
          <div>
            <span className="text-white/70">{c.networkMode}:</span> {c.networkModeValue}
          </div>
        </div>
      </div>

      {/* Top KPI row */}
      <div className="grid grid-cols-2 gap-2 p-3 sm:grid-cols-4">
        <KpiCard icon={ShieldCheck} label={c.kpiDrives} value={num(box.drives)} />
        <KpiCard icon={FileText} label={c.kpiSecuredFiles} value={num(box.files)} note={c.encryptedNote} />
        <KpiCard
          icon={HardDrive}
          label={c.kpiDataSize}
          value={typeof box.dataSizeBytes === "number" ? fmtBytes(box.dataSizeBytes) : null}
        />
        <KpiCard icon={Lock} label={c.kpiIndexedChunks} value={num(ai.chunks)} note={c.auditedNote} />
      </div>

      {/* Split view: ViVeSecBox (left) vs AI Box (right) */}
      <div className="grid grid-cols-1 gap-px border-t border-white/[0.07] bg-white/[0.06] md:grid-cols-2">
        {/* ViVeSecBox — storage side */}
        <div className="p-4" style={{ backgroundColor: PANEL }}>
          <div className="mb-2 flex items-center gap-1.5 border-b border-white/[0.07] pb-2 text-[12px] font-bold text-white">
            <HardDrive className="h-3.5 w-3.5" style={{ color: LIME }} />
            {c.boxTitle}
          </div>
          <Row label={c.kpiVvsUsers} value={num(box.users)} />
          <Row label={c.kpiMessages} value={num(box.messages)} />
          <Row label={c.kpiFolders} value={num(box.folders)} />
          <Row label={c.storageMode} value={num(box.storageMode)} />
          <Row label={c.wsFs} value={box.wsFsConnected === null ? null : box.wsFsConnected ? c.wsFsOn : c.wsFsOff} />

          <SectionTitle>{c.backupTitle}</SectionTitle>
          <Row label={c.lastBackupTime} value={num(box.lastBackupTime)} />
          <Row label={c.lastBackupSize} value={num(box.lastBackupSize)} />

          {box.perDrive.length > 0 && (
            <>
              <SectionTitle>{c.perDrive}</SectionTitle>
              {box.perDrive.map((d) => (
                <Row key={d.name} label={d.name} value={`${fmtNumber(d.files)}${unit} · ${fmtBytes(d.bytes)}`} />
              ))}
            </>
          )}

          {box.types.length > 0 && (
            <>
              <SectionTitle>{c.fileDistribution}</SectionTitle>
              {box.types.slice(0, 8).map((t) => (
                <Row key={t.ext} label={`.${t.ext}`} value={`${fmtNumber(t.files)}${unit} · ${fmtBytes(t.bytes)}`} />
              ))}
            </>
          )}
        </div>

        {/* AI Box — index + runtime */}
        <div className="p-4" style={{ backgroundColor: PANEL }}>
          <div className="mb-2 flex items-center gap-1.5 border-b border-white/[0.07] pb-2 text-[12px] font-bold text-white">
            <Cpu className="h-3.5 w-3.5" style={{ color: LIME }} />
            {c.aiTitle}
          </div>

          <SectionTitle>{c.indexTitle}</SectionTitle>
          <Row label={c.docs} value={num(ai.documents)} />
          <Row label={c.pages} value={num(ai.pages)} />
          <Row label={c.chunks} value={num(ai.chunks)} />
          <Row label={c.corpora} value={num(ai.corpora)} />

          <SectionTitle>{c.runtime}</SectionTitle>
          <Row label={c.activeSessions} value={num(ai.activeSessions)} />
          <Row label={c.generatedFiles} value={num(ai.generatedFiles)} />
          <Row label={c.features} value={num(ai.features)} />
          <Row label={c.watchdog} value={num(ai.watchdogSeconds)} />
          <Row label={c.totalRequests} value={num(ai.totalRequests)} />

          <SectionTitle>{c.computeProfile}</SectionTitle>
          <Row label={c.llmEngine} value={num(ai.llmModel)} />
          <Row label={c.tokenSpeed} value={num(ai.tokensPerSec)} />
          <Row label={c.gpuAlloc} value={num(ai.gpuPartitions)} />
          <Row label={c.powerDraw} value={typeof ai.powerWatts === "number" ? `${ai.powerWatts} W` : null} />
        </div>
      </div>

      {/* Audit footer */}
      <div className="border-t border-white/[0.07] px-4 py-3 text-[10px] leading-relaxed text-white/45">
        <div>
          <span className="text-white/65">{c.auditTitle}</span>
        </div>
        <div className="mt-1">
          <span className="text-white/60">{c.sources}:</span> ViVeSecBox drive sync · AI Box adapter status
        </div>
      </div>

      {/* Air-gapped stamp */}
      <div
        className="flex items-center gap-1.5 border-t border-white/[0.07] px-4 py-2 text-[10px]"
        style={{ color: LIME, backgroundColor: `${LIME}0d` }}
      >
        <Lock className="h-3 w-3 shrink-0" />
        {c.stamp}
      </div>
    </div>
  );
}
