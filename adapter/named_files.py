"""Resolve file names mentioned in a question against the metadata mirror.

`llm.source_files` finds names by regex and therefore stops at the first
whitespace: "AI SUMMIT nevek.xlsx" becomes "nevek.xlsx", which matches no
document and turns into a confident "the file does not exist" refusal. The
mirror knows every file the box has announced, so the question is checked
against the real basenames instead (case-insensitive, whole-word, with the
extension dot optionally written as a space: "teszt xlsx" -> TESZT.xlsx).
"""
import re

MAX_FILES = 20
_MIN_STEM = 3
# Guard against scanning a runaway mirror on every question.
_SCAN_LIMIT = 50000


def _variants(basename):
    stem, dot, ext = basename.rpartition(".")
    if not dot or len(stem) < _MIN_STEM or not ext:
        return []
    return [basename, "%s %s" % (stem, ext)]


def _mentioned(question_cf, basename):
    for variant in _variants(basename):
        pattern = r"(?<![\w])" + re.escape(variant.casefold()) + r"(?![\w])"
        if re.search(pattern, question_cf):
            return True
    return False


def resolve(mirror, drive_roots, question, fallback=None):
    """Return drive-absolute paths of the files the question names.

    Mirror matches win (longest basename first, so "Q1 report v2.xlsx" beats
    "report v2.xlsx"); regex names from `fallback` that no mirror file
    explains are kept so a file the mirror has not seen yet still narrows the
    search the old way. Capped at MAX_FILES."""
    question_cf = (question or "").casefold()
    if not question_cf.strip():
        return list(fallback or [])[:MAX_FILES]
    matches = []
    seen = set()
    for root in drive_roots or []:
        for meta in mirror.find(root, "", files_only=True, limit=_SCAN_LIMIT):
            path = meta.get("path") or ""
            if path in seen:
                continue
            basename = path.rsplit("/", 1)[-1]
            if _mentioned(question_cf, basename):
                seen.add(path)
                matches.append(path)
    matches.sort(key=lambda p: (-len(p.rsplit("/", 1)[-1]), p))
    resolved_names = {p.rsplit("/", 1)[-1].casefold() for p in matches}
    for name in fallback or []:
        name_cf = str(name).casefold()
        # A regex hit that is the tail of a resolved basename ("nevek.xlsx"
        # inside "AI SUMMIT nevek.xlsx") is already covered.
        if any(r.endswith(name_cf) for r in resolved_names):
            continue
        if name not in matches:
            matches.append(name)
    return matches[:MAX_FILES]
