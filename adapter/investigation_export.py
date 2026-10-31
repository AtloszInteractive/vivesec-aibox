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
"""
import os
import re
import sys
import time

import orchestrator
import xlsx

COLUMNS = ("level", "name", "role", "registry_number", "id_number", "phone", "address",
           "email", "other", "path", "also", "source", "page", "joined")
# A row without its name or its source cannot be checked against the documents.
REQUIRED = ("name", "source")
SHEET_MODES = ("single", "per_level")
LANGS = ("en", "hu", "da", "de")
MAX_START = 120

TEXT = {
    "columns": {
        "level": {"en": "Level", "hu": "Szint", "da": "Niveau", "de": "Ebene"},
        "name": {"en": "Name", "hu": "Név", "da": "Navn", "de": "Name"},
        "role": {"en": "Role", "hu": "Szerep", "da": "Rolle", "de": "Rolle"},
        "registry_number": {"en": "Registry number", "hu": "Nyilvántartási szám",
                            "da": "Registreringsnummer", "de": "Registernummer"},
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
                  "may exist.",
            "hu": "Csonkolva: személyenként legfeljebb %d új személy, összesen %d sor; további "
                  "személyek is lehetnek.",
            "da": "Afkortet: højst %d nye personer fra én person, %d rækker i alt; der kan "
                  "være flere personer.",
            "de": "Gekürzt: höchstens %d neue Personen je Person, insgesamt %d Zeilen; es kann "
                  "weitere Personen geben."},
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
    return {"columns": columns, "sheets": sheets}


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


def investigate(run, start, levels):
    """The investigation of one starting point within the run's scope and
    access, or None when nothing of it is visible. Raises orchestrator.Clarify
    when the start fits several different entities."""
    run.run([{"tool": "investigate", "args": {"start": start, "levels": levels}}])
    return run.investigations[0] if run.investigations else None


def _joined(values, sep="; "):
    return sep.join(str(v) for v in values or [] if v not in (None, ""))


def cell(row, col, lang):
    if col in ("level", "page", "joined"):
        value = row.get(col)
        return value if isinstance(value, int) and not isinstance(value, bool) else None
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


def build(inv, lang="en", conf=None, meta=None):
    """(xlsx bytes, file name without extension, title) of one investigation.
    meta: user, drives, case_ref, legal_basis, audit_id, prof, config, name_join, now."""
    lang = lang_code(lang)
    conf = conf or settings({})
    meta = meta or {}
    cols = conf["columns"]
    header = [TEXT["columns"][col].get(lang) or TEXT["columns"][col]["en"] for col in cols]
    rows = inv.get("rows") or []
    table = [[cell(row, col, lang) for col in cols] for row in rows]
    if conf["sheets"] == "per_level":
        sheets = []
        for level in sorted({row.get("level", 0) for row in rows}):
            name = _t("sheet_start", lang) if level == 0 else _t("sheet_level", lang) % level
            sheets.append({"name": name, "rows": [header] + [
                values for row, values in zip(rows, table) if row.get("level", 0) == level]})
    else:
        sheets = [{"name": _t("sheet", lang), "rows": [header] + table}]
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
