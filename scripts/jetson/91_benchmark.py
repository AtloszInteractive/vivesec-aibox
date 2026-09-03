#!/usr/bin/env python3
"""ViVeSec AIBox benchmark — one box, one run, one comparable JSON report.

Built to answer a specific question: what did the qwen3.6:35b swap and the
language / whole-document fixes actually change? The two boxes hold DIFFERENT
corpora, so raw factual accuracy between them would measure the corpus, not the
build. Everything that decides the comparison is therefore corpus-independent:

  * capabilities  — which code is running (probed, not assumed)
  * language      — the answer must follow the QUESTION's language
  * refusal       — no evidence must produce a refusal, not an invention
  * grounding     — ungrounded numbers, suppression, confidence bands
  * analyze       — whole-document coverage (absent on the old build)
  * performance   — latency and tok/s per action

Corpus-dependent facts are still recorded, but under "informational", never in
the verdict. The suite adapts to whatever drive it finds, so it runs unchanged
on a box that only holds the demo material.

    python3 91_benchmark.py --out ~/bench_dev.json
    python3 91_benchmark.py --adapter http://127.0.0.1:80 --out ~/bench_demo.json

Stdlib only, Python 3.6+, no box-side install.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

SCHEMA = 9

# Nothing in any ViVeSec corpus answers these: the box must refuse, in the
# language the question was asked in. This is the language + honesty probe in
# one, and it needs no knowledge of what the drive contains.
OUT_OF_CORPUS = [
    ("English", "What was Microsoft's dividend per share in the 2027 financial year?"),
    ("Hungarian", "Mennyi volt a Microsoft egy részvényre jutó osztaléka a 2027-es pénzügyi évben?"),
    ("Danish", "Hvad var Microsofts udbytte per aktie i regnskabsåret 2027?"),
    ("German", "Wie hoch war die Dividende von Microsoft je Aktie im Geschäftsjahr 2027?"),
]

# Accented letters that only appear in that language, used to tell the answer's
# language apart without a language-detection dependency.
LANG_MARKERS = {
    "Hungarian": "áéíóöőúüű",
    "Danish": "æøå",
    "German": "äöüß",
}
# Words that betray the wrong language even when no accents are present.
LANG_WORDS = {
    "Hungarian": ("nem", "dokumentum", "adat", "nincs", "válasz"),
    "Danish": ("ikke", "dokument", "data", "der", "svar"),
    "German": ("nicht", "dokument", "daten", "keine", "antwort"),
    "English": ("not", "document", "data", "there", "answer"),
}

# Generic enough to run against any drive, but the brief is anchored to a
# document the drive actually holds (filled in at runtime): an unanchored brief
# just makes every box refuse, which measures nothing.
ACTION_PROBES = [
    ("summary", "the content of %s"),
    ("report", "the status described in %s"),
    ("presentation", "an overview of %s"),
    ("memo", "the decisions and open points in %s"),
]

# These invite numbers, which is where hallucination shows up.
GROUNDING_PROBES = [
    "List the exact figures this drive reports, with their units.",
    "What are the most recent dated commitments and their deadlines?",
    "Summarise the key quantitative results and cite the source for each.",
]


class Box(object):
    # The adapter keeps conversation history per (user, drive) and feeds it back
    # into generation, so probes sharing one identity form a single 24-turn
    # conversation instead of 24 independent questions.
    _probe = 0

    def __init__(self, adapter, drive, user, timeout):
        self.adapter = adapter.rstrip("/")
        self.drive = drive
        self.user = user
        self.timeout = timeout

    def isolated(self):
        """A caller identity nobody else used, so this probe starts with no
        history behind it."""
        Box._probe += 1
        return "%s-p%d" % (self.user, Box._probe)

    def _headers(self, user=None):
        d = base64.urlsafe_b64encode(self.drive.encode("utf-8")).decode().rstrip("=")
        return {"Content-Type": "application/json", "VVS-Drive": d,
                "VVS-User": user or self.user}

    def post(self, path, payload, headers=None, timeout=None):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.adapter + path, data=data,
                                     headers=headers or self._headers())
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                body = json.loads(r.read().decode("utf-8") or "{}")
                return r.status, body, time.time() - t0
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8") or "{}")
            except Exception:  # noqa: BLE001
                body = {}
            return e.code, body, time.time() - t0
        except Exception as e:  # noqa: BLE001
            return 0, {"error": str(e)}, time.time() - t0

    def query(self, payload, timeout=None, isolate=True):
        headers = self._headers(self.isolated() if isolate else None)
        return self.post("/api/v1/ui/query", payload, headers=headers, timeout=timeout)


def sh(cmd):
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        return (out.stdout or out.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return "error: %s" % e


def tok_per_s(backend):
    m = re.search(r"([\d.]+)\s*tok/s", backend or "")
    return float(m.group(1)) if m else None


# Every displayed answer carries the mandatory audit footer, which is appended
# AFTER generation. Truncation must therefore be judged on the model's own text.
_FOOTER = re.compile(r"\n-{3,}\s*\n\s*\*\*Adatkontroll|\*\*Adatkontroll & Audit Info")


def model_text(answer):
    return _FOOTER.split(answer or "", 1)[0].strip()


def anchor_term(files):
    """A phrase from a document this drive really holds, so the quick actions
    generate instead of refusing. Cross-corpus by construction."""
    usable = [f for f in files if 2000 < (f.get("size") or 0) < 200000]
    if not usable:
        usable = files
    if not usable:
        return None
    usable.sort(key=lambda f: f.get("size") or 0)
    name = usable[len(usable) // 2]["path"].split("/")[-1]
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name)
    return re.sub(r"[_\-]+", " ", stem).strip()


# The exact sentences the adapter emits when it has nothing to answer from
# (adapter/llm.py _REFUSALS). A refusal must be recognised by what it says, not
# by being short: a valid answer can be brief, and a length threshold would
# score it as a refusal.
REFUSALS = {
    "Hungarian": "Erre nincs adat a dokumentumokban.",
    "English": "There is no data for this in the documents.",
    "Danish": "Der er ingen data om dette i dokumenterne.",
    "German": "Dazu liegen in den Dokumenten keine Daten vor.",
}


def refusal_language(text):
    """Which refusal was emitted, if any — also shows whether the refusal itself
    was localised, which is where a language bug hides most easily."""
    low = (text or "").strip().lower()
    for lang, phrase in REFUSALS.items():
        if phrase.lower() in low:
            return lang
    return None


def answer_language(text):
    """Best-effort language of an answer, by exclusive accents then stopwords."""
    low = (text or "").lower()
    scores = {}
    for lang, letters in LANG_MARKERS.items():
        scores[lang] = sum(low.count(c) for c in letters) * 3
    for lang, words in LANG_WORDS.items():
        scores.setdefault(lang, 0)
        scores[lang] += sum(1 for w in words if re.search(r"\b%s\b" % w, low))
    best = max(scores, key=lambda k: scores[k]) if scores else None
    return best if scores.get(best) else None


# --------------------------------------------------------------------------- #
# sections
# --------------------------------------------------------------------------- #
def probe_environment(box):
    env = {"adapter_url": box.adapter, "drive": box.drive}
    code, status, _ = box.post("/api/v1/status", {},
                               headers={"Content-Type": "application/json"}, timeout=20)
    env["status_http"] = code
    for k in ("ui_ready", "fs_ready", "features", "storage_locked"):
        env[k] = status.get(k)
    env["index"] = status.get("index")
    env["mirror"] = status.get("mirror")

    env["ollama_models"] = [l.split()[0] for l in sh("ollama list").splitlines()[1:] if l.strip()]
    keys = ("ADAPTER_GEN_MODEL", "ADAPTER_THINK", "ADAPTER_NUM_PREDICT", "ADAPTER_NUM_CTX",
            "ADAPTER_ANALYZE_NUM_CTX", "ADAPTER_ANALYZE_MAX_CHARS",
            "ADAPTER_ANALYZE_MAX_CONTEXT_TOKENS", "ADAPTER_TENANT_ID")
    raw = sh("docker inspect vivesec-adapter --format "
             "'{{range .Config.Env}}{{println .}}{{end}}'")
    env["adapter_env"] = {k: v for k, v in
                          (l.split("=", 1) for l in raw.splitlines() if "=" in l)
                          if k in keys}
    env["images"] = {
        "adapter": sh("docker inspect -f '{{.Image}}' vivesec-adapter")[7:19],
        "rag": sh("docker inspect -f '{{.Image}}' vivesec-rag")[7:19],
    }
    env["rag_health"] = sh("curl -s -m 10 http://127.0.0.1:8090/health")[:400]
    env["host"] = {"hostname": sh("hostname"), "mem": sh("free -g | sed -n 2p")}
    return env


def probe_capabilities(box, sample_file):
    """Which build is running — probed, never assumed."""
    caps = {}
    key = sh("cat ~/prod_rag_api_key.txt")
    code = sh("curl -s -o /dev/null -w '%%{http_code}' -m 15 -X POST "
              "-H 'X-API-Key: %s' -H 'Content-Type: application/json' -d '{}' "
              "http://127.0.0.1:8090/rag/document_context" % key)
    caps["rag_document_context_http"] = code
    caps["rag_document_context"] = code not in ("404", "", "000")

    if sample_file:
        st, res, secs = box.query({"action": "analyze", "query": "coverage probe",
                                   "lang": "English", "files": [sample_file]})
        caps["analyze_http"] = st
        caps["analyze_action_echoed"] = res.get("action")
        caps["analyze_document_block"] = bool(res.get("document"))
        caps["analyze_supported"] = res.get("action") == "analyze" and bool(res.get("document"))
        caps["analyze_seconds"] = round(secs, 1)
        doc = res.get("document") or {}
        caps["analyze_probe"] = {
            "file": sample_file, "chunks_used": doc.get("chunks_used"),
            "chunks_total": doc.get("chunks_total"), "truncated": doc.get("truncated"),
            "body_chars": len(model_text(res.get("answer"))),
        }
    return caps


def probe_corpus(box):
    """Whatever this drive holds — recorded for context, never for the verdict."""
    st, res, secs = box.query({"action": "search", "mode": "files", "query": "files:"},
                              timeout=120)
    files = res.get("files") or []
    sizes = sorted((f.get("size") or 0) for f in files)
    info = {"http": st, "seconds": round(secs, 2), "file_count": len(files),
            "truncated": res.get("truncated")}
    if sizes:
        info["size_bytes"] = {"min": sizes[0], "median": sizes[len(sizes) // 2],
                              "max": sizes[-1], "total": sum(sizes)}
    info["files"] = files
    return info


# Extensions the indexer extracts text from. A certificate, a key or an archive
# carries no text, so analysing one measures the refusal path rather than the
# model, and scoring it as a bad answer would be plain wrong.
TEXT_EXT = (".pdf", ".docx", ".doc", ".odt", ".rtf", ".txt", ".md", ".csv",
            ".pptx", ".ppt", ".xlsx", ".xls", ".html", ".htm", ".eml", ".msg")


def pick_documents(files, depth=10):
    """Smallest / median / largest real document, so analyze is measured across
    the range instead of on one lucky file.

    Only text-bearing types qualify, and every slot carries fallbacks: if a file
    turns out to have no indexed text, the run steps to the next candidate
    instead of recording a zero that says nothing about the build. The extremes
    need the depth — the smallest files are often empty scans and the largest
    are often past the indexer's size limit.
    """
    usable = [f for f in files
              if (f.get("size") or 0) > 2000
              and (f.get("path") or "").lower().endswith(TEXT_EXT)]
    if not usable:
        return []
    usable.sort(key=lambda f: f.get("size") or 0)
    mid = len(usable) // 2
    slots = (
        ("small", usable[:depth]),
        ("median", usable[mid:mid + depth]),
        ("large", list(reversed(usable[-depth:]))),
    )
    seen, out = set(), []
    for label, cands in slots:
        cands = [f for f in cands if f["path"] not in seen]
        if not cands:
            continue
        seen.add(cands[0]["path"])
        out.append((label, cands))
    return out


def run_language(box, results, repeats=3):
    """The answer must follow the QUESTION's language, whatever the corpus is.

    Measured twice per question, because these are two different mechanisms:

      explicit  the caller states the language — the guard only has to obey
      auto      no language is given, so the box must work it out first

    The UI never sends a language, so 'auto' is what a user actually gets;
    'explicit' is the control that separates a detection fault from a model
    that simply ignores instructions.

    Repeated on purpose: a weaker model obeys only some of the time, and a
    single pass cannot tell a real improvement from run-to-run luck.
    """
    rows = []
    for trial in range(repeats):
        for lang, question in OUT_OF_CORPUS:
            for mode in ("explicit", "auto"):
                payload = {"query": question, "top_k": 5}
                if mode == "explicit":
                    payload["lang"] = lang
                st, res, secs = box.query(payload)
                answer = (res.get("answer") or "").split("---")[0].strip()
                detected = answer_language(answer)
                conf = res.get("confidence") or {}
                refused_in = refusal_language(answer)
                rows.append({
                    "trial": trial + 1, "mode": mode,
                    "asked": lang, "http": st, "seconds": round(secs, 1),
                    "detected": detected, "language_ok": detected == lang,
                    "refused": bool(refused_in) or bool(res.get("refused"))
                               or (res.get("backend") or "") == "no-context",
                    "refused_in": refused_in,
                    "refusal_localised": refused_in == lang if refused_in else None,
                    "confidence": conf.get("score"), "band": conf.get("band"),
                    "citations": len(res.get("citations") or []),
                    "answer_head": answer[:160],
                })
    by_mode = {}
    for mode in ("explicit", "auto"):
        got = [r for r in rows if r["mode"] == mode]
        by_mode[mode] = {"ok": sum(1 for r in got if r["language_ok"]),
                         "trials": len(got)}
    auto = [r for r in rows if r["mode"] == "auto"]
    per_lang = {}
    for lang, _ in OUT_OF_CORPUS:
        got = [r for r in auto if r["asked"] == lang]
        per_lang[lang] = {
            "ok": sum(1 for r in got if r["language_ok"]),
            "trials": len(got),
            "answered_in": sorted({str(r["detected"]) for r in got}),
        }
    results["language"] = {
        "repeats": repeats,
        "cases": rows,
        "by_mode": by_mode,
        "per_language": per_lang,
        "language_match": sum(1 for r in rows if r["language_ok"]),
        "refusal_correct": sum(1 for r in rows if r["refused"]),
        "refusal_localised": sum(1 for r in rows if r["refusal_localised"]),
        "total": len(rows),
        # A language that is right in some trials and wrong in others is not a
        # capability, it is a coin flip, and must be reported as such.
        "unstable_languages": sorted(l for l, v in per_lang.items()
                                     if 0 < v["ok"] < v["trials"]),
    }


def run_grounding(box, results, repeats=3):
    """Ungrounded numbers and suppression — the hallucination guard, measured.

    Repeated for the same reason the language probe is: confidence moved by
    ten points between identical runs, so one pass per probe cannot tell a
    build apart from a mood.
    """
    rows = []
    for trial in range(repeats):
        for q in GROUNDING_PROBES:
            st, res, secs = box.query({"query": q, "lang": "English", "top_k": 5})
            conf = res.get("confidence") or {}
            comp = conf.get("components") or {}
            adherence = comp.get("context_adherence") or {}
            backend = res.get("backend") or ""
            rows.append({
                "trial": trial + 1,
                "question": q[:70], "http": st, "seconds": round(secs, 1),
                "confidence": conf.get("score"), "band": conf.get("band"),
                "ungrounded": adherence.get("ungrounded"),
                "suppressed": "suppressed" in backend,
                "citations": len(res.get("citations") or []),
                "tok_per_s": tok_per_s(backend),
                "body_chars": len(model_text(res.get("answer"))),
            })
    results["grounding"] = {
        "cases": rows,
        "suppressed": sum(1 for r in rows if r["suppressed"]),
        "with_ungrounded": sum(1 for r in rows if r["ungrounded"]),
        "mean_confidence": round(
            sum(r["confidence"] or 0 for r in rows) / max(len(rows), 1), 1),
    }


def run_actions(box, results, anchor, repeats=3):
    """Latency and output shape per quick action, anchored to real content.

    Repeated: the same brief on the same build produced a full deliverable in
    one pass and a refusal in the next, so a single pass measures luck.
    """
    rows = []
    for trial in range(repeats):
        for action, template in ACTION_PROBES:
            brief = template % (anchor or "this drive")
            st, res, secs = box.query({"action": action, "query": brief,
                                       "lang": "English"})
            backend = res.get("backend") or ""
            body = model_text(res.get("answer"))
            conf = res.get("confidence") or {}
            refused = (bool(refusal_language(body)) or bool(res.get("refused"))
                       or backend == "no-context")
            rows.append({
                "trial": trial + 1,
                "action": action, "http": st, "seconds": round(secs, 1),
                "echoed": res.get("action"), "tok_per_s": tok_per_s(backend),
                "body_chars": len(body), "citations": len(res.get("citations") or []),
                "confidence": conf.get("score"), "band": conf.get("band"),
                "refused": refused, "backend": backend[:60],
                # A deliverable that is neither a refusal nor of usable length is a
                # third outcome, and lumping it in with either would hide it.
                "short_output": (not refused) and len(body) < 300,
                # Only a generated answer can be judged truncated; a refusal is short
                # by design. Ending mid-sentence means num_predict cut the output.
                "ends_cleanly": None if refused else body[-1:] in ".!?\"')]`*_",
            })
    generated = [r for r in rows if not r["refused"]]
    per_action = {}
    for action, _ in ACTION_PROBES:
        got = [r for r in rows if r["action"] == action]
        per_action[action] = {"generated": sum(1 for r in got if not r["refused"]),
                              "trials": len(got)}
    results["actions"] = {
        "repeats": repeats,
        "anchor": anchor,
        "cases": rows,
        "per_action": per_action,
        "generated": len(generated),
        "refused": len(rows) - len(generated),
        "short_outputs": sum(1 for r in rows if r.get("short_output")),
        "mean_seconds": round(
            sum(r["seconds"] for r in generated) / max(len(generated), 1), 1),
        # Wall time depends on how many probes refused (a refusal is cheap), so
        # throughput is the only speed figure that compares across runs.
        "mean_tok_per_s": round(
            sum(r["tok_per_s"] or 0 for r in generated) / max(len(generated), 1), 1),
        "truncated_outputs": sum(1 for r in generated if r["ends_cleanly"] is False),
    }


def run_analyze(box, results, docs, supported):
    rows = []
    for label, candidates in docs:
        row = None
        # Stepping to the next candidate only makes sense when the build can
        # report chunk coverage. Without the analyze action every attempt is an
        # ordinary query that always looks like "no text", so one probe is both
        # sufficient and all the box's time is worth spending.
        tries = candidates if supported else candidates[:1]
        for attempt, f in enumerate(tries):
            payload = {"action": "analyze", "query": f["path"].split("/")[-1],
                       "lang": "English", "files": [f["path"]]}
            st, res, secs = box.query(payload)
            doc = res.get("document") or {}
            answer = model_text(res.get("answer"))
            conf = res.get("confidence") or {}
            used, total = doc.get("chunks_used"), doc.get("chunks_total")
            row = {
                "size": label, "file": f["path"].split("/")[-1], "bytes": f.get("size"),
                "http": st, "seconds": round(secs, 1), "echoed": res.get("action"),
                "chunks_used": used, "chunks_total": total,
                "coverage": round(used / total, 3) if used and total else None,
                "truncated": doc.get("truncated"),
                "citations": len(res.get("citations") or []),
                "confidence": conf.get("score"), "band": conf.get("band"),
                "body_chars": len(answer),
                # The task asks for five numbered sections; count what came back.
                "sections": len(re.findall(r"^\s*\d\.\s", answer, re.M)),
                "tok_per_s": tok_per_s(res.get("backend") or ""),
                "attempt": attempt + 1,
                # Nothing was extracted from this file at indexing time, so the
                # short answer is a correct refusal, not a bad analysis.
                "no_text": not total,
            }
            if total:
                break
        if row:
            rows.append(row)
    scored = [r for r in rows if not r["no_text"]]
    full = [r for r in scored if r["coverage"] == 1.0]
    results["analyze"] = {
        "supported": supported,
        "cases": rows,
        "analyzable": len(scored),
        "fully_covered": len(full),
        "degenerate_answers": sum(1 for r in scored if r["body_chars"] < 120),
        "no_text_documents": len(rows) - len(scored),
        "mean_seconds": round(sum(r["seconds"] for r in scored) / max(len(scored), 1), 1),
    }


def verdict(results):
    """Only corpus-independent signals decide, so two different drives can still
    be compared build-to-build."""
    lang = results.get("language") or {}
    by_mode = lang.get("by_mode") or {}
    ground = results.get("grounding") or {}
    an = results.get("analyze") or {}
    act = results.get("actions") or {}
    return {
        "language_match": "%s/%s" % (lang.get("language_match"), lang.get("total")),
        # 'auto' is the UI's path: no language is supplied, so the box must
        # detect it. 'explicit' only shows whether the model obeys an order.
        "language_auto": "%s/%s" % ((by_mode.get("auto") or {}).get("ok"),
                                    (by_mode.get("auto") or {}).get("trials")),
        "language_explicit": "%s/%s" % ((by_mode.get("explicit") or {}).get("ok"),
                                        (by_mode.get("explicit") or {}).get("trials")),
        "unstable_languages": lang.get("unstable_languages"),
        "refusal_correct": "%s/%s" % (lang.get("refusal_correct"), lang.get("total")),
        "ungrounded_cases": ground.get("with_ungrounded"),
        "mean_confidence": ground.get("mean_confidence"),
        "analyze_supported": an.get("supported"),
        "analyze_analyzable": an.get("analyzable"),
        "analyze_fully_covered": an.get("fully_covered"),
        "analyze_degenerate": an.get("degenerate_answers"),
        "actions_generated": act.get("generated"),
        "truncated_outputs": act.get("truncated_outputs"),
        "mean_action_seconds": act.get("mean_seconds"),
        "mean_action_tok_per_s": act.get("mean_tok_per_s"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default="http://127.0.0.1:8088")
    ap.add_argument("--drive", default=None, help="default: the drive the UI uses")
    ap.add_argument("--user", default="demo")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--label", default=None)
    ap.add_argument("--repeats", type=int, default=3,
                    help="passes over the language, grounding and action probes; "
                         ">1 separates a build from run-to-run variance")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    drive = args.drive
    if not drive:
        drive = sh("docker inspect vivesec-ui --format "
                   "'{{range .Config.Env}}{{println .}}{{end}}' "
                   "| grep ADAPTER_DEMO_DRIVE= | cut -d= -f2").strip()
    if not drive:
        print("no drive given and none discoverable; pass --drive")
        return 2

    box = Box(args.adapter, drive, args.user, args.timeout)
    started = time.time()
    results = {
        "schema": SCHEMA,
        "label": args.label or sh("hostname"),
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    print("== environment ==")
    results["environment"] = probe_environment(box)
    print("   model:", (results["environment"].get("adapter_env") or {}).get("ADAPTER_GEN_MODEL"))
    print("   index:", results["environment"].get("index"))

    print("== corpus ==")
    results["corpus"] = probe_corpus(box)
    files = results["corpus"].pop("files", [])
    results["corpus"]["informational"] = True
    print("   files:", results["corpus"].get("file_count"))
    docs = pick_documents(files)

    print("== capabilities ==")
    results["capabilities"] = probe_capabilities(box, docs[0][1][0]["path"] if docs else None)
    supported = results["capabilities"].get("analyze_supported")
    print("   document_context:", results["capabilities"].get("rag_document_context"),
          " analyze:", supported)

    print("== language / refusal (%d cases) ==" % (8 * args.repeats))
    run_language(box, results, args.repeats)
    print("   language match:", results["language"]["language_match"], "/",
          results["language"]["total"],
          " by mode:", results["language"]["by_mode"],
          " unstable:", results["language"]["unstable_languages"] or "none")

    print("== grounding (%d cases) ==" % (3 * args.repeats))
    run_grounding(box, results, args.repeats)
    print("   mean confidence:", results["grounding"]["mean_confidence"])

    print("== quick actions (%d cases) ==" % (4 * args.repeats))
    run_actions(box, results, anchor_term(files), args.repeats)
    print("   generated:", results["actions"]["generated"],
          " refused:", results["actions"]["refused"],
          " mean tok/s:", results["actions"]["mean_tok_per_s"])

    print("== analyze (%d documents) ==" % len(docs))
    run_analyze(box, results, docs, supported)
    print("   fully covered:", results["analyze"]["fully_covered"],
          " of analyzable:", results["analyze"]["analyzable"],
          " no-text:", results["analyze"]["no_text_documents"])

    results["wall_seconds"] = round(time.time() - started, 1)
    results["verdict"] = verdict(results)

    out = args.out or os.path.expanduser("~/bench_%s.json" % results["label"])
    with open(out, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print("\n=== verdict ===")
    for k, v in results["verdict"].items():
        print("  %-24s %s" % (k, v))
    print("\nwritten: %s  (%.0f s)" % (out, results["wall_seconds"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
