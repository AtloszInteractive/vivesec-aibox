"""Renders the hidden world (world.py) into a mixed, realistic document set.

Every rendered document records which entities it mentions (with the exact
surface string used) and which world relations it is evidence for, so the
answer key can score extraction, entity resolution and graph queries.
"""
from __future__ import annotations

import datetime as dt
import random
import unicodedata
from dataclasses import dataclass, field

from world import Person, Relation, World

FOOTER = "SZINTETIKUS TESZTADAT - kitalált személyek"


@dataclass
class Doc:
    key: str
    drive: str
    rel: str
    fmt: str  # pdf | pdf_scanned | docx | txt | csv | xlsx
    kind: str
    title: str
    blocks: list[tuple[str, str]] = field(default_factory=list)
    body: str | None = None
    sheets: dict[str, list[list]] | None = None
    mentions: list[tuple[str, str]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    noise_list: bool = False

    def text(self) -> str:
        if self.body is not None:
            return self.title + "\n" + self.body
        if self.sheets is not None:
            return self.title + "\n" + "\n".join(
                " ".join(str(c) for c in row) for rows in self.sheets.values() for row in rows)
        return self.title + "\n" + "\n".join(t for _, t in self.blocks)


def hu_date(d: dt.date) -> str:
    return "%d. %02d. %02d." % (d.year, d.month, d.day)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _typo(rng: random.Random, p: Person) -> str:
    family = p.family
    accented = [i for i, c in enumerate(family) if strip_accents(c) != c]
    if accented:
        i = rng.choice(accented)
        family = family[:i] + strip_accents(family[i]) + family[i + 1:]
    elif len(family) > 4:
        i = rng.randint(1, len(family) - 3)
        family = family[:i] + family[i + 1] + family[i] + family[i + 2:]
    else:
        family = family + family[-1]
    return "%s %s" % (family, p.given)


def surface(rng: random.Random, p: Person, style: str | None = None) -> str:
    if style is None:
        r = rng.random()
        style = ("full" if r < 0.55 else "noaccent" if r < 0.67 else "initial" if r < 0.79
                 else "given_first" if r < 0.87 else "typo")
    if style == "upper":
        return p.name.upper()
    if style == "noaccent":
        return strip_accents(p.name)
    if style == "initial":
        return "%s %s." % (p.family, p.given[0])
    if style == "given_first":
        return "%s %s" % (p.given, p.family)
    if style == "typo":
        return _typo(rng, p)
    return p.name


class Renderer:
    def __init__(self, world: World, seed: int):
        self.w = world
        self.rng = random.Random(seed * 7 + 1)
        self.docs: list[Doc] = []
        self.n = 0
        self.by_type_a: dict[tuple[str, str], list[Relation]] = {}
        self.by_type: dict[str, list[Relation]] = {}
        for r in world.relations:
            self.by_type_a.setdefault((r.type, r.a), []).append(r)
            self.by_type.setdefault(r.type, []).append(r)

    # -- helpers -------------------------------------------------------------

    def new(self, drive: str, folder: str, stem: str, fmt: str, kind: str, title: str) -> Doc:
        self.n += 1
        ext = {"pdf_scanned": "pdf"}.get(fmt, fmt)
        doc = Doc("D%05d" % self.n, drive, "%s/%s_%05d.%s" % (folder, stem, self.n, ext),
                  fmt, kind, title)
        self.docs.append(doc)
        return doc

    def mention(self, doc: Doc, p: Person, style: str | None = None) -> str:
        s = surface(self.rng, p, style)
        doc.mentions.append((p.id, s))
        return s

    def cite(self, doc: Doc, *rels: Relation) -> None:
        for r in rels:
            if doc.key not in r.evidence:
                r.evidence.append(doc.key)
            if r.id not in doc.evidence:
                doc.evidence.append(r.id)

    def find(self, type_: str, a: str, b: str | None = None) -> list[Relation]:
        return [r for r in self.by_type_a.get((type_, a), []) if b is None or r.b == b]

    def rels(self, type_: str) -> list[Relation]:
        return self.by_type.get(type_, [])

    def label(self, place_id: str) -> str:
        return self.w.places[place_id].label

    def pick_fmt(self, weights: dict[str, float]) -> str:
        r, acc = self.rng.random(), 0.0
        for fmt, w in weights.items():
            acc += w
            if r < acc:
                return fmt
        return next(iter(weights))

    # -- document types ------------------------------------------------------

    def intake_sheets(self) -> None:
        first = {}
        for r in self.rels("PLACED_IN"):
            if r.a not in first or r.start < first[r.a].start:
                first[r.a] = r
        for pid, place in sorted(first.items()):
            p = self.w.persons[pid]
            doc = self.new("nyilvantartas", "befogadas/%d" % place.start.year, "Befogadasi_adatlap",
                           self.pick_fmt({"pdf": 0.65, "pdf_scanned": 0.35}), "befogadasi_adatlap",
                           "Befogadási adatlap")
            b = doc.blocks
            b.append(("h2", "Személyi adatok"))
            b.append(("", "Név: %s" % self.mention(doc, p, "upper")))
            b.append(("", "Nyilvántartási szám: %s" % p.registry_number))
            b.append(("", "Születési hely, idő: %s, %s" % (p.birthplace, hu_date(p.dob))))
            b.append(("", "Anyja neve: %s" % p.mother))
            b.append(("", "Személyazonosító igazolvány száma: %s" % p.id_number))
            b.append(("", "Lakcím: %s" % p.address))
            b.append(("h2", "Befogadás"))
            b.append(("", "Befogadás napja: %s" % hu_date(place.start)))
            b.append(("", "Intézet: %s" % self.label(place.attrs["institution"])))
            b.append(("", "Elhelyezés: %s" % self.label(place.b)))
            self.cite(doc, place, *self.find("RESIDES_AT", pid))
            b.append(("h2", "Kapcsolattartók"))
            rels = self.find("RELATIVE_OF", pid)
            if not rels:
                b.append(("", "Kapcsolattartót nem jelölt meg."))
            for r in rels:
                q = self.w.persons[r.b]
                phone = q.phones[0]
                b.append(("", "%s (%s), telefon: %s, lakcím: %s"
                          % (self.mention(doc, q, "full"), r.attrs["kind"], phone, q.address)))
                self.cite(doc, r, *self.find("USES_PHONE", q.id, phone),
                          *self.find("RESIDES_AT", q.id))
            b.append(("h2", "Megjegyzés"))
            b.append(("", self.rng.choice([
                "Egészségügyi vizsgálaton részt vett, észrevétel nélkül.",
                "Személyes tárgyait jegyzőkönyv szerint átvettük.",
                "A házirendet megismerte, átvételét aláírásával igazolta.",
            ])))

    def transfer_orders(self) -> None:
        for r in self.rels("TRANSFERRED"):
            p = self.w.persons[r.a]
            new_place = next(x for x in self.find("PLACED_IN", p.id) if x.start == r.start)
            doc = self.new("nyilvantartas", "hatarozatok/%d" % r.start.year, "Atszallitasi_hatarozat",
                           "pdf", "hatarozat", "Határozat átszállításról")
            doc.blocks += [
                ("", "Ügyszám: BV-%d/%05d" % (r.start.year, self.rng.randint(1, 99999))),
                ("", "%s (nyilvántartási szám: %s; születési hely, idő: %s, %s; anyja neve: %s) "
                     "elítéltet %s napi hatállyal "
                     "a(z) %s intézetből a(z) %s intézetbe szállítom át."
                 % (self.mention(doc, p, "full"), p.registry_number, p.birthplace, hu_date(p.dob),
                    p.mother, hu_date(r.start), self.label(r.attrs["from_institution"]),
                    self.label(r.b))),
                ("", "Elhelyezése a befogadó intézetben: %s." % self.label(new_place.b)),
                ("h2", "Indokolás"),
                ("", "Az átszállításra a fogvatartotti létszám kiegyenlítése érdekében került sor."),
            ]
            self.cite(doc, r, new_place)

    def placement_lists(self) -> None:
        for inst in [x for x in self.w.places.values() if x.kind == "institution"]:
            rels = sorted((r for r in self.rels("PLACED_IN") if r.attrs["institution"] == inst.id),
                          key=lambda r: (r.b, r.start))
            if not rels:
                continue
            doc = self.new("nyilvantartas", "elhelyezes", "Elhelyezesi_kimutatas", "xlsx",
                           "elhelyezesi_kimutatas", "Elhelyezési kimutatás - %s" % inst.label)
            rows = [["Zárka", "Név", "Nyilvántartási szám", "Születési dátum", "Elhelyezés kezdete",
                     "Elhelyezés vége"]]
            for r in rels:
                p = self.w.persons[r.a]
                rows.append([self.label(r.b), self.mention(doc, p, "full"), p.registry_number,
                             p.dob.isoformat(), r.start.isoformat(),
                             r.end.isoformat() if r.end else "jelenleg is"])
                self.cite(doc, r)
            doc.sheets = {"Elhelyezés": rows}
            doc.noise_list = True

    def visit_logs(self) -> None:
        buckets: dict[tuple[str, int, int], list[Relation]] = {}
        for r in self.rels("VISITED"):
            key = (r.attrs["institution"], r.start.year, (r.start.month - 1) // 3 + 1)
            buckets.setdefault(key, []).append(r)
        for (inst, year, q), rels in sorted(buckets.items()):
            fmt = "xlsx" if (year + q) % 2 else "csv"
            doc = self.new("kommunikacio", "latogatas/%s" % inst, "Latogatasi_naplo_%d_Q%d" % (year, q),
                           fmt, "latogatasi_naplo", "Látogatási napló - %s - %d. %d. negyedév"
                           % (self.label(inst), year, q))
            rows = [["Dátum", "Fogvatartott neve", "Fogvatartott nyilvántartási száma",
                     "Látogató neve", "Látogató okmányszáma", "Kapcsolat"]]
            for r in sorted(rels, key=lambda x: x.start):
                inmate, visitor = self.w.persons[r.b], self.w.persons[r.a]
                rows.append([r.start.isoformat(), self.mention(doc, inmate, "full"),
                             inmate.registry_number, self.mention(doc, visitor, "full"),
                             visitor.id_number, r.attrs["relation"]])
                self.cite(doc, r)
            doc.noise_list = True
            if fmt == "xlsx":
                doc.sheets = {"Látogatások": rows}
            else:
                doc.body = "\n".join(";".join(str(c) for c in row) for row in rows)

    def call_logs(self) -> None:
        by_inmate: dict[str, list[Relation]] = {}
        for r in self.rels("CALLED"):
            by_inmate.setdefault(r.a, []).append(r)
        for pid, rels in sorted(by_inmate.items()):
            p = self.w.persons[pid]
            doc = self.new("kommunikacio", "hivasok", "Hivasnaplo", "csv", "hivasnaplo",
                           "Telefonhívás-nyilvántartás")
            name = self.mention(doc, p, "full")
            rows = ["Dátum;Fogvatartott;Nyilvántartási szám;Születési dátum;Hívott szám;"
                    "Időtartam (perc)"]
            for r in sorted(rels, key=lambda x: x.start):
                rows.append("%s;%s;%s;%s;%s;%d" % (r.start.isoformat(), name, p.registry_number,
                                                   p.dob.isoformat(), r.b, r.attrs["minutes"]))
                self.cite(doc, r)
            doc.body = "\n".join(rows)

    def subscriber_extracts(self) -> None:
        rels = sorted(self.rels("USES_PHONE"), key=lambda r: r.b)
        rels = [r for r in rels if not r.attrs.get("seized")]
        for k in range(0, len(rels), 60):
            doc = self.new("kommunikacio", "elofizetoi_adatok", "Elofizetoi_kivonat", "csv",
                           "elofizetoi_kivonat", "Előfizetői és használói adatok kivonata")
            rows = ["Hívószám;Használó neve;Születési dátum;Anyja neve"]
            for r in rels[k:k + 60]:
                p = self.w.persons[r.a]
                rows.append("%s;%s;%s;%s" % (r.b, self.mention(doc, p, "full"), p.dob.isoformat(),
                                             p.mother))
                self.cite(doc, r)
            doc.body = "\n".join(rows)
            doc.noise_list = True

    def address_extracts(self) -> None:
        people = [p for p in self.w.persons.values() if p.role != "inmate"]
        for k in range(0, len(people), 50):
            doc = self.new("egyeb", "nyilvantartas", "Lakcim_kivonat", "xlsx", "lakcim_kivonat",
                           "Lakcímnyilvántartási kivonat")
            rows = [["Név", "Születési dátum", "Anyja neve", "Lakcím", "Okmányszám"]]
            for p in people[k:k + 50]:
                rows.append([self.mention(doc, p, "full"), p.dob.isoformat(), p.mother, p.address,
                             p.id_number])
                self.cite(doc, *self.find("RESIDES_AT", p.id))
            doc.sheets = {"Kivonat": rows}
            doc.noise_list = True

    def incident_reports(self) -> None:
        for inc in self.w.incidents:
            doc = self.new("jelentesek", "rendkivuli_esemeny/%d" % inc.date.year, "Jelentes",
                           self.pick_fmt({"pdf": 0.55, "docx": 0.3, "pdf_scanned": 0.15}),
                           "jelentes", "Jelentés rendkívüli eseményről")
            people = [self.w.persons[x] for x in inc.persons]
            names = [self.mention(doc, p) for p in people]
            time = "%02d:%02d" % (self.rng.randint(6, 21), self.rng.choice([0, 10, 15, 30, 45, 50]))
            doc.blocks += [
                ("", "Iktatószám: J-%d/%04d" % (inc.date.year, self.rng.randint(1, 9999))),
                ("", "Helyszín: %s, %s" % (self.label(inc.institution), self.label(inc.cell))),
                ("", "%s %s órakor a(z) %s ellenőrzése során %s és %s között %s történt."
                 % (hu_date(inc.date), time, self.label(inc.cell), names[0], names[1],
                    {"verekedés": "dulakodás", "rendbontás": "hangos szóváltás"}.get(inc.kind, inc.kind))),
            ]
            if len(names) > 2:
                doc.blocks.append(("", "Az eseménynél jelen volt %s is, aki elmondta, hogy a "
                                       "vitát nem látta." % names[2]))
            if inc.phone:
                doc.blocks.append(("", "A zárka átvizsgálásakor %s birtokában egy mobiltelefont "
                                       "találtunk, amelyben a(z) %s hívószám több alkalommal "
                                       "szerepelt a híváslistán." % (self.mention(doc, people[0]),
                                                                   inc.phone)))
                self.cite(doc, *[r for r in self.find("USES_PHONE", people[0].id, inc.phone)
                                 if r.attrs.get("seized")])
            doc.blocks += [
                ("", "Nevezett az eseményt tagadta, a többi érintett meghallgatása folyamatban van."),
                ("", "Az eseményt a parancsnoknak jelentettem."),
                ("", "főtörzsőrmester (aláírás olvashatatlan)"),
            ]
            self.cite(doc, *[r for r in self.rels("INVOLVED_IN") if r.b == inc.id])

    def meeting_notes(self) -> None:
        meetings: dict[str, list[Relation]] = {}
        for r in self.rels("AT_PLACE"):
            meetings.setdefault(r.attrs["meeting"], []).append(r)
        for mid, at in sorted(meetings.items()):
            day, place = at[0].start, at[0].b
            fmt = self.pick_fmt({"pdf": 0.4, "docx": 0.35, "txt": 0.25})
            doc = self.new("jelentesek", "feljegyzesek/%d" % day.year, "Feljegyzes", fmt,
                           "feljegyzes", "Feljegyzés")
            people = [self.w.persons[r.a] for r in at]
            names = [self.mention(doc, p) for p in people]
            joined = names[0] + "".join((" és " if i == len(names) - 1 else ", ") + n
                                        for i, n in enumerate(names[1:], start=1))
            lines = [
                "Tárgy: környezettanulmány, észlelés",
                "%s %02d:%02d körül a(z) %s címen %s találkozott egymással."
                % (hu_date(day), self.rng.randint(8, 20), self.rng.choice([0, 15, 20, 40]), place, joined),
            ]
            for p in people:
                came_by_car = False
                if p.plates and self.rng.random() < 0.6:
                    plate = self.rng.choice(p.plates)
                    lines.append("%s a(z) %s forgalmi rendszámú gépjárművel érkezett."
                                 % (self.mention(doc, p), plate))
                    self.cite(doc, *self.find("USES_VEHICLE", p.id, plate))
                    came_by_car = True
                if self.rng.random() < 0.3:
                    phone = self.rng.choice(p.phones)
                    # "Nevezett" only when the previous sentence named the same person.
                    who = "Nevezett" if came_by_car else self.mention(doc, p)
                    lines.append("%s a találkozót követően a(z) %s hívószámról telefonált."
                                 % (who, phone))
                    self.cite(doc, *self.find("USES_PHONE", p.id, phone))
            lines.append("Az észlelésről fénykép nem készült.")
            self.cite(doc, *at, *[r for r in self.rels("MET") if r.attrs.get("meeting") == mid])
            if fmt == "txt":
                doc.body = "\n".join(lines)
            else:
                doc.blocks = [("", line) for line in lines]

    def vehicle_extracts(self) -> None:
        owned = {r.b for r in self.rels("OWNS_VEHICLE")}
        rels = sorted((r for r in self.rels("USES_VEHICLE") if r.b not in owned), key=lambda r: r.b)
        if not rels:
            return
        doc = self.new("egyeb", "nyilvantartas", "Gepjarmu_kivonat", "csv", "gepjarmu_kivonat",
                       "Gépjármű-nyilvántartási kivonat")
        rows = ["Forgalmi rendszám;Üzemben tartó;Születési dátum"]
        for r in rels:
            p = self.w.persons[r.a]
            rows.append("%s;%s;%s" % (r.b, self.mention(doc, p, "full"), p.dob.isoformat()))
            self.cite(doc, r)
        doc.body = "\n".join(rows)
        doc.noise_list = True

    def org_sheets(self) -> None:
        for org in self.w.orgs.values():
            staff = [r for r in self.rels("WORKS_FOR") if r.b == org.id]
            cars = [r for r in self.rels("OWNS_VEHICLE") if r.a == org.id]
            if not staff and not cars:
                continue
            doc = self.new("egyeb", "cegadatok", "Cegadatlap", "docx", "cegadatlap",
                           "Cégadatlap - %s" % org.name)
            doc.blocks.append(("", "Székhely: %s" % org.address))
            doc.blocks.append(("h2", "Alkalmazottak"))
            for r in staff:
                doc.blocks.append(("", "%s, munkakör: %s" % (
                    self.mention(doc, self.w.persons[r.a], "full"),
                    self.rng.choice(["gépkocsivezető", "raktáros", "ügyintéző", "biztonsági őr"]))))
                self.cite(doc, r)
            doc.blocks.append(("h2", "Céges gépjárművek"))
            for r in cars:
                users = [u for u in self.rels("USES_VEHICLE") if u.b == r.b]
                doc.blocks.append(("", "%s forgalmi rendszámú gépjármű, rendszeres használója: %s."
                                   % (r.b, ", ".join(self.mention(doc, self.w.persons[u.a], "full")
                                                     for u in users))))
                self.cite(doc, r, *users)

    def roll_calls(self) -> None:
        for inst in [x for x in self.w.places.values() if x.kind == "institution"]:
            day = dt.date(2026, 6, 30)
            present = [self.w.persons[r.a] for r in self.rels("PLACED_IN")
                       if r.attrs["institution"] == inst.id and r.start <= day
                       and (r.end is None or day < r.end)]
            if not present:
                continue
            doc = self.new("nyilvantartas", "letszam", "Letszamjelentes", "txt", "letszamjelentes",
                           "Létszámjelentés - %s - %s" % (inst.label, hu_date(day)))
            doc.body = "A mai napon az intézet nyilvántartott létszáma %d fő:\n%s" % (
                len(present), "\n".join("%s (%s)" % (self.mention(doc, p, "full"),
                                                     p.registry_number)
                                        for p in sorted(present, key=lambda x: x.name)))
            doc.noise_list = True

    def distractors(self) -> None:
        texts = {
            "Hazirend": ("Házirend - kivonat", [
                "A fogvatartottak napirendjét az intézet parancsnoka határozza meg.",
                "A látogatás a látogatási rend szerint, előzetes bejelentkezés alapján történik.",
                "Tiltott tárgy birtoklása fegyelmi eljárást von maga után.",
            ]),
            "Latogatasi_rend": ("Látogatási rend", [
                "Látogatásra havonta legalább egy alkalommal, legfeljebb négy fő részére van lehetőség.",
                "A látogatónak személyazonosságát érvényes okmánnyal kell igazolnia.",
            ]),
            "Adatkezelesi_tajekoztato": ("Adatkezelési tájékoztató", [
                "Az intézet a személyes adatokat a vonatkozó jogszabályok szerint kezeli.",
                "Az érintett tájékoztatást kérhet személyes adatai kezeléséről.",
            ]),
        }
        for stem, (title, lines) in texts.items():
            doc = self.new("egyeb", "szabalyzatok", stem, "pdf", "szabalyzat", title)
            doc.blocks = [("", line) for line in lines]

    def render_all(self) -> list[Doc]:
        self.intake_sheets()
        self.transfer_orders()
        self.placement_lists()
        self.visit_logs()
        self.call_logs()
        self.subscriber_extracts()
        self.address_extracts()
        self.incident_reports()
        self.meeting_notes()
        self.vehicle_extracts()
        self.org_sheets()
        self.roll_calls()
        self.distractors()
        return self.docs
