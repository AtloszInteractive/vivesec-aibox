// Shared pieces of the administration page (E27 S7): buttons, fields, dialogs
// and the texts of the error codes. Every visible string goes through tm().

import { useEffect, useRef, type ReactNode } from "react";
import { Loader2, X } from "lucide-react";

import { AdminError } from "@/lib/auth/admin-client";
import { ACCENT, ACCENT_TEXT, ON_ACCENT } from "../brand";
import type { Lang } from "../i18n";

export type Tm = (text: string) => string;

const LOCALES: Record<Lang, string> = { EN: "en-GB", HU: "hu-HU", DA: "da-DK", DE: "de-DE" };

export function formatTime(seconds: number | null | undefined, lang: Lang): string {
  if (!seconds) return "—";
  return new Date(seconds * 1000).toLocaleString(LOCALES[lang] ?? "en-GB", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** `{n}`-style placeholders filled in after the translation. */
export function fill(text: string, values: Record<string, string | number>): string {
  return text.replace(/\{(\w+)\}/g, (all, key: string) =>
    key in values ? String(values[key]) : all,
  );
}

/** Comma- or line-separated list typed by the administrator. */
export function splitList(raw: string): string[] {
  const out: string[] = [];
  for (const item of raw.split(/[,\n]/)) {
    const value = item.trim();
    if (value && !out.includes(value)) out.push(value);
  }
  return out;
}

/** The last segment of a drive path, without the trailing slash. */
export function roomName(path: string): string {
  const clean = path.replace(/\/+$/, "");
  return clean.slice(clean.lastIndexOf("/") + 1) || clean;
}

export const withSlash = (path: string) => `${path.replace(/\/+$/, "")}/`;

const LEVEL_TEXT: Record<string, string> = {
  public: "Public",
  internal: "Internal",
  confidential: "Confidential",
  strictly_confidential: "Strictly confidential",
  personal: "Personal data",
};

/** The display name of a classification level (adapter/acl.py LEVELS). */
export function levelLabel(level: string | null | undefined, tm: Tm): string {
  if (!level) return "—";
  return LEVEL_TEXT[level] ? tm(LEVEL_TEXT[level]) : level;
}

// Error codes of the authentication service, the adapter and the gate, mapped
// to the English source text of the i18n table.
const ERROR_TEXT: Record<string, string> = {
  network: "The AI Box could not be reached.",
  auth_unavailable: "The sign-in service is not available. Try again in a minute.",
  csrf: "The page has expired. Reload it and try again.",
  forbidden: "This needs the administrator role.",
  login_required: "Your session has ended. Sign in again.",
  user_exists: "A user with this name already exists.",
  user_not_found: "The user no longer exists. Reload the list.",
  invalid_username:
    "The username may contain lower-case letters, digits and . _ @ - (at most 64 characters).",
  invalid_roles: "A role may contain lower-case letters, digits and _ . - only.",
  invalid_groups: "A group name may contain letters, digits, space and _ . @ - only.",
  invalid_drives: "A room path is not valid.",
  invalid_display_name: "The display name is too long or contains control characters.",
  last_admin: "The last active administrator cannot be removed, suspended or demoted.",
  managed_by_directory: "This is managed in Active Directory and cannot be changed here.",
  password_too_short: "The new password is too short.",
  password_too_long: "The new password is too long.",
  password_invalid_characters: "The new password contains characters that are not allowed.",
  password_same_as_username: "The new password must differ from the username.",
  password_too_simple: "The new password is too simple: use more different characters.",
  invalid_group_map: "The group mapping is not valid.",
  map_changed: "Someone else changed the group mapping meanwhile. Reload it and try again.",
  directory_off: "Active Directory is not configured on this AI Box.",
  directory_invalid: "The Active Directory settings are not usable.",
  invalid_rules: "The permission rules are not valid.",
  rules_changed: "Someone else changed the permission rules meanwhile. Reload them and try again.",
  rules_not_configured:
    "No permission rules file is configured on this AI Box (ADAPTER_ACL_RULES).",
  rules_unreadable: "The permission rules file cannot be read.",
  rules_unwritable: "The permission rules file cannot be written.",
  exists: "A room with this name already exists.",
  invalid_path: "This name is not allowed (no leading dot, no control characters).",
  not_a_room: "This is not a room.",
  not_empty: "The room still holds files. Delete them in Documents first.",
  busy: "Uploads into this room are still in progress. Try again when they are finished.",
  "not-found": "Not found. It may have been deleted meanwhile.",
  store_disabled: "The file store is not available on this AI Box.",
  store_unavailable: "The file store is not available on this AI Box.",
  "storage locked": "The storage of the AI Box is locked.",
};

export function errorText(error: unknown, tm: Tm): string {
  if (error instanceof AdminError) {
    const known = ERROR_TEXT[error.code];
    const text = known ? tm(known) : `${tm("The request failed.")} (${error.code})`;
    return error.detail ? `${text} ${error.detail}` : text;
  }
  return tm("The request failed.");
}

/* ------------------------------------------------------------------ controls */

type ButtonKind = "primary" | "secondary" | "danger";

export function Button({
  kind = "secondary",
  busy = false,
  disabled = false,
  onClick,
  type = "button",
  title,
  children,
}: {
  kind?: ButtonKind;
  busy?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  type?: "button" | "submit";
  title?: string;
  children: ReactNode;
}) {
  const base =
    "inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition disabled:opacity-50";
  const styles: Record<ButtonKind, string> = {
    primary: "hover:brightness-110",
    secondary: "border border-white/15 bg-white/5 text-white/85 hover:bg-white/10",
    danger: "border border-rose-400/40 bg-rose-500/10 text-rose-100 hover:bg-rose-500/20",
  };
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={busy || disabled}
      className={`${base} ${styles[kind]}`}
      style={kind === "primary" ? { backgroundColor: ACCENT, color: ON_ACCENT } : undefined}
    >
      {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      {children}
    </button>
  );
}

export function TextField({
  label,
  hint,
  ...input
}: { label: string; hint?: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium text-white/65">{label}</span>
      <input
        {...input}
        className="w-full rounded-lg border border-white/15 bg-white/5 px-2.5 py-1.5 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-[#B48CF5] focus:ring-2 focus:ring-[#8C49E9]/40 disabled:opacity-60"
      />
      {hint && <span className="mt-1 block text-[11px] text-white/45">{hint}</span>}
    </label>
  );
}

export function SelectField({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium text-white/65">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-white/15 bg-[#0B1C2E] px-2.5 py-1.5 text-sm text-white outline-none focus:border-[#B48CF5]"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Checkbox({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-xs text-white/80">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="h-3.5 w-3.5 accent-[#8C49E9]"
      />
      <span>{label}</span>
    </label>
  );
}

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "accent" | "ok" | "warn" | "bad";
  children: ReactNode;
}) {
  const tones = {
    neutral: "border-white/15 bg-white/5 text-white/70",
    accent: "border-[#8C49E9]/50 bg-[#8C49E9]/15 text-[#D9C5FA]",
    ok: "border-emerald-400/40 bg-emerald-500/10 text-emerald-100",
    warn: "border-amber-400/40 bg-amber-500/10 text-amber-100",
    bad: "border-rose-400/40 bg-rose-500/10 text-rose-100",
  };
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-[10.5px] ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

export function Card({
  title,
  icon,
  actions,
  children,
}: {
  title?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-white/10 bg-[#10243A] p-4 shadow-xl shadow-black/30">
      {(title || actions) && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-white">
            {icon && <span style={{ color: ACCENT_TEXT }}>{icon}</span>}
            {title}
          </h2>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Notice({ tone, children }: { tone: "error" | "warn" | "ok"; children: ReactNode }) {
  const tones = {
    error: "border-rose-400/40 bg-rose-500/10 text-rose-100",
    warn: "border-amber-400/40 bg-amber-500/10 text-amber-100",
    ok: "border-emerald-400/40 bg-emerald-500/10 text-emerald-100",
  };
  return (
    <p
      role={tone === "error" ? "alert" : "status"}
      className={`rounded-lg border px-3 py-2 text-xs ${tones[tone]}`}
    >
      {children}
    </p>
  );
}

export function Dialog({
  title,
  onClose,
  children,
  footer,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close.current();
    };
    document.addEventListener("keydown", onKey);
    ref.current?.querySelector<HTMLElement>("input, select, textarea")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 py-6"
      role="presentation"
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`flex max-h-full w-full ${wide ? "max-w-2xl" : "max-w-md"} flex-col rounded-2xl border border-white/10 bg-[#10243A] shadow-2xl shadow-black/60`}
      >
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <h3 className="text-sm font-semibold text-white">{title}</h3>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-white/60 hover:bg-white/10 hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">{children}</div>
        <div className="flex justify-end gap-2 border-t border-white/10 px-4 py-3">{footer}</div>
      </div>
    </div>
  );
}

/** A random password that passes the box's policy (16 characters, mixed). */
export function generatePassword(): string {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_.";
  const limit = 256 - (256 % alphabet.length);
  const out: string[] = [];
  while (out.length < 16) {
    const bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    for (const b of bytes) {
      if (b < limit && out.length < 16) out.push(alphabet[b % alphabet.length]);
    }
  }
  return out.join("");
}
