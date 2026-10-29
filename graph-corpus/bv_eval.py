"""BV query requirements as an eval set with gold answers (E26b).

The prison service (BV) described two investigation "agents" (docs/bv_spec,
2026-10): one starts from an inmate's name or registry number and goes up to
four levels deep, the other starts from a bank account, phone number, e-mail
address or address and goes at least two levels deep. Both chain through the
inmates' approved contacts to further inmates, then visualise, summarise and
export what they found. Each step becomes a question here; its gold answer is
computed from the hidden world (world.py), limited to what the documents state.

    python graph-corpus/bv_eval.py --out graph-corpus/out/bv_eval.json

How the BV terms map onto the synthetic corpus (assumptions to confirm with BV):
- approved contact ("engedélyezett kapcsolattartó") = a contact listed on the
  intake sheet (RELATIVE_OF in the world, "Kapcsolattartók" in the document);
  visitors and called numbers are acceptable extras, never required;
- intelligence log / open police information = incident reports and field
  notes; the corpus has nothing closer;
- inmate hierarchy, BV Bank and e-mail data are not in the corpus: questions on
  them must be answered with "no data" (negative controls).

Every question carries `must` items (people, registry numbers, phone numbers,
addresses, file numbers, phrases) scored against the answer text, the
coverage status and the gaps behind it, and a reference plan of analyst tool
calls (bv_toolcheck.py runs those on a perfect-extraction graph).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.join(os.path.dirname(HERE), "demo-corpus"))

import world as world_mod  # noqa: E402
from render import Renderer  # noqa: E402

DRIVE_PREFIX = "/storage/drives"
# Not in the corpus on purpose: the negative controls ask about them.
ABSENT_EMAIL = "kovacs.gabor.1979@freemail.hu"
ABSENT_ACCOUNT = "11773016-10293847-00000000"
NO_DATA_WORDS = ("nincs", "nem talal", "nem szerepel", "nem tartalmaz", "nem all rendelkezes",
                 "nem ismert", "nem azonosithato", "nincsen")


# -- the world, indexed by what the documents state ----------------------------------

class Facts:
    """Evidenced relations of the world, indexed for the BV chains."""

    def __init__(self, w: world_mod.World, docs):
        self.w = w
        self.docs = {d.key: d for d in docs}
        self.contacts = defaultdict(list)      # inmate -> [(contact, kind)]
        self.inmates_of = defaultdict(list)    # contact -> [inmate]
        self.users = defaultdict(set)          # phone -> persons (not seized)
        self.callers = defaultdict(set)        # phone -> inmates who called it
        self.residents = defaultdict(set)      # address -> persons
        self.incidents = defaultdict(list)     # person -> [Incident]
        self.visitors = defaultdict(set)       # inmate -> visitors
        evidenced = [r for r in w.relations if r.evidence]
        for r in evidenced:
            if r.type == "RELATIVE_OF":
                self.contacts[r.a].append((r.b, r.attrs.get("kind", "")))
                self.inmates_of[r.b].append(r.a)
            elif r.type == "USES_PHONE" and not r.attrs.get("seized"):
                self.users[r.b].add(r.a)
            elif r.type == "CALLED":
                self.callers[r.b].add(r.a)
            elif r.type == "RESIDES_AT":
                self.residents[r.b].add(r.a)
            elif r.type == "VISITED":
                self.visitors[r.b].add(r.a)
        involved = {(r.a, r.b) for r in evidenced if r.type == "INVOLVED_IN"}
        for inc in w.incidents:
            for pid in inc.persons:
                if (pid, inc.id) in involved:
                    self.incidents[pid].append(inc)
        counts = defaultdict(int)
        for p in w.persons.values():
            counts[p.name] += 1
        # People sharing a name with someone else: never a seed (the answer
        # would rightly ask back which one is meant).
        self.namesake = {pid for pid, p in w.persons.items() if counts[p.name] > 1}
        self.report_of = {}
        for d in docs:
            if d.kind != "jelentes":
                continue
            for _, line in d.blocks:
                if line.startswith("Iktatószám: "):
                    self.report_of.setdefault(d.key, line.split(": ", 1)[1])

    def person(self, pid):
        return self.w.persons[pid]

    def contact_ids(self, inmate):
        return [c for c, _ in self.contacts.get(inmate, [])]

    def other_inmates(self, contact, exclude=()):
        return sorted({i for i in self.inmates_of.get(contact, []) if i not in exclude})

    def incident_docs(self, inc):
        return sorted(k for k, d in self.docs.items()
                      if d.kind == "jelentes" and any(r.type == "INVOLVED_IN" and r.b == inc.id
                                                      for r in self._rels(d)))

    def _rels(self, doc):
        ids = set(doc.evidence)
        return [r for r in self.w.relations if r.id in ids]


# -- seeds ------------------------------------------------------------------------

def person_seed(f: Facts):
    """The inmate whose approved contacts reach the most other inmates."""
    best = None
    for inmate in sorted(f.contacts):
        if inmate in f.namesake:
            continue
        others = {j for c in f.contact_ids(inmate) for j in f.other_inmates(c, {inmate})}
        second = sum(len(f.contacts.get(j, [])) for j in others)
        key = (len(others), second, len(f.contacts[inmate]))
        if others and (best is None or key > best[0]):
            best = (key, inmate)
    return best[1] if best else None


def incident_seed(f: Facts):
    """The inmate in the most incidents whose fellow inmates there have contacts."""
    best = None
    for pid in sorted(f.incidents):
        if pid in f.namesake or f.person(pid).role != "inmate":
            continue
        mates = {m for inc in f.incidents[pid] for m in inc.persons if m != pid}
        with_contacts = [m for m in mates if f.contacts.get(m)]
        key = (len(with_contacts), len(f.incidents[pid]))
        if with_contacts and (best is None or key > best[0]):
            best = (key, pid)
    return best[1] if best else None


def summary_seed(f: Facts):
    """An inmate found with a phone in an incident, in the most incidents."""
    best = None
    for inc in f.w.incidents:
        if not inc.phone:
            continue
        pid = inc.persons[0]
        if pid in f.namesake:
            continue
        key = len(f.incidents.get(pid, []))
        if best is None or key > best[0]:
            best = (key, pid)
    return best[1] if best else None


def phone_seed(f: Facts):
    """A phone number used by several people whose users are contacts of the most inmates."""
    best = None
    for phone in sorted(f.users):
        users = f.users[phone]
        if len(users) < 2 or users & f.namesake:
            continue
        inmates = {i for u in users for i in f.inmates_of.get(u, [])}
        key = (len(inmates), len(f.callers.get(phone, ())))
        if inmates and (best is None or key > best[0]):
            best = (key, phone)
    return best[1] if best else None


def address_seed(f: Facts):
    """An address an inmate shares with someone outside who is a contact of other inmates."""
    best = None
    for addr in sorted(f.residents):
        people = f.residents[addr]
        if len(people) < 2 or people & f.namesake:
            continue
        outside = [p for p in people if f.person(p).role != "inmate"]
        inside = [p for p in people if f.person(p).role == "inmate"]
        inmates = {i for p in outside for i in f.inmates_of.get(p, [])}
        key = (len(inmates - set(inside)), len(inside))
        if inside and outside and inmates and (best is None or key > best[0]):
            best = (key, addr)
    return best[1] if best else None


# -- gold items ---------------------------------------------------------------------

def _item(kind, value, note="", alt=()):
    out = {"kind": kind, "value": value}
    if alt:
        out["alt"] = list(alt)
    if note:
        out["note"] = note
    return out


# How the reports word an incident (render.py), and the world's own word for it.
INCIDENT_WORDS = {"verekedés": ("dulakodás",), "rendbontás": ("hangos szóváltás", "szóváltás")}


def incident_item(kind):
    return _item("text", kind, "esemény jellege", INCIDENT_WORDS.get(kind, ()))


def person_items(f: Facts, pid, registry=True):
    p = f.person(pid)
    items = [_item("person", p.name)]
    if registry and p.registry_number:
        items.append(_item("registry", p.registry_number, p.name))
    return items


def contact_items(f: Facts, pid, identifiers=True):
    p = f.person(pid)
    items = [_item("person", p.name)]
    if identifiers:
        items += [_item("phone", ph, p.name) for ph in p.phones[:1]]
        items.append(_item("address", p.address, p.name))
    return items


def _dedupe(items):
    seen, out = set(), []
    for it in items:
        key = (it["kind"], it["value"])
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _question(qid, agent, step, text, seed, must, coverage, needs, plan, acceptable=(),
              documents=(), no_data=False, notes=""):
    return {"id": qid, "agent": agent, "bv_step": step, "question": text, "seed": seed,
            "must": _dedupe(must), "acceptable": sorted(set(acceptable)),
            "documents": sorted(set(documents)), "no_data": no_data,
            "coverage": coverage, "needs": list(needs), "reference_plan": plan,
            "notes": notes}


def _source(f: Facts, key):
    d = f.docs[key]
    return "%s/%s/%s" % (DRIVE_PREFIX, d.drive, d.rel)


def _intake(f: Facts, pid):
    return [k for k, d in f.docs.items() if d.kind == "befogadasi_adatlap"
            and any(m[0] == pid for m in d.mentions)]


# -- agent 1: from an inmate ---------------------------------------------------------

def agent_person(f: Facts):
    out = []
    s = person_seed(f)
    sp = f.person(s)
    contacts = f.contact_ids(s)
    visitors = [f.person(v).name for v in f.visitors.get(s, ())]
    seed = {"kind": "inmate", "name": sp.name, "registry_number": sp.registry_number}

    must = [_item("registry", sp.registry_number, sp.name),
            _item("text", sp.mother, "anyja neve"), _item("text", sp.birthplace, "születési hely")]
    for c in contacts:
        must += contact_items(f, c)
    out.append(_question(
        "P-1.1-adatok", "person", "1.1",
        "Ellenőrizd %s fogvatartott személyes adatait (nyilvántartási szám, születési hely, "
        "anyja neve), és sorold fel az engedélyezett kapcsolattartóit azonosító adataikkal: "
        "név, telefonszám, lakcím." % sp.name,
        seed, must, "partial",
        ["person attributes (registry number, mother's name, birthplace) are not in the "
         "graph facts the answer is written from",
         "contact phone and address are two links away (inmate -> contact -> phone/address)"],
        [{"tool": "relations", "args": {"entity": sp.name}},
         {"tool": "find_pattern", "args": {"start": {"name": sp.name}, "steps": [
             {"relation": "RELATIVE_OF", "kind": "person"}, {"relation": "USES", "kind": "phone"}]}},
         {"tool": "find_pattern", "args": {"start": {"name": sp.name}, "steps": [
             {"relation": "RELATIVE_OF", "kind": "person"},
             {"relation": "RESIDES_AT", "kind": "place"}]}}],
        acceptable=visitors, documents=[_source(f, k) for k in _intake(f, s)]))

    must = [_item("person", sp.name)] + [_item("person", f.person(c).name) for c in contacts]
    out.append(_question(
        "P-1.1-nyilvszam", "person", "1.1",
        "Ki a %s nyilvántartási számú fogvatartott, és kik az engedélyezett "
        "kapcsolattartói?" % sp.registry_number,
        seed, must, "gap",
        ["lookup by registry number: the number is a person attribute, the name search "
         "does not see it"],
        [{"tool": "relations", "args": {"entity": sp.registry_number}}],
        acceptable=visitors, documents=[_source(f, k) for k in _intake(f, s)]))

    others = {}
    for c in contacts:
        for j in f.other_inmates(c, {s}):
            others.setdefault(j, []).append(c)
    must = []
    for j, via in sorted(others.items()):
        must += person_items(f, j)
    out.append(_question(
        "P-1.2.2-kozos-kapcsolattarto", "person", "1.2.2",
        "%s engedélyezett kapcsolattartói mely további fogvatartottaknak engedélyezett "
        "kapcsolattartói? Add meg ezeknek a fogvatartottaknak a nevét és nyilvántartási "
        "számát, és hogy melyik kapcsolattartón keresztül kapcsolódnak." % sp.name,
        seed, must, "partial",
        ["registry numbers of the further inmates are attributes, not facts"],
        [{"tool": "find_pattern", "args": {"start": {"name": sp.name}, "steps": [
            {"relation": "RELATIVE_OF", "kind": "person"},
            {"relation": "RELATIVE_OF", "kind": "inmate"}]}}],
        documents=[_source(f, k) for j in others for k in _intake(f, j)],
        notes="via: " + "; ".join("%s <- %s" % (f.person(j).name, ", ".join(
            f.person(c).name for c in via)) for j, via in sorted(others.items()))))

    second = {}
    for j in others:
        for c in f.contact_ids(j):
            if c not in contacts:
                second.setdefault(c, set()).add(j)
    must = []
    for j in sorted(others):
        must.append(_item("person", f.person(j).name))
    for c in sorted(second):
        must += contact_items(f, c, identifiers=False)
    out.append(_question(
        "P-1.2.2-masodik-szint", "person", "1.2.2",
        "Kik azok a fogvatartottak, akikkel %s közös engedélyezett kapcsolattartón keresztül "
        "áll kapcsolatban, és kik ezeknek a fogvatartottaknak a további kapcsolattartói?"
        % sp.name,
        seed, must, "partial",
        ["three links deep (inmate -> contact -> inmate -> contact): at the limit of "
         "find_pattern (3 steps) and of the facts budget"],
        [{"tool": "find_pattern", "args": {"start": {"name": sp.name}, "steps": [
            {"relation": "RELATIVE_OF", "kind": "person"},
            {"relation": "RELATIVE_OF", "kind": "inmate"}]}},
         # The longer chain drops the inmates with no contact of their own.
         {"tool": "find_pattern", "args": {"start": {"name": sp.name}, "steps": [
            {"relation": "RELATIVE_OF", "kind": "person"},
            {"relation": "RELATIVE_OF", "kind": "inmate"},
            {"relation": "RELATIVE_OF", "kind": "person"}]}}]))

    i = incident_seed(f)
    ip = f.person(i)
    seed_i = {"kind": "inmate", "name": ip.name, "registry_number": ip.registry_number}
    reports = sorted({k for inc in f.incidents[i] for k in f.incident_docs(inc)})
    must = [_item("case", f.report_of[k]) for k in reports if k in f.report_of]
    must += [incident_item(inc.kind) for inc in f.incidents[i]]
    out.append(_question(
        "P-1.1-naplok", "person", "1.1",
        "Mely jelentésekben szerepel %s? Add meg a jelentések iktatószámát, dátumát és "
        "az esemény jellegét." % ip.name,
        seed_i, must, "partial",
        ["file numbers (iktatószám) are in the report text, not linked to the incident in "
         "the graph: needs the document side (search / read_all)",
         "no intelligence logs or open police information in the corpus"],
        [{"tool": "relations", "args": {"entity": ip.name}},
         {"tool": "read_all", "args": {"terms": [ip.name.split()[0]], "mode": "any"}}],
        documents=[_source(f, k) for k in reports]))

    mates = {}
    for inc in f.incidents[i]:
        for m in inc.persons:
            if m != i:
                mates.setdefault(m, []).append(inc)
    must = []
    for m in sorted(mates):
        must += person_items(f, m)
        for c in f.contact_ids(m):
            must.append(_item("person", f.person(c).name, "kapcsolattartója: %s"
                              % f.person(m).name))
    out.append(_question(
        "P-1.2.1-esemenytarsak", "person", "1.2.1",
        "A %s fogvatartottat érintő jelentésekben mely további fogvatartottak szerepelnek, "
        "és kik az ő engedélyezett kapcsolattartóik?" % ip.name,
        seed_i, must, "partial",
        ["registry numbers are attributes",
         "three links deep (inmate -> incident -> inmate -> contact)"],
        [{"tool": "find_pattern", "args": {"start": {"name": ip.name}, "steps": [
            {"relation": "INVOLVED_IN", "kind": "incident"},
            {"relation": "INVOLVED_IN", "kind": "inmate"}]}},
         {"tool": "find_pattern", "args": {"start": {"name": ip.name}, "steps": [
            {"relation": "INVOLVED_IN", "kind": "incident"},
            {"relation": "INVOLVED_IN", "kind": "inmate"},
            {"relation": "RELATIVE_OF", "kind": "person"}]}}],
        documents=[_source(f, k) for k in reports]))

    further = {}
    for m in mates:
        for c in f.contact_ids(m):
            for j in f.other_inmates(c, {m, i}):
                further.setdefault(j, set()).add(c)
    must = []
    for j in sorted(further):
        must += person_items(f, j)
    out.append(_question(
        "P-1.2.3-tovabbi-fogvatartottak", "person", "1.2.3",
        "A %s fogvatartottal közös eseményben érintett fogvatartottak kapcsolattartói mely "
        "további fogvatartottaknak engedélyezett kapcsolattartói? (név, nyilvántartási szám)"
        % ip.name,
        seed_i, must, "partial" if further else "gap",
        ["four links deep: needs two planning rounds (find_pattern allows 3 steps)",
         "registry numbers are attributes"],
        [{"tool": "find_pattern", "args": {"start": {"name": ip.name}, "steps": [
            {"relation": "INVOLVED_IN", "kind": "incident"},
            {"relation": "INVOLVED_IN", "kind": "inmate"},
            {"relation": "RELATIVE_OF", "kind": "person"}]}}]
        + [{"tool": "find_pattern", "round": 2, "args": {"start": {"name": f.person(c).name},
                                                          "steps": [{"relation": "RELATIVE_OF",
                                                                     "kind": "inmate"}]}}
           for c in sorted({c for cs in further.values() for c in cs})],
        notes="no further inmate in this corpus" if not further else ""))

    s3 = summary_seed(f)
    p3 = f.person(s3)
    incs = f.incidents[s3]
    must = [incident_item(inc.kind) for inc in incs]
    must += [_item("phone", inc.phone, "lefoglalt telefonban") for inc in incs if inc.phone
             and inc.persons[0] == s3]
    out.append(_question(
        "P-1.4-osszefoglalo", "person", "1.4",
        "Készíts rövid összefoglalót %s fogvatartottról a jelentések és feljegyzések alapján: "
        "milyen rendkívüli eseményekben érintett, találtak-e nála tiltott tárgyat, például "
        "mobiltelefont, és milyen hívószám szerepelt rajta?" % p3.name,
        {"kind": "inmate", "name": p3.name, "registry_number": p3.registry_number},
        must, "partial",
        ["the seized phone is a USES link only when extraction marks it; the number itself "
         "is in the report text",
         "no intelligence logs or open police information in the corpus"],
        [{"tool": "relations", "args": {"entity": p3.name}},
         {"tool": "timeline", "args": {"entity": p3.name}}],
        documents=[_source(f, k) for inc in incs for k in f.incident_docs(inc)]))

    out.append(_capability("P-1.3-vizualis", "person", "1.3",
                           "Jelenítsd meg a feltárt összefüggéseket egy kapcsolati ábrán, a "
                           "talált adatokkal együtt (%s)." % sp.name,
                           ["the Relationships view shows one entity's neighbourhood, not the "
                            "subgraph an answer was built from"]))
    out.append(_capability("P-1.5-excel", "person", "1.5",
                           "Exportáld Excel-táblázatba a %s kapcsán feltárt személyeket és "
                           "azonosító adataikat." % sp.name,
                           ["no XLSX export of an answer's people and identifiers; the "
                            "dossier is DOCX/PDF for one entity"]))
    return out


def _capability(qid, agent, step, text, needs):
    return _question(qid, agent, step, text, None, [], "gap", needs, [],
                     notes="capability check, not scored from the answer text")


# -- agent 2: from an identifier -------------------------------------------------------

def agent_identifier(f: Facts):
    out = []
    phone = phone_seed(f)
    users = sorted(f.users[phone])
    callers = sorted(f.callers.get(phone, ()))
    seed = {"kind": "phone", "value": phone}
    must = [it for u in users for it in person_items(f, u, registry=False)]
    must += [it for c in callers for it in person_items(f, c)]
    out.append(_question(
        "I-2.1-telefon", "identifier", "2.1",
        "Kik használják a %s telefonszámot, és mely fogvatartottak hívták ezt a számot? "
        "Add meg a fogvatartottak nyilvántartási számát is." % phone,
        seed, must, "partial",
        ["registry numbers of the callers are attributes"],
        [{"tool": "relations", "args": {"entity": phone}}]))

    inmates = {}
    for u in users:
        for j in f.inmates_of.get(u, []):
            inmates.setdefault(j, set()).add(u)
    must = [it for j in sorted(inmates) for it in person_items(f, j)]
    out.append(_question(
        "I-2.2-telefon-kapcsolattarto", "identifier", "2.2",
        "A %s telefonszám használói mely fogvatartottaknak engedélyezett "
        "kapcsolattartói? Add meg a fogvatartottak nevét és nyilvántartási számát." % phone,
        seed, must, "partial",
        ["registry numbers are attributes"],
        [{"tool": "find_pattern", "args": {"start": {"name": phone}, "steps": [
            {"relation": "USES", "kind": "person"},
            {"relation": "RELATIVE_OF", "kind": "inmate"}]}}],
        acceptable=[f.person(c).name for c in callers],
        notes="via: " + "; ".join("%s <- %s" % (f.person(j).name, ", ".join(
            f.person(u).name for u in sorted(v))) for j, v in sorted(inmates.items()))))

    addr = address_seed(f)
    people = sorted(f.residents[addr])
    seed = {"kind": "address", "value": addr}
    outside = [p for p in people if f.person(p).role != "inmate"]
    inside = [p for p in people if f.person(p).role == "inmate"]
    must = [it for p in people for it in person_items(f, p)]
    out.append(_question(
        "I-2.1-lakcim", "identifier", "2.1",
        "Kik szerepelnek a(z) %s lakcímmel az iratokban? Van-e köztük fogvatartott?" % addr,
        seed, must, "partial",
        ["the address must be found as a place entity by its text; registry numbers are "
         "attributes"],
        [{"tool": "relations", "args": {"entity": addr}}]))

    linked = {}
    for p in outside:
        for j in f.inmates_of.get(p, []):
            linked.setdefault(j, set()).add(p)
    must = [it for j in sorted(linked) for it in person_items(f, j)]
    out.append(_question(
        "I-2.2-lakcim-kapcsolattarto", "identifier", "2.2",
        "A(z) %s címen lakó személyek mely fogvatartottaknak engedélyezett "
        "kapcsolattartói? (név, nyilvántartási szám)" % addr,
        seed, must, "partial",
        ["registry numbers are attributes"],
        [{"tool": "find_pattern", "args": {"start": {"name": addr}, "steps": [
            {"relation": "RESIDES_AT", "kind": "person"},
            {"relation": "RELATIVE_OF", "kind": "inmate"}]}}],
        acceptable=[f.person(p).name for p in inside],
        notes="via: " + "; ".join("%s <- %s" % (f.person(j).name, ", ".join(
            f.person(p).name for p in sorted(v))) for j, v in sorted(linked.items()))))

    out.append(_question(
        "I-2.1-email", "identifier", "2.1",
        "Ki használja a %s e-mail-címet, és mely fogvatartottakkal áll kapcsolatban?"
        % ABSENT_EMAIL,
        {"kind": "email", "value": ABSENT_EMAIL}, [], "gap",
        ["no e-mail addresses in the corpus (BV: contact data, FŐNIX)"],
        [{"tool": "relations", "args": {"entity": ABSENT_EMAIL}}], no_data=True,
        notes="negative control: the answer must say there is no data and name nobody"))
    out.append(_question(
        "I-2.1-szamlaszam", "identifier", "2.1",
        "Kihez tartozik a %s bankszámlaszám, és mely fogvatartottnak utaltak róla pénzt?"
        % ABSENT_ACCOUNT,
        {"kind": "bank_account", "value": ABSENT_ACCOUNT}, [], "gap",
        ["no BV Bank data (account history, transfers, referring contacts) in the corpus",
         "no transfer relation in the profile"],
        [{"tool": "relations", "args": {"entity": ABSENT_ACCOUNT}}], no_data=True,
        notes="negative control: the answer must say there is no data and name nobody"))
    out.append(_capability("I-2.3-vizualis", "identifier", "2.3",
                           "Jelenítsd meg a %s telefonszámtól induló összefüggéseket egy "
                           "kapcsolati ábrán." % phone,
                           ["no view of an answer's subgraph"]))
    out.append(_capability("I-2.5-excel", "identifier", "2.5",
                           "Exportáld Excel-táblázatba a %s telefonszám kapcsán feltárt "
                           "személyeket és azonosítóikat." % phone,
                           ["no XLSX export of an answer"]))
    return out


def build_eval(persons: int = 120, seed: int = 26) -> dict:
    w = world_mod.build(persons, seed)
    docs = Renderer(w, seed).render_all()
    f = Facts(w, docs)
    questions = agent_person(f) + agent_identifier(f)
    return {"seed": seed, "persons": persons,
            "names": sorted({p.name for p in w.persons.values()}),
            "phones": sorted({ph for p in w.persons.values() for ph in p.phones}),
            "questions": questions}


# -- scoring --------------------------------------------------------------------------

def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (text or "").casefold())
                   if unicodedata.category(c) != "Mn")


def _words(text: str) -> str:
    return " ".join(re.findall(r"\w+", fold(text)))


_STREET = {"utca": "u", "ut": "u", "u": "u", "ter": "ter", "krt": "korut", "korut": "korut"}


def _address_core(text: str) -> str:
    """Street, kind and number: "Szeged, Hunyadi utca 78." -> "hunyadi u 78"."""
    words = _words(text).split()
    if words and len(words) > 3:
        words = words[1:]  # the town
    return " ".join(_STREET.get(w, w) for w in words)


def _phone_pattern(value: str) -> str:
    """The last nine digits, any of them separated by a space, dash or slash,
    after an optional +36 / 06."""
    tail = re.sub(r"\D", "", value)[-9:]
    return r"(?<!\d)(?:(?:\+?36|06)[ \-/]?)?%s(?![ \-/]?\d)" % r"[ \-/]?".join(tail)


def found(item: dict, answer: str) -> bool:
    if item.get("alt"):
        return any(found({"kind": item["kind"], "value": v}, answer)
                   for v in [item["value"], *item["alt"]])
    kind, value = item["kind"], item["value"]
    if kind == "person":
        words = _words(answer)
        parts = _words(value).split()
        return any(re.search(r"\b%s" % re.escape(" ".join(order)), words)
                   for order in (parts, parts[::-1]))
    if kind == "registry":
        digits = re.sub(r"\D", "", value)
        return re.search(r"ny\W*%s(?!\d)" % digits, fold(answer)) is not None
    if kind == "phone":
        return re.search(_phone_pattern(value), answer or "") is not None
    if kind == "address":
        answer_core = " ".join(_STREET.get(w, w) for w in _words(answer).split())
        return _address_core(value) in answer_core
    return _words(value) in _words(answer)


def score(question: dict, answer: str, names=(), phones=()) -> dict:
    """Recall of the gold items, plus the corpus people the answer names besides
    them (`extra`: noise or a wrong chain, never counted as a miss)."""
    must = question.get("must") or []
    hits = [it for it in must if found(it, answer)]
    expected = {fold(it["value"]) for it in must if it["kind"] == "person"}
    expected |= {fold(n) for n in question.get("acceptable") or []}
    seed = question.get("seed") or {}
    if seed.get("name"):
        expected.add(fold(seed["name"]))
    extra = sorted(n for n in names if fold(n) not in expected
                   and found({"kind": "person", "value": n}, answer))
    out = {"id": question["id"], "must": len(must), "hits": len(hits),
           "recall": round(len(hits) / len(must), 3) if must else None,
           "missed": [it for it in must if it not in hits], "extra": extra}
    if question.get("no_data"):
        named = extra or [p for p in phones if found({"kind": "phone", "value": p}, answer)]
        says_none = any(w in _words(answer) for w in NO_DATA_WORDS)
        out["no_data_ok"] = not named and says_none
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(HERE, "out", "bv_eval.json"))
    ap.add_argument("--persons", type=int, default=120)
    ap.add_argument("--seed", type=int, default=26)
    args = ap.parse_args()
    data = build_eval(args.persons, args.seed)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with io.open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    for q in data["questions"]:
        print("%-34s %-4s %-7s must %2d  %s" % (q["id"], q["bv_step"], q["coverage"],
                                                len(q["must"]), q["question"][:70]))
    print("%d questions -> %s" % (len(data["questions"]), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
