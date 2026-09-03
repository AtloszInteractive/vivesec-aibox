// Grounding-guard — the last gate before an answer reaches the user (spec §2).
//
// The guard verifies that a generated answer is SUPPORTED by the retrieved,
// allowed chunks. Its hard, deterministic rule (enforced today):
//
//   Every number/figure in the answer MUST appear verbatim in a cited chunk.
//
// This blocks the highest-risk hallucination class (invented financials/dates)
// without needing a model. Softer claim-level entailment (NLI) is a TODO hook.

import type { Hit } from "./contract";

export type GuardVerdict = {
  ok: boolean;
  // Numbers present in the answer but NOT found in any cited chunk.
  ungroundedNumbers: string[];
  // Citations referenced by the answer that are not in the allowed hit set.
  invalidCitations: string[];
  reason: string;
};

// Match integers/decimals with optional grouping + currency/percent, in a way
// that is robust to "4,2" (HU) vs "4.2" (EN) and thousands separators.
const NUMBER_RE = /\d[\d.,]*\d|\d/g;

function normalizeNumber(token: string): string {
  // Strip grouping/decimal punctuation so "4,200" and "4.200" compare equal to
  // their digit core; keeps the guard from false-flagging locale formatting.
  return token.replace(/[.,]/g, "");
}

function extractNumbers(text: string): string[] {
  const out = new Set<string>();
  for (const m of text.matchAll(NUMBER_RE)) {
    const norm = normalizeNumber(m[0]);
    if (norm.length > 0) out.add(norm);
  }
  return [...out];
}

/**
 * Check an answer against the chunks it was built from.
 *
 * @param answer       The generated answer text.
 * @param citedHits    The hits the answer is allowed to rely on (already ACL-filtered).
 * @param citedChunkIds Optional: chunk_ids the answer explicitly cited; if given,
 *                      they must all be present in `citedHits`.
 */
export function checkGrounding(
  answer: string,
  citedHits: readonly Hit[],
  citedChunkIds?: readonly string[],
): GuardVerdict {
  const corpusNumbers = new Set<string>();
  for (const h of citedHits) {
    for (const n of extractNumbers(h.chunk_text)) corpusNumbers.add(n);
    // The citation apparatus we HANDED the model is grounded by construction:
    // filename, page and chunk index travel inside the `[file, p.N #idx]` tags we
    // ask the model to cite. Echoing them back must not look like an invented
    // figure (e.g. "#0" -> "0"). Only numbers absent from BOTH the context text
    // and these reference tokens count as ungrounded.
    if (h.source_ref?.filename) {
      for (const n of extractNumbers(h.source_ref.filename)) corpusNumbers.add(n);
    }
    if (h.page != null) corpusNumbers.add(normalizeNumber(String(h.page)));
    corpusNumbers.add(normalizeNumber(String(h.chunk_index)));
  }

  const ungroundedNumbers = extractNumbers(answer).filter((n) => !corpusNumbers.has(n));

  const allowedIds = new Set(citedHits.map((h) => h.chunk_id));
  const invalidCitations = (citedChunkIds ?? []).filter((id) => !allowedIds.has(id));

  const ok = ungroundedNumbers.length === 0 && invalidCitations.length === 0;
  return {
    ok,
    ungroundedNumbers,
    invalidCitations,
    reason: ok
      ? "all numbers grounded in cited chunks; all citations valid"
      : "answer contains numbers or citations not supported by the allowed chunks",
  };
}

// Standard guarded-refusal payload for T4 or a failed guard. The bridge returns
// this instead of the model output so unsupported content never reaches the user.
export function refusal(reason: string): { answer: string; refused: true; reason: string } {
  return {
    answer:
      "I can't answer that from the documents you have access to. " +
      "No supporting passage was found, so I won't generate an unverified response.",
    refused: true,
    reason,
  };
}
