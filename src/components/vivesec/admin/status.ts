// Which conditions of a standalone box need the administrator's attention
// (E27 S7). Shared by the overview tab and the warning dot of the account menu.

import type { AdapterSystem, AuthSystemReading } from "@/lib/auth/admin-client";

/** Clock difference to this computer that is worth a warning, in seconds. */
export const CLOCK_WARN_SECONDS = 10;
/** Beyond this TOTP codes start to fail (one 30 s step of tolerance). */
export const CLOCK_BAD_SECONDS = 25;

export type Level = "ok" | "warn" | "bad" | "unknown";

export function clockLevel(reading: AuthSystemReading | null): Level {
  if (!reading) return "unknown";
  const drift = Math.abs(reading.offsetSeconds);
  if (drift >= CLOCK_BAD_SECONDS) return "bad";
  if (drift >= CLOCK_WARN_SECONDS || reading.clock.state === "unsynchronized") return "warn";
  return "ok";
}

export function scannerLevel(system: AdapterSystem | null): Level {
  if (!system) return "unknown";
  if (system.scan === "off") return "warn";
  if (!system.clamd.reachable) return "bad";
  if (system.clamd.stale || system.clamd.age_days === null) return "warn";
  return "ok";
}

export function directoryLevel(reading: AuthSystemReading | null): Level {
  if (!reading) return "unknown";
  return reading.directory.state === "invalid" ? "bad" : "ok";
}

export function rulesLevel(system: AdapterSystem | null): Level {
  if (!system) return "unknown";
  if (system.acl.rules_error) return "bad";
  return system.acl.configured ? "ok" : "warn";
}

/** True when anything is not fine; unknown (not readable) counts as a warning. */
export function needsAttention(
  auth: AuthSystemReading | null,
  adapter: AdapterSystem | null,
): boolean {
  return [clockLevel(auth), scannerLevel(adapter), directoryLevel(auth), rulesLevel(adapter)].some(
    (level) => level !== "ok",
  );
}
