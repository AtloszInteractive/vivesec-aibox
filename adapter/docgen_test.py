"""Unit tests for docgen -- the /ui/save renderers. No service needed:

    python adapter/docgen_test.py
"""
import io
import os
import posixpath
import re
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import docgen


class FormatNameTest(unittest.TestCase):
    def test_aliases_and_unknown(self):
        self.assertEqual("md", docgen.normalize_format("Markdown"))
        self.assertEqual("txt", docgen.normalize_format(".TXT"))
        self.assertEqual("pptx", docgen.normalize_format("pptx"))
        self.assertIsNone(docgen.normalize_format("docx"))
        self.assertIsNone(docgen.normalize_format(""))

    def test_extension_is_forced_and_not_doubled(self):
        self.assertEqual("memo.pdf", docgen.with_extension("memo.md", "pdf"))
        self.assertEqual("memo.pdf", docgen.with_extension("memo.pdf", "pdf"))
        self.assertEqual("deck.pptx", docgen.with_extension("deck", "pptx"))
        self.assertEqual("document.md", docgen.with_extension("  ", "md"))

    def test_dotted_name_keeps_its_stem(self):
        self.assertEqual("q4.2026 report.pdf",
                         docgen.with_extension("q4.2026 report", "pdf"))


class MarkdownParsingTest(unittest.TestCase):
    def test_inline_markers_stripped(self):
        self.assertEqual("bold and code",
                         docgen.strip_markers("**bold** and `code`"))

    def test_headings_become_blocks(self):
        blocks = docgen.parse_blocks("# Title\n\n## Sub\nbody")
        self.assertEqual([("h1", "Title"), ("", ""), ("h2", "Sub"), ("", "body")],
                         blocks)

    def test_slides_split_on_second_level_headings(self):
        title, slides = docgen.parse_slides(
            "# Deck\n\n## Slide 1: Intro\n- one\n- two\n\n## Slide 2: Numbers\n42% growth")
        self.assertEqual("Deck", title)
        self.assertEqual(["Intro", "Numbers"], [s["title"] for s in slides])
        self.assertEqual(["one", "two"], slides[0]["bullets"])
        self.assertEqual(["42% growth"], slides[1]["body"])

    def test_text_without_headings_still_yields_one_slide(self):
        _, slides = docgen.parse_slides("just a paragraph")
        self.assertEqual(1, len(slides))
        self.assertEqual(["just a paragraph"], slides[0]["body"])


class ChartParsingTest(unittest.TestCase):
    def test_data_line_becomes_points_and_unit(self):
        chart = docgen.parse_chart(
            "Chart: Q1 2026 = 13.2 | Q2 2026 = EUR 14.7 million | unit: EUR million")
        self.assertEqual("EUR million", chart["unit"])
        self.assertEqual(["Q1 2026", "Q2 2026"], [p["label"] for p in chart["points"]])
        self.assertEqual([13.2, 14.7], [p["value"] for p in chart["points"]])

    def test_european_decimals_and_negative_values(self):
        chart = docgen.parse_chart("Chart: net debt = -2,9 | ebitda = 1.234,5")
        self.assertEqual([-2.9, 1234.5], [p["value"] for p in chart["points"]])

    def test_a_single_figure_is_not_a_chart(self):
        self.assertIsNone(docgen.parse_chart("Chart: Q2 = 14.7"))
        self.assertIsNone(docgen.parse_chart("- suggested visuals: a bar chart"))

    def test_a_value_cut_off_mid_number_is_dropped(self):
        # The generation length cap truncates the answer mid-line; "99." must
        # not become a plausible-looking 99 bar.
        chart = docgen.parse_chart(
            "Chart: Q1 = 99.8% | Q2 = 99.87% | Q3 = 99.")
        self.assertEqual(["Q1", "Q2"], [p["label"] for p in chart["points"]])

    def test_chart_line_leaves_the_bullet_list(self):
        _, slides = docgen.parse_slides(
            "## Slide 1: Revenue\n- grounded claim\n- Chart: Q1 = 13.2 | Q2 = 14.7")
        self.assertEqual(["grounded claim"], slides[0]["bullets"])
        self.assertEqual(2, len(slides[0]["chart"]["points"]))


class PdfTest(unittest.TestCase):
    def test_structure_and_trailer(self):
        pdf = docgen.render_pdf("# Report\n\nline one\nline two")
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertIn(b"/Type /Catalog", pdf)
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))

    def test_xref_offsets_point_at_their_objects(self):
        pdf = docgen.render_pdf("hello")
        start = pdf.rindex(b"startxref\n")
        xref_at = int(pdf[start + 10:pdf.index(b"\n", start + 10)])
        self.assertEqual(b"xref", pdf[xref_at:xref_at + 4])
        first = pdf.index(b"1 0 obj")
        self.assertIn(b"%010d 00000 n " % first, pdf)

    def test_long_text_paginates(self):
        pdf = docgen.render_pdf("\n".join("line %d" % i for i in range(200)))
        self.assertIn(b"/Count 4", pdf)

    def test_hungarian_double_acute_uses_the_differences_encoding(self):
        pdf = docgen.render_pdf("k\u0151olaj \u0171rlap")
        self.assertIn(b"/Differences [", pdf)
        self.assertIn(b"ohungarumlaut", pdf)
        self.assertIn(bytes([0x8D]), pdf)  # not degraded to '?'

    def test_parentheses_are_escaped(self):
        pdf = docgen.render_pdf("total (net) 5")
        self.assertIn(b"total \\(net\\) 5", pdf)


class PptxTest(unittest.TestCase):
    def _open(self, blob):
        return zipfile.ZipFile(io.BytesIO(blob))

    def test_package_has_the_required_parts(self):
        z = self._open(docgen.render_pptx("# Deck\n\n## Slide 1: Intro\n- one"))
        names = set(z.namelist())
        for part in ("[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml",
                     "ppt/_rels/presentation.xml.rels",
                     "ppt/slideMasters/slideMaster1.xml",
                     "ppt/slideLayouts/slideLayout1.xml", "ppt/theme/theme1.xml",
                     "ppt/slides/slide1.xml", "ppt/slides/_rels/slide1.xml.rels"):
            self.assertIn(part, names)
        self.assertIsNone(z.testzip())

    def test_one_slide_per_heading_with_its_text(self):
        blob = docgen.render_pptx(
            "# Deck\n\n## Slide 1: Intro\n- first\n\n## Slide 2: Close\n- last")
        z = self._open(blob)
        self.assertIn("ppt/slides/slide2.xml", z.namelist())
        self.assertIn("<a:t>Intro</a:t>", z.read("ppt/slides/slide1.xml").decode())
        self.assertIn("<a:t>first</a:t>", z.read("ppt/slides/slide1.xml").decode())
        self.assertIn("<a:t>last</a:t>", z.read("ppt/slides/slide2.xml").decode())
        rels = z.read("ppt/_rels/presentation.xml.rels").decode()
        self.assertIn("slides/slide2.xml", rels)

    def test_xml_special_characters_are_escaped(self):
        z = self._open(docgen.render_pptx("## Q1\n- margin < 5% & rising"))
        body = z.read("ppt/slides/slide1.xml").decode("utf-8")
        self.assertIn("margin &lt; 5% &amp; rising", body)

    def test_16_9_slide_size(self):
        z = self._open(docgen.render_pptx("## One"))
        self.assertIn('cx="12192000" cy="6858000"',
                      z.read("ppt/presentation.xml").decode())

    def test_notes_size_is_in_the_presentationml_namespace(self):
        # Google Slides' importer reads notesSz and rejects the file when it is
        # not <p:notesSz> ("Nem sikerült megnyitni a fájlt", punchNotesSize).
        pres = self._open(docgen.render_pptx("## One")).read(
            "ppt/presentation.xml").decode()
        self.assertIn('<p:notesSz cx="6858000" cy="9144000"/>', pres)
        self.assertNotIn("a:notesSz", pres)

    def test_every_relationship_target_exists(self):
        z = self._open(docgen.render_pptx("## One\n- a\n\n## Two\n- b"))
        names = set(z.namelist())
        for part in names:
            if not part.endswith(".rels"):
                continue
            base = posixpath.dirname(posixpath.dirname(part))
            for target in re.findall(r'Target="([^"]+)"',
                                     z.read(part).decode()):
                resolved = posixpath.normpath(posixpath.join(base, target))
                self.assertIn(resolved, names, "%s -> %s" % (part, target))

    def test_chart_line_is_drawn_as_bars(self):
        z = self._open(docgen.render_pptx(
            "## Revenue\n- Chart: Q1 = 13.2 | Q2 = 14.7 | unit: EUR million"))
        slide = z.read("ppt/slides/slide1.xml").decode()
        self.assertEqual(2, slide.count('name="Bar '))
        self.assertIn("A6E22E", slide)
        self.assertIn("<a:t>13.2</a:t>", slide)
        self.assertIn("<a:t>Q2</a:t>", slide)
        self.assertIn("<a:t>EUR million</a:t>", slide)
        self.assertNotIn("<a:t>Chart:", slide)

    def test_a_narrow_band_far_from_zero_crops_the_axis(self):
        z = self._open(docgen.render_pptx(
            "## Availability\n- Chart: Q1 = 99.8 | Q2 = 99.87 | Q3 = 99.79 | unit: %"))
        slide = z.read("ppt/slides/slide1.xml").decode()
        self.assertIn("axis starts at", slide)

    def test_a_zero_based_range_keeps_the_zero_axis(self):
        z = self._open(docgen.render_pptx(
            "## Interventions\n- Chart: Q1 = 17 | Q2 = 20 | Q3 = 42"))
        self.assertNotIn("axis starts at",
                         z.read("ppt/slides/slide1.xml").decode())


class RenderTest(unittest.TestCase):
    def test_md_is_verbatim_and_txt_is_stripped(self):
        blob, fmt = docgen.render("**hi** there", "md")
        self.assertEqual(("**hi** there", "md"), (blob.decode("utf-8"), fmt))
        blob, fmt = docgen.render("**hi** there", "txt")
        self.assertEqual(("hi there", "txt"), (blob.decode("utf-8"), fmt))

    def test_unknown_format_falls_back_to_md(self):
        blob, fmt = docgen.render("x", "docx")
        self.assertEqual(("x", "md"), (blob.decode("utf-8"), fmt))

    def test_utf8_round_trip_for_md(self):
        blob, _ = docgen.render("\u0151rl\u0151 \u00e1rv\u00edzt\u0171r\u0151", "md")
        self.assertEqual("\u0151rl\u0151 \u00e1rv\u00edzt\u0171r\u0151",
                         blob.decode("utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
