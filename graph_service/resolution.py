"""Cross-document entity resolution (E26).

Maps the entities of one document onto global ids. The rules come from the
profile's resolution block (auto_merge / review thresholds):
  identifiers  same normalized value -> same entity (exact);
  places, organizations, objects  same folded label -> same entity;
  events       never merged across documents;
  persons      scored against earlier people with the same name or ID number:
               same ID or registry number 1.0, same held strong identifier (profile
               strong_keys, e.g. an ID card the person uses) 1.0 -- under a
               different name these are only a lead (0.8);
               name + birth date 0.98, name + mother 0.97,
               name only 0.75, misspelt name 0.7, initial only 0.6;
               a conflicting birth date, mother, ID or registry number rules a pair out.
A score at or above auto_merge links to the existing person. A score between
review and auto_merge creates a new person and queues the pair for a human
decision ("may be the same"), so a wrong automatic merge cannot create a false
connection.

Structured records (structured_import.py) add two rules:
  person_key  the salted key of name, birth place, birth date and mother's name
              (person_key.py) is a strong key like a registry number: the same
              key links the records;
  key_only    a record marked so (a contact: no ID number) links to anyone only
              through a strong key. Name + birth date or mother's name, and the
              same name + a shared phone, e-mail or address, only queue a review
              pair; the name alone gives neither. Both sides count: an extracted
              person is not merged into a key-only record without a key either.
"""
from __future__ import annotations

import hashlib
import threading

from extraction import LocalEntity, fold
from model import KEY_ONLY_ATTR, PERSON_KEY_ATTR

# Numbers that name one person: a personal ID number, or the person's number in an
# institution's own register (an inmate registry number); and the structured
# records' person key.
ID_ATTRS = ("id_number", "registry_number", PERSON_KEY_ATTR)


def _key_label(key: str) -> str:
    """"same registry_number" -- the person key only as what it stands for."""
    kind, name = key.split(":")[:2]
    if kind != "attr":
        return "same " + kind
    return "same personal data" if name == PERSON_KEY_ATTR else "same " + name


def _attr_keys(attrs: dict) -> set[str]:
    """Index keys for the ID numbers a person record states."""
    return {"attr:%s:%s" % (k, fold(v)) for k, v in attrs.items()
            if k in ID_ATTRS and isinstance(v, str) and v.strip()}


def _person_keys(name: str) -> tuple[str, str]:
    """(full key, family+initial key) of one spelling; order-insensitive."""
    tokens = [t.strip(".,") for t in fold(name).split() if t.strip(".,")]
    full = " ".join(sorted(tokens))
    longest = max(tokens, key=len) if tokens else ""
    initials = sorted(t[0] for t in tokens if t != longest)
    return full, "%s|%s" % (longest, "".join(initials))


def _edit1(a: str, b: str) -> bool:
    """True when a and b differ by one substitution, insertion, deletion or swap."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


class Resolver:
    def __init__(self, profile: dict):
        self.rules = profile.get("resolution", {})
        self.holding = {r["id"] for r in profile["relation_types"] if r.get("holding")}
        self.lock = threading.Lock()
        self.people: dict[str, dict] = {}  # id -> {"names", "attrs", "keys", "contacts", ...}
        self.by_full: dict[str, set[str]] = {}
        self.by_initial: dict[str, set[str]] = {}
        self.by_key: dict[str, set[str]] = {}
        # Held phone, e-mail and address ids -> people (key-only review pairs).
        self.by_contact: dict[str, set[str]] = {}
        # Folded person -> the person it was merged into (a human decision): a
        # document read again resolves its earlier person to the survivor.
        self.merged: dict[str, str] = {}
        self.review: list[dict] = []
        self._pending: set[str] = set()
        self._pairs: set[tuple[str, str]] = set()
        self._pending_n = 0
        # {folded name: person id} for the document being re-extracted (ingest_jobs).
        self.sticky: dict[str, str] = {}

    def strong_key_subtypes(self) -> set[str]:
        return set((self.rules.get("person") or {}).get("strong_keys") or [])

    def _thresholds(self, type_: str) -> tuple[float, float]:
        rule = self.rules.get(type_) or {}
        return rule.get("auto_merge", 0.95), rule.get("review", 0.7)

    @staticmethod
    def _blank() -> dict:
        return {"names": set(), "attrs": {}, "keys": set(), "contacts": set(), "key_only": False,
                # (attribute, folded value) / contact id -> the documents stating it: a
                # key-only review pair names them, and is shown only where one is visible.
                "attr_docs": {}, "contact_docs": {}}

    def remember_person(self, pid: str, names, attrs: dict, keys=(), contacts=(),
                        key_only: bool = False, doc_id: str = "", contact_docs=None) -> None:
        p = self.people.setdefault(pid, self._blank())
        for k in set(keys) | _attr_keys(attrs):
            p["keys"].add(k)
            self.by_key.setdefault(k, set()).add(pid)
        for c in contacts:
            p["contacts"].add(c)
            self.by_contact.setdefault(c, set()).add(pid)
            docs = (contact_docs or {}).get(c) or ({doc_id} if doc_id else set())
            p["contact_docs"].setdefault(c, set()).update(docs)
        p["key_only"] = p["key_only"] or bool(key_only)
        for n in names:
            p["names"].add(n)
            full, initial = _person_keys(n)
            self.by_full.setdefault(full, set()).add(pid)
            self.by_initial.setdefault(initial, set()).add(pid)
        for k, v in attrs.items():
            if k != KEY_ONLY_ATTR:
                p["attrs"].setdefault(k, set()).add(fold(v))
                if doc_id:
                    p["attr_docs"].setdefault((k, fold(v)), set()).add(doc_id)

    def survivor(self, pid: str) -> str:
        """The person `pid` was (over any number of merges) folded into."""
        seen = set()
        while pid in self.merged and pid not in seen:
            seen.add(pid)
            pid = self.merged[pid]
        return pid

    def merge(self, keep: str, drop: str) -> None:
        """Follow a store merge: later documents resolve to `keep`, never to `drop`."""
        with self.lock:
            self.merged[drop] = keep
            gone = self.people.pop(drop, None)
            if not gone:
                return
            for index in (self.by_full, self.by_initial, self.by_key, self.by_contact):
                for ids in index.values():
                    if drop in ids:
                        ids.discard(drop)
                        ids.add(keep)
            p = self.people.setdefault(keep, self._blank())
            p["names"] |= gone["names"]
            p["keys"] |= gone["keys"]
            p["contacts"] |= gone["contacts"]
            p["key_only"] = p["key_only"] or gone["key_only"]
            for k, v in gone["attrs"].items():
                p["attrs"].setdefault(k, set()).update(v)
            for field in ("attr_docs", "contact_docs"):
                for k, docs in gone[field].items():
                    p[field].setdefault(k, set()).update(docs)

    def rebuild(self, store) -> None:
        """Re-read every person from the store (after an undone merge split one)."""
        with self.lock:
            self.people.clear()
            self.by_full.clear()
            self.by_initial.clear()
            self.by_key.clear()
            self.by_contact.clear()
            self.merged.clear()
            with store._lock:
                self.load_from_store(store)

    def load_from_store(self, store) -> None:
        """Rebuild the person index from an existing (mirror) memory store."""
        strong = self.strong_key_subtypes()
        keys: dict[str, set[str]] = {}
        contacts: dict[str, dict[str, set[str]]] = {}
        for r in store.relations.values():
            if r.type in self.holding and r.a.startswith("person:") \
                    and not r.b.startswith("person:"):
                contacts.setdefault(r.a, {}).setdefault(r.b, set()).update(
                    ev.doc_id for ev in r.evidence)
            if r.type in self.holding and r.a.startswith("person:") and r.b.startswith("id:"):
                _, sub, value = r.b.split(":", 2)
                if sub in strong:
                    keys.setdefault(r.a, set()).add("%s:%s" % (sub, value))
        for e in store.entities.values():
            if e.type == "person" and e.status == "merged" and e.merged_into:
                self.merged[e.id] = e.merged_into
            if e.type != "person" or e.status != "active":
                continue
            names = {store.mentions[m].text for m in store._mentions_of.get(e.id, ())}
            attrs = {}
            for a in e.attributes:
                attrs.setdefault(a.name, a.value)
            # A person may hold the keys of several records (a merge): all count.
            own = {"attr:%s:%s" % (PERSON_KEY_ATTR, fold(a.value)) for a in e.attributes
                   if a.name == PERSON_KEY_ATTR and a.value}
            held = contacts.get(e.id, {})
            self.remember_person(e.id, names, attrs, keys.get(e.id, set()) | own, held,
                                 any(a.name == KEY_ONLY_ATTR for a in e.attributes),
                                 contact_docs=held)
            p = self.people[e.id]
            for a in e.attributes:
                p["attr_docs"].setdefault((a.name, fold(a.value)), set()).add(a.doc_id)

    def _score(self, ent: LocalEntity, pid: str, keys=frozenset(), contacts=frozenset(),
               key_only: bool = False) -> tuple[float, str]:
        other = self.people[pid]
        mine = {k: fold(v) for k, (v, _) in ent.attrs.items()}
        for key in ("id_number", "registry_number", "birth_date", "mother_name"):
            if key in mine and other["attrs"].get(key) and mine[key] not in other["attrs"][key]:
                return 0.0, "conflicting " + key
        keys = set(keys) | _attr_keys({k: v for k, (v, _) in ent.attrs.items()})
        mine_full = {_person_keys(n)[0] for n in ent.names}
        theirs_full = {_person_keys(n)[0] for n in other["names"]}
        mine_initial = {_person_keys(n)[1] for n in ent.names}
        theirs_initial = {_person_keys(n)[1] for n in other["names"]}
        if mine_full & theirs_full:
            name_score, why = 0.75, "same name"
        elif any(_edit1(a, b) for a in mine_full for b in theirs_full):
            name_score, why = 0.7, "near-identical name"
        elif mine_initial & theirs_initial:
            name_score, why = 0.6, "same family name and initial"
        else:
            name_score, why = 0.0, "different name"
        shared = sorted(keys & other["keys"])
        if shared:
            # The same ID or registry number, or the same held ID card, links the same
            # person; under another name it is only a lead for a human (a typo in the
            # name or the number, or a borrowed document).
            label = _key_label(shared[0])
            return (1.0, label) if name_score >= 0.6 else (0.8, label + ", different name")
        if not name_score:
            return 0.0, why
        if key_only or other["key_only"]:
            # A key-only record: what would otherwise merge is a review pair at most.
            auto, review = self._thresholds("person")
            if review >= auto:
                return 0.0, why
            if "birth_date" in mine and mine["birth_date"] in other["attrs"].get("birth_date", ()):
                return review, why + " + birth date"
            if "mother_name" in mine and mine["mother_name"] in other["attrs"].get("mother_name",
                                                                                   ()):
                return review, why + " + mother's name"
            if set(contacts) & other["contacts"]:
                return review, why + " + shared contact data"
            return 0.0, why + " only"
        if "birth_date" in mine and mine["birth_date"] in other["attrs"].get("birth_date", ()):
            return (0.98 if name_score >= 0.75 else 0.95), why + " + birth date"
        if "mother_name" in mine and mine["mother_name"] in other["attrs"].get("mother_name", ()):
            return (0.97 if name_score >= 0.75 else 0.93), why + " + mother's name"
        return name_score, why

    def _candidates(self, ent: LocalEntity, keys=frozenset(), contacts=frozenset()) -> set[str]:
        out = set()
        for k in set(keys) | _attr_keys({k: v for k, (v, _) in ent.attrs.items()}):
            out |= self.by_key.get(k, set())
        for c in contacts:
            out |= self.by_contact.get(c, set())
        for n in ent.names:
            full, initial = _person_keys(n)
            out |= self.by_full.get(full, set()) | self.by_initial.get(initial, set())
            for key in self.by_full:
                if _edit1(full, key):
                    out |= self.by_full[key]
        return out

    def resolve(self, doc_id: str, ent: LocalEntity, keys=frozenset(), contacts=frozenset(),
                key_only: bool = False, new_id: str = "") -> str:
        """Global id of a document entity; keys: "subtype:value" strong identifiers it
        holds; contacts: ids of the phones, e-mails and addresses it holds (only
        key-only pairs use them); new_id: the id a new person gets (default: from
        the document and the entity's ref)."""
        if ent.type == "identifier":
            return "id:%s:%s" % (ent.subtype or "other", ent.value or fold(ent.names[0]))
        if ent.type in ("place", "organization", "object"):
            return "%s:%s" % (ent.type, min(fold(n) for n in ent.names))
        if ent.type == "event":
            return "event:%s:%s" % (doc_id, ent.ref)
        new_id = new_id or "person:" + hashlib.sha1(
            ("%s\0%s" % (doc_id, ent.ref)).encode()).hexdigest()[:16]
        auto, review = self._thresholds("person")
        attrs = {k: v for k, (v, _) in ent.attrs.items()}
        key_only = key_only or KEY_ONLY_ATTR in attrs
        contacts = frozenset(contacts)
        with self.lock:
            # A person this document named before keeps its id (unless the
            # document now says something that conflicts with it).
            for name in ent.names:
                target = self.sticky.get(fold(name))
                if target not in self.people:
                    continue
                score, why = self._score(ent, target, keys, contacts, key_only)
                # A key-only record keeps a person only while a strong key holds.
                if not why.startswith("conflicting") and (not key_only or score >= auto):
                    self.remember_person(target, ent.names, attrs, keys, contacts, key_only, doc_id)
                    return target
            # The person this record made before was merged by a human: stay merged.
            target = self.survivor(new_id)
            if target != new_id and target in self.people and not self._score(
                    ent, target, keys, contacts, key_only)[1].startswith("conflicting"):
                self.remember_person(target, ent.names, attrs, keys, contacts, key_only, doc_id)
                return target
            scored = sorted(((*self._score(ent, pid, keys, contacts, key_only), pid)
                             for pid in self._candidates(ent, keys, contacts) - {new_id}),
                            reverse=True)
            if scored and scored[0][0] >= auto:
                target = scored[0][2]
                self.remember_person(target, ent.names, attrs, keys, contacts, key_only, doc_id)
                return target
            # Review pairs go to the best-scoring earlier people, and to people that
            # are themselves waiting for review only when nobody else fits: one
            # same-name person mentioned in ten notes gives ten pairs, not 45.
            if len(self.review) != self._pending_n:  # review may be refilled from outside
                self._pending = {item["a"] for item in self.review}
                self._pairs = {(item["a"], item["b"]) for item in self.review}
                self._pending_n = len(self.review)
            pending = self._pending
            candidates = [s for s in scored if s[0] >= review]
            settled = [s for s in candidates if s[2] not in pending]
            candidates = settled or candidates
            best = candidates[0][0] if candidates else 0.0
            for score, why, pid in candidates:
                if score == best and (new_id, pid) not in self._pairs \
                        and (pid, new_id) not in self._pairs:
                    self._pairs.add((new_id, pid))
                    item = {"a": new_id, "b": pid, "score": score, "reason": why,
                            "doc_id": doc_id, "names": sorted(ent.names)}
                    if key_only or self.people[pid]["key_only"]:
                        item["basis"] = self._basis(ent, pid, contacts, why)
                    self.review.append(item)
                    self._pending.add(new_id)
                    self._pending_n += 1
            self.remember_person(new_id, ent.names, attrs, keys, contacts, key_only, doc_id)
            return new_id

    def _basis(self, ent: LocalEntity, pid: str, contacts, why: str) -> list[str]:
        """The documents of `pid` that state what a key-only pair matched on: the
        pair is shown only to a caller who may read one of them."""
        other = self.people[pid]
        if why.endswith("shared contact data"):
            return sorted({d for c in set(contacts) & other["contacts"]
                           for d in other["contact_docs"].get(c, ())})
        name = "birth_date" if why.endswith("birth date") else "mother_name"
        value = fold(ent.attrs[name][0]) if name in ent.attrs else ""
        return sorted(other["attr_docs"].get((name, value), ()))
