"""Cross-document entity resolution (E26).

Maps the entities of one document onto global ids. The rules come from the
profile's resolution block (auto_merge / review thresholds):
  identifiers  same normalized value -> same entity (exact);
  places, organizations, objects  same folded label -> same entity;
  events       never merged across documents;
  persons      scored against earlier people with the same name:
               same ID number 1.0, same held strong identifier (profile
               strong_keys, e.g. an ID card the person uses) 1.0,
               name + birth date 0.98, name + mother 0.97,
               name only 0.75, misspelt name 0.7, initial only 0.6;
               a conflicting birth date, mother or ID number rules a pair out.
A score at or above auto_merge links to the existing person. A score between
review and auto_merge creates a new person and queues the pair for a human
decision ("may be the same"), so a wrong automatic merge cannot create a false
connection.
"""
from __future__ import annotations

import hashlib
import threading

from extraction import LocalEntity, fold


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
        self.people: dict[str, dict] = {}  # id -> {"names", "attrs", "keys"}
        self.by_full: dict[str, set[str]] = {}
        self.by_initial: dict[str, set[str]] = {}
        self.by_key: dict[str, set[str]] = {}
        self.review: list[dict] = []

    def strong_key_subtypes(self) -> set[str]:
        return set((self.rules.get("person") or {}).get("strong_keys") or [])

    def _thresholds(self, type_: str) -> tuple[float, float]:
        rule = self.rules.get(type_) or {}
        return rule.get("auto_merge", 0.95), rule.get("review", 0.7)

    def remember_person(self, pid: str, names, attrs: dict, keys=()) -> None:
        p = self.people.setdefault(pid, {"names": set(), "attrs": {}, "keys": set()})
        for k in keys:
            p["keys"].add(k)
            self.by_key.setdefault(k, set()).add(pid)
        for n in names:
            p["names"].add(n)
            full, initial = _person_keys(n)
            self.by_full.setdefault(full, set()).add(pid)
            self.by_initial.setdefault(initial, set()).add(pid)
        for k, v in attrs.items():
            p["attrs"].setdefault(k, set()).add(fold(v))

    def merge(self, keep: str, drop: str) -> None:
        """Follow a store merge: later documents resolve to `keep`, never to `drop`."""
        with self.lock:
            gone = self.people.pop(drop, None)
            if not gone:
                return
            for index in (self.by_full, self.by_initial, self.by_key):
                for ids in index.values():
                    if drop in ids:
                        ids.discard(drop)
                        ids.add(keep)
            p = self.people.setdefault(keep, {"names": set(), "attrs": {}, "keys": set()})
            p["names"] |= gone["names"]
            p["keys"] |= gone["keys"]
            for k, v in gone["attrs"].items():
                p["attrs"].setdefault(k, set()).update(v)

    def load_from_store(self, store) -> None:
        """Rebuild the person index from an existing (mirror) memory store."""
        strong = self.strong_key_subtypes()
        keys: dict[str, set[str]] = {}
        for r in store.relations.values():
            if r.type in self.holding and r.a.startswith("person:") and r.b.startswith("id:"):
                _, sub, value = r.b.split(":", 2)
                if sub in strong:
                    keys.setdefault(r.a, set()).add("%s:%s" % (sub, value))
        for e in store.entities.values():
            if e.type != "person" or e.status != "active":
                continue
            names = {store.mentions[m].text for m in store._mentions_of.get(e.id, ())}
            attrs = {}
            for a in e.attributes:
                attrs.setdefault(a.name, a.value)
            self.remember_person(e.id, names, attrs, keys.get(e.id, ()))

    def _score(self, ent: LocalEntity, pid: str, keys=frozenset()) -> tuple[float, str]:
        other = self.people[pid]
        mine = {k: fold(v) for k, (v, _) in ent.attrs.items()}
        for key in ("id_number", "birth_date", "mother_name"):
            if key in mine and other["attrs"].get(key) and mine[key] not in other["attrs"][key]:
                return 0.0, "conflicting " + key
        if "id_number" in mine and mine["id_number"] in other["attrs"].get("id_number", ()):
            return 1.0, "same id_number"
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
            # The same held ID card links the same person; under another name it is
            # only a lead for a human (typo in the name, or a borrowed document).
            label = "same " + shared[0].split(":")[0]
            return (1.0, label) if name_score >= 0.6 else (0.8, label + ", different name")
        if not name_score:
            return 0.0, why
        if "birth_date" in mine and mine["birth_date"] in other["attrs"].get("birth_date", ()):
            return (0.98 if name_score >= 0.75 else 0.95), why + " + birth date"
        if "mother_name" in mine and mine["mother_name"] in other["attrs"].get("mother_name", ()):
            return (0.97 if name_score >= 0.75 else 0.93), why + " + mother's name"
        return name_score, why

    def _candidates(self, ent: LocalEntity, keys=frozenset()) -> set[str]:
        out = set()
        for k in keys:
            out |= self.by_key.get(k, set())
        for n in ent.names:
            full, initial = _person_keys(n)
            out |= self.by_full.get(full, set()) | self.by_initial.get(initial, set())
            for key in self.by_full:
                if _edit1(full, key):
                    out |= self.by_full[key]
        return out

    def resolve(self, doc_id: str, ent: LocalEntity, keys=frozenset()) -> str:
        """Global id of a document entity; keys: "subtype:value" strong identifiers it holds."""
        if ent.type == "identifier":
            return "id:%s:%s" % (ent.subtype or "other", ent.value or fold(ent.names[0]))
        if ent.type in ("place", "organization", "object"):
            return "%s:%s" % (ent.type, min(fold(n) for n in ent.names))
        if ent.type == "event":
            return "event:%s:%s" % (doc_id, ent.ref)
        new_id = "person:" + hashlib.sha1(("%s\0%s" % (doc_id, ent.ref)).encode()).hexdigest()[:16]
        auto, review = self._thresholds("person")
        attrs = {k: v for k, (v, _) in ent.attrs.items()}
        with self.lock:
            scored = sorted(((*self._score(ent, pid, keys), pid)
                             for pid in self._candidates(ent, keys)), reverse=True)
            if scored and scored[0][0] >= auto:
                target = scored[0][2]
                self.remember_person(target, ent.names, attrs, keys)
                return target
            for score, why, pid in scored:
                if score >= review:
                    self.review.append({"a": new_id, "b": pid, "score": score, "reason": why,
                                        "doc_id": doc_id, "names": sorted(ent.names)})
            self.remember_person(new_id, ent.names, attrs, keys)
            return new_id
