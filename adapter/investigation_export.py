"""Investigation table export (E26b, BV 1.5 / 2.5): one investigation as XLSX.

The table is neither the model's answer nor anything the client sends. The
export runs the same deterministic investigation again (orchestrator.Run, the
code path of the answer) with the caller's scope and access at download time:
the client names only the starting point, the levels and the drives. What the
caller may no longer see is not in the file, and every row keeps the document
that states its link. The same graph gives the same table (the caps pick in
name order); a graph changed since the answer gives the current table, and the
info sheet says when and from what it was made.

Column order and the sheet layout are customer decisions (BV question 9), so
they are settings: ADAPTER_INVESTIGATION_XLSX_COLUMNS, ADAPTER_INVESTIGATION_XLSX_SHEETS.
An administrator may instead set the organisation's own layout (E18, from the
header row of a sample spreadsheet, templates.py): column order, header texts,
widths and empty columns kept for hand entry. It only arranges the columns:
which personal data a row carries is still decided by with_personal_data.
BV's answer (2026-11-13): an inmate is identified by name and registry number;
for anyone else the birth date and mother's name are shown where the visible
documents state them (ADAPTER_INVESTIGATION_XLSX_BIRTH_DATA_EXCEPT names the
kinds left without them).
"""
import os
import re
import sys
import time
import unicodedata

import orchestrator
import xlsx

COLUMNS = ("level", "name", "role", "registry_number", "birth_date", "mother_name",
           "id_number", "phone", "address", "email", "other", "path", "also", "more", "source",
           "page", "joined")
# A row without its name or its source cannot be checked against the documents.
REQUIRED = ("name", "source")
PERSONAL = ("birth_date", "mother_name")
SHEET_MODES = ("single", "per_level")
LANGS = ("en", "hu", "da", "de")
MAX_START = 120
MAX_LAYOUT_COLUMNS = 40
MAX_LABEL = 80
# Header texts of a sample spreadsheet that name a column besides its own
# labels (TEXT) and id; compared casefolded, without punctuation.
ALIASES = {
    "name": ("nev", "teljes nev", "full name", "person"),
    "registry_number": ("nyilvantartasi szam", "nyilv szam", "fogvatartotti nyilvantartasi szam",
                        "registry no"),
    "birth_date": ("szuletesi datum", "szul ido", "szul datum", "date of birth", "dob",
                   "birth date"),
    "mother_name": ("anyja neve", "anya neve", "mother name"),
    "id_number": ("szemelyi igazolvany szam", "szig szam", "id no", "id card number"),
    "phone": ("telefonszam", "telefon szam", "phone number", "mobile"),
    "address": ("cim", "lakhely", "lakcim", "address"),
    "email": ("email cim", "e mail cim", "email address"),
    "source": ("forrasdokumentum", "forras dokumentum", "dokumentum", "source document"),
    "page": ("oldalszam", "page number"),
    "role": ("minoseg", "szerepkor"),
}

TEXT = {
    "columns": {
        "level": {"en": "Level", "hu": "Szint", "da": "Niveau", "de": "Ebene"},
        "name": {"en": "Name", "hu": "Név", "da": "Navn", "de": "Name"},
        "role": {"en": "Role", "hu": "Szerep", "da": "Rolle", "de": "Rolle"},
        "registry_number": {"en": "Registry number", "hu": "Nyilvántartási szám",
                            "da": "Registreringsnummer", "de": "Registernummer"},
        "birth_date": {"en": "Date of birth", "hu": "Születési idő", "da": "Fødselsdato",
                       "de": "Geburtsdatum"},
        "mother_name": {"en": "Mother's name", "hu": "Anyja neve", "da": "Mors navn",
                        "de": "Name der Mutter"},
        "id_number": {"en": "ID number", "hu": "Azonosító szám", "da": "ID-nummer",
                      "de": "Ausweisnummer"},
        "phone": {"en": "Phone", "hu": "Telefon", "da": "Telefon", "de": "Telefon"},
        "address": {"en": "Address", "hu": "Lakcím", "da": "Adresse", "de": "Adresse"},
        "email": {"en": "E-mail", "hu": "E-mail", "da": "E-mail", "de": "E-Mail"},
        "other": {"en": "Other identifiers", "hu": "Egyéb azonosítók",
                  "da": "Andre identifikatorer", "de": "Weitere Kennungen"},
        "path": {"en": "Path of the link", "hu": "Kapcsolódás útja", "da": "Forbindelsesvej",
                 "de": "Verbindungsweg"},
        "also": {"en": "Also through", "hu": "További szál", "da": "Også gennem",
                 "de": "Auch über"},
        "more": {"en": "Left out by the limit", "hu": "Korlát miatt kimaradt",
                 "da": "Udeladt pga. grænsen", "de": "Wegen der Grenze ausgelassen"},
        "source": {"en": "Source", "hu": "Forrás", "da": "Kilde", "de": "Quelle"},
        "page": {"en": "Page", "hu": "Oldal", "da": "Side", "de": "Seite"},
        "joined": {"en": "Joined records", "hu": "Összevont rekordok",
                   "da": "Sammenlagte poster", "de": "Zusammengeführte Einträge"},
    },
    "sheet": {"en": "Investigation", "hu": "Vizsgálat", "da": "Undersøgelse",
              "de": "Untersuchung"},
    "sheet_start": {"en": "Starting point", "hu": "Kiindulópont", "da": "Udgangspunkt",
                    "de": "Ausgangspunkt"},
    "sheet_level": {"en": "Level %d", "hu": "%d. szint", "da": "Niveau %d", "de": "Ebene %d"},
    "sheet_info": {"en": "About", "hu": "Tájékoztató", "da": "Om", "de": "Info"},
    "start_path": {"en": "starting point", "hu": "kiindulópont", "da": "udgangspunkt",
                   "de": "Ausgangspunkt"},
    "field": {"en": "Field", "hu": "Tétel", "da": "Felt", "de": "Feld"},
    "value": {"en": "Value", "hu": "Érték", "da": "Værdi", "de": "Wert"},
    "title": {"en": "Investigation table", "hu": "Vizsgálati tábla",
              "da": "Undersøgelsestabel", "de": "Untersuchungstabelle"},
    "info_start": {"en": "Starting point", "hu": "Kiindulópont", "da": "Udgangspunkt",
                   "de": "Ausgangspunkt"},
    "info_levels": {"en": "Levels asked for", "hu": "Kért szintek", "da": "Ønskede niveauer",
                    "de": "Angefragte Ebenen"},
    "info_rows": {"en": "Rows", "hu": "Sorok", "da": "Rækker", "de": "Zeilen"},
    "info_coverage": {"en": "Coverage", "hu": "Teljesség", "da": "Dækning",
                      "de": "Vollständigkeit"},
    "complete": {"en": "Complete for these levels.", "hu": "Ezekre a szintekre teljes.",
                 "da": "Fuldstændig for disse niveauer.",
                 "de": "Für diese Ebenen vollständig."},
    "cut": {"en": "Cut: at most %d new people from one person, %d rows in all; more people "
                  "may exist. The \u201cLeft out by the limit\u201d column gives, per row, how "
                  "many linked people of the next level are not listed.",
            "hu": "Csonkolva: személyenként legfeljebb %d új személy, összesen %d sor; további "
                  "személyek is lehetnek. A \u201eKorlát miatt kimaradt\u201d oszlop soronként "
                  "megadja, hány kapcsolódó személy maradt ki a következő szintről.",
            "da": "Afkortet: højst %d nye personer fra én person, %d rækker i alt; der kan "
                  "være flere personer. Kolonnen \u201dUdeladt pga. grænsen\u201d angiver for "
                  "hver række, hvor mange forbundne personer på næste niveau der ikke er med.",
            "de": "Gekürzt: höchstens %d neue Personen je Person, insgesamt %d Zeilen; es kann "
                  "weitere Personen geben. Die Spalte \u201eWegen der Grenze ausgelassen\u201c "
                  "nennt je Zeile, wie viele verbundene Personen der nächsten Ebene fehlen."},
    "info_birth": {"en": "Date of birth, mother's name", "hu": "Születési idő, anyja neve",
                   "da": "Fødselsdato, mors navn", "de": "Geburtsdatum, Name der Mutter"},
    "birth_except": {"en": "Where the documents state them; not for: %s.",
                     "hu": "Ahol az iratok tartalmazzák; nem szerepel: %s.",
                     "da": "Hvor dokumenterne angiver dem; ikke for: %s.",
                     "de": "Soweit die Dokumente sie nennen; nicht für: %s."},
    "birth_all": {"en": "Where the documents state them.",
                  "hu": "Ahol az iratok tartalmazzák.",
                  "da": "Hvor dokumenterne angiver dem.",
                  "de": "Soweit die Dokumente sie nennen."},
    "birth_none": {"en": "Not shown.", "hu": "Nem szerepel.", "da": "Vises ikke.",
                   "de": "Nicht angezeigt."},
    "info_follows": {"en": "Links followed", "hu": "Követett kapcsolatok",
                     "da": "Fulgte forbindelser", "de": "Verfolgte Verbindungen"},
    "shared": {"en": "%s (people linked to the same entity)",
               "hu": "%s (ugyanahhoz a szereplőhöz kapcsolódó személyek)",
               "da": "%s (personer forbundet med samme enhed)",
               "de": "%s (Personen, die mit demselben Eintrag verbunden sind)"},
    "info_scope": {"en": "Scope (drives)", "hu": "Hatókör (meghajtók)", "da": "Omfang (drev)",
                   "de": "Umfang (Laufwerke)"},
    "info_case": {"en": "Case reference", "hu": "Ügyszám", "da": "Sagsnummer",
                  "de": "Aktenzeichen"},
    "info_basis": {"en": "Legal basis", "hu": "Jogalap", "da": "Retsgrundlag",
                   "de": "Rechtsgrundlage"},
    "info_audit": {"en": "Audit ID of the answer", "hu": "A válasz audit-azonosítója",
                   "da": "Svarets audit-ID", "de": "Audit-ID der Antwort"},
    "info_generated": {"en": "Generated", "hu": "Készült", "da": "Udarbejdet",
                       "de": "Erstellt"},
    "info_by": {"en": "Requested by", "hu": "Kérte", "da": "Rekvireret af",
                "de": "Angefordert von"},
    "info_joined": {"en": "Same-named records", "hu": "Azonos nevű rekordok",
                    "da": "Poster med samme navn", "de": "Gleichnamige Einträge"},
    "joined_on": {"en": "Same-named person records that do not conflict count as one person "
                        "(\u201cJoined records\u201d column).",
                  "hu": "Az azonos nevű, egymásnak nem ellentmondó személyrekordok egy "
                        "személyként szerepelnek (\u201eÖsszevont rekordok\u201d oszlop).",
                  "da": "Personposter med samme navn, der ikke er i modstrid, tælles som én "
                        "person (kolonnen \u201dSammenlagte poster\u201d).",
                  "de": "Gleichnamige Personeneinträge ohne Widerspruch zählen als eine Person "
                        "(Spalte \u201eZusammengeführte Einträge\u201c)."},
    "joined_off": {"en": "Kept apart.", "hu": "Külön szerepelnek.", "da": "Holdes adskilt.",
                   "de": "Getrennt gehalten."},
    "info_note": {"en": "Note", "hu": "Megjegyzés", "da": "Bemærkning", "de": "Hinweis"},
    "note": {"en": "Compiled by the AI Box at download time from the documents the requester "
                   "may see (not from the model's answer). Each row names the document that "
                   "states its link; check the rows against the sources.",
             "hu": "Az AI Box a letöltés pillanatában állította össze a kérő számára látható "
                   "dokumentumokból (nem a modell válaszából). Minden sor megnevezi a "
                   "kapcsolatát kimondó dokumentumot; a sorokat a forrással kell ellenőrizni.",
             "da": "Udarbejdet af AI Boxen ved download ud fra de dokumenter, rekvirenten har "
                   "adgang til (ikke ud fra modellens svar). Hver række nævner det dokument, "
                   "der angiver forbindelsen; kontrollér rækkerne mod kilderne.",
             "de": "Von der AI Box beim Herunterladen aus den für den Anfragenden sichtbaren "
                   "Dokumenten erstellt (nicht aus der Antwort des Modells). Jede Zeile nennt "
                   "das Dokument, das ihre Verbindung belegt; prüfen Sie die Zeilen an den "
                   "Quellen."},
    "file": {"en": "investigation", "hu": "vizsgalat", "da": "undersoegelse",
             "de": "untersuchung"},
}


def _t(key, lang):
    return TEXT[key].get(lang) or TEXT[key]["en"]


def lang_code(value):
    code = str(value or "").strip().lower()[:2]
    return code if code in LANGS else "en"


def settings(env=None):
    """{columns, sheets} from the environment. Unknown column ids are dropped,
    none left gives the default order; the name and source columns are always
    there. An unknown sheet layout falls back to one sheet."""
    env = os.environ if env is None else env
    columns = []
    for raw in (env.get("ADAPTER_INVESTIGATION_XLSX_COLUMNS") or "").split(","):
        col = raw.strip().lower()
        if col in COLUMNS and col not in columns:
            columns.append(col)
        elif col and col not in columns:
            sys.stderr.write("[adapter] ADAPTER_INVESTIGATION_XLSX_COLUMNS: unknown %r "
                             "ignored\n" % col)
    columns = columns or list(COLUMNS)
    if "name" not in columns:
        columns.insert(1 if columns[0] == "level" else 0, "name")
    if "source" not in columns:
        columns.append("source")
    sheets = (env.get("ADAPTER_INVESTIGATION_XLSX_SHEETS") or "single").strip().lower()
    if sheets not in SHEET_MODES:
        sys.stderr.write("[adapter] unknown ADAPTER_INVESTIGATION_XLSX_SHEETS %r -> single\n"
                         % sheets)
        sheets = "single"
    raw = (env.get("ADAPTER_INVESTIGATION_XLSX_BIRTH_DATA_EXCEPT") or "inmate").strip().lower()
    if raw == "all":
        birth_except = ["*"]
    elif raw == "none":
        birth_except = []
    else:
        birth_except = list(dict.fromkeys(k.strip() for k in raw.split(",") if k.strip()))
    return {"columns": columns, "sheets": sheets, "birth_except": birth_except}


def _key(text):
    """A header text for matching: no accents, case or punctuation."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", str(text or ""))
                    if not unicodedata.combining(c))
    return " ".join(re.sub(r"[\W_]+", " ", plain.casefold()).split())


def _column_names():
    names = {}
    for col in COLUMNS:
        for text in (col, *TEXT["columns"][col].values(), *ALIASES.get(col, ())):
            names.setdefault(_key(text), col)
    return names


def propose_layout(headers):
    """A layout for the header row of a sample spreadsheet
    (templates.read_xlsx_header): each header text with the column it names,
    None (an empty column) where none fits. The administrator checks it."""
    names, used, out = _column_names(), set(), []
    for header in headers:
        col = names.get(_key(header.get("label")))
        if col in used:
            col = None
        if col:
            used.add(col)
        out.append({"column": col, "label": header.get("label") or "",
                    "width": header.get("width")})
    return out


def validate_layout(raw):
    """The normalised layout an administrator saves, or ValueError: 1 to
    MAX_LAYOUT_COLUMNS entries {column: id or None, label, width}, each column
    at most once, an empty column with a header text, name and source present."""
    if not isinstance(raw, list) or not raw or len(raw) > MAX_LAYOUT_COLUMNS:
        raise ValueError("columns must be a list of 1-%d entries" % MAX_LAYOUT_COLUMNS)
    out, seen = [], set()
    for number, entry in enumerate(raw, 1):
        if not isinstance(entry, dict) or set(entry) - {"column", "label", "width"}:
            raise ValueError("column %d: column, label and width are expected" % number)
        col = entry.get("column")
        if col is not None and (col not in COLUMNS or col in seen):
            raise ValueError("column %d: unknown or repeated column %r" % (number, col))
        label = entry.get("label")
        if label is not None and not isinstance(label, str):
            raise ValueError("column %d: label must be text" % number)
        label = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", label or "").split())
        if len(label) > MAX_LABEL:
            raise ValueError("column %d: label longer than %d characters" % (number, MAX_LABEL))
        if col is None and not label:
            raise ValueError("column %d: an empty column needs a header text" % number)
        width = entry.get("width")
        if width is not None:
            if isinstance(width, bool) or not isinstance(width, (int, float)) \
                    or not 3 <= width <= 100:
                raise ValueError("column %d: width must be 3-100" % number)
            width = round(float(width), 1)
        if col:
            seen.add(col)
        out.append({"column": col, "label": label or None, "width": width})
    missing = [col for col in REQUIRED if col not in seen]
    if missing:
        raise ValueError("the %s column(s) are required" % ", ".join(missing))
    return out


def with_layout(conf, layout):
    """The export settings with the organisation's layout, when one is set."""
    return dict(conf, layout=layout) if layout else conf


def with_personal_data(run, inv, conf, prof):
    """The investigation with each person's date of birth and mother's name
    from the documents the caller may see, except for the kinds the setting
    names (an inmate is identified by the registry number). A kind the
    profile does not know leaves them out for everyone: a mistyped setting
    must not show them for inmates. The rows are copies; the run's own table
    (the answer's) is not changed."""
    kinds = {s.get("id") for s in (prof or {}).get("entity_subtypes") or []
             if s.get("type") == "person"}
    excluded = list((conf or {}).get("birth_except", ["inmate"]))
    unknown = [k for k in excluded if k != "*" and k not in kinds]
    if unknown:
        sys.stderr.write("[adapter] ADAPTER_INVESTIGATION_XLSX_BIRTH_DATA_EXCEPT: unknown "
                         "kind(s) %s -> no birth data in the spreadsheet\n" % ", ".join(unknown))
        excluded = ["*"]
    rows = []
    for row in inv.get("rows") or []:
        row = dict(row)
        subtype, values = run.personal_data(row.get("id"), PERSONAL)
        if subtype is not None and "*" not in excluded and subtype not in excluded:
            for key in PERSONAL:
                row[key] = values.get(key) or []
        rows.append(row)
    return dict(inv, rows=rows, birth_except=excluded)


def parse_request(payload):
    """(start, levels) of an export request; ValueError when unusable."""
    start = payload.get("start")
    start = start.strip() if isinstance(start, str) else ""
    if not start or len(start) > MAX_START:
        raise ValueError("start must be a name or identifier of at most %d characters"
                         % MAX_START)
    levels = payload.get("levels", orchestrator.DEFAULT_INVESTIGATE_LEVELS)
    if isinstance(levels, str) and levels.strip().isdigit():
        levels = int(levels.strip())
    if isinstance(levels, bool) or not isinstance(levels, int) \
            or not 1 <= levels <= orchestrator.MAX_INVESTIGATE_LEVELS:
        raise ValueError("levels must be 1-%d" % orchestrator.MAX_INVESTIGATE_LEVELS)
    return start, levels


def parse_shared_contact(payload):
    """Whether the request's investigation took the shared-contact step (E28)."""
    value = payload.get("shared_contact", False)
    if not isinstance(value, bool):
        raise ValueError("shared_contact must be true or false")
    return value


def investigate(run, start, levels, shared_contact=False):
    """The investigation of one starting point within the run's scope and
    access, or None when nothing of it is visible. Raises orchestrator.Clarify
    when the start fits several different entities."""
    args = {"start": start, "levels": levels}
    if shared_contact:
        args["shared_contact"] = True
    run.run([{"tool": "investigate", "args": args}])
    return run.investigations[0] if run.investigations else None


def _joined(values, sep="; "):
    return sep.join(str(v) for v in values or [] if v not in (None, ""))


def cell(row, col, lang):
    if col in ("level", "page", "joined"):
        value = row.get(col)
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    if col == "more":
        value = row.get("more")
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 \
            else None
    if col == "path":
        return row.get("path") if row.get("level") else _t("start_path", lang)
    if col == "also":
        return _joined(row.get("also"), ", ")
    if col == "other":
        return " | ".join("%s: %s" % (kind, ", ".join(vals))
                          for kind, vals in sorted((row.get("other") or {}).items()) if vals)
    value = row.get(col)
    return _joined(value) if isinstance(value, list) else value


def _follows(prof, cfg, lang):
    labels = {r.get("id"): (r.get("label") or {}).get(lang) or (r.get("label") or {}).get("en")
              or r.get("id") for r in (prof or {}).get("relation_types") or []}
    out = [labels.get(r, r) for r in cfg.get("relations") or []]
    out += [_t("shared", lang) % labels.get(r, r) for r in cfg.get("via") or []]
    return ", ".join(out)


def _kind_names(prof, kinds, lang):
    labels = {s.get("id"): (s.get("label") or {}).get(lang) or (s.get("label") or {}).get("en")
              or s.get("id") for s in (prof or {}).get("entity_subtypes") or []}
    return ", ".join(labels.get(k, k) for k in kinds)


def _birth_note(inv, prof, lang):
    excluded = inv.get("birth_except")
    if "*" in excluded:
        return _t("birth_none", lang)
    if excluded:
        return _t("birth_except", lang) % _kind_names(prof, excluded, lang)
    return _t("birth_all", lang)


def build(inv, lang="en", conf=None, meta=None):
    """(xlsx bytes, file name without extension, title) of one investigation.
    meta: user, drives, case_ref, legal_basis, audit_id, prof, config, name_join, now."""
    lang = lang_code(lang)
    conf = conf or settings({})
    meta = meta or {}
    entries = [dict(e) for e in conf.get("layout") or ()] or [
        {"column": col, "label": None, "width": None} for col in conf["columns"]]
    cols = [e["column"] for e in entries]
    if inv.get("truncated") and "more" not in cols:
        # A cut table always says where it was cut.
        at = cols.index("source") if "source" in cols else len(cols)
        entries.insert(at, {"column": "more", "label": None, "width": None})
        cols.insert(at, "more")
    header = [e.get("label") or (TEXT["columns"][e["column"]].get(lang)
                                 or TEXT["columns"][e["column"]]["en"] if e["column"] else "")
              for e in entries]
    widths = [e.get("width") for e in entries]
    rows = inv.get("rows") or []
    table = [[cell(row, col, lang) if col else None for col in cols] for row in rows]
    if conf["sheets"] == "per_level":
        sheets = []
        for level in sorted({row.get("level", 0) for row in rows}):
            name = _t("sheet_start", lang) if level == 0 else _t("sheet_level", lang) % level
            sheets.append({"name": name, "widths": widths, "rows": [header] + [
                values for row, values in zip(rows, table) if row.get("level", 0) == level]})
    else:
        sheets = [{"name": _t("sheet", lang), "widths": widths, "rows": [header] + table}]
    cfg = meta.get("config") or {}
    now = meta.get("now") or time.time()
    coverage = (_t("cut", lang) % (cfg.get("branch", 0), cfg.get("max_rows", 0))
                if inv.get("truncated") else _t("complete", lang))
    info = [[_t("field", lang), _t("value", lang)],
            [_t("info_start", lang), inv.get("start")],
            [_t("info_levels", lang), inv.get("levels")],
            [_t("info_rows", lang), len(rows)],
            [_t("info_coverage", lang), coverage],
            [_t("info_follows", lang), _follows(meta.get("prof"), cfg, lang)],
            [_t("info_joined", lang), _t("joined_on" if meta.get("name_join") else "joined_off",
                                         lang)],
            [_t("info_scope", lang), ", ".join(meta.get("drives") or [])]]
    if "birth_except" in inv and any(c in cols for c in PERSONAL):
        info.append([_t("info_birth", lang), _birth_note(inv, meta.get("prof"), lang)])
    for key, field in (("case_ref", "info_case"), ("legal_basis", "info_basis"),
                       ("audit_id", "info_audit")):
        if meta.get(key):
            info.append([_t(field, lang), str(meta[key])])
    info += [[_t("info_generated", lang), time.strftime("%Y-%m-%d %H:%M:%S %z",
                                                        time.localtime(now))],
             [_t("info_by", lang), meta.get("user") or ""],
             [_t("info_note", lang), _t("note", lang)]]
    sheets.append({"name": _t("sheet_info", lang), "rows": info, "filter": False,
                   "widths": [28, 100]})
    title = "%s \u2013 %s" % (_t("title", lang), inv.get("start") or "")
    content = xlsx.workbook(sheets, title=title, now=now)
    safe = re.sub(r"[^\w\- ]+", "", str(inv.get("start") or ""), flags=re.UNICODE).strip()
    name = "%s_%s_%s" % (_t("file", lang), safe.replace(" ", "_")[:60] or "table",
                         time.strftime("%Y-%m-%d", time.localtime(now)))
    return content, name, title
