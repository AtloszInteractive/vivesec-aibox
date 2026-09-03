"""Confidence scoring for AIBox answers (function specification v2).

The spec mandates a 0-100% confidence on every generated output, split into:

  CR — Retrieval confidence (max 50):
        vector similarity 25 | source diversity 15 | temporal relevance 10
  CG — Generative confidence (max 50):
        strict context adherence 25 | citation density 15 | token probability 10

Deviation (flagged to ViVeSec): Ollama does not expose token logprobs, so the
token-probability component (10 pts) is APPROXIMATED with a deterministic
substantive-token overlap heuristic (fraction of the answer's substantive
tokens — words >= 4 chars and digit groups — that appear in the retrieved
context): >=0.90 -> 10 | 0.75-0.89 -> 5 | <0.75 -> 0.
The adherence component uses the fail-honest number-grounding proxy: every
digit-group in the answer must appear in the retrieved context (or the
question); a violation is treated as detected hallucination.

Spec system rule implemented here: adherence = 0  =>  the caller must suppress
the answer and emit the standard defensive response (`suppress` flag +
STANDARD_REFUSAL); every displayed answer carries the mandatory
"Adatkontroll & Audit Info" footer (Score / Sources / Audit ID) built by
footer(), and each band carries the spec user message (band message + the
amber "degraded metric" warning flag).

Bands: >=85 green | 60-84 amber | <60 red.

Pure stdlib, no I/O — unit-testable.
"""
import hashlib
import os
import re
import time

GREEN = "green"
AMBER = "amber"
RED = "red"

# The spec's standard defensive response (suppressed/blocked answers).
STANDARD_REFUSAL_HU = (
    "Rendszerüzenet: A megadott vállalati dokumentumok alapján a kérdés nem "
    "válaszolható meg megnyugtató pontossággal."
)
STANDARD_REFUSAL_EN = (
    "System message: Based on the corporate documents, the question cannot be "
    "answered with sufficient accuracy."
)
STANDARD_REFUSAL_DA = (
    "Systembesked: Ud fra virksomhedens dokumenter kan spørgsmålet ikke "
    "besvares med tilstrækkelig nøjagtighed."
)
STANDARD_REFUSAL_DE = (
    "Systemmeldung: Auf Basis der Unternehmensdokumente kann die Frage nicht "
    "mit ausreichender Genauigkeit beantwortet werden."
)

# Band user messages (spec: final score classification & system actions).
BAND_MESSAGES = {
    GREEN: {"hu": "Biztos válasz a vállalati dokumentáció alapján.",
            "en": "Confident answer based on corporate documentation.",
            "da": "Sikkert svar baseret på virksomhedens dokumentation.",
            "de": "Sichere Antwort auf Basis der Unternehmensdokumentation."},
    AMBER: {"hu": "Részben alátámasztott válasz. Kérjük, ellenőrizze a "
                  "forrásokat.",
            "en": "Partially supported answer. Please verify sources.",
            "da": "Delvist understøttet svar. Kontrollér venligst kilderne.",
            "de": "Teilweise belegte Antwort. Bitte prüfen Sie die Quellen."},
    RED: {"hu": "Alacsony megbízhatóságú válasz.",
          "en": "Low-confidence answer.",
          "da": "Svar med lav pålidelighed.",
          "de": "Antwort mit geringer Zuverlässigkeit."},
}


def _lang_key(lang):
    """Normalise a language label to hu/en/da/de; Hungarian is the spec
    default when no language was requested or detected."""
    l = (lang or "").lower()
    if l in ("", "hu", "hungarian"):
        return "hu"
    if l in ("da", "danish", "dansk"):
        return "da"
    if l in ("de", "german", "deutsch"):
        return "de"
    return "en"

_NUM_RE = re.compile(r"\d[\d  ,.:/-]*\d|\d")
_CIT_RE = re.compile(r"\[#\d+\]")
# Structural numbering APPARATUS instructed by the task templates (numbered
# sections, "Slide N:" outlines). These are format, not facts — they must not
# trip the number-grounding proxy (same principle as citation markers).
_ENUM_RE = re.compile(r"(?m)^[\s>#*+-]*\d+[.):]\s")
_SLIDE_RE = re.compile(r"(?i)\bslide\s+\d+\s*[:.]")
_WORD_RE = re.compile(r"[^\W\d_]{4,}", re.UNICODE)


def _numbers(text):
    """Digit-groups normalized to bare digit strings ('4,2' -> '42')."""
    out = set()
    for m in _NUM_RE.finditer(text or ""):
        digits = re.sub(r"\D", "", m.group(0))
        if digits:
            out.add(digits)
    return out


# Vector-similarity bands, expressed in cosine SIMILARITY (our contexts carry
# similarity; the spec is written in cosine DISTANCE: <=0.15 -> 25 | <=0.25 ->
# 15 | else 0, i.e. it assumes a good hit scores 0.85+).
#
# That assumption does not hold for the embedding model actually deployed.
# Measured with bge-m3 on the demo corpus (52 questions whose answer is known
# to be in the corpus, demo-corpus/score_probe.py): top-1 similarity ranges
# 0.49-0.72, median 0.61. A 0.85 threshold is unreachable, so the raw spec
# bands award 0/25 to EVERY answer and cap the total at 75 -- no answer can
# ever be green, however well grounded it is.
#
# The bands below keep the spec's shape (three steps, same maximum) but are
# calibrated to the model in use. Override per deployment if the embedding
# model changes.
SIM_HIGH = float(os.environ.get("ADAPTER_CONF_SIM_HIGH", "0.65") or 0.65)
SIM_MID = float(os.environ.get("ADAPTER_CONF_SIM_MID", "0.55") or 0.55)
SIM_LOW = float(os.environ.get("ADAPTER_CONF_SIM_LOW", "0.45") or 0.45)

# A synthesis answer (weekly report, memo) quotes dozens of figures, and one
# reformatted date used to zero the component and suppress the whole answer.
# Below MIN_NUMBERS the old all-or-nothing rule still applies: a short answer
# whose only figure is fabricated IS a hallucination.
ADHERENCE_MINOR_RATIO = float(
    os.environ.get("ADAPTER_CONF_ADHERENCE_MINOR", "0.2") or 0.2)
ADHERENCE_MIN_NUMBERS = int(
    os.environ.get("ADAPTER_CONF_ADHERENCE_MIN_NUMBERS", "6") or 6)


def _cosine_points(contexts):
    """Vector similarity of the best hit, max 25. See SIM_* above for why the
    thresholds are model-calibrated rather than taken literally from the spec."""
    best = 0.0
    for c in contexts:
        try:
            best = max(best, float(c.get("score") or 0.0))
        except (TypeError, ValueError):
            continue
    if best >= SIM_HIGH:
        return 25
    if best >= SIM_MID:
        return 15
    if best >= SIM_LOW:
        return 8
    return 0


def _diversity_points(contexts):
    """Source diversity, max 15: 1-2 highly matching files -> 15; 3+ files
    (context-mixing risk) -> 10; nothing/fallback -> 5."""
    files = {c.get("source_path") for c in contexts if c.get("source_path")}
    if not files:
        return 5
    return 15 if len(files) <= 2 else 10


def _temporal(contexts, now=None):
    """Temporal relevance: (points, max). Updated <180 days -> 10; older -> 5.
    When no context carries an mtime the component is EXCLUDED (max=0) so
    engines that do not report mtime are not unfairly zeroed."""
    now = now if now is not None else time.time()
    mtimes = []
    for c in contexts:
        meta = c.get("metadata") or {}
        mt = meta.get("mtime")
        if mt:
            try:
                mtimes.append(float(mt))
            except (TypeError, ValueError):
                continue
    if not mtimes:
        return 0, 0
    freshest = max(mtimes)
    return (10 if (now - freshest) <= 180 * 86400 else 5), 10


def _history_texts(history):
    """Prior ASSISTANT answers only: they were grounded and gated when they
    were produced, so their facts are legitimately reusable. User turns are
    not a fact source (the user can type any number)."""
    out = []
    for turn in (history or []):
        if turn.get("role") == "assistant" and turn.get("content"):
            out.append(turn["content"])
    return out


def _adherence(answer, contexts, question, history=None):
    """Strict context adherence, max 25 — fail-honest number-grounding proxy:
    every number in the answer must appear in the context (or the question,
    or a prior grounded answer of this conversation). No numbers -> full
    points (there is nothing to fabricate that we can verify mechanically).
    Citation markers and structural numbering (list enumeration, "Slide N:")
    are format apparatus instructed by us, not facts — masked before the
    check.

    Graded, not all-or-nothing: a synthesis answer (weekly report, memo) cites
    dozens of figures, and one reformatted date used to zero the component and
    suppress the whole answer. From ADHERENCE_MIN_NUMBERS figures upwards, up
    to ADHERENCE_MINOR_RATIO of them may stay unmatched for reduced points;
    shorter answers, and a larger share, still count as fabrication and the
    caller replaces the answer with the defensive reply.
    Returns (points, ungrounded_numbers)."""
    cleaned = _CIT_RE.sub(" ", answer or "")
    cleaned = _ENUM_RE.sub(" ", cleaned)
    cleaned = _SLIDE_RE.sub(" ", cleaned)
    need = _numbers(cleaned)
    if not need:
        return 25, []
    have = _numbers(question or "")
    for text in _history_texts(history):
        have |= _numbers(text)
    for c in contexts:
        have |= _numbers(c.get("text") or "")
        # The citation apparatus WE hand to the model (the [#n] tag carries the
        # source path + page) is grounded by construction — dates/numbers in a
        # filename are legitimately quotable (same fix as the TS bridge had).
        have |= _numbers(c.get("source_path") or "")
        if c.get("page_number") is not None:
            have |= _numbers(str(c.get("page_number")))
    bad = sorted(n for n in need
                 if not any(n == h or n in h for h in have))
    if not bad:
        return 25, []
    if (len(need) >= ADHERENCE_MIN_NUMBERS
            and len(bad) <= ADHERENCE_MINOR_RATIO * len(need)):
        return 10, bad
    return 0, bad


def _citation_density(answer, contexts):
    """Citation density, max 15: every context-backed claim cited. Proxy:
    >=1 [#n] marker AND all markers point at real contexts -> 15; some
    citations -> 5; none -> 0."""
    refs = set(int(m.group(0)[2:-1]) for m in _CIT_RE.finditer(answer or ""))
    if not refs:
        return 0
    valid = all(1 <= r <= len(contexts) for r in refs)
    return 15 if valid else 5


def _token_probability(answer, contexts, question, history=None):
    """Token-probability APPROXIMATION, max 10 (spec wants avg logprobs of
    substantive tokens; Ollama exposes none — flagged deviation). Deterministic
    proxy: the fraction of substantive tokens (words >= 4 chars + digit groups,
    citation/structural apparatus stripped) that appear in the retrieved
    context, the question or a prior grounded answer. Spec thresholds kept:
    >=0.90 -> 10 | 0.75-0.89 -> 5 | <0.75 -> 0."""
    cleaned = _CIT_RE.sub(" ", answer or "")
    cleaned = _ENUM_RE.sub(" ", cleaned)
    cleaned = _SLIDE_RE.sub(" ", cleaned)
    tokens = set(w.lower() for w in _WORD_RE.findall(cleaned)) | _numbers(cleaned)
    if not tokens:
        return 10  # nothing substantive to verify
    have = set(w.lower() for w in _WORD_RE.findall(question or ""))
    have |= _numbers(question or "")
    for text in _history_texts(history):
        have |= set(w.lower() for w in _WORD_RE.findall(text))
        have |= _numbers(text)
    for c in contexts:
        for field in (c.get("text"), c.get("source_path")):
            have |= set(w.lower() for w in _WORD_RE.findall(field or ""))
            have |= _numbers(field or "")
    covered = sum(1 for t in tokens if t in have)
    ratio = covered / float(len(tokens))
    if ratio >= 0.90:
        return 10
    if ratio >= 0.75:
        return 5
    return 0


def score(contexts, answer, question=None, now=None, history=None,
          conversational=False):
    """Score one answer. Returns a dict:

        {score: 0-100, band: green|amber|red, suppress: bool,
         components: {...}, max_achievable: int}

    `answer` may be None/empty (generation off/unreachable): the CG side is
    then excluded and the score reflects retrieval-only confidence.
    `history` (prior conversation turns) extends the grounding sources: a fact
    reused from an earlier GATED answer is not a hallucination.
    `conversational` marks a chat-only turn (no retrieval by design): the CR
    components and citation density are excluded — there was nothing to
    retrieve or cite — and the score reflects adherence to the conversation.
    """
    contexts = contexts or []
    components = {}
    achieved = 0
    achievable = 0

    # --- CR ------------------------------------------------------------------
    if not conversational:
        pts = _cosine_points(contexts)
        components["vector_similarity"] = {"points": pts, "max": 25}
        achieved += pts
        achievable += 25

        pts = _diversity_points(contexts)
        components["source_diversity"] = {"points": pts, "max": 15}
        achieved += pts
        achievable += 15

        pts, mx = _temporal(contexts, now=now)
        components["temporal_relevance"] = {"points": pts, "max": mx}
        achieved += pts
        achievable += mx

    # --- CG ------------------------------------------------------------------
    suppress = False
    has_answer = bool((answer or "").strip())
    if has_answer:
        pts, bad = _adherence(answer, contexts, question, history=history)
        comp = {"points": pts, "max": 25}
        if bad:
            comp["ungrounded"] = bad[:10]
        components["context_adherence"] = comp
        achieved += pts
        achievable += 25
        if pts == 0:
            suppress = True  # spec: detected hallucination -> defensive reply

        if not conversational:
            pts = _citation_density(answer, contexts)
            components["citation_density"] = {"points": pts, "max": 15}
            achieved += pts
            achievable += 15

        # token probability: spec wants logprobs; Ollama has none -> the
        # deterministic overlap approximation (flagged deviation, B5).
        pts = _token_probability(answer, contexts, question, history=history)
        components["token_probability"] = {"points": pts, "max": 10,
                                           "approximated": True}
        achieved += pts
        achievable += 10

    pct = int(round(100.0 * achieved / achievable)) if achievable else 0
    band = GREEN if pct >= 85 else (AMBER if pct >= 60 else RED)
    # Spec (amber): flag the exact metrics that degraded the score.
    degraded = sorted(
        name for name, c in components.items()
        if c.get("max", 0) > 0 and c["points"] < c["max"])
    return {
        "score": pct,
        "band": band,
        "suppress": suppress,
        "components": components,
        "degraded": degraded,
        "max_achievable": achievable,
    }


def band_message(band, lang=None):
    """The spec's per-band user message; Hungarian unless an explicit
    non-Hungarian answer language was requested."""
    return BAND_MESSAGES.get(band, BAND_MESSAGES[RED])[_lang_key(lang)]


def standard_refusal(lang=None):
    """The spec's standard defensive response (suppressed/blocked answers)."""
    key = _lang_key(lang)
    return {"hu": STANDARD_REFUSAL_HU, "da": STANDARD_REFUSAL_DA,
            "de": STANDARD_REFUSAL_DE}.get(key, STANDARD_REFUSAL_EN)


def audit_id(user, drive, query, answer, now=None):
    """Deterministic local audit hash for the footer (no I/O, replayable
    within the same minute; `now` injectable for tests)."""
    now = now if now is not None else time.time()
    basis = "\x00".join([user or "", drive or "", query or "", answer or "",
                         str(int(now // 60))])
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def footer(conf, citations, aid, lang=None):
    """The mandatory 'Adatkontroll & Audit Info' footer (spec: appended to
    every displayed output exactly as formatted). Sources = the distinct
    cited files (path basename + page when present)."""
    seen = []
    for c in citations or []:
        path = (c.get("path") or "").rstrip("/")
        name = path.rsplit("/", 1)[-1] if path else (c.get("chunk_id") or "?")
        page = c.get("page_number")
        label = "%s, p.%s" % (name, page) if page else name
        if label not in seen:
            seen.append(label)
    sources = "; ".join(seen[:6]) if seen else "-"
    lines = [
        "---",
        "**Adatkontroll & Audit Info:**",
        "* **Confidence Score:** %d%% (%s)" % (conf["score"], conf["band"]),
        "* **Felhasznált források (Sources):** %s" % sources,
        "* **Audit ID:** %s" % aid,
    ]
    msg = band_message(conf["band"], lang)
    if conf["band"] == AMBER and conf.get("degraded"):
        msg += " [degraded: %s]" % ", ".join(conf["degraded"])
    lines.append("* %s" % msg)
    return "\n".join(lines)
