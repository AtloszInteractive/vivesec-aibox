// Retrieval-augmented answer pipeline — the HÍD orchestration (spec §2).
//
// Wires the in-house bridge stages around the swappable rag-engine:
//
//   user + docs ──► ACL pre-filter ──► /search (rag-engine) ──► T1-T4 routing
//                                                                   │
//                                            ┌──────────────────────┘
//                                            ▼
//                       T4: guarded refusal
//                       T1/T2: cited extract (no free generation)
//                       T3: LLM synthesis (Ollama) ──► grounding-guard ──► answer
//
// Generation (the Ollama call) is intentionally a single injected function so
// this stays testable and the LLM lives OUTSIDE the rag-engine (spec §2).

import { computeAllowedFileIds, type DocAcl, type UserContext } from "./acl";
import type { Hit, SearchFilters } from "./contract";
import { searchEngine } from "./engine-client";
import { checkGrounding, refusal } from "./grounding-guard";
import { routeQuery, type Tier } from "./routing";

export type AnswerResult = {
  tier: Tier;
  answer: string;
  refused: boolean;
  citations: Hit[];
  reason: string;
};

// The bridge injects the actual Ollama call here (kept out of this module so the
// pipeline is pure-ish and the LLM boundary stays explicit).
export type GenerateFn = (args: {
  query: string;
  context: Hit[];
  lang?: string;
}) => Promise<string>;

export type AnswerOptions = {
  query: string;
  user: UserContext;
  docs: readonly DocAcl[];
  lang?: string;
  topK?: number;
  filters?: SearchFilters;
  generate: GenerateFn;
};

/** A T1/T2 answer is the strongest cited chunk text, verbatim — no generation. */
function citedExtract(hits: Hit[]): string {
  const top = hits[0];
  if (!top) return "";
  const src = top.source_ref?.filename ?? top.file_id;
  return `${top.chunk_text}\n\n— ${src}${top.page ? `, p. ${top.page}` : ""}`;
}

export async function answerQuestion(opts: AnswerOptions): Promise<AnswerResult> {
  // 1) ACL pre-filter — the bridge owns authorization (invariant 2).
  const allowedFileIds = computeAllowedFileIds(opts.user, opts.docs);

  // 2) Retrieval, constrained to the allowed set.
  const search = await searchEngine({
    query: opts.query,
    allowed_file_ids: allowedFileIds,
    top_k: opts.topK ?? 5,
    language_hint: opts.lang,
    filters: opts.filters,
  });
  const hits = search.hits;

  // 3) Tier routing — no grounding forces a guarded refusal (T4).
  const route = routeQuery(opts.query, hits.length > 0);

  if (route.tier === "T4") {
    const r = refusal(route.reason);
    return { tier: "T4", answer: r.answer, refused: true, citations: [], reason: r.reason };
  }

  // 4a) T1/T2 — deterministic cited extract, no free generation.
  if (route.tier === "T1" || route.tier === "T2") {
    return {
      tier: route.tier,
      answer: citedExtract(hits),
      refused: false,
      citations: hits,
      reason: route.reason,
    };
  }

  // 4b) T3 — LLM synthesis over retrieved context, then guard it.
  const draft = await opts.generate({ query: opts.query, context: hits, lang: opts.lang });
  const verdict = checkGrounding(draft, hits);
  if (!verdict.ok) {
    const r = refusal(
      `grounding-guard blocked the answer: ${verdict.reason} ` +
        `(ungrounded: ${verdict.ungroundedNumbers.join(", ") || "-"})`,
    );
    return { tier: "T3", answer: r.answer, refused: true, citations: hits, reason: r.reason };
  }

  return {
    tier: "T3",
    answer: draft,
    refused: false,
    citations: hits,
    reason: route.reason,
  };
}
