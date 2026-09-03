import { useState, type CSSProperties } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ShieldCheck,
  Cpu,
  Server,
  Lock,
  Zap,
  CheckCircle2,
  Loader2,
  HardDrive,
  Layers,
} from "lucide-react";
import { useLang } from "./i18n";
import { getHardwareCopy } from "./hardware-data";

const LIME = "#C6F24E";
const PANEL = "#2A2F35";

function renderInline(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) =>
    p.startsWith("**") && p.endsWith("**") ? (
      <strong key={i} className="font-semibold text-white">{p.slice(2, -2)}</strong>
    ) : (
      <span key={i}>{p}</span>
    ),
  );
}

function Badge({ label }: { label: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium"
      style={{ borderColor: `${LIME}55`, color: LIME, backgroundColor: `${LIME}12` }}
    >
      {label}
    </span>
  );
}

export function HardwareCard({ onModuleExplain }: { onModuleExplain: (text: string) => void }) {
  const { lang } = useLang();
  const c = getHardwareCopy(lang);
  const [layer, setLayer] = useState<"l1" | "l2">("l1");
  const [config, setConfig] = useState<"standard" | "enterprise">("standard");
  const [scan, setScan] = useState<"idle" | "running" | "done">("idle");

  function runCheck() {
    if (scan === "running") return;
    setScan("running");
    setTimeout(() => setScan("done"), 3000);
  }

  const activeConfig = c.configs.find((x) => x.id === config) ?? c.configs[0];

  return (
    <div className="overflow-hidden rounded-2xl border border-white/[0.07]" style={{ backgroundColor: PANEL }}>
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-white/[0.07] px-4 py-2.5">
        <Layers className="h-4 w-4" style={{ color: LIME }} />
        <div className="text-[13px] font-semibold">{c.cardTitle}</div>
      </div>

      {/* Layer tabs */}
      <div className="flex gap-1 p-3 pb-0">
        {([
          { id: "l1" as const, label: c.l1Tab, icon: ShieldCheck },
          { id: "l2" as const, label: c.l2Tab, icon: Cpu },
        ]).map((tabItem) => {
          const TabIcon = tabItem.icon;
          const active = layer === tabItem.id;
          return (
            <button
              key={tabItem.id}
              onClick={() => setLayer(tabItem.id)}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-[12px] font-medium transition"
              style={{
                backgroundColor: active ? `${LIME}1a` : "rgba(255,255,255,0.04)",
                color: active ? LIME : "rgba(255,255,255,0.6)",
                border: active ? `1px solid ${LIME}55` : "1px solid transparent",
              }}
            >
              <TabIcon className="h-3.5 w-3.5" />
              <span className="truncate">{tabItem.label}</span>
            </button>
          );
        })}
      </div>

      <div className="p-4">
        <AnimatePresence mode="wait">
          {layer === "l1" ? (
            <motion.div
              key="l1"
              initial={{ opacity: 0, x: -12 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 12 }}
              transition={{ duration: 0.25 }}
            >
              <div className="mb-2 flex items-center gap-2">
                <HardDrive className="h-4 w-4" style={{ color: LIME }} />
                <h4 className="text-[14px] font-semibold text-white">{c.l1Title}</h4>
              </div>
              <p className="mb-3 text-[12px] leading-relaxed text-white/60">{c.l1Desc}</p>
              <div className="mb-3 flex flex-wrap gap-1.5">
                {c.l1Badges.map((b) => <Badge key={b} label={b} />)}
              </div>
              <div className="space-y-2">
                {c.l1Specs.map((s) => (
                  <div key={s.title} className="rounded-xl border border-white/[0.06] bg-black/20 p-3">
                    <div className="mb-0.5 flex items-center gap-1.5 text-[13px] font-medium text-white">
                      <CheckCircle2 className="h-3.5 w-3.5" style={{ color: LIME }} />
                      {s.title}
                    </div>
                    <div className="pl-5 text-[12px] leading-relaxed text-white/55">{s.detail}</div>
                  </div>
                ))}
              </div>

              {/* Integrity check */}
              <div className="relative mt-3">
                <button
                  onClick={runCheck}
                  disabled={scan === "running"}
                  className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-[12px] font-semibold transition disabled:opacity-70"
                  style={{ backgroundColor: LIME, color: "#15181B" }}
                >
                  <Zap className="h-3.5 w-3.5" />
                  {c.runCheck}
                </button>

                <AnimatePresence>
                  {scan === "running" && (
                    <motion.div
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      className="mt-2 overflow-hidden rounded-lg border border-white/10 bg-black/40 p-3"
                    >
                      <div className="flex items-center gap-2 text-[12px] text-white/80">
                        <Loader2 className="h-3.5 w-3.5 animate-spin" style={{ color: LIME }} />
                        {c.scanning}
                      </div>
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/10">
                        <motion.div
                          className="h-full rounded-full"
                          style={{ backgroundColor: LIME }}
                          initial={{ width: "0%" }}
                          animate={{ width: "100%" }}
                          transition={{ duration: 3, ease: "easeInOut" }}
                        />
                      </div>
                    </motion.div>
                  )}
                  {scan === "done" && (
                    <motion.div
                      initial={{ opacity: 0, scale: 0.96 }}
                      animate={{ opacity: 1, scale: 1 }}
                      className="mt-2 inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-[12px] font-semibold"
                      style={{ borderColor: `${LIME}55`, color: LIME, backgroundColor: `${LIME}12` }}
                    >
                      <CheckCircle2 className="h-4 w-4" />
                      {c.scanDone}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </motion.div>
          ) : (
            <motion.div
              key="l2"
              initial={{ opacity: 0, x: 12 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -12 }}
              transition={{ duration: 0.25 }}
            >
              <div className="mb-2 flex items-center gap-2">
                <Server className="h-4 w-4" style={{ color: LIME }} />
                <h4 className="text-[14px] font-semibold text-white">{c.l2Title}</h4>
              </div>
              <p className="mb-3 text-[12px] leading-relaxed text-white/60">{c.l2Desc}</p>
              <div className="mb-3">
                <Badge label={c.l2Badge} />
              </div>

              {/* Config toggle */}
              <div className="mb-3 flex gap-1 rounded-lg bg-black/30 p-1">
                {c.configs.map((cfg) => {
                  const active = config === cfg.id;
                  return (
                    <button
                      key={cfg.id}
                      onClick={() => setConfig(cfg.id)}
                      className="flex-1 rounded-md px-3 py-1.5 text-[12px] font-medium transition"
                      style={{
                        backgroundColor: active ? LIME : "transparent",
                        color: active ? "#15181B" : "rgba(255,255,255,0.6)",
                      }}
                    >
                      {cfg.label}
                    </button>
                  );
                })}
              </div>

              <AnimatePresence mode="wait">
                <motion.div
                  key={config}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  transition={{ duration: 0.22 }}
                  className="grid grid-cols-2 gap-2"
                >
                  {[
                    { label: c.topsLabel, value: activeConfig.tops },
                    { label: c.powerLabel, value: activeConfig.power },
                    { label: c.agentsLabel, value: activeConfig.agents },
                    { label: c.bestForLabel, value: activeConfig.bestFor },
                  ].map((m) => (
                    <div key={m.label} className="rounded-xl border border-white/[0.06] bg-black/20 p-3">
                      <div className="text-[10px] uppercase tracking-wider text-white/40">{m.label}</div>
                      <div className="mt-0.5 text-[13px] font-semibold text-white">{m.value}</div>
                    </div>
                  ))}
                </motion.div>
              </AnimatePresence>

              {/* Module grid */}
              <div className="mt-4">
                <div className="mb-2 text-[12px] font-medium text-white/70">{c.modulesTitle}</div>
                <div className="flex flex-wrap gap-1.5">
                  {c.modules.map((mod) => (
                    <button
                      key={mod.id}
                      onClick={() => onModuleExplain(mod.explain)}
                      className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-[11px] text-white/75 transition hover:bg-white/10 hover:border-[color:var(--lime)]/40"
                      style={{ ["--lime" as never]: LIME } as CSSProperties}
                    >
                      <Cpu className="h-3 w-3" style={{ color: LIME }} />
                      {mod.label}
                    </button>
                  ))}
                </div>
                <div className="mt-2 text-[11px] text-white/40">{c.rolloutLabel}</div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Metadata stamp */}
        <div className="mt-4 flex items-center gap-1.5 border-t border-white/[0.06] pt-3 text-[10px] text-white/40">
          <Lock className="h-3 w-3" style={{ color: LIME }} />
          {c.stamp}
        </div>
      </div>
    </div>
  );
}

export { renderInline as hwRenderInline };
