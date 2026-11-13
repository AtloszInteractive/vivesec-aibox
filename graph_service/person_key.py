"""Salted person key from the four natural identifiers (E28, BV E26d plan 3.).

A contact has no ID number, and the same person appears in several contact
records (the mother of two inmates, once for each). The importer links such
records automatically only when all four natural identifiers agree: name,
birth place, birth date and mother's name, normalised. It stores a keyed hash
of them (HMAC-SHA256 with the box's own salt) as the record's hidden
`person_key` attribute; resolution uses it as a strong key like a registry
number. The key is never shown, and it cannot be made from a name alone: a
missing field gives no key at all.

The salt lives in a file (GRAPH_PERSON_KEY_SALT_FILE, at least 16 bytes). Without
a usable salt no key is made, so no contact record is linked automatically
(fail safe: they stay apart, partial matches go to review). Changing the salt
makes every later key differ from the stored ones: re-import the sources.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import os
import re

from extraction import fold

MIN_SALT_BYTES = 16
VERSION = "v1"
_SEP = "\x1f"
# Titles that do not tell people apart; "ifj."/"id." (junior/senior) do, and stay.
_TITLES = {"dr", "prof", "ozv"}


def load_salt(path: str | None = None) -> bytes | None:
    """The salt from GRAPH_PERSON_KEY_SALT_FILE (or `path`); None when unset,
    unreadable or too short."""
    path = os.environ.get("GRAPH_PERSON_KEY_SALT_FILE", "") if path is None else path
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            salt = f.read().strip()
    except OSError:
        return None
    return salt if len(salt) >= MIN_SALT_BYTES else None


def norm_name(text: str) -> str:
    """Case, accents, punctuation and word order folded; titles dropped."""
    words = re.sub(r"[^\w]+", " ", fold(text or "")).split()
    return " ".join(sorted(w for w in words if w not in _TITLES))


def norm_place(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", fold(text or "")).split())


def iso_date(text) -> str:
    """ISO date of one date as written ("1970. 03. 02.", "1970-03-02", "1970/3/2",
    a spreadsheet date); "" when there is none, more than one, or it is invalid."""
    if hasattr(text, "year") and hasattr(text, "month") and hasattr(text, "day"):
        return "%04d-%02d-%02d" % (text.year, text.month, text.day)
    found = re.findall(r"(?<!\d)(\d{4})[.\-/] ?(\d{1,2})[.\-/] ?(\d{1,2})(?!\d)", str(text or ""))
    if len(set(found)) != 1:
        return ""
    y, m, d = (int(x) for x in found[0])
    try:
        return datetime.date(y, m, d).isoformat()
    except ValueError:
        return ""


def norm_date(text: str) -> str:
    """ISO birth date as written; "" if none."""
    return iso_date(text)


def person_key(salt: bytes | None, name: str, birth_place: str, birth_date: str,
               mother_name: str) -> str:
    """Hex key, or "" when the salt or any of the four fields is missing."""
    parts = (norm_name(name), norm_place(birth_place), norm_date(birth_date),
             norm_name(mother_name))
    if not salt or not all(parts):
        return ""
    message = _SEP.join((VERSION,) + parts).encode("utf-8")
    return hmac.new(salt, message, hashlib.sha256).hexdigest()
