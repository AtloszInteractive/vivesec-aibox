import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { KeyRound, Loader2, LogOut, ShieldCheck } from "lucide-react";

import {
  fetchGateSession,
  gateChangePassword,
  gateLogin,
  gateLoginCode,
  gateLogout,
  gatePath,
} from "@/lib/auth/client";
import { LOGIN_PATH, PASSWORD_PATH } from "@/lib/auth/protocol";
import {
  ACCENT,
  ACCENT_TEXT,
  LOGO_ASPECT,
  LOGO_FILE_RATIO,
  LOGO_ON_DARK,
  NAVY,
  ON_ACCENT,
} from "./brand";
import { LANGS, LanguageProvider, useLang, type Lang } from "./i18n";

// Error codes of the login gate and the authentication service, mapped to the
// English source text of the i18n table.
const ERROR_TEXT: Record<string, string> = {
  invalid_credentials: "Wrong username or password.",
  invalid_code: "The code is not correct. Check the time on your phone and try the next code.",
  challenge_expired: "The sign-in took too long. Please start again.",
  account_locked:
    "Too many failed attempts: the account is locked for a while. Contact the administrator if this persists.",
  too_many_attempts:
    "Too many failed attempts from this computer. Wait a few minutes and try again.",
  auth_unavailable: "The sign-in service is not available. Try again in a minute.",
  network: "The sign-in service is not available. Try again in a minute.",
  csrf: "The page has expired. Reload it and try again.",
  wrong_password: "The current password is not correct.",
  password_too_short: "The new password is too short.",
  password_too_long: "The new password is too long.",
  password_invalid_characters: "The new password contains characters that are not allowed.",
  password_same_as_username: "The new password must differ from the username.",
  password_too_simple: "The new password is too simple: use more different characters.",
  password_unchanged: "The new password must differ from the current one.",
};

function errorText(code: string, tm: (s: string) => string): string {
  return tm(ERROR_TEXT[code] ?? "Sign-in failed. Please try again.");
}

const go = (path: string) => window.location.assign(gatePath(path));

/* ------------------------------------------------------------------ shell */

function GateLogo() {
  const width = Math.round(180 * LOGO_FILE_RATIO);
  const height = Math.round(width * LOGO_ASPECT);
  return (
    <img
      src={LOGO_ON_DARK}
      alt="ViVeSec"
      width={width}
      height={height}
      draggable={false}
      className="select-none"
      style={{ width, height }}
    />
  );
}

function LanguagePicker() {
  const { lang, setLang, tm } = useLang();
  return (
    <div role="group" aria-label={tm("Language")} className="flex gap-1">
      {LANGS.map((l) => (
        <button
          key={l.code}
          type="button"
          onClick={() => setLang(l.code as Lang)}
          title={l.native}
          aria-pressed={l.code === lang}
          className="rounded-md px-2 py-1 text-[11px] font-semibold transition hover:bg-white/10"
          style={l.code === lang ? { color: ACCENT_TEXT } : { color: "rgba(255,255,255,0.55)" }}
        >
          {l.code}
        </button>
      ))}
    </div>
  );
}

function GateShell({
  title,
  icon,
  children,
}: {
  title: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <div
      className="flex min-h-screen flex-col items-center justify-center px-4 py-10 text-white"
      style={{
        backgroundColor: NAVY,
        fontFamily: "Arial, 'Liberation Sans', 'Nimbus Sans', Helvetica, sans-serif",
      }}
    >
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center justify-between">
          <GateLogo />
          <LanguagePicker />
        </div>
        <main className="rounded-2xl border border-white/10 bg-[#10243A] p-6 shadow-2xl shadow-black/50">
          <h1 className="mb-5 flex items-center gap-2 text-lg font-semibold">
            <span style={{ color: ACCENT_TEXT }}>{icon}</span>
            {title}
          </h1>
          {children}
        </main>
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  ...input
}: { label: string; hint?: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="mb-4 block">
      <span className="mb-1.5 block text-xs font-medium text-white/70">{label}</span>
      <input
        {...input}
        className="w-full rounded-lg border border-white/15 bg-white/5 px-3 py-2.5 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-[#B48CF5] focus:ring-2 focus:ring-[#8C49E9]/40"
      />
      {hint && <span className="mt-1 block text-[11px] text-white/50">{hint}</span>}
    </label>
  );
}

function PrimaryButton({ busy, children }: { busy: boolean; children: ReactNode }) {
  // Disabled until the page script runs: on a slow link the server-rendered
  // form shows up long before it, and a native submit must never happen.
  const ready = useHydrated();
  return (
    <button
      type="submit"
      disabled={busy || !ready}
      className="inline-flex w-full items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold transition hover:brightness-110 disabled:opacity-60"
      style={{ backgroundColor: ACCENT, color: ON_ACCENT }}
    >
      {(busy || !ready) && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  );
}

function useHydrated(): boolean {
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => setHydrated(true), []);
  return hydrated;
}

function SecondaryButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs text-white/70 transition hover:bg-white/10 hover:text-white"
    >
      {children}
    </button>
  );
}

function ErrorLine({ text }: { text: string }) {
  if (!text) return null;
  return (
    <p
      role="alert"
      className="mb-4 rounded-lg border border-rose-400/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-100"
    >
      {text}
    </p>
  );
}

/** The otpauth:// QR code as SVG squares, with the standard 4-module quiet zone. */
function QrCode({ rows, label }: { rows: string[]; label: string }) {
  const quiet = 4;
  const size = rows.length + quiet * 2;
  const path = useMemo(() => {
    let d = "";
    rows.forEach((row, y) => {
      for (let x = 0; x < row.length; x++) {
        if (row[x] === "1") d += `M${x + quiet} ${y + quiet}h1v1h-1z`;
      }
    });
    return d;
  }, [rows]);
  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`0 0 ${size} ${size}`}
      width={216}
      height={216}
      shapeRendering="crispEdges"
      className="rounded-lg"
    >
      <rect width={size} height={size} fill="#FFFFFF" />
      <path d={path} fill="#000000" />
    </svg>
  );
}

const cleanCode = (raw: string) => raw.replace(/\D/g, "").slice(0, 8);

/* ------------------------------------------------------------------ login */

type Step =
  | { kind: "credentials" }
  | { kind: "totp" }
  | { kind: "enroll"; secret: string; account: string; issuer: string; qr: string[] };

function LoginFlow() {
  const { tm } = useLang();
  const [step, setStep] = useState<Step>({ kind: "credentials" });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Also issues the CSRF cookie the sign-in calls need.
  useEffect(() => {
    void fetchGateSession().then((s) => {
      if (!s.ok) return;
      if (!s.standalone) go("/");
      else if (s.authenticated) go(s.password_change_required ? PASSWORD_PATH : "/");
    });
  }, []);

  function restart() {
    setStep({ kind: "credentials" });
    setCode("");
    setError("");
  }

  async function submitPassword(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    const reply = await gateLogin(username, password);
    setBusy(false);
    setPassword("");
    if (!reply.ok) {
      setError(errorText(reply.error, tm));
      if (reply.error === "csrf") void fetchGateSession();
      return;
    }
    setCode("");
    setStep(reply.step === "enroll" ? { kind: "enroll", ...reply } : { kind: "totp" });
  }

  async function submitCode(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    const reply = await gateLoginCode(cleanCode(code));
    if (reply.ok) {
      go(reply.password_change_required ? PASSWORD_PATH : "/");
      return;
    }
    setBusy(false);
    setCode("");
    setError(errorText(reply.error, tm));
    if (reply.error !== "invalid_code" && reply.error !== "too_many_attempts") {
      setStep({ kind: "credentials" });
    }
  }

  if (step.kind === "credentials") {
    return (
      <GateShell title={tm("Sign in to the AI Box")} icon={<KeyRound className="h-5 w-5" />}>
        <form method="post" onSubmit={submitPassword} noValidate>
          <ErrorLine text={error} />
          <Field
            label={tm("Username")}
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            required
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <Field
            label={tm("Password")}
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <PrimaryButton busy={busy}>{busy ? tm("Signing in…") : tm("Sign in")}</PrimaryButton>
        </form>
      </GateShell>
    );
  }

  const codeField = (
    <Field
      label={tm("Verification code")}
      name="code"
      inputMode="numeric"
      autoComplete="one-time-code"
      pattern="[0-9]*"
      maxLength={8}
      required
      autoFocus
      value={code}
      onChange={(e) => setCode(cleanCode(e.target.value))}
    />
  );

  if (step.kind === "totp") {
    return (
      <GateShell title={tm("Two-step verification")} icon={<ShieldCheck className="h-5 w-5" />}>
        <form method="post" onSubmit={submitCode} noValidate>
          <p className="mb-4 text-sm text-white/75">
            {tm("Enter the 6-digit code from your authenticator app.")}
          </p>
          <ErrorLine text={error} />
          {codeField}
          <PrimaryButton busy={busy}>{busy ? tm("Checking…") : tm("Verify")}</PrimaryButton>
          <div className="mt-3 flex justify-end">
            <SecondaryButton onClick={restart}>{tm("Start over")}</SecondaryButton>
          </div>
        </form>
      </GateShell>
    );
  }

  const groupedSecret = (step.secret.match(/.{1,4}/g) ?? [step.secret]).join(" ");
  return (
    <GateShell
      title={tm("Set up two-step verification")}
      icon={<ShieldCheck className="h-5 w-5" />}
    >
      <form method="post" onSubmit={submitCode} noValidate>
        <ol className="mb-4 space-y-3 text-sm text-white/75">
          <li>
            {tm(
              "1. Install an authenticator app on your phone (for example Microsoft Authenticator, Google Authenticator or FreeOTP).",
            )}
          </li>
          <li>{tm("2. Scan this QR code with the app.")}</li>
        </ol>
        <div className="mb-4 flex justify-center">
          <QrCode rows={step.qr} label={tm("QR code for the authenticator app")} />
        </div>
        <div className="mb-4 rounded-lg border border-white/10 bg-white/[0.03] p-3 text-xs text-white/70">
          <p className="mb-1.5">{tm("Can't scan? Enter this key in the app:")}</p>
          <p className="select-all break-all font-mono text-sm tracking-wider text-white">
            {groupedSecret}
          </p>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px] text-white/55">
            <dt>{tm("Account")}</dt>
            <dd className="text-white/80">{step.account}</dd>
            {step.issuer && (
              <>
                <dt>{tm("Issuer")}</dt>
                <dd className="text-white/80">{step.issuer}</dd>
              </>
            )}
          </dl>
        </div>
        <p className="mb-3 text-sm text-white/75">
          {tm("3. Enter the 6-digit code the app shows.")}
        </p>
        <ErrorLine text={error} />
        {codeField}
        <PrimaryButton busy={busy}>{busy ? tm("Checking…") : tm("Finish setup")}</PrimaryButton>
        <div className="mt-3 flex justify-end">
          <SecondaryButton onClick={restart}>{tm("Start over")}</SecondaryButton>
        </div>
      </form>
    </GateShell>
  );
}

export function LoginPage() {
  return (
    <LanguageProvider>
      <LoginFlow />
    </LanguageProvider>
  );
}

/* ------------------------------------------------------------------ password */

function PasswordFlow() {
  const { tm } = useLang();
  const [loaded, setLoaded] = useState(false);
  const [forced, setForced] = useState(false);
  const [minLength, setMinLength] = useState(12);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    void fetchGateSession().then((s) => {
      if (!s.ok) {
        setError(errorText(s.error, tm));
        setLoaded(true);
        return;
      }
      if (!s.standalone) return go("/");
      if (!s.authenticated) return go(LOGIN_PATH);
      setForced(s.password_change_required);
      setMinLength(s.password_min_length);
      setLoaded(true);
    });
    // The page loads once; the language may change meanwhile.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError("");
    setDone(false);
    if (next !== repeat) {
      setError(tm("The two new passwords do not match."));
      return;
    }
    setBusy(true);
    const reply = await gateChangePassword(current, next);
    setBusy(false);
    setCurrent("");
    if (!reply.ok) {
      if (reply.error === "login_required") return go(LOGIN_PATH);
      setError(errorText(reply.error, tm));
      return;
    }
    setNext("");
    setRepeat("");
    if (forced) return go("/");
    setDone(true);
  }

  async function signOut() {
    await gateLogout();
    go(LOGIN_PATH);
  }

  return (
    <GateShell title={tm("Change password")} icon={<KeyRound className="h-5 w-5" />}>
      {!loaded ? (
        <div className="flex justify-center py-6">
          <Loader2 className="h-5 w-5 animate-spin text-white/60" />
        </div>
      ) : (
        <form method="post" onSubmit={submit} noValidate>
          {forced && (
            <p className="mb-4 rounded-lg border border-amber-300/40 bg-amber-400/10 px-3 py-2 text-xs text-amber-100">
              {tm("Your password is temporary. Choose a new one to continue.")}
            </p>
          )}
          {done && (
            <p
              role="status"
              className="mb-4 rounded-lg border border-emerald-300/40 bg-emerald-400/10 px-3 py-2 text-xs text-emerald-100"
            >
              {tm("Password changed. Your other sessions were signed out.")}
            </p>
          )}
          <ErrorLine text={error} />
          <Field
            label={tm("Current password")}
            name="current-password"
            type="password"
            autoComplete="current-password"
            required
            autoFocus
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
          <Field
            label={tm("New password")}
            name="new-password"
            type="password"
            autoComplete="new-password"
            required
            minLength={minLength}
            hint={tm("At least {n} characters, and not your username.").replace(
              "{n}",
              String(minLength),
            )}
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
          <Field
            label={tm("Repeat the new password")}
            name="repeat-password"
            type="password"
            autoComplete="new-password"
            required
            value={repeat}
            onChange={(e) => setRepeat(e.target.value)}
          />
          <PrimaryButton busy={busy}>{tm("Save new password")}</PrimaryButton>
          <div className="mt-3 flex items-center justify-between">
            {forced ? (
              <span />
            ) : (
              <SecondaryButton onClick={() => go("/")}>
                {tm("Back to the workspace")}
              </SecondaryButton>
            )}
            <SecondaryButton onClick={() => void signOut()}>
              <LogOut className="h-3.5 w-3.5" />
              {tm("Sign out")}
            </SecondaryButton>
          </div>
        </form>
      )}
    </GateShell>
  );
}

export function PasswordPage() {
  return (
    <LanguageProvider>
      <PasswordFlow />
    </LanguageProvider>
  );
}
