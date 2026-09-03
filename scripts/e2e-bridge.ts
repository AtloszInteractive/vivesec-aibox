// E2E pipeline test for the HÍD — exercises the full bridge chain against the
// LIVE rag-engine, WITHOUT booting the TanStack dev server.
//
//   ACL pre-filter -> /search (rag-engine) -> T1-T4 routing -> generation -> guard
//
// By default it uses deterministic STUB generators (no cloud cost) so the whole
// chain — including the grounding-guard pass/block paths — can be validated on any
// machine. Pass `--cloud` to route the single T3 synthesis case through the
// configured provider (selectGenerator), which honours RAG_ALLOW_CLOUD_LLM and the
// provider's API key. Use the synthetic demo corpus ONLY with cloud providers.
//
// Prereqs: rag-engine up on :8081 with the demo corpus ingested
//   (rag-engine: uvicorn app.main:app --port 8081 ; python ingest_corpus.py)
//
// Run (Node + jiti, no bun/tsx needed):
//   node node_modules/jiti/lib/jiti-cli.mjs scripts/e2e-bridge.ts
//   # real cloud (you set the key in the shell/.env first):
//   $env:RAG_ALLOW_CLOUD_LLM="true"; $env:RAG_GEN_PROVIDER="gemini"
//   node node_modules/jiti/lib/jiti-cli.mjs scripts/e2e-bridge.ts --cloud

import process from "node:process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import type { UserContext } from "../src/lib/rag/acl";
import { engineHealth } from "../src/lib/rag/engine-client";
import { selectGenerator } from "../src/lib/rag/generate.server";
import { answerQuestion, type AnswerResult, type GenerateFn } from "../src/lib/rag/pipeline";
import { getDocRegistry, resolveUser } from "../src/lib/rag/registry.server";

// Load repo-root .env so the bridge sees RAG_*/provider keys without needing the
// Vite dev server. Shell-provided vars win (we never overwrite an existing one).
function loadDotEnv() {
  const envPath = join(dirname(fileURLToPath(import.meta.url)), "..", ".env");
  let text: string;
  try {
    text = readFileSync(envPath, "utf8");
  } catch {
    return; // no .env — rely on the shell environment
  }
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let val = line.slice(eq + 1).trim();
    // strip an inline comment that isn't part of a quoted value
    if (!val.startsWith('"') && !val.startsWith("'")) val = val.replace(/\s+#.*$/, "").trim();
    val = val.replace(/^["']|["']$/g, "");
    if (key && process.env[key] === undefined) process.env[key] = val;
  }
}

loadDotEnv();

const useCloud = process.argv.includes("--cloud");

// ---- deterministic stub generators (no external calls) ------------------- //

// Clean stub: a grounded summary that introduces NO numbers of its own, so the
// grounding-guard must pass it. Cites the retrieved source tags.
const cleanStub: GenerateFn = async ({ context }) => {
  const cites = context
    .map((h) => h.source_ref?.filename ?? h.file_id)
    .filter((v, i, a) => a.indexOf(v) === i)
    .join(", ");
  return (
    "Based on the retrieved documents, the relevant findings are summarised " +
    `above. See the cited passages for details. Sources: ${cites}.`
  );
};

// Hallucinating stub: states a figure that is NOT in any retrieved chunk, so the
// grounding-guard MUST block it (refused === true).
const hallucinatedStub: GenerateFn = async () =>
  "The confirmed total is 999,999,999 USD across all departments.";

// ---- test harness -------------------------------------------------------- //

const docs = getDocRegistry();
const fullUser = resolveUser(); // demo: full access
const noScopeUser: UserContext = { grantedScopes: [], clearance: "internal" };

type Check = { name: string; pass: boolean; detail: string };
const results: Check[] = [];

function record(name: string, pass: boolean, detail: string) {
  results.push({ name, pass, detail });
  const tag = pass ? "PASS" : "FAIL";
  console.log(`[${tag}] ${name} — ${detail}`);
}

function summarise(r: AnswerResult): string {
  const cites = r.citations.map((c) => c.file_id).join(",") || "-";
  const ans = r.answer.slice(0, 60).replace(/\s+/g, " ").trim();
  return `tier=${r.tier} refused=${r.refused} cites=[${cites}] ans="${ans}…"`;
}

async function main() {
  console.log(`\n== E2E bridge pipeline ${useCloud ? "(CLOUD generator)" : "(stub generators)"} ==\n`);

  // 0) engine reachable
  try {
    const h = await engineHealth();
    record("engine /healthz", h.index_ready === true, `index_ready=${h.index_ready} backend=${h.vector_backend}`);
  } catch (err) {
    record("engine /healthz", false, err instanceof Error ? err.message : "unreachable");
    console.log("\nEngine not reachable — start it on :8081 and ingest the corpus first.\n");
    process.exit(1);
  }

  // 1) T2 fact lookup — routes to T2, deterministic cited extract (no generation).
  {
    const r = await answerQuestion({
      query: "What was the total Q4 revenue?",
      user: fullUser,
      docs,
      lang: "EN",
      topK: 5,
      generate: cleanStub,
    });
    record(
      "T2 fact lookup is cited & not refused",
      r.tier === "T2" && !r.refused && r.citations.length > 0,
      summarise(r),
    );
  }

  // 2) T1 verbatim quote — routes to T1 cited extract.
  {
    const r = await answerQuestion({
      query: "Give me the exact wording of the security audit conclusion.",
      user: fullUser,
      docs,
      lang: "EN",
      topK: 5,
      generate: cleanStub,
    });
    record("T1 quote is cited & not refused", r.tier === "T1" && !r.refused && r.citations.length > 0, summarise(r));
  }

  // 3) T3 synthesis — clean grounded generation passes the guard.
  {
    const gen = useCloud ? selectGenerator() : cleanStub;
    const r = await answerQuestion({
      query: "Summarize the key findings of the security audit.",
      user: fullUser,
      docs,
      lang: "EN",
      topK: 5,
      generate: gen,
    });
    record(
      "T3 synthesis answers (grounded, not refused)",
      r.tier === "T3" && !r.refused && r.answer.length > 0,
      summarise(r),
    );
  }

  // 4) T3 synthesis — hallucinated figure MUST be blocked by the grounding-guard.
  //    (Stub only — we never ask a real model to hallucinate on purpose.)
  //    Query uses a synthesis cue ("explain") and NO fact cue, so it routes to T3.
  {
    const r = await answerQuestion({
      query: "Explain the overall security posture described in the documents.",
      user: fullUser,
      docs,
      lang: "EN",
      topK: 5,
      generate: hallucinatedStub,
    });
    record("grounding-guard blocks ungrounded numbers", r.tier === "T3" && r.refused === true, summarise(r));
  }

  // 5) ACL — a user with no scopes gets an empty allowed set => refusal, no leak.
  {
    const r = await answerQuestion({
      query: "What was the total Q4 revenue?",
      user: noScopeUser,
      docs,
      lang: "EN",
      topK: 5,
      generate: cleanStub,
    });
    record(
      "no-scope user is refused with zero citations (no leak)",
      r.refused === true && r.citations.length === 0,
      summarise(r),
    );
  }

  const failed = results.filter((r) => !r.pass).length;
  console.log(`\nRESULT: ${failed === 0 ? "ALL PASS" : `${failed} FAILED`}\n`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((err) => {
  console.error("E2E run crashed:", err);
  process.exit(1);
});
