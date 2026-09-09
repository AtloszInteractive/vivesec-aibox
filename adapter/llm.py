"""Grounded answer generation for the adapter (the AIBox agentic layer).

This sits ABOVE the RAG contract: the RAG service only returns retrieved
contexts (`/rag/search_context`), and synthesizing a grounded answer from them
is the AIBox's job. Keeping generation here keeps the RAG service contract-pure
and drop-in swappable.

Pure stdlib (urllib -> Ollama on the host). The model (qwen2.5:14b on the
Jetson) is told to use ONLY the retrieved context and to cite `[#n]` markers, so
the answer is source-grounded and refuses out-of-corpus questions instead of
inventing figures. If Ollama is unreachable (e.g. dev machine) generation is
skipped gracefully and the caller still gets the raw hits.
"""
import json
import os
import re
import socket
import threading
import urllib.request

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
GEN_MODEL = os.environ.get("ADAPTER_GEN_MODEL", "qwen2.5:14b")
# auto = generate only if Ollama answers; on = always try; off = never generate.
GENERATE = os.environ.get("ADAPTER_GENERATE", "auto").lower()
ANSWER_LANG = os.environ.get("ADAPTER_ANSWER_LANG", "")
NUM_PREDICT = int(os.environ.get("ADAPTER_NUM_PREDICT", "512") or 0)
# Ollama silently truncates a prompt that exceeds the context window, so a
# whole-document analysis would lose its tail without an explicit num_ctx.
# 0 = leave the model default (only safe for short retrieved contexts).
NUM_CTX = int(os.environ.get("ADAPTER_NUM_CTX", "0") or 0)
TEMPERATURE = float(os.environ.get("ADAPTER_TEMPERATURE", "0.2") or 0)
# "" = omit the field (pre-thinking models); "off" = think:false (qwen3.x —
# without it the reply lands in the thinking field and content comes back empty).
THINK = os.environ.get("ADAPTER_THINK", "").lower()
# Conversational mode: rewrite follow-ups into standalone retrieval queries and
# let pure conversation turns (rephrase/summarize what you said) be answered
# from the session history. "off" restores the old one-shot behaviour.
CHAT = (os.environ.get("ADAPTER_CHAT", "on") or "on").strip().lower() != "off"
CONDENSE_TIMEOUT = float(os.environ.get("ADAPTER_CONDENSE_TIMEOUT", "60") or 60)


class ThinkingBudgetExhausted(RuntimeError):
    """The model spent its whole num_predict budget on reasoning."""


class GenerationCancelled(RuntimeError):
    """The caller cancelled a streaming generation."""


# Agent personas. The persona shapes ROLE and TONE only — GROUNDING_RULES and
# the confidence gate bind every agent equally, so no persona can loosen the
# grounding. The UI's agent picker sends an optional `agent` payload field;
# when it does not (today), DEFAULT_AGENT applies. Unknown ids fall back to
# the generic assistant rather than impersonating another agent.
DEFAULT_AGENT = (os.environ.get("ADAPTER_AGENT", "operations") or "operations").strip().lower()

PERSONA = (
    "You are the ViVeSec AIBox assistant. You answer questions about the user's "
    "documents using only the retrieved context provided to you."
)

PERSONAS = {
    # The one agent wired to the box today (UI: "Operations Assistant").
    "operations": (
        "You are the ViVeSec AIBox Operations Assistant — the company's "
        "day-to-day operations copilot, warm, proactive and highly organized. "
        "You help run the business from its own documents: meeting follow-ups, "
        "weekly status, project and task tracking, decision memos, "
        "presentations and fact lookup.\n"
        "How you work:\n"
        "- Read past typos and awkward phrasing to the real business outcome; "
        "answer that, and do not correct the user directly.\n"
        "- Think in owners, deadlines, blockers, decisions and next steps; "
        "when the material states them, surface them even if the user did "
        "not ask explicitly.\n"
        "- Lead with the direct answer, then the supporting detail; use short "
        "bullets for anything longer than a few sentences.\n"
        "- Point out risks, overdue dates or contradictions you notice in the "
        "retrieved material — briefly, with their citations.\n"
        "- Be businesslike and concise; no filler, no speculation.\n"
        "You answer strictly from the retrieved context provided to you."
    ),
    # Roadmap agents (legal, finance, talent, strategy, sales, compliance,
    # chiefofstaff) get their persona here when the UI activates them.
}


def persona_for(agent=None):
    """System-prompt persona for an agent id; no id -> the default agent,
    unknown id -> the generic assistant (never another agent's role)."""
    key = (agent or "").strip().lower() or DEFAULT_AGENT
    return PERSONAS.get(key, PERSONA)

GROUNDING_RULES = (
    "STRICT RULES:\n"
    "- Use ONLY facts that appear in CONTEXT; do not use outside knowledge.\n"
    "- Never invent or guess numbers, dates, names or percentages. Every figure "
    "you write MUST appear verbatim in CONTEXT.\n"
    "- Copy dates and figures EXACTLY as CONTEXT writes them. Do not reformat, "
    "convert, add up or derive a new one (no computed week-ending dates, no "
    "totals or percentages you calculated yourself).\n"
    "- If the question covers a period, a range or a whole population but "
    "CONTEXT only covers PART of it, say which part CONTEXT actually covers "
    "and do NOT present that part as if it were the whole. A ranking or total "
    "taken from one month is not a ranking or total for the half-year.\n"
    "- Cite the source of each fact with its [#n] marker.\n"
    "- Do not repeat sentences; answer once and concisely.\n"
)

_REFUSALS = {
    "Hungarian": "Erre nincs adat a dokumentumokban.",
    "English": "There is no data for this in the documents.",
    "Danish": "Der er ingen data om dette i dokumenterne.",
    "German": "Dazu liegen in den Dokumenten keine Daten vor.",
}

# Question-language detection (stopword scoring). The UI never sends `lang`,
# so without this the guard carries no language instruction and the model may
# answer a Hungarian question in English. Words are matched as whole tokens.
_LANG_MARKERS = {
    "Hungarian": ("a", "az", "és", "van", "volt", "mennyi", "milyen", "melyik",
                  "hány", "mekkora", "mi", "hogyan", "nem", "egy", "ez",
                  "kell", "lehet", "szerint", "között", "után"),
    "English": ("the", "is", "was", "are", "what", "which", "how", "much",
                "many", "does", "did", "and", "of", "in", "to", "for", "a",
                "who", "when", "where"),
    "Danish": ("er", "var", "og", "hvad", "hvilket", "hvilken", "hvor",
               "mange", "meget", "har", "en", "et", "på", "af", "til", "ikke",
               "med", "om", "der", "det"),
    "German": ("der", "die", "das", "ist", "war", "und", "wie", "was",
               "welche", "welcher", "viele", "hoch", "hat", "ein", "eine",
               "im", "für", "von", "zu", "nicht"),
}
# Diacritics that identify a language on their own (unique to one of the four).
_LANG_CHARS = {"Hungarian": "őűŐŰ", "Danish": "æøåÆØÅ", "German": "ßẞ"}

_TOKEN_RE = re.compile(r"[\wőűöüóáéíúäøåæß]+", re.UNICODE | re.IGNORECASE)


def detect_lang(text):
    """Best-guess question language ('Hungarian'/'English'/'Danish'/'German'),
    or '' when there is no usable signal (caller keeps the old behaviour)."""
    if not text:
        return ""
    for lang, chars in _LANG_CHARS.items():
        if any(ch in text for ch in chars):
            return lang
    tokens = [t.lower() for t in _TOKEN_RE.findall(text)]
    if not tokens:
        return ""
    scores = {}
    for lang, words in _LANG_MARKERS.items():
        marks = set(words)
        scores[lang] = sum(1 for t in tokens if t in marks)
    best = max(scores, key=lambda k: scores[k])
    if scores[best] == 0:
        return ""
    tied = [k for k, v in scores.items() if v == scores[best]]
    if len(tied) > 1:
        return ""  # ambiguous -> no forced language
    return best

# Task-specific output instructions (function specification v2). The grounding
# rules still bind every line to CONTEXT; a task only shapes the STRUCTURE.
TASK_INSTRUCTIONS = {
    # F1 — #summary: meeting summary & follow-up email
    "summary": (
        "TASK: produce a meeting summary from CONTEXT in EXACTLY this structure:\n"
        "1. Meeting details — Date | Topic | Participants (only what CONTEXT states).\n"
        "2. Executive summary — 3-6 bullet points: key discussion points and decisions.\n"
        "3. Action items — one line per item in the form: Task | Owner | Deadline "
        "(write '?' where CONTEXT does not say).\n"
        "4. Follow-up email — a short professional draft to the participants "
        "summarizing decisions and asks.\n"
        "Ground EVERY line in CONTEXT with [#n] citations; omit a section entirely "
        "when CONTEXT has nothing for it."
    ),
    # F2 — #report: weekly status report (categorize logs of the last 7 days)
    "report": (
        "TASK: compile a weekly status report from CONTEXT (treat the retrieved "
        "logs, weekly notes and project updates as the source material) in "
        "EXACTLY this structure:\n"
        "1. Weekly progress overview — 2-4 sentences on the week's overall progress.\n"
        "2. Key achievements — bullet points of completed tasks and milestones hit; "
        "include any quantifiable KPI or metric verbatim from CONTEXT.\n"
        "3. Bottlenecks & blockers (requiring attention) — bullet points of "
        "in-progress items, blockers and risks.\n"
        "4. Strategic next-step suggestions — bullet points of next week's priorities.\n"
        "5. Key events / important deadlines — one line per dated item.\n"
        "Ground EVERY line in CONTEXT with [#n] citations; omit a section entirely "
        "when CONTEXT has nothing for it."
    ),
    # F6 — #tracking: project summary & task tracking (milestones + health)
    "tracking": (
        "TASK: produce a project status summary and task tracker from CONTEXT "
        "in EXACTLY this structure:\n"
        "1. Project health status — a single indicator: On Track, At Risk, or "
        "Delayed, with a one-sentence justification drawn from CONTEXT.\n"
        "2. Milestones — Achieved vs. Pending, listed against their planned "
        "target dates where CONTEXT states them.\n"
        "3. Active task tracker — one line per outstanding task in the form: "
        "Task | Assignee | Pending action (write '?' where CONTEXT does not say).\n"
        "Ground EVERY line in CONTEXT with [#n] citations; omit a section "
        "entirely when CONTEXT has nothing for it. Choose the health indicator "
        "ONLY from evidence in CONTEXT; if there is not enough information, say "
        "so instead of guessing."
    ),
    # F3 — #presentation: slide-by-slide outline (audience/purpose parameters)
    "presentation": (
        "TASK: build a presentation outline from CONTEXT in EXACTLY this "
        "structure:\n"
        "1. Presentation cover — Title | Subtitle | Date | Writer/Presenter "
        "(only what CONTEXT states; write '?' where it does not).\n"
        "2. Presentation structure — a numbered table of contents following a "
        "logical narrative (problem -> data analysis -> proposed solution -> "
        "ROI / next steps), adapted to what CONTEXT actually supports.\n"
        "3. Slide-by-slide outline — one block per slide. The block STARTS with "
        "the slide's own headline on the 'Slide N: ' line, e.g. "
        "'Slide 2: Revenue and EBITDA performance' — write the real headline "
        "there, never the word 'Title' or any other placeholder. Then, on the "
        "following lines: 'Core message: ' (1-2 sentences) and 'Suggested "
        "visuals: ' (only data that appears in CONTEXT).\n"
        "When — and ONLY when — CONTEXT gives at least two comparable figures "
        "for that slide (plan vs actual, a quarterly series, a split by unit), "
        "add one more line in EXACTLY this machine-readable form so the deck "
        "can draw a chart:\n"
        "Chart: <label> = <number> | <label> = <number> | unit: <unit>\n"
        "e.g. 'Chart: Q1 2026 = 13.2 | Q2 2026 = 14.7 | unit: EUR million'. "
        "Write it as ONE line that starts with 'Chart:' and already carries "
        "the data — never an empty 'Chart:' label with the data underneath. "
        "Every number there must appear VERBATIM in CONTEXT — never compute, "
        "convert or estimate one — and the labels must name what CONTEXT calls "
        "them. Omit the Chart line entirely when the slide has no such data; a "
        "chart is optional, an invented one is a hard error.\n"
        "Every slide must carry NEW information: never continue a slide under "
        "the same headline (no '(continued)'), and never repeat a series you "
        "have already charted on an earlier slide — put the whole series on "
        "one slide instead.\n"
        "Ground EVERY line in CONTEXT with [#n] citations; omit a slide or "
        "section entirely when CONTEXT has nothing for it."
    ),
    # F5 — #memo: decision-making memo (audience/purpose parameters)
    "memo": (
        "TASK: write a decision-making memo from CONTEXT in EXACTLY this "
        "structure:\n"
        "1. Background / The issue at hand — 2-4 sentences framing the "
        "decision situation.\n"
        "2. Options analysis matrix — one block per option in the form: "
        "Option: name | Pros | Cons | Estimated risks (financial, operational "
        "and legal/compliance implications where CONTEXT states them).\n"
        "3. Final strategic recommendation — a reasoned path forward with "
        "mandatory [#n] source references for every claim.\n"
        "Ground EVERY line in CONTEXT with [#n] citations; omit a section "
        "entirely when CONTEXT has nothing for it. Never invent options, "
        "numbers or risks that CONTEXT does not support."
    ),
    # Document analysis: CONTEXT is the COMPLETE file, not retrieved excerpts.
    "analyze": (
        "TASK: analyse ONE document. CONTEXT contains the COMPLETE file in "
        "reading order — these are not search excerpts, so read it end to end "
        "and cover every part of it. Use EXACTLY this structure:\n"
        "1. Document identity — Title | Type | Date | Author or owner (only "
        "what CONTEXT states; write '?' where it does not).\n"
        "2. Purpose — 1-3 sentences on what the document is for.\n"
        "3. Key content — 5-10 bullet points that follow the document's own "
        "order and together cover the whole file; quote every figure, date and "
        "name verbatim from CONTEXT.\n"
        "4. Decisions, obligations & deadlines — one line per item in the "
        "form: Item | Owner | Date (write '?' where CONTEXT does not say).\n"
        "5. Risks, gaps & open questions — bullet points of what the document "
        "itself leaves unresolved.\n"
        "Ground EVERY line in CONTEXT with [#n] citations; omit a section "
        "entirely when CONTEXT has nothing for it. Never state that "
        "information is missing merely because it appears late in the file."
    ),
}


def refusal(lang):
    return _REFUSALS.get(lang or "", _REFUSALS["English"])


def _ollama_up(timeout=1.5):
    try:
        req = urllib.request.Request(OLLAMA_URL + "/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            json.loads(r.read().decode("utf-8"))
        return True
    except Exception:  # noqa: BLE001
        return False


def _build_guard(lang, has_history=False):
    rules = GROUNDING_RULES
    if has_history:
        # Earlier assistant answers were themselves grounded (and gated), so
        # reusing them keeps the conversation coherent without opening a door
        # to outside knowledge. User turns are NOT a fact source.
        rules += ("- You may reuse facts from your OWN earlier answers in "
                  "this conversation; any other fact must appear in CONTEXT.\n")
    where = ("neither in CONTEXT nor in your earlier answers"
             if has_history else "not in CONTEXT")
    if lang:
        rules += "- If the answer is %s, reply EXACTLY: '%s' and nothing else.\n" % (where, refusal(lang))
        rules += "- Always write the answer in %s.\n" % lang
    else:
        rules += "- If the answer is %s, say plainly that the documents do not contain it.\n" % where
    return rules


# Guard for pure conversation turns (no retrieval): the material is the
# conversation itself, so the only hard rule is "no new facts".
def _build_chat_guard(lang):
    rules = (
        "STRICT RULES:\n"
        "- This message refers to the conversation itself. Answer it from the "
        "conversation above.\n"
        "- Do not introduce any fact, number, date or name that does not "
        "already appear in the conversation.\n"
        "- Keep the [#n] citation markers attached to the facts they support "
        "when you restate them.\n"
    )
    if lang:
        rules += "- Always write the answer in %s.\n" % lang
    return rules


_CHAT_ONLY_MARKER = "CHAT_ONLY"

_CONDENSE_INSTRUCTION = (
    "You prepare a follow-up message for a document search engine, using the "
    "conversation so far.\n"
    "Rules:\n"
    "- Rewrite the message into ONE standalone question: resolve pronouns and "
    "references ('it', 'that project', 'and the previous quarter?') from the "
    "conversation.\n"
    "- Keep the rewritten question in the same language as the message.\n"
    "- If the message is already self-contained, return it unchanged.\n"
    "- If the message only refers to the conversation itself (asks to "
    "summarize, shorten, rephrase, translate or explain something already "
    "said, or is a greeting/thanks), reply with exactly %s.\n"
    "Reply with ONLY the rewritten question or %s — no explanation, no "
    "quotes." % (_CHAT_ONLY_MARKER, _CHAT_ONLY_MARKER)
)

# Bound the condense prompt: recent turns matter most, and a spilled multi-KB
# report in the history must not eat the rewriter's window.
_CONDENSE_MAX_TURNS = 6
_CONDENSE_TURN_CHARS = 500


def condense(question, history, lang=None):
    """Rewrite a follow-up into a standalone retrieval question using the
    conversation. Returns (retrieval_query, chat_only). Fails open: any error,
    empty output or disabled generation returns the original question, so the
    old single-turn behaviour is the floor, never worse."""
    if not CHAT or not history or not (question or "").strip():
        return question, False
    if GENERATE == "off" or (GENERATE == "auto" and not _ollama_up()):
        return question, False
    lines = []
    for turn in history[-_CONDENSE_MAX_TURNS:]:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if len(content) > _CONDENSE_TURN_CHARS:
            content = content[:_CONDENSE_TURN_CHARS] + " …"
        lines.append("%s: %s" % ("USER" if role == "user" else "ASSISTANT",
                                 content))
    if not lines:
        return question, False
    user = ("CONVERSATION:\n%s\n\nFOLLOW-UP MESSAGE: %s"
            % ("\n".join(lines), question))
    try:
        content, _, _ = _chat(_CONDENSE_INSTRUCTION, user,
                              timeout=CONDENSE_TIMEOUT)
    except Exception:  # noqa: BLE001
        return question, False
    # First non-empty line; models sometimes wrap the answer in quotes.
    line = next((l.strip().strip('"\'') for l in content.splitlines()
                 if l.strip()), "")
    if not line:
        return question, False
    if _CHAT_ONLY_MARKER in line.upper():
        return question, True
    return line, False


def build_context(contexts):
    """Render the RAG contexts as a citation-tagged block and the parallel
    citation list returned to the UI."""
    blocks = []
    citations = []
    for i, c in enumerate(contexts, 1):
        src = c.get("source_path") or c.get("title") or "document"
        page = c.get("page_number")
        tag = "[#%d] %s%s" % (i, src, (" (p.%s)" % page) if page else "")
        blocks.append("%s\n%s" % (tag, (c.get("text") or "").strip()))
        citations.append({
            "ref": i,
            "path": c.get("source_path"),
            "page_number": page,
            "chunk_id": c.get("chunk_id"),
            "score": c.get("score"),
            "snippet": " ".join((c.get("text") or "")[:200].split()),
        })
    return "\n\n".join(blocks), citations


def _chat(system, user, history=None, timeout=600, num_ctx=None,
          cancel_event=None, progress_callback=None, stream=None):
    options = {"temperature": TEMPERATURE}
    if NUM_PREDICT:
        options["num_predict"] = NUM_PREDICT
    ctx = num_ctx or NUM_CTX
    if ctx:
        options["num_ctx"] = ctx
    messages = [{"role": "system", "content": system}]
    for turn in (history or []):
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user})
    use_stream = ((os.environ.get("ADAPTER_STREAM", "on") or "on").lower()
                  not in ("off", "false", "0")) if stream is None else bool(stream)
    payload = {
        "model": GEN_MODEL,
        "messages": messages,
        "stream": use_stream,
        "options": options,
    }
    if THINK in ("off", "false", "0"):
        payload["think"] = False
    elif THINK in ("on", "true", "1"):
        payload["think"] = True
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL + "/api/chat", data=data,
                                 headers={"Content-Type": "application/json"})
    if not use_stream:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8"))
    else:
        response = urllib.request.urlopen(req, timeout=min(float(timeout), 2.0))
        if not hasattr(response, "readline"):
            try:
                out = json.loads(response.read().decode("utf-8"))
            finally:
                if hasattr(response, "close"):
                    response.close()
        else:
            out = _read_stream(response, cancel_event=cancel_event,
                               progress_callback=progress_callback)
    message = out.get("message") or {}
    content = (message.get("content") or "").strip()
    thinking = (message.get("thinking") or "").strip()
    if not content and thinking:
        # Reasoning models spend num_predict on `thinking` first; if the budget
        # runs out there the answer is empty. Fail loudly -- the callers fall
        # back to the raw hits, which beats showing a confident blank answer.
        raise ThinkingBudgetExhausted(
            "model returned %d chars of reasoning and no answer; "
            "raise ADAPTER_NUM_PREDICT (now %s)" % (len(thinking), NUM_PREDICT))
    return content, out.get("eval_count"), out.get("eval_duration")


def _read_stream(response, cancel_event=None, progress_callback=None):
    """Read Ollama's newline-delimited JSON stream and aggregate its final
    metadata. A short socket timeout lets cancellation become responsive even
    when the model pauses between chunks."""
    content_parts, thinking_parts = [], []
    final = {}
    chars = 0
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled("generation cancelled")
            try:
                line = response.readline()
            except socket.timeout:
                continue
            if not line:
                break
            try:
                chunk = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            message = chunk.get("message") or {}
            text = message.get("content") or ""
            thinking = message.get("thinking") or ""
            if text:
                content_parts.append(text)
                chars += len(text)
            if thinking:
                thinking_parts.append(thinking)
            final.update({k: chunk[k] for k in ("eval_count", "eval_duration")
                          if k in chunk})
            if progress_callback is not None:
                progress_callback(chars, final.get("eval_count"))
            if chunk.get("done"):
                break
    finally:
        response.close()
    final["message"] = {"content": "".join(content_parts),
                         "thinking": "".join(thinking_parts)}
    return final


def generate(question, contexts, lang=None, history=None, task=None,
             params=None, num_ctx=None, chat_only=False, agent=None,
             cancel_event=None, progress_callback=None):
    """Return (answer, backend_label). answer is None when generation is off or
    Ollama is unreachable (caller still has the raw hits). 'history' is an
    optional list of prior {"role","content"} turns for multi-turn follow-ups;
    the grounding rules still bind every answer to the current CONTEXT.
    'task' selects an optional structured-output instruction (TASK_INSTRUCTIONS,
    e.g. the #summary quick action); 'params' are optional task parameters
    (e.g. the #presentation/#memo audience+purpose dialog answers) that shape
    tone and framing but never override grounding. 'num_ctx' widens the model's
    context window for this call (whole-document analysis). 'chat_only' marks
    a turn that refers to the conversation itself (condense() detected it):
    it is answered from the history with a no-new-facts guard instead of the
    deterministic no-context refusal. 'agent' selects the persona (PERSONAS);
    it shapes role and tone only, never the grounding."""
    lang = lang if lang is not None else ANSWER_LANG
    if GENERATE == "off":
        return None, "disabled"
    conversational = bool(chat_only and history)
    if not contexts and not conversational:
        # No retrieved context -> never call the model, refuse deterministically.
        return refusal(lang), "no-context"
    if GENERATE == "auto" and not _ollama_up():
        return None, "unavailable"
    if conversational:
        system = persona_for(agent) + "\n\n" + _build_chat_guard(lang)
        try:
            chat_kwargs = {"history": history, "num_ctx": num_ctx}
            if cancel_event is not None:
                chat_kwargs["cancel_event"] = cancel_event
            if progress_callback is not None:
                chat_kwargs["progress_callback"] = progress_callback
            content, ec, ed = _chat(system, question, **chat_kwargs)
        except Exception as e:  # noqa: BLE001
            return None, "error: %s" % e
        label = "ollama:" + GEN_MODEL + " (chat)"
        if ec and ed:
            label += " (%.1f tok/s)" % (ec / (ed / 1e9))
        return content, label

    context_tagged, _ = build_context(contexts)
    system = persona_for(agent) + "\n\n" + _build_guard(lang, has_history=bool(history))
    task_block = TASK_INSTRUCTIONS.get(task or "")
    if task_block and params:
        plines = ["- %s: %s" % (k, v) for k, v in sorted(params.items()) if v]
        if plines:
            task_block += ("\nPARAMETERS — tailor the tone, depth and framing "
                           "to these (they never override the grounding "
                           "rules):\n" + "\n".join(plines))
    user = ((task_block + "\n\n") if task_block else "") + (
        "CONTEXT:\n%s\n\nQUESTION: %s\nAnswer using only the context above. "
        "Use only numbers that appear verbatim in CONTEXT, and cite [#n]." %
        (context_tagged, question))
    try:
        chat_kwargs = {"history": history, "num_ctx": num_ctx}
        if cancel_event is not None:
            chat_kwargs["cancel_event"] = cancel_event
        if progress_callback is not None:
            chat_kwargs["progress_callback"] = progress_callback
        content, ec, ed = _chat(system, user, **chat_kwargs)
    except Exception as e:  # noqa: BLE001
        return None, "error: %s" % e
    if task_block:
        content = _strip_trailing_refusal(content, lang)
    label = "ollama:" + GEN_MODEL
    if ec and ed:
        label += " (%.1f tok/s)" % (ec / (ed / 1e9))
    return content, label


def _strip_trailing_refusal(content, lang):
    """Structured tasks (report/summary/tracking) instruct the model to OMIT
    empty sections, but the guard's 'reply EXACTLY <refusal>' rule tempts it to
    append the refusal sentence after an otherwise grounded document. Strip
    such trailing refusal lines; keep the refusal when it IS the whole answer."""
    text = (content or "").strip()
    refusals = set(_REFUSALS.values())
    if lang:
        refusals.add(refusal(lang))
    changed = True
    while changed:
        changed = False
        for r in refusals:
            if text != r and text.endswith(r):
                text = text[: -len(r)].rstrip()
                changed = True
    return text
