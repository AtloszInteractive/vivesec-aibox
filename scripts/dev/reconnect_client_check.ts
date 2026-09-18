// Ad-hoc check for the reconnect fixes in adapter-client.ts (run: npx tsx scripts/dev/reconnect_client_check.ts)
import { adapterSession, adapterListJobs } from "../../src/lib/rag/adapter-client";

(globalThis as { window?: unknown }).window = {};

let initCalls = 0;
let initMode: "fail" | "http503" | "ok" = "fail";
let jobsMode: "adapter404" | "tunnel404" | "ok" = "adapter404";

(globalThis as { fetch: unknown }).fetch = async (input: string) => {
  if (input.endsWith("/api/v1/ui/init")) {
    initCalls += 1;
    if (initMode === "fail") throw new TypeError("fetch failed");
    if (initMode === "http503") return new Response("{}", { status: 503 });
    return new Response(JSON.stringify({ ok: true, drive: "/storage/drives/x/", user: "u1" }), { status: 200 });
  }
  if (input.endsWith("/api/v1/ui/jobs")) {
    if (jobsMode === "adapter404") return new Response(JSON.stringify({ ok: false, error: "Not found: /api/v1/ui/jobs" }), { status: 404 });
    if (jobsMode === "tunnel404") return new Response("<html>box offline</html>", { status: 404 });
    return new Response(JSON.stringify({ ok: true, jobs: [] }), { status: 200 });
  }
  throw new Error("unexpected " + input);
};

function assert(cond: unknown, msg: string) {
  if (!cond) { console.error("FAIL:", msg); process.exitCode = 1; } else console.log("ok  :", msg);
}

(async () => {
  await adapterSession().then(() => assert(false, "network failure must reject"), () => assert(true, "network failure rejects"));
  initMode = "http503";
  await adapterSession().then(() => assert(false, "HTTP 503 must reject"), () => assert(true, "HTTP 503 rejects (not cached as empty)"));
  initMode = "ok";
  const s = await adapterSession();
  assert(s.drive === "/storage/drives/x/" && s.user === "u1", "success after failures returns the real session");
  const before = initCalls;
  await adapterSession();
  assert(initCalls === before, "success is cached (no extra /ui/init call)");
  assert(initCalls === 3, "exactly one /ui/init per attempt (" + initCalls + ")");

  assert((await adapterListJobs({ drive: "d" })) === null, "adapter's own 404 -> jobs unsupported (null)");
  jobsMode = "tunnel404";
  await adapterListJobs({ drive: "d" }).then(() => assert(false, "foreign 404 must throw"), (e) => assert(e.status === 404, "foreign 404 -> transient error, not 'unsupported'"));
  jobsMode = "ok";
  assert(Array.isArray(await adapterListJobs({ drive: "d" })), "200 -> job list");
})();
