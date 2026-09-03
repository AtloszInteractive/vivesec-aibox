"""Shared types and helpers for the demo-corpus builders."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Doc:
    """One generated document.

    `rel` is the path inside the drive; the generator prefixes it with the
    ViVeSecBox drive root (`/storage/drives/<name>/...`).
    """

    drive: str                       # bible drive id, e.g. "drive_finance"
    rel: str                         # e.g. "reports/Q2_2026_Management_Accounts.md"
    title: str
    fmt: str                         # md|txt|csv|xlsx|docx|pdf|pdf_scanned
    language: str = "en"
    body: str | None = None          # md / txt / csv
    blocks: list[tuple[str, str]] = field(default_factory=list)   # pdf / docx
    sheets: dict[str, list[list[Any]]] = field(default_factory=dict)  # xlsx
    scanned_pages: int = 2
    tags: list[str] = field(default_factory=list)   # e.g. ["/finance", "difficulty:xlsx"]
    facts: list[str] = field(default_factory=list)  # bible ids this doc is derived from


# --------------------------------------------------------------------------
# markdown helpers
# --------------------------------------------------------------------------


def md(*parts: str) -> str:
    return "\n".join(p.rstrip() for p in parts).strip() + "\n"


def h1(text: str) -> str:
    return "# %s\n" % text


def h2(text: str) -> str:
    return "\n## %s\n" % text


def h3(text: str) -> str:
    return "\n### %s\n" % text


def para(text: str) -> str:
    return "\n".join(line.strip() for line in text.strip().splitlines()) + "\n"


def bullets(items: list[str]) -> str:
    return "\n".join("- %s" % i for i in items) + "\n"


def numbered(items: list[str]) -> str:
    return "\n".join("%d. %s" % (n, i) for n, i in enumerate(items, 1)) + "\n"


def table(header: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(str(h) for h in header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in row) + " |")
    return "\n".join(out) + "\n"


def kv(pairs: list[tuple[str, Any]]) -> str:
    return "\n".join("**%s:** %s  " % (k, v) for k, v in pairs) + "\n"


def meur(value: float) -> str:
    return "EUR %.2f million" % value


def eur(value: float) -> str:
    return "EUR %s" % format(int(round(value)), ",d")


def pct(value: float) -> str:
    return "%.1f%%" % value


# --------------------------------------------------------------------------
# bible lookup helpers
# --------------------------------------------------------------------------


class Bible:
    def __init__(self, data: dict):
        self.d = data
        self.people = {p["id"]: p for p in data["people"]}
        for ned in data["board"]["non_executive_directors"]:
            self.people.setdefault(ned["id"], {"id": ned["id"], "name": ned["name"],
                                               "title": "Non-executive director"})
        self.entities = {e["id"]: e for e in data["company"]["legal_entities"]}
        self.projects = {p["id"]: p for p in data["projects"]}
        self.contracts = {c["id"]: c for c in data["contracts"]}
        self.drives = {x["id"]: x for x in data["drives"]}
        self.quarters = {q["period"]: q for q in data["finance"]["quarterly"]}
        self.years = {y["year"]: y for y in data["finance"]["annual"]}

    # convenience accessors -------------------------------------------------
    def name(self, pid: str) -> str:
        return self.people[pid]["name"]

    def who(self, pid: str) -> str:
        p = self.people[pid]
        return "%s (%s)" % (p["name"], p.get("title", ""))

    def email(self, pid: str) -> str:
        return self.people[pid].get("email", "")

    def clause(self, cid: str, ref: str) -> dict:
        for c in self.contracts[cid].get("key_clauses", []):
            if c["ref"] == ref:
                return c
        raise KeyError("%s has no clause %s" % (cid, ref))

    @property
    def company(self) -> dict:
        return self.d["company"]

    @property
    def today(self) -> str:
        return self.d["meta"]["demo_date"]

    @property
    def as_of(self) -> str:
        return self.d["meta"]["as_of"]
