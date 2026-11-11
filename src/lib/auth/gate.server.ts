// Login gate of the box-hosted web UI in standalone mode (E27 S4).
//
// With ADAPTER_INTEGRATION=standalone there is no ViVeSecBox in front of the
// box to vouch for its users, so this server does it: it signs users in against
// the authentication service (auth_service/, loopback only, service key), keeps
// the session secret in an HttpOnly cookie, and relays every /api/v1 call to the
// adapter with a short-lived signed identity token (VVS-Identity) instead of the
// demo identity. The adapter verifies the token (ADAPTER_IDENTITY_MODE=require).
//
//   Browser ──HTTPS──▶ nginx ──▶ this gate ──service key──▶ auth_service
//                                   │
//                                   └──VVS-Identity (JWT)──▶ adapter
//
// Guarantees:
//   * Every /api/v1 call needs a session; the identity headers come only from
//     the token, never from the browser. VVS-Drive is one of the token's rooms
//     (the adapter always searches the active drive, so it must be granted).
//   * State-changing calls need the double-submit CSRF token and a same-origin
//     Origin / Sec-Fetch-Site; the cookies are __Host-, Secure, SameSite=Strict.
//   * Unknown ADAPTER_INTEGRATION values keep the gate on (fail closed).
//
// Env (server-only): ADAPTER_INTEGRATION, AUTH_URL (loopback, default
// http://127.0.0.1:8097), AUTH_SERVICE_KEY_FILE (default /data/auth/service.key),
// ADAPTER_IDENTITY_HEADER (default VVS-Identity), ADAPTER_DRIVE_PREFIX.

import { Buffer } from "node:buffer";
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { readFileSync } from "node:fs";
import process from "node:process";

import { encodeVvsDrive, isRelayed, relayToAdapter } from "../rag/adapter-proxy.server";
import {
  AUTH_SIGNAL_HEADER,
  CSRF_COOKIE,
  CSRF_HEADER,
  LOGIN_COOKIE,
  LOGIN_PATH,
  PASSWORD_PATH,
  SESSION_COOKIE,
  type GateUser,
} from "./protocol";
import { qrModules } from "./qr.server";

const AUTH_TIMEOUT_MS = 20_000; // scrypt waits for a free slot under load
const MAX_AUTH_BODY = 8 * 1024;
const TOKEN_REFRESH_MARGIN_S = 30;
const MAX_CACHED_TOKENS = 1000;
const SECRET_RE = /^[A-Za-z0-9_-]{20,128}$/;

// Served without a session: the login page needs its own scripts and styles.
// Only plain file names qualify: the app router decodes and normalises paths
// itself, so anything it could read differently goes through the session check.
const PUBLIC_ASSET_RE = /^\/(?:assets|icons)(?:\/[A-Za-z0-9_-][A-Za-z0-9._-]*)+$/;
const PUBLIC_EXACT = new Set([
  "/favicon.ico",
  "/manifest.webmanifest",
  "/sw.js",
  "/offline.html",
  "/robots.txt",
]);
// Percent-encoded control characters, dots, slashes and backslashes: the router
// strips or resolves them after this gate looked at the path (e.g.
// /assets/.%01./_serverFn/x becomes /_serverFn/x), so no app path may carry them.
const AMBIGUOUS_PATH_RE = /%(?:[01][0-9a-f]|7f|2e|2f|5c)/i;

type Mode = "vivesecbox" | "standalone";

const warned = new Set<string>();
function warnOnce(message: string) {
  if (warned.has(message)) return;
  warned.add(message);
  console.warn(`[gate] ${message}`);
}

export function integrationMode(): Mode {
  const raw = (process.env.ADAPTER_INTEGRATION ?? "").trim().toLowerCase();
  if (!raw || raw === "vivesecbox") return "vivesecbox";
  if (raw !== "standalone") {
    warnOnce(`unknown ADAPTER_INTEGRATION=${JSON.stringify(raw)}: login gate stays on`);
  }
  return "standalone";
}

// ---------------------------------------------------------------- responses

function noStore(headers?: Headers): Headers {
  const out = headers ?? new Headers();
  out.set("cache-control", "no-store");
  return out;
}

function json(body: unknown, status = 200, headers?: Headers): Response {
  const out = noStore(headers);
  out.set("content-type", "application/json; charset=utf-8");
  return new Response(JSON.stringify(body), { status, headers: out });
}

function redirect(location: string, headers?: Headers): Response {
  const out = noStore(headers);
  out.set("location", location);
  return new Response(null, { status: 302, headers: out });
}

type CookieOptions = { httpOnly: boolean; maxAge?: number };

function cookie(name: string, value: string, options: CookieOptions): string {
  const parts = [`${name}=${value}`, "Path=/", "Secure", "SameSite=Strict"];
  if (options.httpOnly) parts.push("HttpOnly");
  if (options.maxAge !== undefined)
    parts.push(`Max-Age=${Math.max(0, Math.floor(options.maxAge))}`);
  return parts.join("; ");
}

function clearCookie(name: string, httpOnly = true): string {
  return cookie(name, "", { httpOnly, maxAge: 0 });
}

function readCookies(request: Request): Map<string, string> {
  const jar = new Map<string, string>();
  for (const part of (request.headers.get("cookie") ?? "").split(";")) {
    const at = part.indexOf("=");
    if (at <= 0) continue;
    const name = part.slice(0, at).trim();
    // The first value wins, as browsers send the most specific cookie first.
    if (!jar.has(name)) jar.set(name, part.slice(at + 1).trim());
  }
  return jar;
}

function secretCookie(request: Request, name: string): string | null {
  const value = readCookies(request).get(name) ?? "";
  return SECRET_RE.test(value) ? value : null;
}

const newCsrfToken = () => randomBytes(32).toString("base64url");

// ---------------------------------------------------------------- request facts

function isLoopbackAddress(address: string): boolean {
  const a = address
    .trim()
    .toLowerCase()
    .replace(/^::ffff:/, "");
  return a === "::1" || a.startsWith("127.");
}

/**
 * The browser's address for the per-client throttle and the event log. Only a
 * loopback peer (nginx, or the Tailscale relay on the box) is trusted to report
 * it: both write X-Forwarded-For so that its last entry is the address they saw.
 */
export function clientAddress(request: Request): string {
  const peer = String((request as { ip?: unknown }).ip ?? "");
  let address = peer;
  if (peer && isLoopbackAddress(peer)) {
    const forwarded = (request.headers.get("x-forwarded-for") ?? "").split(",");
    const last = forwarded[forwarded.length - 1]?.trim();
    if (last) address = last;
  }
  return address.replace(/^::ffff:/i, "").slice(0, 64);
}

function sameOrigin(request: Request): boolean {
  const site = request.headers.get("sec-fetch-site");
  if (site && site !== "same-origin") return false;
  const origin = request.headers.get("origin");
  if (origin === null) return true;
  const host = (request.headers.get("host") ?? "").toLowerCase();
  try {
    return Boolean(host) && new URL(origin).host.toLowerCase() === host;
  } catch {
    return false;
  }
}

function csrfValid(request: Request): boolean {
  if (!sameOrigin(request)) return false;
  const expected = Buffer.from(readCookies(request).get(CSRF_COOKIE) ?? "");
  const presented = Buffer.from(request.headers.get(CSRF_HEADER) ?? "");
  if (expected.length < 32 || presented.length !== expected.length) return false;
  return timingSafeEqual(presented, expected);
}

const isSafeMethod = (method: string) => method === "GET" || method === "HEAD";

// ---------------------------------------------------------------- auth service

type AuthReply = { status: number; data: Record<string, unknown> };

let serviceKey: string | null = null;

function authBase(): string | null {
  const raw = (process.env.AUTH_URL ?? "http://127.0.0.1:8097").trim().replace(/\/+$/, "");
  try {
    const url = new URL(raw);
    const host = url.hostname.replace(/^\[|\]$/g, "").toLowerCase();
    if (url.protocol === "http:" && (host === "localhost" || isLoopbackAddress(host))) return raw;
  } catch {
    // reported below
  }
  warnOnce(`AUTH_URL=${JSON.stringify(raw)} is not a loopback http:// URL; nobody can sign in`);
  return null;
}

function readServiceKey(): string | null {
  if (serviceKey) return serviceKey;
  const path = (process.env.AUTH_SERVICE_KEY_FILE ?? "/data/auth/service.key").trim();
  try {
    const key = readFileSync(path, "utf-8").trim();
    if (key.length >= 32) {
      serviceKey = key;
      return key;
    }
    warnOnce(`AUTH_SERVICE_KEY_FILE ${path} is too short`);
  } catch {
    warnOnce(`AUTH_SERVICE_KEY_FILE ${path} is not readable`);
  }
  return null;
}

async function authCall(path: string, body: Record<string, unknown>): Promise<AuthReply> {
  const base = authBase();
  const key = readServiceKey();
  if (!base || !key) return { status: 503, data: { ok: false, error: "auth_unavailable" } };
  try {
    const res = await fetch(`${base}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json", "X-Auth-Service-Key": key },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(AUTH_TIMEOUT_MS),
    });
    let data: Record<string, unknown> = {};
    try {
      data = (await res.json()) as Record<string, unknown>;
    } catch {
      data = {};
    }
    if (res.status === 401 && data.error === "service_key") {
      // The key file may have been replaced; read it again next time.
      serviceKey = null;
      console.error("[gate] the authentication service refused the service key");
      return { status: 503, data: { ok: false, error: "auth_unavailable" } };
    }
    if (res.status >= 500) return { status: 503, data: { ok: false, error: "auth_unavailable" } };
    return { status: res.status, data };
  } catch (err) {
    console.error(`[gate] authentication service unreachable: ${(err as Error).message}`);
    return { status: 503, data: { ok: false, error: "auth_unavailable" } };
  }
}

function errorCode(reply: AuthReply): string {
  return typeof reply.data.error === "string" ? reply.data.error : "auth_failed";
}

function asUser(raw: unknown): GateUser | null {
  if (!raw || typeof raw !== "object") return null;
  const u = raw as Record<string, unknown>;
  if (typeof u.username !== "string" || !u.username) return null;
  const list = (v: unknown) =>
    Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  return {
    username: u.username,
    display_name: typeof u.display_name === "string" ? u.display_name : "",
    source: u.source === "ldap" ? "ldap" : "local",
    roles: list(u.roles),
    drives: list(u.drives),
  };
}

// ---------------------------------------------------------------- tokens

type CachedToken = { token: string; expiresAt: number; user: GateUser };
const tokens = new Map<string, CachedToken>();

const cacheKey = (secret: string) => createHash("sha256").update(secret).digest("hex");

function forgetSession(secret: string | null) {
  if (secret) tokens.delete(cacheKey(secret));
}

type TokenResult =
  | { ok: true; token: string; user: GateUser }
  | { ok: false; reason: "login" | "password" | "unavailable" };

async function identityToken(secret: string): Promise<TokenResult> {
  const key = cacheKey(secret);
  const now = Date.now() / 1000;
  const cached = tokens.get(key);
  if (cached && cached.expiresAt - now > TOKEN_REFRESH_MARGIN_S) {
    return { ok: true, token: cached.token, user: cached.user };
  }
  tokens.delete(key);
  const reply = await authCall("/auth/token", { session: secret });
  const user = asUser(reply.data.user);
  if (reply.status === 200 && typeof reply.data.token === "string" && user) {
    const expiresAt = Number(reply.data.expires_at) || now;
    if (tokens.size >= MAX_CACHED_TOKENS) {
      const oldest = tokens.keys().next().value;
      if (oldest !== undefined) tokens.delete(oldest);
    }
    tokens.set(key, { token: reply.data.token, expiresAt, user });
    return { ok: true, token: reply.data.token, user };
  }
  if (reply.status === 403 && reply.data.error === "password_change_required") {
    return { ok: false, reason: "password" };
  }
  if (reply.status === 401) return { ok: false, reason: "login" };
  return { ok: false, reason: "unavailable" };
}

type SessionState =
  | { state: "none" }
  | { state: "unavailable" }
  | {
      state: "active";
      secret: string;
      user: GateUser;
      passwordChangeRequired: boolean;
      passwordMinLength: number;
    };

async function currentSession(request: Request): Promise<SessionState> {
  const secret = secretCookie(request, SESSION_COOKIE);
  if (!secret) return { state: "none" };
  const reply = await authCall("/auth/me", { session: secret });
  const user = asUser(reply.data.user);
  if (reply.status === 200 && user) {
    return {
      state: "active",
      secret,
      user,
      passwordChangeRequired: reply.data.password_change_required === true,
      passwordMinLength: Number(reply.data.password_min_length) || 12,
    };
  }
  if (reply.status === 401) {
    forgetSession(secret);
    return { state: "none" };
  }
  return { state: "unavailable" };
}

// ---------------------------------------------------------------- rooms

const normDrive = (path: string) => path.replace(/\/+$/, "");

/** The active drive: the browser's choice when the token grants it, else the first room. */
export function activeDrive(granted: string[], hint: string | null): string | null {
  if (hint) {
    const wanted = normDrive(hint.trim());
    const match = granted.find((d) => normDrive(d) === wanted);
    if (match) return match;
  }
  return granted[0] ?? null;
}

const withSlash = (path: string) => `${normDrive(path)}/`;

// ---------------------------------------------------------------- handlers

/** The body as JSON, or null when it is larger than MAX_AUTH_BODY or not an object.
 *  Read in chunks, so an oversized body is dropped before it is held in memory. */
async function readJson(request: Request): Promise<Record<string, unknown> | null> {
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (declared > MAX_AUTH_BODY) return null;
  const reader = request.body?.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  if (reader) {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_AUTH_BODY) {
        await reader.cancel().catch(() => undefined);
        return null;
      }
      chunks.push(value);
    }
  }
  try {
    const parsed = JSON.parse(Buffer.concat(chunks).toString("utf-8")) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

const str = (v: unknown, max: number) => (typeof v === "string" && v.length <= max ? v : "");

function issuerOf(uri: string): string {
  try {
    return new URL(uri).searchParams.get("issuer") ?? "";
  } catch {
    return "";
  }
}

async function sessionInfo(request: Request): Promise<Response> {
  const headers = new Headers();
  if ((readCookies(request).get(CSRF_COOKIE) ?? "").length < 32) {
    headers.append("set-cookie", cookie(CSRF_COOKIE, newCsrfToken(), { httpOnly: false }));
  }
  const session = await currentSession(request);
  if (session.state === "unavailable") {
    return json({ ok: false, error: "auth_unavailable" }, 503, headers);
  }
  if (session.state === "none") {
    if (readCookies(request).has(SESSION_COOKIE)) {
      headers.append("set-cookie", clearCookie(SESSION_COOKIE));
    }
    return json({ ok: true, standalone: true, authenticated: false }, 200, headers);
  }
  return json(
    {
      ok: true,
      standalone: true,
      authenticated: true,
      user: session.user,
      password_change_required: session.passwordChangeRequired,
      password_min_length: session.passwordMinLength,
    },
    200,
    headers,
  );
}

async function login(request: Request, body: Record<string, unknown>): Promise<Response> {
  const username = str(body.username, 256).trim().toLowerCase();
  const reply = await authCall("/auth/login", {
    username,
    password: str(body.password, 4096),
    client: clientAddress(request),
  });
  if (
    reply.status !== 200 ||
    typeof reply.data.challenge !== "string" ||
    !SECRET_RE.test(reply.data.challenge)
  ) {
    return json({ ok: false, error: errorCode(reply) }, reply.status === 200 ? 502 : reply.status);
  }
  const headers = new Headers();
  const maxAge = Number(reply.data.expires_in) || 300;
  headers.append(
    "set-cookie",
    cookie(LOGIN_COOKIE, reply.data.challenge, { httpOnly: true, maxAge }),
  );
  if (reply.data.step === "enroll") {
    const secret = str(reply.data.totp_secret, 128);
    const uri = str(reply.data.otpauth_uri, 1024);
    if (!secret || !uri) return json({ ok: false, error: "auth_failed" }, 502);
    return json(
      {
        ok: true,
        step: "enroll",
        secret,
        account: username,
        issuer: issuerOf(uri),
        qr: qrModules(uri),
      },
      200,
      headers,
    );
  }
  return json({ ok: true, step: "totp" }, 200, headers);
}

async function loginCode(request: Request, body: Record<string, unknown>): Promise<Response> {
  const challenge = secretCookie(request, LOGIN_COOKIE);
  if (!challenge) return json({ ok: false, error: "challenge_expired" }, 401);
  const reply = await authCall("/auth/login/totp", {
    challenge,
    code: str(body.code, 32),
    client: clientAddress(request),
  });
  const headers = new Headers();
  if (
    reply.status === 200 &&
    typeof reply.data.session === "string" &&
    SECRET_RE.test(reply.data.session)
  ) {
    // A session this browser held before is ended, not just forgotten.
    const previous = secretCookie(request, SESSION_COOKIE);
    if (previous) {
      forgetSession(previous);
      await authCall("/auth/logout", { session: previous, client: clientAddress(request) });
    }
    headers.append("set-cookie", clearCookie(LOGIN_COOKIE));
    headers.append("set-cookie", cookie(SESSION_COOKIE, reply.data.session, { httpOnly: true }));
    // A fresh CSRF token per session, so a value planted before the login is useless.
    headers.append("set-cookie", cookie(CSRF_COOKIE, newCsrfToken(), { httpOnly: false }));
    return json(
      { ok: true, password_change_required: reply.data.password_change_required === true },
      200,
      headers,
    );
  }
  const error = errorCode(reply);
  // A wrong code leaves the challenge usable (the service counts the attempts);
  // anything else ends it and the user starts again with the password.
  if (error !== "invalid_code" && error !== "too_many_attempts") {
    headers.append("set-cookie", clearCookie(LOGIN_COOKIE));
  }
  return json({ ok: false, error }, reply.status === 200 ? 502 : reply.status, headers);
}

async function changePassword(request: Request, body: Record<string, unknown>): Promise<Response> {
  const secret = secretCookie(request, SESSION_COOKIE);
  if (!secret) return loginRequired();
  const reply = await authCall("/auth/password", {
    session: secret,
    current_password: str(body.current_password, 4096),
    new_password: str(body.new_password, 4096),
    client: clientAddress(request),
  });
  forgetSession(secret);
  if (reply.status === 200) return json({ ok: true });
  if (reply.status === 401) return loginRequired();
  return json({ ok: false, error: errorCode(reply) }, reply.status);
}

async function logout(request: Request): Promise<Response> {
  const secret = secretCookie(request, SESSION_COOKIE);
  forgetSession(secret);
  if (secret) await authCall("/auth/logout", { session: secret, client: clientAddress(request) });
  const headers = new Headers();
  headers.append("set-cookie", clearCookie(SESSION_COOKIE));
  headers.append("set-cookie", clearCookie(LOGIN_COOKIE));
  headers.append("set-cookie", cookie(CSRF_COOKIE, newCsrfToken(), { httpOnly: false }));
  return json({ ok: true }, 200, headers);
}

function loginRequired(): Response {
  const headers = new Headers();
  headers.set(AUTH_SIGNAL_HEADER, "login");
  headers.append("set-cookie", clearCookie(SESSION_COOKIE));
  return json({ ok: false, error: "login_required" }, 401, headers);
}

function passwordRequired(): Response {
  const headers = new Headers();
  headers.set(AUTH_SIGNAL_HEADER, "password");
  return json({ ok: false, error: "password_change_required" }, 403, headers);
}

async function handleAuthEndpoint(request: Request, path: string): Promise<Response> {
  if (path === "/auth/session") {
    return isSafeMethod(request.method)
      ? sessionInfo(request)
      : json({ ok: false, error: "method_not_allowed" }, 405);
  }
  const actions: Record<string, (r: Request, b: Record<string, unknown>) => Promise<Response>> = {
    "/auth/login": login,
    "/auth/login/totp": loginCode,
    "/auth/password": changePassword,
    "/auth/logout": (r) => logout(r),
  };
  const action = actions[path];
  if (!action) return json({ ok: false, error: "not_found" }, 404);
  if (request.method !== "POST") return json({ ok: false, error: "method_not_allowed" }, 405);
  if (!csrfValid(request)) return json({ ok: false, error: "csrf" }, 403);
  const body = await readJson(request);
  if (!body) return json({ ok: false, error: "bad_request" }, 400);
  return action(request, body);
}

async function handleApi(request: Request, url: URL): Promise<Response> {
  if (!isSafeMethod(request.method) && !csrfValid(request)) {
    return json({ ok: false, error: "csrf" }, 403);
  }
  const secret = secretCookie(request, SESSION_COOKIE);
  if (!secret) return loginRequired();
  const identity = await identityToken(secret);
  if (!identity.ok) {
    if (identity.reason === "login") return loginRequired();
    if (identity.reason === "password") return passwordRequired();
    return json({ ok: false, error: "auth_unavailable" }, 503);
  }
  const rooms = identity.user.drives;
  const drive = activeDrive(rooms, request.headers.get("X-Demo-Drive"));
  const pathname = url.pathname.replace(/\/+$/, "");
  if (pathname === "/api/v1/ui/demo-config") {
    // The room picker of the standalone UI: the rooms come from the token,
    // never from the drive listing of the box.
    const driveRoot = process.env.ADAPTER_DRIVE_PREFIX ?? "/storage/drives";
    return json({
      drive: drive ? withSlash(drive) : "",
      driveRoot,
      picker: rooms.length > 1,
      drives: rooms.map(withSlash),
    });
  }
  if (!isRelayed(url.pathname))
    return json({ ok: false, error: `Not found: ${url.pathname}` }, 404);
  const headerName = (process.env.ADAPTER_IDENTITY_HEADER ?? "").trim() || "VVS-Identity";
  const identityHeaders: Record<string, string> = {
    [headerName]: identity.token,
    "VVS-User": identity.user.username,
  };
  if (drive) identityHeaders["VVS-Drive"] = encodeVvsDrive(drive);
  return relayToAdapter(request, url, identityHeaders);
}

function isPublicAsset(path: string): boolean {
  return PUBLIC_EXACT.has(path) || PUBLIC_ASSET_RE.test(path);
}

async function handlePage(request: Request, url: URL): Promise<Response | null> {
  const path = url.pathname.replace(/\/+$/, "") || "/";
  const serverFn = path.startsWith("/_serverFn");
  if (serverFn && !sameOrigin(request)) return json({ ok: false, error: "csrf" }, 403);
  const session = await currentSession(request);
  if (session.state === "unavailable") {
    return new Response("The sign-in service is not available. Try again in a minute.", {
      status: 503,
      headers: noStore(new Headers({ "content-type": "text/plain; charset=utf-8" })),
    });
  }
  const signedIn = session.state === "active";
  const mustChange = signedIn && session.passwordChangeRequired;
  const clear = new Headers();
  if (!signedIn && readCookies(request).has(SESSION_COOKIE)) {
    clear.append("set-cookie", clearCookie(SESSION_COOKIE));
  }
  if (path === LOGIN_PATH) {
    if (signedIn) return redirect(mustChange ? PASSWORD_PATH : "/");
    return null;
  }
  if (!signedIn) {
    if (serverFn || !isSafeMethod(request.method)) return loginRequired();
    return redirect(LOGIN_PATH, clear);
  }
  if (path === PASSWORD_PATH) return null;
  if (mustChange) {
    if (serverFn || !isSafeMethod(request.method)) return passwordRequired();
    return redirect(PASSWORD_PATH);
  }
  return null;
}

/**
 * The login gate. Returns the response for a request it decides, or null to
 * let the app render it (signed-in pages, the login page, public assets). In
 * vivesecbox mode it only answers /auth/session, so the UI can tell the modes
 * apart; everything else is left to the existing demo proxy.
 */
export async function handleLoginGate(request: Request): Promise<Response | null> {
  const url = new URL(request.url);
  const path = url.pathname;
  if (integrationMode() === "vivesecbox") {
    return path === "/auth/session" ? json({ ok: true, standalone: false }) : null;
  }
  if (AMBIGUOUS_PATH_RE.test(path)) return json({ ok: false, error: "bad_path" }, 400);
  if (path === "/auth" || path.startsWith("/auth/")) return handleAuthEndpoint(request, path);
  if (path.startsWith("/api/")) {
    return path.startsWith("/api/v1/")
      ? handleApi(request, url)
      : json({ ok: false, error: "not_found" }, 404);
  }
  if (isPublicAsset(path)) return null;
  return handlePage(request, url);
}
