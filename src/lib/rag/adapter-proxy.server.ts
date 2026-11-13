// Demo/dev only: forwards the browser's /api/v1/* calls to the AIBox adapter.
//
// Embedded in the ViVeSecBox the same relative URLs travel through their tunnel,
// which injects the authoritative VVS-* headers. When the UI is served straight
// off the box (LAN demo) nothing does that, so this proxy fills them in from env
// and keeps the client code identical in both worlds.
//
// In standalone mode (ADAPTER_INTEGRATION=standalone) the login gate
// (src/lib/auth/gate.server.ts) answers every /api/v1 call before this demo
// proxy is reached, and relays through relayToAdapter with the signed identity.

import { Buffer } from "node:buffer";
import process from "node:process";

const ADAPTER_URL = () => process.env.ADAPTER_URL ?? "http://127.0.0.1:80";
const DEMO_DRIVE = () => process.env.ADAPTER_DEMO_DRIVE ?? "/storage/drives/finance/";
const DEMO_USER = () => process.env.ADAPTER_DEMO_USER ?? "demo";
const DRIVE_ROOT = () => process.env.ADAPTER_DRIVE_PREFIX ?? "/storage/drives";
const PICKER = () => (process.env.ADAPTER_DEMO_DRIVE_PICKER ?? "") === "1";

const encodeDrive = (drive: string) => Buffer.from(drive, "utf-8").toString("base64url");

// Only what the UI itself calls is relayed. Box-only endpoints (pairing, sync,
// storage unlock, identity lifecycle, ws-fs) must never become reachable from
// the network just because this server talks to the adapter over loopback.
const RELAYED_EXACT = new Set(["/api/v1/status", "/api/v1/version", "/api/v1/ui-version"]);
// The box sync read carries no identity and no ACL (E03). It is relayed only for
// the demo drive picker, and only for the drive root, i.e. the drive names.
const CHILDREN_PATH = "/api/v1/index/get/children";
// The standalone file store's upload (adapter drivestore.py): the only relayed
// call whose body is streamed rather than buffered.
const STORE_UPLOAD_PATH = "/api/v1/ui/store/upload";
export const isRelayed = (pathname: string) => {
  const path = pathname.replace(/\/+$/, "");
  return RELAYED_EXACT.has(path) || path.startsWith("/api/v1/ui/");
};

export const encodeVvsDrive = encodeDrive;

// Hop-by-hop headers belong to one connection and must not be relayed. `expect`
// is the one that actually bites: any client sending `Expect: 100-continue`
// (curl does it automatically above 1 KB, which the voice upload always is)
// makes the outbound fetch fail with a bare "fetch failed".
const HOP_BY_HOP = [
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "expect",
  "content-length",
];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

/**
 * Removes every identity header a browser sent. VVS-Drive/User are set below,
 * but VVS-Other-Drives would widen the adapter's scope and VVS-Identity /
 * VVS-Session would bind to a token the box never issued: only the box may set
 * any of them. The adapter's identity header name is configurable.
 */
export function dropClientIdentityHeaders(headers: Headers): void {
  const identityHeader = (process.env.ADAPTER_IDENTITY_HEADER ?? "").trim().toLowerCase();
  const names = [...headers.keys()].filter(
    (name) =>
      name.toLowerCase().startsWith("vvs-") ||
      (identityHeader && name.toLowerCase() === identityHeader),
  );
  for (const name of names) headers.delete(name);
}

/** Handles /api/v1/* for the box-hosted demo, or null when it is not our call. */
export async function handleAdapterProxy(request: Request): Promise<Response | null> {
  const url = new URL(request.url);
  if (!url.pathname.startsWith("/api/v1/")) return null;

  if (url.pathname === "/api/v1/ui/demo-config") {
    return json({ drive: DEMO_DRIVE(), driveRoot: DRIVE_ROOT(), picker: PICKER() });
  }

  const pathname = url.pathname.replace(/\/+$/, "");
  let pickerBody: ArrayBuffer | undefined;
  if (pathname === CHILDREN_PATH && PICKER() && request.method === "POST") {
    pickerBody = await request.arrayBuffer();
    let asked = "";
    try {
      asked = String(
        (JSON.parse(new TextDecoder().decode(pickerBody)) as { path?: unknown }).path ?? "",
      );
    } catch {
      asked = "";
    }
    if (asked.replace(/\/+$/, "") !== DRIVE_ROOT().replace(/\/+$/, "")) {
      return json({ ok: false, error: `Not found: ${url.pathname}` }, 404);
    }
  } else if (!isRelayed(url.pathname)) {
    return json({ ok: false, error: `Not found: ${url.pathname}` }, 404);
  }

  // The drive picker is a demo affordance, so the hint is only honoured when it
  // is explicitly enabled; otherwise the configured drive always wins.
  const hinted = PICKER() ? request.headers.get("X-Demo-Drive") : null;
  const drive = hinted || DEMO_DRIVE();

  return relayToAdapter(
    request,
    url,
    { "VVS-Drive": encodeDrive(drive), "VVS-User": DEMO_USER() },
    pickerBody,
  );
}

/**
 * Forwards one browser call to the adapter with the identity headers the
 * caller decided on. Whatever identity the browser sent is dropped first, and
 * so are its cookies: the login gate's session secret must never leave this
 * server.
 */
export async function relayToAdapter(
  request: Request,
  url: URL,
  identity: Record<string, string>,
  bodyOverride?: ArrayBuffer,
): Promise<Response> {
  const headers = new Headers(request.headers);
  dropClientIdentityHeaders(headers);
  headers.delete("X-Demo-Drive");
  headers.delete("X-AIBox-CSRF");
  headers.delete("cookie");
  headers.delete("authorization");
  headers.delete("host");
  for (const h of HOP_BY_HOP) headers.delete(h);
  for (const [name, value] of Object.entries(identity)) headers.set(name, value);
  // Marks the call as relayed: the adapter must not mistake it for a process on
  // the box just because it arrives over loopback.
  headers.set("X-Forwarded-Host", url.host || "unknown");

  const target = `${ADAPTER_URL()}${url.pathname}${url.search}`;
  // An upload (up to the store's limit, 20 MB by default) streams through
  // instead of being held in this server's memory; the adapter reads the
  // chunked body as it arrives and enforces the limit itself.
  const streamed =
    !bodyOverride &&
    request.method === "POST" &&
    request.body !== null &&
    url.pathname.replace(/\/+$/, "") === STORE_UPLOAD_PATH;
  const body = streamed
    ? request.body
    : (bodyOverride ??
      (request.method === "GET" || request.method === "HEAD"
        ? undefined
        : await request.arrayBuffer()));

  try {
    const init: RequestInit & { duplex?: "half" } = { method: request.method, headers, body };
    if (streamed) init.duplex = "half";
    const res = await fetch(target, init);
    const out = new Headers(res.headers);
    out.delete("content-encoding");
    out.delete("content-length");
    out.delete("set-cookie");
    return new Response(res.body, { status: res.status, headers: out });
  } catch (err) {
    return json(
      { ok: false, error: err instanceof Error ? err.message : "adapter unreachable" },
      502,
    );
  }
}
