import { Clock3, Network, RefreshCw, ShieldAlert, ShieldCheck, Users } from "lucide-react";

import type { AdapterSystem, AuthSystemReading } from "@/lib/auth/admin-client";
import { useLang } from "../i18n";
import {
  CLOCK_BAD_SECONDS,
  clockLevel,
  directoryLevel,
  rulesLevel,
  scannerLevel,
  type Level,
} from "./status";
import { Badge, Button, Card, fill, formatTime, levelLabel } from "./ui";

const TONE: Record<Level, "ok" | "warn" | "bad" | "neutral"> = {
  ok: "ok",
  warn: "warn",
  bad: "bad",
  unknown: "neutral",
};

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-white/5 py-1.5 text-xs last:border-0">
      <span className="text-white/55">{label}</span>
      <span className="text-right text-white/90">{children}</span>
    </div>
  );
}

export function OverviewTab({
  auth,
  adapter,
  authError,
  adapterError,
  busy,
  onRefresh,
}: {
  auth: AuthSystemReading | null;
  adapter: AdapterSystem | null;
  authError: string;
  adapterError: string;
  busy: boolean;
  onRefresh: () => void;
}) {
  const { tm, lang } = useLang();
  const levelText: Record<Level, string> = {
    ok: tm("OK"),
    warn: tm("Check"),
    bad: tm("Problem"),
    unknown: tm("Unknown"),
  };
  const clock = clockLevel(auth);
  const scanner = scannerLevel(adapter);
  const directory = directoryLevel(auth);
  const rules = rulesLevel(adapter);
  const kernelText = auth
    ? {
        synchronized: tm("synchronised with the time server"),
        unsynchronized: tm("not synchronised with any time server"),
        unknown: tm("cannot be read"),
      }[auth.clock.state]
    : "—";
  const scanText: Record<string, string> = {
    required: tm("required: files wait in quarantine until scanned"),
    on: tm("on: files are released unscanned when the scanner is down"),
    off: tm("off: uploads are not scanned"),
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button busy={busy} onClick={onRefresh}>
          <RefreshCw className="h-3.5 w-3.5" />
          {tm("Refresh")}
        </Button>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Card
          title={tm("Time")}
          icon={<Clock3 className="h-4 w-4" />}
          actions={<Badge tone={TONE[clock]}>{levelText[clock]}</Badge>}
        >
          {authError && <p className="mb-2 text-xs text-rose-200">{authError}</p>}
          <Row label={tm("Time on the AI Box")}>{auth ? formatTime(auth.now, lang) : "—"}</Row>
          <Row label={tm("Difference to this computer")}>
            {auth
              ? fill(tm("{s} s"), {
                  s: `${auth.offsetSeconds >= 0 ? "+" : ""}${auth.offsetSeconds.toFixed(1)}`,
                })
              : "—"}
          </Row>
          <Row label={tm("Clock of the AI Box")}>
            {kernelText}
            {auth?.clock.est_error_ms !== undefined &&
              auth.clock.state === "synchronized" &&
              ` (±${auth.clock.est_error_ms} ms)`}
          </Row>
          <p className="mt-2 text-[11px] text-white/50">
            {fill(
              tm(
                "The two-factor codes are accepted up to {n} seconds off. Beyond {m} seconds sign-in starts to fail: check the time server of the AI Box (chrony) and the clock of the phones.",
              ),
              {
                n: auth ? auth.totp_step_seconds * auth.totp_tolerance_steps : 30,
                m: CLOCK_BAD_SECONDS,
              },
            )}
          </p>
        </Card>

        <Card
          title={tm("Virus scanning")}
          icon={
            scanner === "ok" ? (
              <ShieldCheck className="h-4 w-4" />
            ) : (
              <ShieldAlert className="h-4 w-4" />
            )
          }
          actions={<Badge tone={TONE[scanner]}>{levelText[scanner]}</Badge>}
        >
          {adapterError && <p className="mb-2 text-xs text-rose-200">{adapterError}</p>}
          <Row label={tm("Scanning of uploads")}>
            {adapter ? (scanText[adapter.scan] ?? adapter.scan) : "—"}
          </Row>
          <Row label={tm("Virus scanner")}>
            {adapter
              ? adapter.clamd.reachable
                ? `ClamAV ${adapter.clamd.engine ?? ""}`
                : tm("not reachable")
              : "—"}
          </Row>
          <Row label={tm("Virus definitions")}>
            {adapter?.clamd.database
              ? `${adapter.clamd.database} · ${formatTime(adapter.clamd.database_time, lang)}`
              : "—"}
          </Row>
          <Row label={tm("Age of the definitions")}>
            {adapter && adapter.clamd.age_days !== null
              ? fill(tm("{n} days (warning above {m})"), {
                  n: adapter.clamd.age_days,
                  m: adapter.max_age_days,
                })
              : "—"}
          </Row>
          {adapter?.clamd.stale && (
            <p className="mt-2 text-[11px] text-amber-100/90">
              {tm(
                "The virus definitions are old. Install the latest definitions with the update package (USB).",
              )}
            </p>
          )}
        </Card>

        <Card
          title={tm("Active Directory")}
          icon={<Users className="h-4 w-4" />}
          actions={<Badge tone={TONE[directory]}>{levelText[directory]}</Badge>}
        >
          <Row label={tm("State")}>
            {auth
              ? {
                  off: tm("not configured: local users only"),
                  ready: tm("configured"),
                  invalid: tm("settings not usable: directory users cannot sign in"),
                }[auth.directory.state]
              : "—"}
          </Row>
          {auth?.directory.problem && (
            <p className="mt-2 break-words text-[11px] text-rose-200">{auth.directory.problem}</p>
          )}
        </Card>

        <Card
          title={tm("Permission rules")}
          icon={<Network className="h-4 w-4" />}
          actions={<Badge tone={TONE[rules]}>{levelText[rules]}</Badge>}
        >
          <Row label={tm("Rules file")}>
            {adapter
              ? adapter.acl.configured
                ? adapter.acl.rules_error
                  ? tm("unusable: every document is hidden")
                  : fill(tm("{n} rule(s)"), { n: adapter.acl.rule_paths })
                : tm("not configured")
              : "—"}
          </Row>
          <Row label={tm("Default classification")}>
            {levelLabel(adapter?.acl.default_classification, tm)}
          </Row>
        </Card>
      </div>
    </div>
  );
}
