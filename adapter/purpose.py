"""Purpose- and case-bound queries (relationship analysis concept 5.2).

Where it is switched on, a question, a graph query or an export must name
what it is for: a case reference (`case_ref`, ügyszám) and/or a legal basis
(`legal_basis`, jogalap). Both travel with the request and land in the query
log (querylog.py); neither changes what the caller may see.

  ADAPTER_PURPOSE            off (default) | optional | required
                             unknown values -> required (the stricter one)
  ADAPTER_PURPOSE_FIELDS     any (default: a case reference or a legal basis) |
                             case_ref | legal_basis (only that one is asked for) |
                             both; unknown values -> both (the stricter one)
  ADAPTER_LEGAL_BASES        allowed legal bases, separated by ';' (empty: free text)
  ADAPTER_CASE_REF_PATTERN   regular expression a case reference must match in full
                             (empty: any); an invalid pattern matches nothing
"""
import os
import re
import sys

MODES = ("off", "optional", "required")
FIELDS = ("any", "case_ref", "legal_basis", "both")
MAX_CASE_REF = 64
MAX_LEGAL_BASIS = 200
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_NOTHING = re.compile(r"(?!)")
_MISSING = {"any": "a case reference or a legal basis is required for this query",
            "case_ref": "a case reference is required for this query",
            "legal_basis": "a legal basis is required for this query",
            "both": "a case reference and a legal basis are required for this query"}


class PurposeError(ValueError):
    """The request does not carry an acceptable purpose; `code` says why."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def settings(env=None):
    env = os.environ if env is None else env
    raw = (env.get("ADAPTER_PURPOSE", "off") or "off").strip().lower()
    mode = raw if raw in MODES else "required"
    if raw not in MODES:
        sys.stderr.write("[adapter] unknown ADAPTER_PURPOSE %r -> required\n" % raw)
    fields = (env.get("ADAPTER_PURPOSE_FIELDS", "any") or "any").strip().lower()
    if fields not in FIELDS:
        sys.stderr.write("[adapter] unknown ADAPTER_PURPOSE_FIELDS %r -> both\n" % fields)
        fields = "both"
    bases = [b.strip() for b in (env.get("ADAPTER_LEGAL_BASES", "") or "").split(";") if b.strip()]
    pattern_text = (env.get("ADAPTER_CASE_REF_PATTERN", "") or "").strip()
    pattern = None
    if pattern_text:
        try:
            pattern = re.compile(pattern_text)
        except re.error as error:
            sys.stderr.write("[adapter] invalid ADAPTER_CASE_REF_PATTERN (%s): no case reference "
                             "is accepted\n" % error)
            pattern = _NOTHING
    return {"mode": mode, "fields": fields, "legal_bases": bases, "pattern": pattern,
            "pattern_text": pattern_text}


def offered(config):
    """The purpose fields a request carries: ["case_ref", "legal_basis"] or one."""
    fields = config.get("fields", "any")
    return [f for f in ("case_ref", "legal_basis") if fields in ("any", "both", f)]


def public(config):
    """What the UI needs to ask for it: the fields to show and whether one of
    them (`any`) or all of them (`all`) must be given when required."""
    return {"mode": config["mode"], "fields": offered(config),
            "require": "any" if config.get("fields", "any") == "any" else "all",
            "legal_bases": list(config["legal_bases"]),
            "case_ref_pattern": config["pattern_text"] or None}


def parse(payload, config):
    """{"case_ref"?, "legal_basis"?} of a request ({} when none is given and none
    is needed). Raises PurposeError when one is required but missing, or a given
    one is not acceptable. A field the box does not ask for is ignored."""
    if config["mode"] == "off":
        return {}
    fields = offered(config)
    case_ref = " ".join(str(payload.get("case_ref") or "").split()) \
        if "case_ref" in fields else ""
    legal_basis = " ".join(str(payload.get("legal_basis") or "").split()) \
        if "legal_basis" in fields else ""
    if _CONTROL.search(case_ref + legal_basis):
        raise PurposeError("purpose_invalid", "control characters in the purpose")
    if len(case_ref) > MAX_CASE_REF or len(legal_basis) > MAX_LEGAL_BASIS:
        raise PurposeError("purpose_invalid", "case reference or legal basis too long")
    if case_ref and config["pattern"] is not None and not config["pattern"].fullmatch(case_ref):
        raise PurposeError("purpose_invalid", "the case reference does not have the expected form")
    if legal_basis and config["legal_bases"] and legal_basis not in config["legal_bases"]:
        raise PurposeError("purpose_invalid", "the legal basis is not one of the allowed ones")
    given = {"case_ref": case_ref, "legal_basis": legal_basis}
    rule = config.get("fields", "any")
    complete = any(given[f] for f in fields) if rule == "any" else all(given[f] for f in fields)
    if config["mode"] == "required" and not complete:
        raise PurposeError("purpose_required", _MISSING.get(rule, _MISSING["both"]))
    out = {}
    if case_ref:
        out["case_ref"] = case_ref
    if legal_basis:
        out["legal_basis"] = legal_basis
    return out
