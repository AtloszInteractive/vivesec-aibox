"""Generated-document rendering for /api/v1/ui/save (aibox_more3 sec 1).

The UI hands us the answer as markdown-ish text; the user picks the format the
file should land on the drive in. Everything here is PURE STDLIB (the adapter
image has no pip packages): the PDF is written by hand and DOCX/PPTX are
OOXML packages built with zipfile.

Formats: 'md' (verbatim), 'txt' (markers stripped), 'pdf', 'docx', 'pptx'.

PDF caveat carried over from demo-corpus/lib/writers.py: the standard-14 fonts
use WinAnsi (cp1252), which has no Hungarian o-double-acute / u-double-acute.
Those four characters are mapped onto cp1252's undefined code points and
declared in a /Differences encoding, and the substitution happens at BYTE level
because chr(0x8D).encode('cp1252') would itself become '?'.
"""
import re
import textwrap
import zipfile
from xml.sax.saxutils import escape

FORMATS = ("md", "txt", "pdf", "docx", "pptx")

_EXT = {"md": ".md", "txt": ".txt", "pdf": ".pdf", "docx": ".docx",
    "pptx": ".pptx"}

_CONTENT_TYPE = {
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "pdf": "application/pdf",
    "docx": ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document"),
    "pptx": ("application/vnd.openxmlformats-officedocument"
             ".presentationml.presentation"),
}


def normalize_format(value):
    """Accepted format name, or None when unknown/absent (-> 'md')."""
    name = (value or "").strip().lower().lstrip(".")
    if name == "markdown":
        name = "md"
    if name == "text":
        name = "txt"
    return name if name in FORMATS else None


def content_type(fmt):
    return _CONTENT_TYPE.get(fmt, "application/octet-stream")


def with_extension(name, fmt):
    """Force the file name to carry the extension of the chosen format."""
    base = (name or "").strip() or "document"
    for ext in _EXT.values():
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break
    return base + _EXT.get(fmt, ".md")


# ---------------------------------------------------------------------------
# markdown -> blocks
# ---------------------------------------------------------------------------

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`]+)`")


def strip_markers(text):
    """Inline markdown markers removed; the layout stays line-based."""
    out = _BOLD_RE.sub(r"\1", text or "")
    out = _ITALIC_RE.sub(r"\1", out)
    return _CODE_RE.sub(r"\1", out)


def parse_blocks(text):
    """(kind, text) blocks for the PDF writer; kind in 'h1'|'h2'|'mono'|''."""
    blocks = []
    fenced = False
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            blocks.append(("mono", raw.rstrip()))
            continue
        if line.startswith("### "):
            blocks.append(("h2", strip_markers(line[4:])))
        elif line.startswith("## "):
            blocks.append(("h2", strip_markers(line[3:])))
        elif line.startswith("# "):
            blocks.append(("h1", strip_markers(line[2:])))
        elif line.startswith("|") and line.endswith("|"):
            blocks.append(("mono", strip_markers(line)))
        else:
            blocks.append(("", strip_markers(line)))
    return blocks


# ---------------------------------------------------------------------------
# docx (minimal OOXML document)
# ---------------------------------------------------------------------------

_DOCX_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

_DOCX_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""

_DOCX_DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_DOCX_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Normal" w:default="1"><w:name w:val="Normal"/><w:rPr><w:sz w:val="22"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="200" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>
</w:styles>"""


def _docx_core(title):
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>%s</dc:title><dc:creator>ViVeSec AI</dc:creator>'
        '</cp:coreProperties>' % escape(title or "Document"))


def _docx_paragraph(kind, text):
    style = {"h1": "Heading1", "h2": "Heading2"}.get(kind)
    ppr = '<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style if style else ""
    clean = text or ""
    if kind == "" and clean.startswith(("- ", "* ")):
        clean = "\u2022 " + clean[2:].strip()
    run_props = '<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/></w:rPr>' if kind == "mono" else ""
    if not clean:
        return "<w:p>%s</w:p>" % ppr
    return ('<w:p>%s<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p>'
            % (ppr, run_props, escape(clean)))


def render_docx(text, title=""):
    """Markdown-ish text -> a portable .docx package (bytes)."""
    import io

    blocks = parse_blocks(text)
    if title and not any(kind == "h1" for kind, _ in blocks):
        blocks.insert(0, ("h1", title))
    body = "".join(_docx_paragraph(kind, value) for kind, value in blocks)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>%s<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/>'
        '</w:sectPr></w:body></w:document>' % body)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        z.writestr("_rels/.rels", _DOCX_RELS)
        z.writestr("docProps/core.xml", _docx_core(title))
        z.writestr("word/_rels/document.xml.rels", _DOCX_DOCUMENT_RELS)
        z.writestr("word/styles.xml", _DOCX_STYLES)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


_CHART_RE = re.compile(r"^chart\s*[:\-\u2013]\s*(.+)$", re.I)
# The model likes to announce the data line with an empty label line first.
_CHART_HEADER_RE = re.compile(r"^chart\s*[:\-\u2013]?\s*$", re.I)
_CHART_KIND_RE = re.compile(r"^(bar|column|bar chart|column chart|chart)$", re.I)
_CHART_NUM_RE = re.compile(r"-?\d[\d\s.,]*")


def _chart_value(text):
    """First number in a cell ('EUR 14.7 million' -> 14.7); EU and US decimals."""
    m = _CHART_NUM_RE.search(text or "")
    if not m:
        return None
    raw = re.sub(r"\s", "", m.group(0)).rstrip(".,")
    if re.search(r",\d{1,2}$", raw):
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_chart(line):
    """'Chart: Q1 = 13.2 | Q2 = 14.7 | unit: EUR million' -> plottable points.

    The deck only writes this line when the answer carried comparable figures
    that were already checked against the sources (adapter/llm.py).
    """
    m = _CHART_RE.match(strip_markers(line or "").strip())
    if not m:
        return None
    unit = ""
    points = []
    for cell in [c.strip() for c in m.group(1).split("|")]:
        if not cell or _CHART_KIND_RE.match(cell):
            continue
        u = re.match(r"^unit\s*[:=]\s*(.+)$", cell, re.I)
        if u:
            unit = u.group(1).strip()
            continue
        kv = re.match(r"^(.+?)\s*[=:]\s*(.+)$", cell)
        if not kv:
            continue
        display = kv.group(2).strip()
        # "Q4 2025 = 99." — the generation cap cut the number in half; 99
        # would be a plausible but wrong bar.
        if re.search(r"\d[.,]\s*$", display):
            continue
        value = _chart_value(display)
        if value is None:
            continue
        points.append({"label": kv.group(1).strip(),
                       "display": display, "value": value})
    if len(points) < 2:
        return None
    return {"unit": unit, "points": points[:8]}


def parse_slides(text):
    """Markdown -> [{'title', 'bullets': [...], 'body': [...], 'chart': ...}].

    A '## ' heading starts a slide (the deck writes '## Slide N: Title'); the
    leading '# ' title becomes the cover slide. Text without any heading ends
    up as a single slide so nothing is lost.
    """
    title = ""
    slides = []
    current = None
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if line.startswith("## "):
            head = strip_markers(line[3:]).strip()
            head = re.sub(r"^Slide\s+\d+\s*[:.\-]\s*", "", head)
            current = {"title": head, "bullets": [], "body": [], "chart": None}
            slides.append(current)
            continue
        if line.startswith("# ") and current is None:
            title = strip_markers(line[2:]).strip()
            continue
        if not line:
            continue
        clean = strip_markers(line)
        if current is None:
            current = {"title": title or "Slide 1", "bullets": [], "body": [],
                       "chart": None}
            slides.append(current)
        if clean.startswith(("- ", "* ")):
            clean = clean[2:].strip()
            if _CHART_HEADER_RE.match(clean):
                continue
            chart = parse_chart(clean)
            if chart and not current["chart"]:
                current["chart"] = chart
            else:
                current["bullets"].append(clean)
        else:
            if _CHART_HEADER_RE.match(clean):
                continue
            chart = parse_chart(clean)
            if chart and not current["chart"]:
                current["chart"] = chart
            else:
                current["body"].append(clean)
    if not slides:
        slides = [{"title": title or "Slide 1", "bullets": [], "body": [],
                   "chart": None}]
    return title, slides


# ---------------------------------------------------------------------------
# pdf
# ---------------------------------------------------------------------------

_PAGE_W, _PAGE_H = 595, 842  # A4 in points
_MARGIN_X, _MARGIN_TOP = 56, 786
_LEADING = 14
_LINES_PER_PAGE = 50
_WRAP = 92

# cp1252 leaves 0x81/0x8D/0x8F/0x90 undefined -> reuse them for the Hungarian
# double-acute letters and declare the mapping in the font encoding.
_EXTRA_GLYPHS = {
    "\u0150": (0x81, "Ohungarumlaut"),
    "\u0151": (0x8D, "ohungarumlaut"),
    "\u0170": (0x8F, "Uhungarumlaut"),
    "\u0171": (0x90, "uhungarumlaut"),
}


def _pdf_escape(text):
    out = bytearray()
    for char in text:
        if char in _EXTRA_GLYPHS:
            raw = bytes([_EXTRA_GLYPHS[char][0]])
        else:
            raw = char.encode("cp1252", "replace")
        for byte in raw:
            if byte in (0x28, 0x29, 0x5C):  # ( ) \
                out.append(0x5C)
            out.append(byte)
    return bytes(out)


def _layout_lines(blocks):
    flowed = []
    for kind, text in blocks:
        if kind == "pagebreak":
            flowed.append(("pagebreak", ""))
            continue
        if not text:
            flowed.append(("", ""))
            continue
        width = 74 if kind in ("h1", "h2") else _WRAP
        for line in textwrap.wrap(text, width=width) or [""]:
            flowed.append((kind, line))
        if kind in ("h1", "h2"):
            flowed.append(("", ""))
    pages = [[]]
    for item in flowed:
        if item[0] == "pagebreak" or len(pages[-1]) >= _LINES_PER_PAGE:
            if pages[-1]:
                pages.append([])
            if item[0] == "pagebreak":
                continue
        pages[-1].append(item)
    return [p for p in pages if p] or [[("", "")]]


def _pdf_page_stream(lines, page_no, total, footer):
    parts = [b"BT\n"]
    y = _MARGIN_TOP
    for kind, text in lines:
        if kind == "h1":
            font, size = b"/F2", 15
        elif kind == "h2":
            font, size = b"/F2", 12
        elif kind == "mono":
            font, size = b"/F3", 9
        else:
            font, size = b"/F1", 10
        parts.append(b"%s %d Tf\n" % (font, size))
        parts.append(b"1 0 0 1 %d %d Tm\n" % (_MARGIN_X, y))
        parts.append(b"(%s) Tj\n" % _pdf_escape(text))
        y -= _LEADING
    parts.append(b"/F1 8 Tf\n1 0 0 1 %d %d Tm\n(%s) Tj\n"
                 % (_MARGIN_X, 40,
                    _pdf_escape("%s  |  page %d of %d" % (footer, page_no, total))))
    parts.append(b"ET\n")
    return b"".join(parts)


def _build_pdf(objects):
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref_at))
    return bytes(out)


def render_pdf(text, title="", footer=""):
    """Markdown-ish text -> a text-extractable A4 PDF (bytes)."""
    blocks = parse_blocks(text)
    if title:
        blocks = [("h1", title), ("", "")] + blocks
    pages = _layout_lines(blocks)
    total = len(pages)
    footer = footer or title or "ViVeSec AI Box"

    n_pages = len(pages)
    page_ids = list(range(3, 3 + n_pages))
    content_ids = list(range(3 + n_pages, 3 + 2 * n_pages))
    encoding_id = 3 + 2 * n_pages
    font_ids = [encoding_id + 1, encoding_id + 2, encoding_id + 3]

    objects = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = b" ".join(b"%d 0 R" % pid for pid in page_ids)
    objects.append(b"<< /Type /Pages /Count %d /Kids [%s] >>" % (n_pages, kids))
    for idx in range(n_pages):
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
            b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R /F3 %d 0 R >> >> "
            b"/Contents %d 0 R >>"
            % (_PAGE_W, _PAGE_H, font_ids[0], font_ids[1], font_ids[2],
               content_ids[idx]))
    for idx, lines in enumerate(pages):
        stream = _pdf_page_stream(lines, idx + 1, total, footer)
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream
                       + b"endstream")
    differences = b" ".join(b"%d /%s" % (code, name.encode("ascii"))
                            for code, name in sorted(_EXTRA_GLYPHS.values()))
    objects.append(b"<< /Type /Encoding /BaseEncoding /WinAnsiEncoding "
                   b"/Differences [%s] >>" % differences)
    for base in (b"/Helvetica", b"/Helvetica-Bold", b"/Courier"):
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont %s "
                       b"/Encoding %d 0 R >>" % (base, encoding_id))
    return _build_pdf(objects)


# ---------------------------------------------------------------------------
# pptx (minimal OOXML presentation, 16:9)
# ---------------------------------------------------------------------------

_EMU_W, _EMU_H = 12192000, 6858000  # 13.333in x 7.5in = 16:9
_NOTES_W, _NOTES_H = 6858000, 9144000  # the portrait notes page PowerPoint writes

_PPTX_CONTENT_TYPES_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
    '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
    '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
    '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
)

_PPTX_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    "</Relationships>"
)

_PPTX_MASTER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    ' xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
    "<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>"
    "<p:grpSpPr/></p:spTree></p:cSld>"
    '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2"'
    ' accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink"'
    ' folHlink="folHlink"/>'
    '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
    "<p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles>"
    "</p:sldMaster>"
)

_PPTX_MASTER_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
    "</Relationships>"
)

_PPTX_LAYOUT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    ' xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank">'
    "<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>"
    "<p:grpSpPr/></p:spTree></p:cSld>"
    '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
    "</p:sldLayout>"
)

_PPTX_LAYOUT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
    "</Relationships>"
)

_PPTX_THEME = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="ViVeSec">'
    "<a:themeElements>"
    '<a:clrScheme name="ViVeSec"><a:dk1><a:srgbClr val="15181B"/></a:dk1>'
    '<a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="1B1F22"/></a:dk2>'
    '<a:lt2><a:srgbClr val="F2F2F2"/></a:lt2><a:accent1><a:srgbClr val="A6E22E"/></a:accent1>'
    '<a:accent2><a:srgbClr val="4FC3F7"/></a:accent2><a:accent3><a:srgbClr val="FFB74D"/></a:accent3>'
    '<a:accent4><a:srgbClr val="E57373"/></a:accent4><a:accent5><a:srgbClr val="9575CD"/></a:accent5>'
    '<a:accent6><a:srgbClr val="4DB6AC"/></a:accent6><a:hlink><a:srgbClr val="0563C1"/></a:hlink>'
    '<a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>'
    '<a:fontScheme name="ViVeSec"><a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/>'
    '<a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/>'
    '<a:cs typeface=""/></a:minorFont></a:fontScheme>'
    '<a:fmtScheme name="ViVeSec"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/>'
    "</a:solidFill></a:fillStyleLst>"
    '<a:lnStyleLst><a:ln><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
    '<a:ln><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
    '<a:ln><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
    "<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>"
    "<a:effectStyle><a:effectLst/></a:effectStyle>"
    "<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>"
    '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>'
    "</a:themeElements></a:theme>"
)


def _pptx_core(title):
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>%s</dc:title><dc:creator>ViVeSec AI Box</dc:creator>"
        "</cp:coreProperties>" % escape(title or "Presentation")
    )


def _pptx_textbox(shape_id, name, x, y, cx, cy, paragraphs, align=None):
    body = []
    for text, size, bold, bullet in paragraphs:
        props = ['<a:pPr marL="%d" indent="%d"%s>'
                 % (285750 if bullet else 0, -285750 if bullet else 0,
                    ' algn="%s"' % align if align else "")]
        props.append('<a:buChar char="\u2022"/>' if bullet else "<a:buNone/>")
        props.append("</a:pPr>")
        body.append(
            "<a:p>%s<a:r><a:rPr lang=\"en-US\" sz=\"%d\"%s dirty=\"0\"/>"
            "<a:t>%s</a:t></a:r></a:p>"
            % ("".join(props), size, ' b="1"' if bold else "", escape(text)))
    if not body:
        body.append("<a:p/>")
    return (
        "<p:sp><p:nvSpPr><p:cNvPr id=\"%d\" name=\"%s\"/>"
        "<p:cNvSpPr txBox=\"1\"/><p:nvPr/></p:nvSpPr>"
        '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        '<p:txBody><a:bodyPr wrap="square"><a:normAutofit/></a:bodyPr><a:lstStyle/>%s</p:txBody>'
        "</p:sp>" % (shape_id, escape(name), x, y, cx, cy, "".join(body))
    )


def _pptx_rect(shape_id, name, x, y, cx, cy, color, alpha=None):
    fill = '<a:srgbClr val="%s">%s</a:srgbClr>' % (
        color, '<a:alpha val="%d"/>' % alpha if alpha is not None else "")
    return (
        "<p:sp><p:nvSpPr><p:cNvPr id=\"%d\" name=\"%s\"/>"
        "<p:cNvSpPr/><p:nvPr/></p:nvSpPr>"
        '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        "<a:solidFill>%s</a:solidFill><a:ln><a:noFill/></a:ln></p:spPr>"
        "<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>"
        % (shape_id, escape(name), x, y, cx, cy, fill)
    )


# Plot box for the optional bar chart, below the title and the bullet body.
_CHART_X, _CHART_Y = 685800, 3000000
_CHART_CX, _CHART_CY = _EMU_W - 1371600, 2100000
_CHART_ACCENT = "A6E22E"


def _trim_number(value):
    """2 decimals at most, without a trailing '.0'."""
    text = "%.2f" % value
    return text.rstrip("0").rstrip(".") or "0"


def chart_scale(values):
    """(floor, ceiling, zoomed) for the bar axis.

    Values clustered far from zero (99.79-99.87%) all render as full-height
    slabs on a zero axis, so there the axis starts at the data instead; the
    slide then states the floor, because a cropped axis exaggerates.
    """
    lo, hi = min(values), max(values)
    spread = hi - lo
    if lo > 0 and spread > 0 and spread < hi * 0.25:
        return max(0.0, lo - spread * 0.6), hi + spread * 0.2, True
    return min(0.0, lo), max(0.0, hi), False


def _pptx_chart(chart, shape_id):
    """Bars for the grounded figures on this slide -> (shapes, next id)."""
    points = (chart or {}).get("points") or []
    if len(points) < 2:
        return [], shape_id
    values = [p["value"] for p in points]
    floor, ceiling, zoomed = chart_scale(values)
    span = (ceiling - floor) or 1.0
    peak = max(abs(v) for v in values) or 1.0

    def height_of(value):
        return int((value - floor) / span * _CHART_CY)

    base_h = 0 if zoomed else height_of(0.0)
    base_y = _CHART_Y + _CHART_CY - base_h
    slot = _CHART_CX // len(points)
    bar_cx = int(slot * 0.55)

    shapes = [_pptx_rect(shape_id, "Baseline", _CHART_X, base_y, _CHART_CX,
                         12700, "BFBFBF")]
    shape_id += 1
    for i, p in enumerate(points):
        value_h = height_of(p["value"])
        height = max(abs(value_h - base_h), 20000)
        bottom_h = min(value_h, base_h)
        y = _CHART_Y + _CHART_CY - bottom_h - height
        x = _CHART_X + i * slot + (slot - bar_cx) // 2
        shapes.append(_pptx_rect(shape_id, "Bar %d" % (i + 1), x,
                                 y, bar_cx, height, _CHART_ACCENT,
                                 None if abs(p["value"]) == peak else 55000))
        shape_id += 1
        value_y = (y - 300000) if value_h >= base_h else (y + height)
        shapes.append(_pptx_textbox(shape_id, "Value %d" % (i + 1),
                                    _CHART_X + i * slot, value_y, slot, 300000,
                                    [(p["display"], 1100, True, False)], "ctr"))
        shape_id += 1
        shapes.append(_pptx_textbox(shape_id, "Label %d" % (i + 1),
                                    _CHART_X + i * slot,
                                    _CHART_Y + _CHART_CY + 60000, slot, 340000,
                                    [(p["label"], 1000, False, False)], "ctr"))
        shape_id += 1
    unit = (chart or {}).get("unit")
    if unit:
        shapes.append(_pptx_textbox(shape_id, "Unit", _CHART_X,
                                    _CHART_Y - 340000, _CHART_CX, 300000,
                                    [(unit, 1000, False, False)]))
        shape_id += 1
    if zoomed:
        note = "axis starts at %s" % _trim_number(floor)
        shapes.append(_pptx_textbox(shape_id, "Axis note", _CHART_X,
                                    _CHART_Y + _CHART_CY + 400000, _CHART_CX,
                                    280000, [(note, 900, False, False)], "r"))
        shape_id += 1
    return shapes, shape_id


def _pptx_slide(slide):
    chart = slide.get("chart")
    shapes = [_pptx_textbox(2, "Title", 685800, 457200, _EMU_W - 1371600, 1143000,
                            [(slide.get("title") or "", 3200, True, False)])]
    paragraphs = []
    for bullet in slide.get("bullets") or []:
        paragraphs.append((bullet, 1800 if not chart else 1400, False, True))
    for line in slide.get("body") or []:
        paragraphs.append((line, 1800 if not chart else 1400, False, False))
    body_cy = 1000000 if chart else _EMU_H - 2286000
    shapes.append(_pptx_textbox(3, "Body", 685800, 1828800, _EMU_W - 1371600,
                                body_cy, paragraphs))
    if chart:
        chart_shapes, _ = _pptx_chart(chart, 4)
        shapes.extend(chart_shapes)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        ' xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        "<p:cSld><p:spTree>"
        "<p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>"
        "<p:grpSpPr/>%s</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>"
        "</p:sld>" % "".join(shapes)
    )


def render_pptx(text, title=""):
    """Markdown-ish deck text -> a 16:9 .pptx package (bytes)."""
    import io

    parsed_title, slides = parse_slides(text)
    title = title or parsed_title or "Presentation"

    slide_ids = []
    rels = []
    for i in range(len(slides)):
        slide_ids.append('<p:sldId id="%d" r:id="rId%d"/>' % (256 + i, 10 + i))
        rels.append(
            '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/slide" Target="slides/slide%d.xml"/>'
            % (10 + i, i + 1))
    presentation = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        ' xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        "<p:sldIdLst>%s</p:sldIdLst>"
        '<p:sldSz cx="%d" cy="%d"/><p:notesSz cx="%d" cy="%d"/>'
        "</p:presentation>" % ("".join(slide_ids), _EMU_W, _EMU_H,
                                _NOTES_W, _NOTES_H))
    presentation_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/>'
        "%s</Relationships>" % "".join(rels))
    content_types = _PPTX_CONTENT_TYPES_HEAD + "".join(
        '<Override PartName="/ppt/slides/slide%d.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' % (i + 1)
        for i in range(len(slides))) + "</Types>"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", _PPTX_ROOT_RELS)
        z.writestr("docProps/core.xml", _pptx_core(title))
        z.writestr("ppt/presentation.xml", presentation)
        z.writestr("ppt/_rels/presentation.xml.rels", presentation_rels)
        z.writestr("ppt/slideMasters/slideMaster1.xml", _PPTX_MASTER)
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", _PPTX_MASTER_RELS)
        z.writestr("ppt/slideLayouts/slideLayout1.xml", _PPTX_LAYOUT)
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _PPTX_LAYOUT_RELS)
        z.writestr("ppt/theme/theme1.xml", _PPTX_THEME)
        for i, slide in enumerate(slides):
            z.writestr("ppt/slides/slide%d.xml" % (i + 1), _pptx_slide(slide))
            z.writestr(
                "ppt/slides/_rels/slide%d.xml.rels" % (i + 1),
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
                "</Relationships>")
    return buf.getvalue()


def render(text, fmt, title=""):
    """Text + format name -> (bytes, format). Unknown format falls back to md."""
    fmt = normalize_format(fmt) or "md"
    if fmt == "pdf":
        return render_pdf(text, title=title), fmt
    if fmt == "docx":
        return render_docx(text, title=title), fmt
    if fmt == "pptx":
        return render_pptx(text, title=title), fmt
    if fmt == "txt":
        return strip_markers(text or "").encode("utf-8"), fmt
    return (text or "").encode("utf-8"), fmt
