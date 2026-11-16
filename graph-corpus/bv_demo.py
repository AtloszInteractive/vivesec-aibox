"""BV demo dataset: the documents and the structured exports of one fictional world.

    python graph-corpus/bv_demo.py --out <dir> [--preset small|medium|large] [--clean]
    python graph-corpus/bv_demo.py --out <dir> --bundle bv-demo.tgz      # also pack it

The documents come from generate.py (world.py + render.py) with the bv profile's
three institutions; the periodic exports of the profile's structured sources
(FŐNIX base data, placements, approved contacts, intelligence log, ODIN) are
written from the same world, so a registry number, an institution or an incident
in a document is the same one in the records. A background population that only
the databases know (as in a real prison register, far more people than the
documents mention) brings the records to the preset's volume.

Output:
    <out>/rooms/<room>/...        the documents by room, for the seed's upload
    <out>/exports/<source>.csv    one export per structured source (AS_OF)
    <out>/manifest.jsonl, <out>/answer_key.json   as generate.py (generator drives)
    <out>/demo.json               rooms, counts, institutions and people to ask about

Everyone and everything is invented. scripts/jetson/bv-demo-seed.py loads it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import random
import shutil
import sys
import tarfile
import unicodedata
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.join(os.path.dirname(HERE), "demo-corpus"))

import generate  # noqa: E402
import structured_samples as samples  # noqa: E402
import world as world_mod  # noqa: E402

AS_OF = "2026-11-01"
# The bv profile's institution code list (graph_service/profiles/bv.json).
INSTITUTIONS = {
    "01": "Kőhegyi Országos Büntetés-végrehajtási Intézet",
    "02": "Nyírvölgyi Fegyház és Börtön",
    "03": "Szőlőhegyi Büntetés-végrehajtási Intézet",
}
# Generator drive -> room the seed creates.
ROOMS = {"nyilvantartas": "Nyilvántartás", "kommunikacio": "Kommunikáció",
         "jelentesek": "Jelentések", "egyeb": "Egyéb"}
EXPORT_ROOM, EXPORT_FOLDER = "Nyilvántartás", "Adatbázis-exportok"
PRESETS = {
    # world persons (-> documents), background inmates, log entries and extra ODIN
    # entries per inmate of the whole register
    "small": {"persons": 250, "background": 1300, "intel": 0.4, "odin": 0.23},
    "medium": {"persons": 750, "background": 4600, "intel": 0.4, "odin": 0.23},
    "large": {"persons": 1500, "background": 14300, "intel": 0.4, "odin": 0.23},
}

EXTRA_FAMILY = [
    "Ács", "Bakos", "Bálint", "Barta", "Bencze", "Berki", "Bozsó", "Csizmadia", "Dobos",
    "Erdős", "Fábián", "Faragó", "Fülöp", "Galambos", "Hajdu", "Halász", "Hegyi", "Hollósi",
    "Illés", "Jónás", "Kántor", "Kerekes", "Kis", "Kovács", "Lengyel", "Lőrinc", "Márton",
    "Mohácsi", "Nemes", "Novák", "Pap", "Péter", "Pölöskei", "Rajki", "Sándor", "Sárközi",
    "Sebestyén", "Soós", "Szekeres", "Szénási", "Szőke", "Tamás", "Tóth", "Ujvári", "Vadász",
    "Vass", "Veres", "Vida", "Vörös", "Zsigmond", "Zentai", "Garai", "Dudás", "Kozma",
]
EXTRA_MALE = ["Gergely", "Kristóf", "Levente", "Márk", "Milán", "Olivér", "Patrik", "Viktor",
              "Barnabás", "Dániel", "Erik", "Ferenc", "Henrik", "Kálmán", "Miklós", "Ottó",
              "Pál", "Rudolf", "Vilmos", "Zsombor"]
EXTRA_FEMALE = ["Boglárka", "Csilla", "Emese", "Fanni", "Gizella", "Hajnalka", "Ibolya",
                "Jolán", "Klára", "Lilla", "Melinda", "Noémi", "Orsolya", "Piroska", "Rozália",
                "Tünde", "Valéria", "Zita", "Rita", "Ildikó"]
RELATIVE_CODE = {"testvére": "TESTV", "élettársa": "ELT", "anyja": "ANYA", "unokatestvére": "ROKON",
                 "nagybátyja": "ROKON", "házastársa": "HAZT"}
INCIDENT_CODE = {"tiltott tárgy birtoklása": "TILT", "verekedés": "TAM", "rendbontás": "RE",
                 "szabálysértő kapcsolattartás": "EGY"}
UNITS = ["I. részleg", "II. részleg", "III. részleg"]
FACILITIES = ["A objektum", "B objektum", "C objektum"]
INTEL_TEXT = {
    "KAB": ["A következő látogatáskor kábítószert akarnak bejuttatni a {cell}. számú zárka lakóinak.",
            "Csomagban, cipőtalpba rejtve szintetikus kábítószert várnak.",
            "A sétaudvaron kábítószert adnak tovább, a fizetés a kintiek számlájára megy."],
    "MOB": ["A {cell}. számú zárkában mobiltelefont rejtenek, töltőt a műhelyből szereztek.",
            "Mobiltelefonon tartja a kapcsolatot kinti társaival, esténként telefonál.",
            "A mosodában SIM-kártyákat cserélnek gazdát."],
    "SZOK": ["Szökési kísérletet terveznek a sétaidőben.",
             "Előállítás közben akar megszökni, kint autó várná.",
             "A kerítésnél gyenge pontot keresnek, a műszakváltást figyelik."],
    "ERO": ["Zárkatársát fenyegeti, pénzt követel tőle.",
            "Bosszút terveznek egy másik részlegen elhelyezett fogvatartott ellen.",
            "Rendszeresen bántalmazza a {cell}. számú zárka újonnan érkezett lakóját."],
    "KORR": ["Pénzért csomagot juttatnak be neki.",
             "Egy kinti ismerősén keresztül meg akar vesztegetni egy munkatársat.",
             "Kedvezményekért cserébe szívességeket ígér."],
    "EGY": ["Éhségsztrájkot szerveznek a részlegen.",
            "Hamis betegségre hivatkozva akar kórházba kerülni.",
            "Más fogvatartottak nevében ír kérelmeket ellenszolgáltatásért."],
}
INTEL_WEIGHTS = {"KAB": 0.24, "MOB": 0.26, "SZOK": 0.08, "ERO": 0.2, "KORR": 0.1, "EGY": 0.12}
SOURCE_WEIGHTS = {"FOGV": 0.5, "KT": 0.2, "TARS": 0.1, "ALL": 0.2}
ODIN_TEXT = {
    "TILT": "A zárkaellenőrzés során a {cell}. számú zárkában tiltott tárgyat ({thing}) találtak.",
    "TAM": "A {cell}. számú zárkában verekedés történt, az egyik résztvevő orvosi ellátásra szorult.",
    "RE": "A részlegen rendbontás történt, a fogvatartottak megtagadták a zárkába vonulást.",
    "EGY": "Szabálysértő kapcsolattartás: engedély nélküli levél- és csomagforgalom.",
    "KAB": "A látogatás után végzett ellenőrzéskor kábítószergyanús anyagot találtak.",
    "SZOK": "Szökési előkészület gyanúja: a sétaudvar kerítésénél vágószerszámot találtak.",
    "ONS": "Önsértés történt a {cell}. számú zárkában, az érintett orvosi ellátást kapott.",
    "EGU": "Egészségügyi esemény: az érintettet a rendelőbe kellett szállítani.",
}
ODIN_WEIGHTS = {"TILT": 0.3, "TAM": 0.2, "RE": 0.12, "EGY": 0.08, "KAB": 0.12, "SZOK": 0.04,
                "ONS": 0.06, "EGU": 0.08}
THINGS = ["mobiltelefon", "SIM-kártya", "házilag készített szúróeszköz", "töltő", "pénz"]
START, END = dt.date(2025, 1, 1), dt.date(2026, 9, 30)


def iso(d: dt.date | None) -> str:
    return d.isoformat() if d else ""


def ascii_of(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn").lower()


def weighted(rng: random.Random, weights: dict[str, float]) -> str:
    r, acc = rng.random() * sum(weights.values()), 0.0
    for key, w in weights.items():
        acc += w
        if r < acc:
            return key
    return key


def phone_variant(rng: random.Random, phone: str) -> str:
    """The register's own spelling of a phone: +36, 06 or no spaces."""
    r = rng.random()
    if r < 0.2:
        return "06" + phone[3:]
    if r < 0.3:
        return phone.replace(" ", "")
    return phone


def cell_parts(index: int) -> tuple[str, str, str]:
    """(facility, unit, cell number) of the cell with this 0-based index; the
    number is the one world.py writes into the documents ("107. számú zárka")."""
    floor, room = index // 30, index % 30
    return FACILITIES[floor % len(FACILITIES)], UNITS[room // 10], "%d%02d" % (floor + 1, room + 1)


class Register:
    """The rows of every export, built from the world and the background."""

    def __init__(self, w: world_mod.World, preset: dict, seed: int):
        self.w = w
        self.rng = random.Random(seed * 31 + 7)
        self.preset = preset
        self.rows = {source: [] for source in samples.exports()}
        self.counter = Counter()
        self.used_reg = {p.registry_number for p in w.persons.values() if p.registry_number}
        self.used_phone = set(w.identifiers)
        self.n_cells = max(6, len(w.persons) // 40)
        # inmate key -> dict(name, reg, dob, birthplace, mother, spans=[(code, cell, start, end)])
        self.inmates: dict[str, dict] = {}
        self.contacts_of: dict[str, list[dict]] = {}
        self.staff = [self.person_name("M" if self.rng.random() < 0.7 else "F")
                      for _ in range(40)]
        self.partners = ["Dr. " + self.person_name("M" if self.rng.random() < 0.6 else "F")
                         for _ in range(12)]

    # -- helpers -----------------------------------------------------------

    def rid(self, prefix: str) -> str:
        self.counter[prefix] += 1
        return "%s-%06d" % (prefix, self.counter[prefix])

    def person_name(self, sex: str) -> str:
        family = self.rng.choice(world_mod.FAMILY + EXTRA_FAMILY)
        given = self.rng.choice((world_mod.MALE + EXTRA_MALE) if sex == "M"
                                else (world_mod.FEMALE + EXTRA_FEMALE))
        return "%s %s" % (family, given)

    def registry_number(self) -> str:
        while True:
            reg = "NY-%06d" % self.rng.randint(100000, 999999)
            if reg not in self.used_reg:
                self.used_reg.add(reg)
                return reg

    def phone(self) -> str:
        while True:
            v = "+36 %s %03d %04d" % (self.rng.choice(["20", "30", "70"]),
                                      self.rng.randint(100, 999), self.rng.randint(0, 9999))
            if v not in self.used_phone:
                self.used_phone.add(v)
                return v

    def date(self, a: dt.date, b: dt.date) -> dt.date:
        return a + dt.timedelta(days=self.rng.randint(0, max(0, (b - a).days)))

    def email(self, name: str) -> str:
        family, _, given = name.partition(" ")
        return "%s.%s%d@example.hu" % (ascii_of(given).replace(" ", ""),
                                        ascii_of(family).replace(" ", ""),
                                        self.rng.randint(1, 99))

    def code_of(self, place_id: str) -> str:
        return "%02d" % int(place_id.split("-")[0][1:])

    def present(self, key: str) -> tuple[str, int, dt.date, dt.date] | None:
        """A random (institution code, cell index, day) the inmate was held there."""
        spans = self.inmates[key]["spans"]
        code, cell, start, end = self.rng.choice(spans)
        end = end or END
        if (end - start).days < 3:
            return None
        return code, cell, self.date(start + dt.timedelta(days=1), end - dt.timedelta(days=1))

    # -- the world ---------------------------------------------------------

    def from_world(self) -> None:
        w = self.w
        placed: dict[str, list] = {}
        for r in w.relations:
            if r.type == "PLACED_IN":
                placed.setdefault(r.a, []).append(r)
        for p in sorted(w.persons.values(), key=lambda x: x.id):
            if p.role != "inmate":
                continue
            spans = []
            for r in sorted(placed.get(p.id, []), key=lambda x: x.start):
                spans.append((self.code_of(r.b), int(r.b.rsplit("C", 1)[1]) - 1, r.start, r.end))
            self.inmates[p.id] = {"name": p.name, "reg": p.registry_number, "dob": p.dob,
                                  "birthplace": p.birthplace, "mother": p.mother, "spans": spans}
        visits: dict[tuple[str, str], list] = {}
        for r in w.relations:
            if r.type == "VISITED":
                visits.setdefault((r.a, r.b), []).append(r.start)
        seen = set()
        for r in w.rel("RELATIVE_OF"):
            other = w.persons[r.b]
            first = min(visits.get((r.b, r.a)) or [self.inmates[r.a]["spans"][0][2]])
            self.add_contact(r.a, {"name": other.name, "birthplace": other.birthplace,
                                   "dob": other.dob, "mother": other.mother,
                                   "phone": other.phones[0], "address": other.address},
                             RELATIVE_CODE.get(r.attrs.get("kind"), "ROKON"),
                             first - dt.timedelta(days=self.rng.randint(10, 60)))
            seen.add((r.b, r.a))
        for (visitor, inmate), days in sorted(visits.items()):
            if (visitor, inmate) in seen or inmate not in self.inmates:
                continue
            seen.add((visitor, inmate))
            other = w.persons[visitor]
            self.add_contact(inmate, {"name": other.name, "birthplace": other.birthplace,
                                      "dob": other.dob, "mother": other.mother,
                                      "phone": other.phones[0], "address": other.address},
                             "ISM", min(days) - dt.timedelta(days=self.rng.randint(10, 60)))
        for inc in w.incidents:
            code = self.code_of(inc.institution)
            category = INCIDENT_CODE.get(inc.kind, "EGY")
            cell = cell_parts(int(inc.cell.rsplit("C", 1)[1]) - 1)[2]
            self.add_odin(inc.date, code, category, list(inc.persons), cell)

    # -- the background ----------------------------------------------------

    def background(self, count: int) -> None:
        codes = sorted(INSTITUTIONS)
        brothers = []
        for i in range(count):
            key = "B%06d" % (i + 1)
            sibling = brothers.pop() if brothers and self.rng.random() < 0.5 else None
            family = sibling["name"].split(" ")[0] if sibling else None
            name = self.person_name("M")
            if family:
                name = "%s %s" % (family, name.split(" ", 1)[1])
            dob = self.date(dt.date(1960, 1, 1), dt.date(2005, 12, 31))
            start = self.date(START - dt.timedelta(days=1500), END - dt.timedelta(days=120))
            code = self.rng.choice(codes)
            spans = []
            cell = self.rng.randrange(self.n_cells)
            moves = self.rng.choice([0, 0, 0, 1, 1, 2])
            for _ in range(moves):
                moved = self.date(start + dt.timedelta(days=40), END - dt.timedelta(days=30))
                if moved <= start + dt.timedelta(days=40):
                    break
                spans.append((code, cell, start, moved))
                if self.rng.random() < 0.5:
                    code = self.rng.choice([c for c in codes if c != code])
                cell, start = self.rng.randrange(self.n_cells), moved
            released = self.rng.random() < 0.12
            spans.append((code, cell, start,
                          self.date(start + dt.timedelta(days=30), END) if released else None))
            mother = sibling["mother"] if sibling else self.person_name("F")
            self.inmates[key] = {
                "name": name, "reg": self.registry_number(), "dob": dob,
                "birthplace": sibling["birthplace"] if sibling else self.rng.choice(world_mod.TOWNS),
                "mother": mother["name"] if isinstance(mother, dict) else mother, "spans": spans}
            if sibling:
                # Brothers: their mother is the approved contact of both, with the
                # same natural identifiers in two records (one person in the graph).
                self.add_contact(key, sibling["mother_contact"], "ANYA",
                                 self.date(START, END - dt.timedelta(days=60)))
                self.add_contact(sibling["key"], sibling["mother_contact"], "ANYA",
                                 self.date(START, END - dt.timedelta(days=60)))
            elif self.rng.random() < 0.06:
                town = self.rng.choice(world_mod.TOWNS)
                mother_contact = {"name": self.inmates[key]["mother"], "birthplace": town,
                                  "dob": self.date(dt.date(1940, 1, 1), dt.date(1975, 12, 31)),
                                  "mother": self.person_name("F"), "phone": self.phone(),
                                  "address": "%s, %s %d." % (town, self.rng.choice(world_mod.STREETS),
                                                            self.rng.randint(1, 120))}
                brothers.append({"key": key, "name": name, "mother": mother_contact,
                                 "birthplace": self.inmates[key]["birthplace"],
                                 "mother_contact": mother_contact})
            for _ in range(int(weighted(self.rng, {"1": .15, "2": .3, "3": .3, "4": .15,
                                                    "5": .1}))):
                self.add_contact(key, self.new_contact(), weighted(self.rng, {
                    "ANYA": .08, "APA": .05, "TESTV": .15, "HAZT": .1, "ELT": .17, "GYERM": .1,
                    "ROKON": .1, "ISM": .2, "VED": .05}),
                    self.date(spans[0][2], END - dt.timedelta(days=30)))
        # A contact who is the approved contact of more inmates (the bridges).
        keys = sorted(self.contacts_of)
        for _ in range(count // 60):
            a, b = self.rng.sample(keys, 2)
            shared = self.rng.choice(self.contacts_of[a])
            self.add_contact(b, shared, "ISM", self.date(START, END - dt.timedelta(days=30)))
        # Different people with one phone (a review pair, a "shared contact" step).
        for _ in range(count // 120):
            a, b = self.rng.sample(keys, 2)
            other = dict(self.new_contact(), phone=self.rng.choice(self.contacts_of[a])["phone"])
            if self.rng.random() < 0.5:
                other.update(birthplace="", dob=None, mother="")
            self.add_contact(b, other, "ISM", self.date(START, END - dt.timedelta(days=30)))

    def new_contact(self) -> dict:
        sex = "F" if self.rng.random() < 0.6 else "M"
        town = self.rng.choice(world_mod.TOWNS)
        return {"name": self.person_name(sex), "birthplace": town,
                "dob": self.date(dt.date(1945, 1, 1), dt.date(2006, 12, 31)),
                "mother": self.person_name("F"), "phone": self.phone(),
                "address": "%s, %s %d." % (town, self.rng.choice(world_mod.STREETS),
                                          self.rng.randint(1, 120))}

    # -- rows --------------------------------------------------------------

    def add_contact(self, inmate: str, c: dict, kind: str, start: dt.date) -> None:
        self.contacts_of.setdefault(inmate, []).append(c)
        m = self.inmates[inmate]
        code = m["spans"][-1][0]
        end = ""
        if self.rng.random() < 0.08:
            end = iso(self.date(start + dt.timedelta(days=30), END))
        self.rows["contacts"].append([
            self.rid("K"), m["reg"], m["name"], c["name"], c["birthplace"], iso(c["dob"]),
            c["mother"], kind, phone_variant(self.rng, c["phone"]),
            self.email(c["name"]) if self.rng.random() < 0.25 else "", c["address"],
            iso(max(start, START - dt.timedelta(days=1500))), end, code, ""])

    def add_odin(self, day: dt.date, code: str, category: str, subjects: list[str],
                 cell: str) -> None:
        self.counter["odin_no"] += 1
        number = "R-%d/%d" % (day.year, self.counter["odin_no"])
        text = ODIN_TEXT[category].format(cell=cell, thing=self.rng.choice(THINGS))
        for key in subjects:
            m = self.inmates[key]
            self.rows["odin"].append([self.rid("O"), number, iso(day), code, category,
                                      m["name"], m["reg"], text, ""])

    def records(self) -> None:
        for key, m in self.inmates.items():
            self.rows["fonix_base"].append([self.rid("F"), m["reg"], m["name"], m["birthplace"],
                                            iso(m["dob"]), m["mother"], m["spans"][-1][0], ""])
            for i, (code, cell, start, end) in enumerate(m["spans"]):
                facility, unit, number = cell_parts(cell)
                moved = i + 1 < len(m["spans"])
                into = "BEF" if i == 0 else ("ATSZ" if m["spans"][i - 1][0] != code else "ATH")
                out = ""
                if moved:
                    out = "ATSZ" if m["spans"][i + 1][0] != code else "ATH"
                elif end:
                    out = "SZAB"
                self.rows["placement"].append([
                    self.rid("E"), m["reg"], m["name"], code, facility, unit, number, iso(start),
                    iso(end), into, out, ""])

    def intel_and_odin(self, total_inmates: int) -> None:
        keys = sorted(self.inmates)
        informants = self.rng.sample(keys, max(4, len(keys) // 25))
        for _ in range(int(total_inmates * self.preset["intel"])):
            subject = self.rng.choice(keys)
            at = self.present(subject)
            if not at:
                continue
            code, cell, day = at
            subjects = [subject]
            if self.rng.random() < 0.2:
                mates = [k for k in self.rng.sample(keys, min(len(keys), 40))
                         if k != subject and any(s[0] == code for s in self.inmates[k]["spans"])]
                subjects += mates[:1]
            category = weighted(self.rng, INTEL_WEIGHTS)
            text = self.rng.choice(INTEL_TEXT[category]).format(cell=cell_parts(cell)[2])
            kind = weighted(self.rng, SOURCE_WEIGHTS)
            source_reg = ""
            if kind == "FOGV":
                informant = self.rng.choice(informants)
                if informant in subjects:
                    continue
                source_name, source_reg = self.inmates[informant]["name"], self.inmates[informant]["reg"]
            elif kind == "KT":
                source_name = self.rng.choice(self.contacts_of.get(subject) or
                                              [self.new_contact()])["name"]
            elif kind == "TARS":
                source_name = self.rng.choice(self.partners)
            else:
                source_name = self.rng.choice(self.staff)
            self.counter["log_no"] += 1
            number = "%d/%d/%d" % (day.year, 10 + int(code), self.counter["log_no"])
            for key in subjects:
                m = self.inmates[key]
                self.rows["intel_log"].append([
                    self.rid("N"), number, iso(day), code, m["name"], m["reg"], category, text,
                    source_name, source_reg, kind, ""])
        for _ in range(int(total_inmates * self.preset["odin"])):
            subject = self.rng.choice(keys)
            at = self.present(subject)
            if not at:
                continue
            code, cell, day = at
            subjects = [subject]
            if self.rng.random() < 0.3:
                mates = [k for k in self.rng.sample(keys, min(len(keys), 40))
                         if k != subject and any(s[0] == code for s in self.inmates[k]["spans"])]
                subjects += mates[:self.rng.randint(1, 2)]
            self.add_odin(day, code, weighted(self.rng, ODIN_WEIGHTS), subjects,
                          cell_parts(cell)[2])


def build(preset: dict, seed: int = 26) -> tuple[world_mod.World, Register]:
    w = world_mod.build(preset["persons"], seed, list(INSTITUTIONS.values()))
    reg = Register(w, preset, seed)
    reg.from_world()
    reg.background(preset["background"])
    reg.records()
    reg.intel_and_odin(len(reg.inmates))
    return w, reg


def people_to_ask(w: world_mod.World, reg: Register, key: dict) -> dict:
    """A few people of the documents worth asking about, for the hand-over."""
    degree = Counter()
    for e in key["person_edges"]:
        degree[e["a"]] += 1
        degree[e["b"]] += 1
    inmates = [pid for pid, _ in degree.most_common() if w.persons[pid].role == "inmate"][:8]
    informed = Counter(row[4] for row in reg.rows["intel_log"] if row[10] == "FOGV")
    in_log = [reg.inmates[pid] for pid in inmates if reg.inmates[pid]["name"] in informed]
    return {
        "most_connected_inmates": [{"name": w.persons[p].name,
                                    "registry_number": w.persons[p].registry_number,
                                    "institution": reg.inmates[p]["spans"][-1][0]}
                                   for p in inmates],
        "in_the_intelligence_log": [{"name": m["name"], "registry_number": m["reg"]}
                                    for m in in_log[:3]],
        "name_twins": [{"name": p.name, "birth_dates": [iso(w.persons[p.homonym_of].dob),
                                                        iso(p.dob)]}
                       for p in w.persons.values() if p.homonym_of][:5],
    }


def write(out_root: str, preset_name: str, seed: int) -> dict:
    preset = PRESETS[preset_name]
    w, reg = build(preset, seed)
    manifest = generate.write_corpus(w, out_root, seed, quiet=True)
    rooms = os.path.join(out_root, "rooms")
    for drive, room in ROOMS.items():
        src = os.path.join(out_root, "drives", drive)
        if os.path.isdir(src):
            os.makedirs(rooms, exist_ok=True)
            shutil.move(src, os.path.join(rooms, room))
    shutil.rmtree(os.path.join(out_root, "drives"), ignore_errors=True)
    exports = os.path.join(out_root, "exports")
    os.makedirs(exports, exist_ok=True)
    headers = {source: header for source, (header, _) in samples.exports().items()}
    for source, rows in reg.rows.items():
        with open(os.path.join(exports, "%s.csv" % source), "wb") as f:
            f.write(samples.to_csv(headers[source], rows))
    with open(os.path.join(out_root, "answer_key.json"), encoding="utf-8") as f:
        key = json.load(f)
    summary = {
        "preset": preset_name, "seed": seed, "as_of": AS_OF,
        "rooms": sorted(ROOMS.values()),
        "export_folder": "%s/%s" % (EXPORT_ROOM, EXPORT_FOLDER),
        "institutions": INSTITUTIONS,
        "documents": len(manifest),
        "documents_by_room": dict(Counter(ROOMS[m["drive"]] for m in manifest)),
        "records": {source: len(rows) for source, rows in reg.rows.items()},
        "inmates": len(reg.inmates),
        "ask_about": people_to_ask(w, reg, key),
    }
    with open(os.path.join(out_root, "demo.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    return summary


def pack(out_root: str, bundle: str) -> None:
    """rooms/, exports/ and demo.json into a tgz with UTF-8 names (PAX)."""
    with tarfile.open(bundle, "w:gz", format=tarfile.PAX_FORMAT, encoding="utf-8") as tar:
        for name in ("demo.json", "exports", "rooms"):
            tar.add(os.path.join(out_root, name), arcname=name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--preset", choices=sorted(PRESETS), default="large")
    ap.add_argument("--seed", type=int, default=26)
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--bundle", help="also write this .tgz for bv-demo-seed.py")
    args = ap.parse_args()
    out_root = os.path.abspath(args.out)
    if os.path.isdir(out_root):
        if not args.clean:
            raise SystemExit("%s exists (use --clean)" % out_root)
        shutil.rmtree(out_root)
    summary = write(out_root, args.preset, args.seed)
    print(json.dumps({k: summary[k] for k in ("preset", "documents", "documents_by_room",
                                              "inmates", "records")}, ensure_ascii=False))
    if args.bundle:
        pack(out_root, args.bundle)
        print("bundle: %s (%.1f MB)" % (args.bundle, os.path.getsize(args.bundle) / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
