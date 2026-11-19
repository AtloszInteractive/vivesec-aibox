"""Structured export formats (E28): every reader gives the same header and
rows, and a file with several tables is read where the source's columns are."""
import io
import json
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import structured_import  # noqa: E402
from structured_import import StructuredImportError, read_table  # noqa: E402

EXPECTED = ["Azonosító", "Név", "Születési dátum"]
WANT = (["Azonosító", "Név", "Születési dátum"],
        [["1", "O'Brien Kiss", "1980-01-02"], ["2", "Nagy Éva", ""]])


def body(header_rows):
    header, rows = header_rows
    return header, [cells for _, cells in rows]


class SqlTest(unittest.TestCase):
    def test_mysql_dump_with_two_tables_reads_the_matching_one(self):
        dump = """-- MySQL dump 10.13
/*!40101 SET NAMES utf8mb4 */;
# a hash comment
CREATE TABLE `naplo` (`id` int, `esemeny` text, PRIMARY KEY (`id`)) ENGINE=InnoDB;
INSERT INTO `naplo` VALUES (1,'belépés; ok'),(2,'kilépés');
CREATE TABLE IF NOT EXISTS `szemely` (
  `Azonosító` int NOT NULL,
  `Név` varchar(80) DEFAULT NULL,
  `Születési dátum` date,
  UNIQUE KEY `u` (`Név`)
) ENGINE=InnoDB;
INSERT INTO `szemely` VALUES (1,'O\\'Brien  Kiss','1980-01-02'),(2,'Nagy Éva',NULL);
"""
        self.assertEqual(WANT, body(read_table(dump.encode(), "export.sql", None, EXPECTED)))

    def test_postgres_copy_and_inserts(self):
        dump = """CREATE TABLE public.szemely (
    "Azonosító" integer NOT NULL,
    "Név" text,
    "Születési dátum" date,
    CONSTRAINT pk PRIMARY KEY ("Azonosító")
);
COPY public.szemely ("Azonosító", "Név", "Születési dátum") FROM stdin;
1\tO'Brien Kiss\t1980-01-02
\\.
INSERT INTO public.szemely VALUES (2, 'Nagy Éva', NULL);
"""
        self.assertEqual(WANT, body(read_table(dump.encode(), "pg.sql")))

    def test_sql_server_script(self):
        script = """SET IDENTITY_INSERT [dbo].[szemely] ON
GO
INSERT [dbo].[szemely] ([Azonosító], [Név], [Születési dátum]) VALUES (1, N'O''Brien Kiss', CAST(N'1980-01-02' AS Date))
GO
INSERT [dbo].[szemely] ([Azonosító], [Név], [Születési dátum]) VALUES (2, N'Nagy Éva', NULL)
GO
"""
        self.assertEqual(WANT, body(read_table(script.encode("utf-8-sig"), "mssql.sql")))

    def test_nothing_to_read_is_an_error(self):
        with self.assertRaises(StructuredImportError):
            read_table(b"DROP TABLE x; SELECT 1;", "x.sql")
        with self.assertRaises(StructuredImportError):
            read_table(b"INSERT INTO t VALUES (1, 'a');", "x.sql")  # no columns anywhere
        with self.assertRaises(StructuredImportError):
            read_table(b"INSERT INTO t (a) VALUES ('never closed);", "x.sql")

    def test_statements_without_semicolons_or_go_each_count(self):
        script = ("SET NOCOUNT ON\nINSERT INTO t (a, b) VALUES (1, 'x')\n"
                  "INSERT [dbo].[t] ([a], [b]) VALUES (2, N'y')\n"
                  "INSERT INTO t (a, b) VALUES (3, 'z')\nGO\n")
        self.assertEqual(["1", "2", "3"], [c[0] for c in body(read_table(script.encode(),
                                                                       "x.sql"))[1]])

    def test_hostile_input_stays_linear(self):
        import time
        for raw in (b"INSERT INTO t (a) VALUES (" + b"1" * 20000 + b" x);",
                    b"CREATE TABLE t (a int);" + b"/* " * 100000,
                    b"INSERT INTO t (a) VALUES ('" + b"\\'" * 50000 + b");"):
            started = time.monotonic()
            try:
                read_table(raw, "x.sql")
            except StructuredImportError:
                pass
            self.assertLess(time.monotonic() - started, 5)


class DocumentFormatsTest(unittest.TestCase):
    RECORDS = [{"Azonosító": 1, "Név": "O'Brien Kiss", "Születési dátum": "1980-01-02"},
               {"Azonosító": 2, "Név": "Nagy Éva", "Születési dátum": None}]

    def test_json_list_object_of_lists_and_lines(self):
        for raw, name in ((json.dumps(self.RECORDS), "a.json"),
                          (json.dumps({"meta": [{"x": 1}], "szemelyek": self.RECORDS}), "b.json"),
                          ("\n".join(json.dumps(r) for r in self.RECORDS), "c.jsonl")):
            with self.subTest(name=name):
                self.assertEqual(WANT, body(read_table(raw.encode(), name, None, EXPECTED)))

    def test_xml_record_elements(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<export xmlns="urn:x"><fejlec><datum>2026-11-01</datum></fejlec>
  <szemely Azonosító="1"><Név>O'Brien Kiss</Név><Születési_dátum>1980-01-02</Születési_dátum></szemely>
  <szemely Azonosító="2"><Név>Nagy Éva</Név></szemely>
</export>"""
        header, rows = read_table(xml.encode(), "x.xml", None, ["Azonosító", "Név"])
        self.assertEqual(["Azonosító", "Név", "Születési_dátum"], header)
        self.assertEqual([["1", "O'Brien Kiss", "1980-01-02"], ["2", "Nagy Éva", ""]],
                         [cells for _, cells in rows])

    def test_xml_entities_are_refused(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x><r><n>&a;</n></r></x>'
        with self.assertRaises(StructuredImportError):
            read_table(bomb, "x.xml")

    def test_ods_with_repeated_cells(self):
        content = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:spreadsheet>
<table:table table:name="Lap1">
 <table:table-row><table:table-cell><text:p>Azonosító</text:p></table:table-cell>
  <table:table-cell><text:p>Név</text:p></table:table-cell>
  <table:table-cell><text:p>Születési dátum</text:p></table:table-cell>
  <table:table-cell table:number-columns-repeated="16000"/></table:table-row>
 <table:table-row><table:table-cell office:value-type="float" office:value="1"><text:p>1</text:p></table:table-cell>
  <table:table-cell><text:p>O'Brien Kiss</text:p></table:table-cell>
  <table:table-cell office:value-type="date" office:date-value="1980-01-02T00:00:00"><text:p>1980.01.02.</text:p></table:table-cell></table:table-row>
 <table:table-row><table:table-cell office:value-type="float" office:value="2"/>
  <table:table-cell><text:p>Nagy Éva</text:p></table:table-cell></table:table-row>
 <table:table-row table:number-rows-repeated="1048000"><table:table-cell table:number-columns-repeated="3"/></table:table-row>
</table:table></office:spreadsheet></office:body></office:document-content>"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
            z.writestr("content.xml", content)
        header, rows = read_table(buf.getvalue(), "x.ods")
        self.assertEqual(WANT[0], header)
        self.assertEqual([["1", "O'Brien Kiss", "1980-01-02"], ["2", "Nagy Éva"]],
                         [cells for _, cells in rows])

    @unittest.skipUnless(__import__("importlib").util.find_spec("xlwt"), "needs xlwt")
    def test_legacy_excel(self):
        import datetime
        import xlwt
        book = xlwt.Workbook()
        sheet = book.add_sheet("Lap1")
        for c, h in enumerate(WANT[0]):
            sheet.write(0, c, h)
        sheet.write(1, 0, 1)
        sheet.write(1, 1, "O'Brien Kiss")
        sheet.write(1, 2, datetime.date(1980, 1, 2), xlwt.easyxf(num_format_str="YYYY-MM-DD"))
        sheet.write(2, 0, 2)
        sheet.write(2, 1, "Nagy Éva")
        buf = io.BytesIO()
        book.save(buf)
        header, rows = read_table(buf.getvalue(), "x.xls")
        self.assertEqual(WANT[0], header)
        self.assertEqual([["1", "O'Brien Kiss", "1980-01-02"], ["2", "Nagy Éva", ""]],
                         [cells for _, cells in rows])


class PickTest(unittest.TestCase):
    TWO = json.dumps({"a": [{"x": 1, "Név": "A"}], "b": [{"y": 2, "Név": "B"}]}).encode()

    def test_an_unclear_choice_is_refused_and_names_the_tables(self):
        with self.assertRaises(StructuredImportError) as e:
            read_table(self.TWO, "x.json", None, ["Név"])
        self.assertIn("a, b", str(e.exception))

    def test_the_profile_names_the_table(self):
        self.assertEqual(["y", "Név"], read_table(self.TWO, "x.json", "B")[0])
        with self.assertRaises(StructuredImportError):
            read_table(self.TWO, "x.json", "c")
        # A sheet name set for the spreadsheet export does not stop a CSV one.
        self.assertEqual(["a", "b"], read_table(b"a;b\n1;2\n", "x.csv", "Munka1")[0])

    def test_unknown_type(self):
        with self.assertRaises(StructuredImportError):
            read_table(b"x", "x.pdf")

    def test_source_columns_cover_the_mapping(self):
        source = {"entities": [{"name": "Név", "attributes": {"birth_date": "Szül"}}],
                  "relations": [{"start": "Kezd", "end": "", "label": ["Ok"]}],
                  "record_id": ["Azon"], "deleted": {"column": "Törölve"}}
        self.assertEqual({"Név", "Szül", "Kezd", "Ok", "Azon", "Törölve"},
                         structured_import.source_columns(source))


if __name__ == "__main__":
    unittest.main()
