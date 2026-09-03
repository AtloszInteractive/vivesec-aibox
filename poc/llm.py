"""Generation layer with two backends.

* ollama  : real generative answer over retrieved context (Jetson production path).
* fallback: extractive synthesis (pick the most relevant sentences) so the PoC
            ALWAYS returns something useful, even with no model installed.
"""
import config
import ollama_client as ollama
from chunking import split_sentences
from embeddings import tokens


def use_ollama_gen():
    if config.BACKEND == "fallback":
        return False
    if config.BACKEND == "ollama":
        return True
    return ollama.available() and ollama.model_pulled(config.GEN_MODEL)


def _collect_sentences(chunk_texts):
    """Sentence list built per-chunk (avoids cross-chunk run-ons), with
    fragment and substring de-duplication for clean fallback output."""
    out = []
    seen = []
    for ch in chunk_texts:
        for s in split_sentences(ch):
            s = " ".join(s.split())
            if not s or s[0].islower():  # drop chunk-boundary fragments
                continue
            low = s.lower()
            dup = False
            for j, ex in enumerate(seen):
                if low in ex:
                    dup = True
                    break
                if ex in low:  # keep the longer, more complete sentence
                    out[j], seen[j] = s, low
                    dup = True
                    break
            if not dup:
                out.append(s)
                seen.append(low)
    return out


def _extractive(question, chunk_texts, shape, max_sentences=6):
    sents = _collect_sentences(chunk_texts)
    qterms = set(tokens(question)) if question else set()
    scored = []
    for i, s in enumerate(sents):
        st = set(tokens(s))
        overlap = len(qterms & st)
        scored.append((overlap + 0.001 * len(st), i, s))
    if qterms:
        scored.sort(key=lambda x: (x[0], -x[1]), reverse=True)
    top = scored[:max_sentences]
    top.sort(key=lambda x: x[1])  # restore original order
    picked = [s for _, _, s in top]
    if shape == "memo":
        return " ".join(picked)
    return "\n".join("- " + s for s in picked)


# Anti-hallucination guard appended to every persona system prompt. In A/B
# testing this is what makes the model refuse out-of-corpus questions instead of
# inventing figures (especially with qwen2.5:3b).
GROUNDING_RULES = (
    "STRICT RULES:\n"
    "- Use ONLY facts that appear in CONTEXT; do not use outside knowledge.\n"
    "- Never invent or guess numbers, dates, names or percentages. Every figure "
    "you write MUST appear verbatim in CONTEXT.\n"
    "- Do not repeat sentences; answer once and concisely.\n"
)

# Exact refusal sentence per answer language, so the guard reads naturally in
# whichever language the UI selected.
_REFUSALS = {
    "Hungarian": "Erre nincs adat a dokumentumokban.",
    "English": "There is no data for this in the documents.",
    "Danish": "Der er ingen data om dette i dokumenterne.",
    "German": "Dazu liegen in den Dokumenten keine Daten vor.",
}


def _build_guard(lang):
    rules = GROUNDING_RULES
    if lang:
        refusal = _REFUSALS.get(lang, "There is no data for this in the documents.")
        rules += "- If the answer is not in CONTEXT, reply EXACTLY: '%s' and nothing else.\n" % refusal
        rules += "- Always write the answer in %s.\n" % lang
    else:
        rules += "- If the answer is not in CONTEXT, say plainly that the documents do not contain it.\n"
    return rules


def answer(system, question, context_tagged, chunk_texts, shape):
    """Return (answer_text, backend_label).

    context_tagged carries [source #n] markers for LLM grounding/citation;
    chunk_texts is the list of clean chunk strings used by the extractive fallback.
    """
    q = question or "Summarize the key points relevant to the user."
    if use_ollama_gen():
        system_full = system.rstrip() + "\n\n" + _build_guard(config.ANSWER_LANG)
        user = ("CONTEXT:\n%s\n\nTASK: %s\nAnswer using only the context above. "
                "Use only numbers that appear verbatim in CONTEXT." % (context_tagged, q))
        content, ec, ed = ollama.chat(system_full, user)
        label = "ollama:" + config.GEN_MODEL
        if ec and ed:
            label += " (%.1f tok/s)" % (ec / (ed / 1e9))
        return content.strip(), label
    body = _extractive(q, chunk_texts, shape)
    note = "[extractive fallback - install Ollama + pull %s for generative output]" % config.GEN_MODEL
    return body + "\n\n" + note, "fallback:extractive"
