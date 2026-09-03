// T1-T4 answer-tier routing (the HÍD decides how a question is answered).
//
// The tier governs how strictly the answer must be grounded and whether the LLM
// may synthesise prose at all. Numbers/figures may ONLY come from verbatim T1
// quotes — never from free generation (enforced later by the grounding-guard).
//
//   T1  QUOTE      Verbatim extract / figure lookup. Answer = cited chunk text.
//                  No LLM prose around numbers. Highest trust.
//   T2  FACT       Structured fact / table lookup (-> `facts` / SQL path).
//                  Deterministic value from a table cell, cited.
//   T3  SYNTHESIS  Multi-passage summary / explanation. LLM prose ALLOWED, but
//                  every claim must be grounded in retrieved chunks + cited.
//   T4  REFUSE     Out-of-scope / no grounded support / unsafe. Guarded refusal,
//                  no generation over un-retrieved knowledge.
//
// This is a deterministic heuristic skeleton. A learned classifier can replace
// `routeQuery` later; the Tier enum + contract stay stable.

export type Tier = "T1" | "T2" | "T3" | "T4";

export type RouteDecision = {
  tier: Tier;
  reason: string;
  allowGeneration: boolean; // may the LLM produce prose? (T3 only)
  numbersFromQuotesOnly: boolean; // figures must come from verbatim quotes
};

// Multilingual cue lists (EN primary; HU/DA/DE supported — spec language policy).
const QUOTE_CUES = [
  "quote",
  "exact",
  "verbatim",
  "wording",
  "idézet",
  "pontos",
  "szó szerint",
  "ordret",
  "citat",
  "wörtlich",
  "zitat",
];

const FACT_CUES = [
  "how much",
  "how many",
  "what is the",
  "amount",
  "total",
  "revenue",
  "figure",
  "number",
  "date",
  "deadline",
  "mennyi",
  "összeg",
  "árbevétel",
  "dátum",
  "határidő",
  "hvor meget",
  "beløb",
  "wie viel",
  "betrag",
];

const SYNTHESIS_CUES = [
  "summarize",
  "summary",
  "explain",
  "compare",
  "why",
  "overview",
  "összefoglal",
  "magyarázd",
  "hasonlítsd",
  "miért",
  "áttekint",
  "opsummer",
  "forklar",
  "zusammenfass",
  "erkläre",
  "vergleich",
];

function matchesAny(text: string, cues: readonly string[]): boolean {
  return cues.some((c) => text.includes(c));
}

/**
 * Route a question to a tier. `hasGroundedHits` reflects whether retrieval found
 * any allowed chunks — no grounding forces T4 regardless of phrasing.
 */
export function routeQuery(query: string, hasGroundedHits: boolean): RouteDecision {
  if (!hasGroundedHits) {
    return {
      tier: "T4",
      reason: "no grounded retrieval hits within the allowed document set",
      allowGeneration: false,
      numbersFromQuotesOnly: true,
    };
  }

  const q = query.toLowerCase();

  if (matchesAny(q, QUOTE_CUES)) {
    return {
      tier: "T1",
      reason: "verbatim quote/extract requested",
      allowGeneration: false,
      numbersFromQuotesOnly: true,
    };
  }

  if (matchesAny(q, FACT_CUES)) {
    return {
      tier: "T2",
      reason: "structured fact / figure lookup",
      allowGeneration: false,
      numbersFromQuotesOnly: true,
    };
  }

  if (matchesAny(q, SYNTHESIS_CUES)) {
    return {
      tier: "T3",
      reason: "multi-passage synthesis requested",
      allowGeneration: true,
      numbersFromQuotesOnly: true,
    };
  }

  // Default: allow grounded synthesis but keep numbers quote-bound.
  return {
    tier: "T3",
    reason: "default grounded synthesis",
    allowGeneration: true,
    numbersFromQuotesOnly: true,
  };
}
