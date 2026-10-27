"""Core graph model shared by every customer profile (E26).

The core is fixed in code; a profile (profile.py) only adds subtypes,
identifier patterns, relation vocabulary and resolution rules on top of it.

Visibility rule: every mention, attribute value and relation carries the
document(s) it came from. A user sees an item only through documents they can
read, so a relation backed by a hidden document is itself hidden.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PERSON = "person"
ORGANIZATION = "organization"
PLACE = "place"
IDENTIFIER = "identifier"
EVENT = "event"
OBJECT = "object"
CORE_TYPES = (PERSON, ORGANIZATION, PLACE, IDENTIFIER, EVENT, OBJECT)

METHODS = ("structured", "rule", "ner", "llm", "cooccurrence", "manual")
# Edges below this strength are "weak" (co-mentions) and excluded unless asked for.
WEAK_METHODS = ("cooccurrence",)
# A document's subtype for an entity is kept as this attribute of that document,
# so a caller sees a subtype only from documents they can read.
SUBTYPE_ATTR = "subtype"

ENTITY_STATUSES = ("active", "merged", "rejected")


@dataclass
class Document:
    doc_id: str
    corpus_id: str
    source_path: str
    title: str = ""
    acl: dict | None = None
    kind: str = ""
    pages: int = 0
    # The domain profile it was extracted with ("bv@3/e3"); "" when unknown (before
    # this was recorded, or put directly). A profile change makes it stale.
    profile: str = ""
    # SHA-256 of the file it was extracted from; "" when unknown. The same file
    # sent again (a drive re-sync) is not extracted again.
    content_sha256: str = ""


@dataclass
class Mention:
    id: str
    doc_id: str
    entity_id: str
    text: str
    page: int | None = None
    start: int | None = None
    end: int | None = None
    method: str = "ner"
    confidence: float = 1.0


@dataclass
class AttributeValue:
    name: str
    value: str
    doc_id: str
    page: int | None = None


@dataclass
class Entity:
    id: str
    type: str
    subtype: str = ""
    label: str = ""
    attributes: list[AttributeValue] = field(default_factory=list)
    status: str = "active"
    merged_into: str | None = None


@dataclass
class Evidence:
    doc_id: str
    quote: str
    page: int | None = None
    method: str = "llm"
    confidence: float = 1.0


@dataclass
class Relation:
    id: str
    type: str
    a: str
    b: str
    start: str | None = None  # ISO date
    end: str | None = None
    raw_label: str = ""
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def weak(self) -> bool:
        return bool(self.evidence) and all(e.method in WEAK_METHODS for e in self.evidence)
