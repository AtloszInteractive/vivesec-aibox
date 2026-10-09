// Demo/dev only: forwards the browser's /api/v1/* calls to the AIBox adapter.
//
// Embedded in the ViVeSecBox the same relative URLs travel through their tunnel,
// which injects the authoritative VVS-* headers. When the UI is served straight
// off the box (LAN demo) nothing does that, so this proxy fills them in from env
// and keeps the client code identical in both worlds.

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
const RELAYED_EXACT = new Set([
  "/api/v1/status",
  "/api/v1/version",
  "/api/v1/ui-version",
  "/api/v1/index/get/children",
]);
const isRelayed = (pathname: string) => {
  const path = pathname.replace(/\/+$/, "");
  return RELAYED_EXACT.has(path) || path.startsWith("/api/v1/ui/");
};

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

/** Handles /api/v1/* for the box-hosted demo, or null when it is not our call. */
export async function handleAdapterProxy(request: Request): Promise<Response | null> {
  const url = new URL(request.url);
  if (!url.pathname.startsWith("/api/v1/")) return null;

  if (url.pathname === "/api/v1/ui/demo-config") {
    return json({ drive: DEMO_DRIVE(), driveRoot: DRIVE_ROOT(), picker: PICKER() });
  }

  if (!isRelayed(url.pathname)) {
    return json({ ok: false, error: `Not found: ${url.pathname}` }, 404);
  }

  // The drive picker is a demo affordance, so the hint is only honoured when it
  // is explicitly enabled; otherwise the configured drive always wins.
  const hinted = PICKER() ? request.headers.get("X-Demo-Drive") : null;
  const drive = hinted || DEMO_DRIVE();

  const headers = new Headers(request.headers);
  headers.delete("X-Demo-Drive");
  headers.delete("host");
  for (const h of HOP_BY_HOP) headers.delete(h);
  headers.set("VVS-Drive", encodeDrive(drive));
  headers.set("VVS-User", DEMO_USER());
  // Marks the call as relayed: the adapter must not mistake it for a process on
  // the box just because it arrives over loopback.
  headers.set("X-Forwarded-Host", url.host || "unknown");

  const target = `${ADAPTER_URL()}${url.pathname}${url.search}`;
  const body =
    request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer();

  try {
    const res = await fetch(target, { method: request.method, headers, body });
    const out = new Headers(res.headers);
    out.delete("content-encoding");
    out.delete("content-length");
    return new Response(res.body, { status: res.status, headers: out });
  } catch (err) {
    return json(
      { ok: false, error: err instanceof Error ? err.message : "adapter unreachable" },
      502,
    );
  }
}
