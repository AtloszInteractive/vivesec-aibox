// Server-only generation for the HÍD (spec §2).
//
// Generation lives in the HÍD, NOT in the rag-engine. The bridge calls the
// configured LLM with a strict grounding prompt: answer ONLY from retrieved
// context, cite sources, refuse if absent. The grounding-guard re-checks numbers
// after generation, regardless of which model produced the answer.
//
// PROVIDERS (RAG_GEN_PROVIDER): the appliance default is `ollama` — fully
// on-device, nothing leaves the box (invariant 1). `openai` / `gemini` are
// DEV/TEST-ONLY conveniences (the local LLM is slow on dev machines) that route
// document context to an EXTERNAL API. They are HARD-GATED behind
// RAG_ALLOW_CLOUD_LLM=true and must ONLY be used with the synthetic demo corpus —
// NEVER with real confidential documents and NEVER on the appliance.

import process from "node:process";

import type { Hit } from "./contract";

const GEN_TIMEOUT_MS = 180_000;
const PING_TIMEOUT_MS = 3_000;

export type GenArgs = { query: string; context: Hit[]; lang?: string };

// ---- shared prompt + context formatting ---------------------------------- //
const LANG_NAME: Record<string, string> = {
  EN: "English",
  HU: "Hungarian",
  DA: "Danish",
  DE: "German",
};

function langName(lang?: string): string {
  if (!lang) return "";
  return LANG_NAME[lang.trim().toUpperCase()] ?? lang.trim();
}

function buildContext(hits: readonly Hit[]): string {
  // Tag each passage so the model (and the reader) can cite it.
  return hits
    .map((h) => {
      const src = h.source_ref?.filename ?? h.file_id;
      const loc = h.page ? `, p. ${h.page}` : "";
      return `[${src}${loc} #${h.chunk_index}]\n${h.chunk_text}`;
    })
    .join("\n\n");
}

const SYSTEM_PROMPT =
  "You are ViVeSec AI, an on-device assistant. Answer ONLY from the provided " +
  "context passages. If the answer is not in the context, say you cannot find it " +
  "in the available documents and do not guess. Never invent figures, dates, or " +
  "names — any number you state must appear verbatim in the context. Cite the " +
  "source tags you used.";

function buildUserContent(args: GenArgs): string {
  const target = langName(args.lang);
  const langLine = target ? `\nRespond in ${target}.` : "";
  return (
    `Question: ${args.query}\n\n` +
    `Context passages:\n${buildContext(args.context)}\n\n` +
    `Answer using only the context above and cite the source tags.${langLine}`
  );
}

// ---- provider selection -------------------------------------------------- //
type ProviderKind = "ollama" | "openai" | "gemini";

function providerKind(): ProviderKind {
  const p = (process.env.RAG_GEN_PROVIDER ?? "ollama").trim().toLowerCase();
  return p === "openai" || p === "gemini" ? p : "ollama";
}

// Cloud providers are OFF unless explicitly opted in. This is the guard that
// keeps confidential context from ever leaving an appliance by accident.
function cloudAllowed(): boolean {
  return (process.env.RAG_ALLOW_CLOUD_LLM ?? "").trim().toLowerCase() === "true";
}

function ollamaUrl(): string {
  return process.env.OLLAMA_URL ?? "http://127.0.0.1:11434";
}

function genModel(fallback: string): string {
  return process.env.RAG_GEN_MODEL ?? fallback;
}

type CloudCfg = { provider: ProviderKind; baseUrl: string; apiKey: string; model: string };

function cloudConfig(kind: "openai" | "gemini"): CloudCfg {
  if (kind === "openai") {
    return {
      provider: "openai",
      baseUrl: (process.env.OPENAI_BASE_URL ?? "https://api.openai.com/v1").replace(/\/$/, ""),
      apiKey: process.env.OPENAI_API_KEY ?? "",
      model: genModel("gpt-4o-mini"),
    };
  }
  // Gemini via its OpenAI-compatible endpoint — one code path for both clouds.
  return {
    provider: "gemini",
    baseUrl: (process.env.GEMINI_BASE_URL ?? "https://generativelanguage.googleapis.com/v1beta/openai").replace(/\/$/, ""),
    apiKey: process.env.GEMINI_API_KEY ?? "",
    model: genModel("gemini-2.0-flash"),
  };
}

// ---- ollama (default, on-device) ----------------------------------------- //
/**
 * Grounded generation via the shared Ollama container. Matches the GenerateFn
 * shape used by pipeline.ts. The pipeline only calls this for T3 (synthesis);
 * the result is then passed through the grounding-guard before the user sees it.
 */
export async function ollamaGenerate(args: GenArgs): Promise<string> {
  const res = await fetch(`${ollamaUrl()}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: genModel("qwen2.5:14b"),
      stream: false,
      options: { temperature: 0.1, repeat_penalty: 1.15, num_predict: 512 },
      messages: [
        { role: "system", content: SYSTEM_PROMPT },
        { role: "user", content: buildUserContent(args) },
      ],
    }),
    signal: AbortSignal.timeout(GEN_TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`Ollama HTTP ${res.status}`);
  const json = (await res.json()) as { message?: { content?: string } };
  return (json.message?.content ?? "").trim();
}

// ---- OpenAI-compatible (openai + gemini compat), DEV/TEST ONLY ------------ //
async function cloudGenerate(args: GenArgs, cfg: CloudCfg): Promise<string> {
  if (!cloudAllowed()) {
    throw new Error(
      "Cloud LLM is disabled. Set RAG_ALLOW_CLOUD_LLM=true to enable (dev/test only, demo corpus only).",
    );
  }
  if (!cfg.apiKey) throw new Error(`${cfg.provider} API key missing (set the provider's *_API_KEY)`);

  const res = await fetch(`${cfg.baseUrl}/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${cfg.apiKey}` },
    body: JSON.stringify({
      model: cfg.model,
      temperature: 0.1,
      max_tokens: 512,
      messages: [
        { role: "system", content: SYSTEM_PROMPT },
        { role: "user", content: buildUserContent(args) },
      ],
    }),
    signal: AbortSignal.timeout(GEN_TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`${cfg.provider} HTTP ${res.status}`);
  const json = (await res.json()) as { choices?: { message?: { content?: string } }[] };
  return (json.choices?.[0]?.message?.content ?? "").trim();
}

/**
 * Resolve the active generator (a GenerateFn) from RAG_GEN_PROVIDER. The default
 * is on-device Ollama; cloud providers are returned only as a thin wrapper that
 * still enforces the RAG_ALLOW_CLOUD_LLM guard at call time.
 */
export function selectGenerator(): (args: GenArgs) => Promise<string> {
  const kind = providerKind();
  if (kind === "ollama") return ollamaGenerate;
  const cfg = cloudConfig(kind);
  return (args: GenArgs) => cloudGenerate(args, cfg);
}

// ---- health -------------------------------------------------------------- //
export async function ollamaAvailable(): Promise<{ ok: boolean; model: string }> {
  try {
    const res = await fetch(`${ollamaUrl()}/api/tags`, {
      method: "GET",
      signal: AbortSignal.timeout(PING_TIMEOUT_MS),
    });
    return { ok: res.ok, model: genModel("qwen2.5:14b") };
  } catch {
    return { ok: false, model: genModel("qwen2.5:14b") };
  }
}

/** Provider-aware health: pings Ollama; for cloud, reports config readiness
 *  (we don't spend a paid API call just to check liveness). */
export async function generatorHealth(): Promise<{ ok: boolean; model: string; provider: string }> {
  const kind = providerKind();
  if (kind === "ollama") {
    const a = await ollamaAvailable();
    return { ok: a.ok, model: a.model, provider: "ollama" };
  }
  const cfg = cloudConfig(kind);
  return { ok: cloudAllowed() && cfg.apiKey.length > 0, model: cfg.model, provider: cfg.provider };
}
