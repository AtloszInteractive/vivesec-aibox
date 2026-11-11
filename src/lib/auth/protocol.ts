// Names shared by the login gate (gate.server.ts) and the browser code. The
// gate is only active in standalone mode (ADAPTER_INTEGRATION=standalone).

/** HttpOnly session cookie; carries the authentication service's session secret. */
export const SESSION_COOKIE = "__Host-aibox_session";
/** HttpOnly cookie that holds the pending login challenge between the two steps. */
export const LOGIN_COOKIE = "__Host-aibox_login";
/** Readable double-submit CSRF cookie; its value goes back in CSRF_HEADER. */
export const CSRF_COOKIE = "__Host-aibox_csrf";
export const CSRF_HEADER = "X-AIBox-CSRF";
/** Set on a refused /api/v1 call: "login" or "password" (the page to go to). */
export const AUTH_SIGNAL_HEADER = "X-AIBox-Auth";

export const LOGIN_PATH = "/login";
export const PASSWORD_PATH = "/password";

export type GateUser = {
  username: string;
  display_name: string;
  /** "ldap": a directory (AD) user, whose password the directory owns. */
  source: "local" | "ldap";
  roles: string[];
  drives: string[];
};

export type GateSession =
  | { ok: true; standalone: false }
  | {
      ok: true;
      standalone: true;
      authenticated: false;
    }
  | {
      ok: true;
      standalone: true;
      authenticated: true;
      user: GateUser;
      password_change_required: boolean;
      password_min_length: number;
    };

export type LoginStep =
  | { ok: true; step: "totp" }
  | { ok: true; step: "enroll"; secret: string; account: string; issuer: string; qr: string[] };

export type GateError = { ok: false; error: string };
