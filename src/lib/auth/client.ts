// Browser side of the standalone login gate (gate.server.ts). Every helper is a
// no-op outside a standalone box: embedded in the ViVeSecBox there is no CSRF
// cookie and no gate signal, so nothing changes there.

import {
  AUTH_SIGNAL_HEADER,
  CSRF_COOKIE,
  CSRF_HEADER,
  LOGIN_PATH,
  PASSWORD_PATH,
  type GateError,
  type GateSession,
  type LoginStep,
} from "./protocol";

const IS_BROWSER = typeof window !== "undefined";

function readCsrfCookie(): string {
  if (!IS_BROWSER) return "";
  for (const part of document.cookie.split(";")) {
    const at = part.indexOf("=");
    if (at > 0 && part.slice(0, at).trim() === CSRF_COOKIE) return part.slice(at + 1).trim();
  }
  return "";
}

/** `init` with the CSRF header added to a state-changing request, when the gate issued one. */
export function withCsrf(init?: RequestInit): RequestInit | undefined {
  const method = (init?.method ?? "GET").toUpperCase();
  if (method === "GET" || method === "HEAD") return init;
  const token = readCsrfCookie();
  if (!token) return init;
  const headers = new Headers(init?.headers);
  headers.set(CSRF_HEADER, token);
  return { ...init, headers };
}

/** Path of a gate page under the deploy prefix. */
export function gatePath(path: string): string {
  const base = (import.meta.env.BASE_URL || "/").replace(/\/+$/, "");
  return `${base}${path}`;
}

/** Sends the browser to the login or password page when the gate refused a call. */
export function followAuthSignal(res: Response): void {
  if (!IS_BROWSER) return;
  const signal = res.headers.get(AUTH_SIGNAL_HEADER);
  const target = signal === "login" ? LOGIN_PATH : signal === "password" ? PASSWORD_PATH : null;
  if (!target) return;
  const path = gatePath(target);
  if (window.location.pathname !== path) window.location.assign(path);
}

async function readJson<T>(res: Response): Promise<T | GateError> {
  try {
    return (await res.json()) as T | GateError;
  } catch {
    return { ok: false, error: res.status === 404 ? "not_found" : "auth_unavailable" };
  }
}

async function post<T>(path: string, body: Record<string, unknown>): Promise<T | GateError> {
  // The CSRF cookie comes with /auth/session, which the page asks for when it
  // loads; on a slow link the form can be sent before that answer arrived.
  if (!readCsrfCookie()) await fetchGateSession();
  try {
    const res = await fetch(
      gatePath(path),
      withCsrf({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        credentials: "same-origin",
      }),
    );
    return await readJson<T>(res);
  } catch {
    return { ok: false, error: "network" };
  }
}

/** The gate's view of this browser: also issues the CSRF cookie on first use. */
export async function fetchGateSession(): Promise<GateSession | GateError> {
  try {
    const res = await fetch(gatePath("/auth/session"), {
      credentials: "same-origin",
      cache: "no-store",
    });
    return await readJson<GateSession>(res);
  } catch {
    return { ok: false, error: "network" };
  }
}

export const gateLogin = (username: string, password: string) =>
  post<LoginStep>("/auth/login", { username, password });

export const gateLoginCode = (code: string) =>
  post<{ ok: true; password_change_required: boolean }>("/auth/login/totp", { code });

export const gateChangePassword = (current_password: string, new_password: string) =>
  post<{ ok: true }>("/auth/password", { current_password, new_password });

export const gateLogout = () => post<{ ok: true }>("/auth/logout", {});
