"""Synthetic periodic exports for the structured import (E28).

The BV has not sent samples yet (A8, A10, A2), so this is the format we assume:
one CSV/XLSX per source with a header row, the column names of the `bv`
profile's `structured_sources`, institution codes, reason and relationship
codes, ISO dates. Everyone is invented.

The case it tells (the tests and the box check rely on it):
  * Sas Tibor (NY-1001, institution 01) and Sas Gábor (NY-1002, institution 02)
    are brothers; their mother, Kerekes Ilona, is the approved contact of each,
    in two contact records with the same four natural identifiers -> one person,
    and the investigation reaches Sas Gábor from Sas Tibor through her;
  * two contacts named Nagy Erzsébet with different birth dates (of Major Ede
    and of Illés Dénes) -> two people, no path;
  * two contact records of Fekete Judit share a phone; the second has no birth
    data -> a review pair, no merge, and the "shared contact" step links them;
  * Sas Tibor and Major Ede share cell 102 (A objektum, B részleg, institution
    01) for overlapping weeks;
  * the intelligence log (E29): five entries on Sas Tibor, Major Ede, Sas Gábor
    and Illés Dénes (2026/14/3 names both in two rows, one per subject), and
    Varga Péter. Their informants -- invented like everyone -- are seen only by
    the FEFO: Bodó Ákos (an inmate of the FŐNIX export, by his registry number:
    a review pair for the FEFO, never a link), twice; Rigó Lajos, a contact;
    Dr. Fehér Gábor of a partner agency; Hollós Péter of the staff.

  python graph-corpus/structured_samples.py <out dir> [--xlsx]
"""
from __future__ import annotations

import argparse
import csv
import io
import os

AS_OF = "2026-11-01"

FONIX = ["Rekordazonosító", "Nyilvántartási szám", "Név", "Születési hely", "Születési idő",
         "Anyja neve", "Intézet kód", "Törölve"]
PLACEMENT = ["Rekordazonosító", "Nyilvántartási szám", "Fogvatartott neve", "Intézet kód",
             "Objektum", "Részleg", "Zárka", "Elhelyezés kezdete", "Elhelyezés vége",
             "Behelyezés indoka", "Kihelyezés indoka", "Törölve"]
CONTACTS = ["Rekordazonosító", "Fogvatartott nyilvántartási száma", "Fogvatartott neve",
            "Kapcsolattartó neve", "Születési hely", "Születési idő", "Anyja neve",
            "Kapcsolat jellege", "Telefonszám", "E-mail", "Lakcím", "Engedély kezdete",
            "Engedély vége", "Intézet kód", "Törölve"]
INTEL = ["Rekordazonosító", "Naplószám", "Dátum", "Intézet kód", "Érintett neve",
         "Érintett nyilvántartási száma", "Információ jellege", "Információ", "Forrás neve",
         "Forrás nyilvántartási száma", "Forrás típusa", "Törölve"]
# The informant columns: only the protected part of a log record shows them.
INFORMANT = ("Forrás neve", "Forrás nyilvántartási száma", "Forrás típusa")

INMATES = [
    ("F-0001", "NY-1001", "Sas Tibor", "Szeged", "1988-05-12", "Kerekes Ilona", "01"),
    ("F-0002", "NY-1002", "Sas Gábor", "Szeged", "1991-09-30", "Kerekes Ilona", "02"),
    ("F-0003", "NY-1003", "Major Ede", "Pécs", "1979-01-20", "Lengyel Rita", "01"),
    ("F-0004", "NY-1004", "Illés Dénes", "Debrecen", "1985-07-07", "Fodor Edit", "02"),
    ("F-0005", "NY-1005", "Bodó Ákos", "Győr", "1993-03-03", "Hajdu Klára", "01"),
    ("F-0006", "NY-1006", "Varga Péter", "Eger", "1990-12-24", "Simon Zsuzsa", "02"),
]

PLACEMENTS = [
    ("E-0001", "NY-1001", "Sas Tibor", "01", "A objektum", "B részleg", "102", "2026-02-01",
     "2026-04-30", "BEF", "ATH"),
    ("E-0002", "NY-1003", "Major Ede", "01", "A objektum", "B részleg", "102", "2026-03-15",
     "2026-06-01", "ATH", "FEGY"),
    ("E-0003", "NY-1001", "Sas Tibor", "01", "A objektum", "C részleg", "214", "2026-04-30",
     "", "ATH", ""),
    ("E-0004", "NY-1002", "Sas Gábor", "02", "Fő épület", "I. részleg", "17", "2025-11-02",
     "", "BEF", ""),
    ("E-0005", "NY-1005", "Bodó Ákos", "01", "A objektum", "B részleg", "103", "2026-01-10",
     "", "BEF", ""),
]

CONTACT_ROWS = [
    # The brothers' mother, twice, with the same four natural identifiers.
    ("K-0001", "NY-1001", "Sas Tibor", "Kerekes Ilona", "Szeged", "1962-04-11",
     "Bodnár Rozália", "ANYA", "+36 30 555 1111", "kerekes.ilona@example.hu",
     "6720 Szeged, Tisza utca 4.", "2025-01-01", "", "01"),
    ("K-0002", "NY-1002", "Sas Gábor", "Kerekes Ilona", "Szeged", "1962. 04. 11.",
     "Bodnár Rozália", "ANYA", "06 30 555 1111", "", "6720 Szeged, Tisza utca 4.",
     "2025-03-01", "", "02"),
    # Namesakes with different birth dates.
    ("K-0003", "NY-1003", "Major Ede", "Nagy Erzsébet", "Pécs", "1975-01-01", "Kiss Anna",
     "ELT", "+36 70 123 4567", "", "", "2025-06-01", "", "01"),
    ("K-0004", "NY-1004", "Illés Dénes", "Nagy Erzsébet", "Pécs", "1980-06-06", "Kiss Anna",
     "TESTV", "+36 70 765 4321", "", "", "2025-06-01", "", "02"),
    # One phone, the second record without birth data.
    ("K-0005", "NY-1005", "Bodó Ákos", "Fekete Judit", "Győr", "1970-02-02", "Tóth Éva",
     "ISM", "+36 20 777 2222", "", "", "2025-02-01", "", "01"),
    ("K-0006", "NY-1006", "Varga Péter", "Fekete Judit", "", "", "", "ISM", "+36 20 777 2222",
     "", "", "2025-09-01", "", "02"),
]

INTEL_ROWS = [
    ("N-0001", "2026/14/1", "2026-03-02", "01", "Sas Tibor", "NY-1001", "KAB",
     "A következő látogatáskor kábítószert akarnak bejuttatni.", "Bodó Ákos", "NY-1005",
     "FOGV"),
    ("N-0002", "2026/14/2", "2026-03-20", "01", "Major Ede", "NY-1003", "MOB",
     "A zárkában mobiltelefont rejtenek.", "Bodó Ákos", "NY-1005", "FOGV"),
    ("N-0003", "2026/14/3", "2026-04-05", "02", "Sas Gábor", "NY-1002", "SZOK",
     "Szökési kísérletet terveznek a sétaidőben.", "Rigó Lajos", "", "KT"),
    ("N-0004", "2026/14/3", "2026-04-05", "02", "Illés Dénes", "NY-1004", "SZOK",
     "Szökési kísérletet terveznek a sétaidőben.", "Rigó Lajos", "", "KT"),
    ("N-0005", "2026/14/4", "2026-05-10", "02", "Varga Péter", "NY-1006", "KORR",
     "Pénzért csomagot juttatnak be neki.", "Dr. Fehér Gábor", "", "TARS"),
    ("N-0006", "2026/14/5", "2026-06-01", "01", "Sas Tibor", "NY-1001", "ERO",
     "Zárkatársát fenyegeti.", "Hollós Péter", "", "ALL"),
]


def exports() -> dict[str, tuple[list[str], list[list[str]]]]:
    """{source id: (header, rows)} of the synthetic export of AS_OF."""
    return {
        "fonix_base": (FONIX, [list(r) + [""] for r in INMATES]),
        "placement": (PLACEMENT, [list(r) + [""] for r in PLACEMENTS]),
        "contacts": (CONTACTS, [list(r) + [""] for r in CONTACT_ROWS]),
        "intel_log": (INTEL, [list(r) + [""] for r in INTEL_ROWS]),
    }


def to_csv(header, rows) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue().encode("utf-8-sig")


def to_xlsx(header, rows) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Export"
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out")
    ap.add_argument("--xlsx", action="store_true", help="XLSX instead of CSV")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    for source, (header, rows) in exports().items():
        ext = "xlsx" if args.xlsx else "csv"
        path = os.path.join(args.out, "%s_%s.%s" % (source, AS_OF, ext))
        with open(path, "wb") as f:
            f.write((to_xlsx if args.xlsx else to_csv)(header, rows))
        print(path)


if __name__ == "__main__":
    main()
