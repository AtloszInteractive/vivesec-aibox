"""Hidden ground-truth world for the relationship-analysis test corpus.

Every document is rendered from this model, so the true entity graph is known
exactly and extraction, entity resolution and graph queries can be scored.
All people, places, organisations and identifiers are fictional.
"""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field

FAMILY = [
    "Kovács", "Szabó", "Tóth", "Nagy", "Horváth", "Varga", "Kiss", "Molnár", "Németh",
    "Farkas", "Balogh", "Papp", "Takács", "Juhász", "Lakatos", "Mészáros", "Oláh", "Simon",
    "Rácz", "Fekete", "Szilágyi", "Török", "Fehér", "Balázs", "Gál", "Szűcs", "Kocsis",
    "Pintér", "Fodor", "Szalai", "Sipos", "Magyar", "Lukács", "Gulyás", "Bíró", "Király",
    "Katona", "Jakab", "Bogdán", "Boros", "Fazekas", "Kelemen", "Antal", "Orbán", "Váradi",
    "Somogyi", "Vincze", "Hegedűs", "Deák", "Bodnár", "Csonka", "Major", "Pásztor", "Kardos",
]
MALE = [
    "László", "István", "József", "János", "Zoltán", "Sándor", "Gábor", "Ferenc", "Attila",
    "Péter", "Tamás", "Zsolt", "Tibor", "András", "Csaba", "Imre", "Lajos", "György",
    "Balázs", "Róbert", "Norbert", "Krisztián", "Gyula", "Dávid", "Ádám", "Roland",
    "Richárd", "Szabolcs", "Bence", "Máté",
]
FEMALE = [
    "Mária", "Erzsébet", "Katalin", "Éva", "Ilona", "Anna", "Zsuzsanna", "Margit", "Judit",
    "Ágnes", "Andrea", "Erika", "Krisztina", "Edit", "Gabriella", "Szilvia", "Anita",
    "Mónika", "Tímea", "Nikolett", "Viktória", "Renáta", "Eszter", "Dóra", "Petra",
]
TOWNS = [
    "Miskolc", "Debrecen", "Szeged", "Pécs", "Győr", "Nyíregyháza", "Kecskemét",
    "Székesfehérvár", "Szombathely", "Szolnok", "Tatabánya", "Kaposvár", "Érd", "Veszprém",
    "Békéscsaba", "Zalaegerszeg", "Sopron", "Eger", "Nagykanizsa", "Dunaújváros", "Budapest",
]
STREETS = [
    "Petőfi Sándor utca", "Kossuth Lajos utca", "Rákóczi út", "Ady Endre utca",
    "Dózsa György út", "Arany János utca", "Széchenyi tér", "Jókai Mór utca",
    "Táncsics Mihály utca", "Béke utca", "Fő utca", "Vasút utca", "Hunyadi utca",
    "Bem József utca", "Damjanich utca", "Wesselényi utca",
]
INSTITUTIONS = [
    "Kőhegyi Büntetés-végrehajtási Intézet",
    "Alsóréti Fegyház és Börtön",
    "Tiszahalmi Büntetés-végrehajtási Intézet",
    "Pannonvölgyi Országos Büntetés-végrehajtási Intézet",
]
ORG_NAMES = [
    "Délibáb Trans Kft.", "Nyírfa Kereskedelmi Bt.", "Holdfény Security Kft.",
    "Kárpát Logisztika Zrt.", "Sárga Kavics Egyesület", "Ezüstpart Szolgáltató Kft.",
    "Vadgesztenye Autókereskedés Kft.",
]
RELATIVE_KINDS = ["testvére", "élettársa", "anyja", "unokatestvére", "nagybátyja", "házastársa"]
ORG_WORDS = ["Tisza", "Mecsek", "Bakony", "Hortobágy", "Zemplén", "Rába", "Sárrét", "Duna",
             "Örség", "Cserhát", "Vasfüggöny", "Kunfalu"]
ORG_FORMS = ["Trans Kft.", "Kereskedő Bt.", "Logisztika Zrt.", "Szolgáltató Kft.",
             "Építőipari Kft.", "Egyesület", "Autószerviz Kft."]

START = dt.date(2025, 1, 1)
END = dt.date(2026, 9, 30)


@dataclass
class Person:
    id: str
    family: str
    given: str
    sex: str
    dob: dt.date
    birthplace: str
    mother: str
    id_number: str
    address: str
    role: str  # inmate | visitor | other
    group: int
    phones: list[str] = field(default_factory=list)
    plates: list[str] = field(default_factory=list)
    homonym_of: str | None = None

    @property
    def name(self) -> str:
        return "%s %s" % (self.family, self.given)


@dataclass
class Place:
    id: str
    kind: str  # institution | cell | address
    label: str
    parent: str | None = None


@dataclass
class Org:
    id: str
    name: str
    address: str


@dataclass
class Relation:
    id: str
    type: str
    a: str
    b: str
    start: dt.date | None = None
    end: dt.date | None = None
    attrs: dict = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)


@dataclass
class Incident:
    id: str
    date: dt.date
    institution: str
    cell: str
    persons: list[str]
    kind: str
    phone: str | None = None


@dataclass
class World:
    seed: int
    persons: dict[str, Person]
    places: dict[str, Place]
    orgs: dict[str, Org]
    identifiers: dict[str, str]  # value -> phone | plate
    relations: list[Relation]
    incidents: list[Incident]
    groups: dict[int, list[str]]

    def rel(self, type_: str) -> list[Relation]:
        return [r for r in self.relations if r.type == type_]


def _date_between(rng: random.Random, a: dt.date, b: dt.date) -> dt.date:
    return a + dt.timedelta(days=rng.randint(0, max(0, (b - a).days)))


def _phone(rng: random.Random, used: set[str]) -> str:
    while True:
        v = "+36 %s %03d %04d" % (rng.choice(["20", "30", "70"]), rng.randint(100, 999),
                                  rng.randint(0, 9999))
        if v not in used:
            used.add(v)
            return v


def _plate(rng: random.Random, used: set[str]) -> str:
    letters = "ABCDEFGHIJKLMNOPRSTUVZ"
    while True:
        if rng.random() < 0.5:
            v = "%s-%03d" % ("".join(rng.choice(letters) for _ in range(3)), rng.randint(1, 999))
        else:
            v = "%s %s-%03d" % ("".join(rng.choice(letters) for _ in range(2)),
                                "".join(rng.choice(letters) for _ in range(2)),
                                rng.randint(1, 999))
        if v not in used:
            used.add(v)
            return v


def _id_number(rng: random.Random, used: set[str]) -> str:
    while True:
        v = "%06d%s" % (rng.randint(0, 999999), "".join(rng.choice("ABCDEFGHJKLMNPRSTUVZ")
                                                       for _ in range(2)))
        if v not in used:
            used.add(v)
            return v


def _address(rng: random.Random) -> str:
    return "%s, %s %d." % (rng.choice(TOWNS), rng.choice(STREETS), rng.randint(1, 120))


def build(n_persons: int = 120, seed: int = 26) -> World:
    rng = random.Random(seed)
    used_ids: set[str] = set()
    used_values: set[str] = set()
    persons: dict[str, Person] = {}
    places: dict[str, Place] = {}
    relations: list[Relation] = []
    identifiers: dict[str, str] = {}
    counter = {"r": 0}

    def add_rel(type_, a, b, start=None, end=None, **attrs) -> Relation:
        counter["r"] += 1
        r = Relation("R%05d" % counter["r"], type_, a, b, start, end, dict(attrs))
        relations.append(r)
        return r

    n_cells = max(6, n_persons // 40)
    for i, name in enumerate(INSTITUTIONS):
        inst = Place("I%d" % (i + 1), "institution", name)
        places[inst.id] = inst
        for c in range(n_cells):
            cell = Place("%s-C%d" % (inst.id, c + 1), "cell",
                         "%d%02d. számú zárka" % (c // 30 + 1, c % 30 + 1), inst.id)
            places[cell.id] = cell

    n_groups = max(3, n_persons // 15)
    groups: dict[int, list[str]] = {g: [] for g in range(n_groups)}
    for i in range(n_persons):
        sex = "M" if rng.random() < 0.75 else "F"
        role = "inmate" if i % 20 < 9 else ("visitor" if i % 20 < 16 else "other")
        if role == "inmate":
            sex = "M"
        p = Person(
            id="P%04d" % (i + 1), family=rng.choice(FAMILY),
            given=rng.choice(MALE if sex == "M" else FEMALE), sex=sex,
            dob=_date_between(rng, dt.date(1958, 1, 1), dt.date(2004, 12, 31)),
            birthplace=rng.choice(TOWNS),
            mother="%s %s" % (rng.choice(FAMILY), rng.choice(FEMALE)),
            id_number=_id_number(rng, used_ids), address=_address(rng), role=role,
            group=i % n_groups,
        )
        persons[p.id] = p
        groups[p.group].append(p.id)

    # Name twins: same name, different person -> must never be merged.
    originals = list(persons.values())
    for k in range(max(2, n_persons // 25)):
        src = originals[rng.randrange(len(originals))]
        twin_id = "P%04d" % (len(persons) + 1)
        twin = Person(
            id=twin_id, family=src.family, given=src.given, sex=src.sex,
            dob=_date_between(rng, dt.date(1958, 1, 1), dt.date(2004, 12, 31)),
            birthplace=rng.choice(TOWNS),
            mother="%s %s" % (rng.choice(FAMILY), rng.choice(FEMALE)),
            id_number=_id_number(rng, used_ids), address=_address(rng),
            role="inmate" if k % 2 == 0 else "visitor",
            group=(src.group + 1 + k) % n_groups, homonym_of=src.id,
        )
        persons[twin_id] = twin
        groups[twin.group].append(twin_id)

    for p in persons.values():
        for _ in range(2 if rng.random() < 0.25 else 1):
            ph = _phone(rng, used_values)
            p.phones.append(ph)
            identifiers[ph] = "phone"
            add_rel("USES_PHONE", p.id, ph)
        if p.role != "inmate" and rng.random() < 0.3:
            pl = _plate(rng, used_values)
            p.plates.append(pl)
            identifiers[pl] = "plate"
            add_rel("USES_VEHICLE", p.id, pl)

    orgs: dict[str, Org] = {}
    org_names = list(ORG_NAMES)
    while len(org_names) < max(len(ORG_NAMES), n_groups // 2):
        org_names.append("%s %s" % (rng.choice(ORG_WORDS), rng.choice(ORG_FORMS)))
        org_names = list(dict.fromkeys(org_names))
    for i, name in enumerate(org_names):
        org = Org("O%d" % (i + 1), name, _address(rng))
        orgs[org.id] = org

    for g, members in groups.items():
        outside = [m for m in members if persons[m].role != "inmate"]
        inside = [m for m in members if persons[m].role == "inmate"]
        # A shared phone and a shared vehicle per group: the key identifier signals.
        if len(outside) >= 2:
            a, b = rng.sample(outside, 2)
            ph = persons[a].phones[0]
            persons[b].phones.append(ph)
            add_rel("USES_PHONE", b, ph)
        if len(outside) >= 2:
            org = orgs["O%d" % (g % len(orgs) + 1)]
            pl = _plate(rng, used_values)
            identifiers[pl] = "plate"
            add_rel("OWNS_VEHICLE", org.id, pl)
            for m in rng.sample(outside, 2):
                persons[m].plates.append(pl)
                add_rel("USES_VEHICLE", m, pl)
                add_rel("WORKS_FOR", m, org.id)
        # Relatives share an address now and then.
        for inmate in inside:
            for rel_id in rng.sample(outside, min(len(outside), rng.randint(0, 2))):
                kind = rng.choice(RELATIVE_KINDS)
                add_rel("RELATIVE_OF", inmate, rel_id, kind=kind)
                if rng.random() < 0.35:
                    persons[rel_id].address = persons[inmate].address
    for p in persons.values():
        add_rel("RESIDES_AT", p.id, p.address)

    # Placements, transfers, cell sharing.
    inmates = [p for p in persons.values() if p.role == "inmate"]
    group_inst = {g: "I%d" % (g % len(INSTITUTIONS) + 1) for g in groups}
    for p in inmates:
        inst = group_inst[p.group] if rng.random() < 0.7 else "I%d" % rng.randint(1, len(INSTITUTIONS))
        cell = "%s-C%d" % (inst, (p.group % n_cells) + 1 if rng.random() < 0.6
                           else rng.randint(1, n_cells))
        start = _date_between(rng, START, END - dt.timedelta(days=200))
        if rng.random() < 0.25:
            moved = _date_between(rng, start + dt.timedelta(days=60), END - dt.timedelta(days=30))
            add_rel("PLACED_IN", p.id, cell, start, moved, institution=inst)
            new_inst = "I%d" % ((int(inst[1:]) % len(INSTITUTIONS)) + 1)
            new_cell = "%s-C%d" % (new_inst, rng.randint(1, n_cells))
            add_rel("TRANSFERRED", p.id, new_inst, moved, None, from_institution=inst)
            add_rel("PLACED_IN", p.id, new_cell, moved, None, institution=new_inst)
        else:
            add_rel("PLACED_IN", p.id, cell, start, None, institution=inst)

    spans_of: dict[str, list[Relation]] = {}
    for r in relations:
        if r.type == "PLACED_IN":
            spans_of.setdefault(r.a, []).append(r)

    def placement_on(pid: str, day: dt.date) -> Relation | None:
        for r in spans_of.get(pid, []):
            if r.start <= day and (r.end is None or day < r.end):
                return r
        return None

    def visitable_day(pid: str) -> dt.date | None:
        r = rng.choice(spans_of[pid])
        end = r.end or END
        if (end - r.start).days < 5:
            return None
        return _date_between(rng, r.start + dt.timedelta(days=2), end - dt.timedelta(days=1))

    # Visits: relatives often, group members sometimes, a few bridges across groups.
    for r in [x for x in relations if x.type == "RELATIVE_OF"]:
        for _ in range(rng.randint(1, 4)):
            day = visitable_day(r.a)
            if day:
                inst = placement_on(r.a, day).attrs["institution"]
                add_rel("VISITED", r.b, r.a, day, day, institution=inst, relation=r.attrs["kind"])
    all_outside = [p.id for p in persons.values() if p.role != "inmate"]
    for p in inmates:
        same = [m for m in groups[p.group] if persons[m].role != "inmate"]
        pool = same if rng.random() < 0.85 else all_outside
        if not pool:
            continue
        for _ in range(rng.randint(0, 2)):
            day = visitable_day(p.id)
            if day:
                inst = placement_on(p.id, day).attrs["institution"]
                add_rel("VISITED", rng.choice(pool), p.id, day, day, institution=inst,
                        relation="ismerőse")

    # Calls from inmates to outside phones.
    for p in inmates:
        same = [m for m in groups[p.group] if persons[m].role != "inmate"]
        pool = same if same else all_outside
        for _ in range(rng.randint(0, 6)):
            target = rng.choice(pool if rng.random() < 0.9 else all_outside)
            day = visitable_day(p.id)
            if day:
                add_rel("CALLED", p.id, rng.choice(persons[target].phones), day, day,
                        minutes=rng.randint(1, 15))

    # Meetings observed outside, with occasional cross-group bridges.
    for g, members in groups.items():
        outside = [m for m in members if persons[m].role != "inmate"]
        for _ in range(rng.randint(2, 4)):
            if len(outside) < 2:
                break
            who = rng.sample(outside, min(len(outside), rng.randint(2, 3)))
            if rng.random() < 0.3:
                other = rng.choice(all_outside)
                if other not in who:
                    who.append(other)
            day = _date_between(rng, START, END)
            place = rng.choice([persons[who[0]].address, _address(rng),
                                orgs["O%d" % (g % len(orgs) + 1)].address])
            meeting = "M%04d" % (counter["r"] + 1)
            for i, a in enumerate(who):
                add_rel("AT_PLACE", a, place, day, day, meeting=meeting)
                for b in who[i + 1:]:
                    add_rel("MET", a, b, day, day, place=place, meeting=meeting)

    # Incidents inside institutions; a seized phone links a cell to the outside.
    incidents: list[Incident] = []
    for inst_id in [p.id for p in places.values() if p.kind == "institution"]:
        for _ in range(rng.randint(2, 4) * max(1, n_persons // 120)):
            day = _date_between(rng, START + dt.timedelta(days=30), END)
            present = [p.id for p in inmates
                       if (pl := placement_on(p.id, day)) and pl.attrs["institution"] == inst_id]
            if len(present) < 2:
                continue
            involved = rng.sample(present, min(len(present), rng.randint(2, 3)))
            cell = placement_on(involved[0], day).b
            inc = Incident("E%03d" % (len(incidents) + 1), day, inst_id, cell, involved,
                           rng.choice(["tiltott tárgy birtoklása", "verekedés",
                                       "rendbontás", "szabálysértő kapcsolattartás"]))
            if inc.kind == "tiltott tárgy birtoklása":
                g = persons[involved[0]].group
                outside = [m for m in groups[g] if persons[m].role != "inmate"] or all_outside
                inc.phone = rng.choice(persons[rng.choice(outside)].phones)
                add_rel("USES_PHONE", involved[0], inc.phone, day, day, seized=True)
            incidents.append(inc)
            for pid in involved:
                add_rel("INVOLVED_IN", pid, inc.id, day, day, kind=inc.kind)

    return World(seed, persons, places, orgs, identifiers, relations, incidents, groups)


def placements_overlap(a: Relation, b: Relation) -> tuple[dt.date, dt.date] | None:
    if a.b != b.b:
        return None
    start = max(a.start, b.start)
    end = min(a.end or END, b.end or END)
    return (start, end) if start < end else None
