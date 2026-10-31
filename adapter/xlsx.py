"""A minimal XLSX (SpreadsheetML) writer: PURE STDLIB, zip + XML, like docgen's DOCX.

Enough for a data table: several sheets, a bold header row frozen at the top
with an auto filter, column widths, text and number cells. Text goes into
inline-string cells, so a value that starts with "=" stays text and is never
evaluated as a formula. Characters XML 1.0 cannot carry are dropped; a cell is
cut at Excel's 32 767-character limit.
"""
import io
import math
import re
import time
import zipfile
from xml.sax.saxutils import escape

CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_CELL = 32767
MAX_SHEET_NAME = 31
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")
_BAD_XML_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

_STYLES = (_HEAD + '<styleSheet xmlns="%s">'
           '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
           '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
           '<fills count="2"><fill><patternFill patternType="none"/></fill>'
           '<fill><patternFill patternType="gray125"/></fill></fills>'
           '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border>'
           '</borders>'
           '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
           '</cellStyleXfs>'
           '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
           '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
           '</cellXfs>'
           '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
           '</styleSheet>') % _NS


def column_letter(index):
    """0 -> A, 25 -> Z, 26 -> AA."""
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def clean_text(value):
    text = _BAD_XML_CHARS.sub("", str(value))
    return text[:MAX_CELL]


def sheet_names(names):
    """Valid, unique sheet names in order (Excel: at most 31 characters, none of
    []:*?/\\, no apostrophe at either end, unique regardless of case)."""
    out, seen = [], set()
    for i, name in enumerate(names, 1):
        base = _BAD_SHEET_CHARS.sub("_", clean_text(name or "")).strip().strip("'").strip()
        base = base[:MAX_SHEET_NAME] or "Sheet%d" % i
        candidate, n = base, 2
        while candidate.casefold() in seen or candidate.casefold() == "history":
            suffix = " (%d)" % n
            candidate = base[:MAX_SHEET_NAME - len(suffix)] + suffix
            n += 1
        seen.add(candidate.casefold())
        out.append(candidate)
    return out


def _cell(ref, value, style):
    attr = ' s="1"' if style else ""
    if isinstance(value, bool) or (isinstance(value, float) and not math.isfinite(value)):
        value = str(value)
    if isinstance(value, (int, float)):
        return '<c r="%s"%s><v>%s</v></c>' % (ref, attr, repr(value) if isinstance(
            value, float) else value)
    return '<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (
        ref, attr, escape(clean_text(value)))


def _width(sheet, col, rows):
    widths = sheet.get("widths") or []
    if col < len(widths) and widths[col]:
        return widths[col]
    longest = max((len(str(r[col])) for r in rows if col < len(r) and r[col] is not None),
                  default=8)
    return max(8, min(longest + 2, 60))


def _sheet_xml(sheet):
    rows = [list(r) for r in sheet.get("rows") or []]
    header = bool(sheet.get("header", True)) and bool(rows)
    ncols = max((len(r) for r in rows), default=1) or 1
    last = "%s%d" % (column_letter(ncols - 1), max(len(rows), 1))
    parts = [_HEAD, '<worksheet xmlns="%s" xmlns:r="%s">' % (_NS, _NS_R),
             '<dimension ref="A1:%s"/>' % last, '<sheetViews><sheetView workbookViewId="0">']
    if header:
        parts.append('<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
                     'state="frozen"/><selection pane="bottomLeft"/>')
    parts.append('</sheetView></sheetViews><sheetFormatPr defaultRowHeight="15"/><cols>')
    parts += ['<col min="%d" max="%d" width="%s" customWidth="1"/>'
              % (c + 1, c + 1, _width(sheet, c, rows)) for c in range(ncols)]
    parts.append("</cols><sheetData>")
    for r, row in enumerate(rows, 1):
        cells = "".join(_cell("%s%d" % (column_letter(c), r), v, header and r == 1)
                        for c, v in enumerate(row) if v is not None and v != "")
        parts.append('<row r="%d">%s</row>' % (r, cells))
    parts.append("</sheetData>")
    if header and sheet.get("filter", True):
        parts.append('<autoFilter ref="A1:%s"/>' % last)
    parts.append("</worksheet>")
    return "".join(parts), (last if header and sheet.get("filter", True) else None)


def _quote_sheet(name):
    return "'%s'" % name.replace("'", "''")


def workbook(sheets, title="", creator="ViVeSec AI", now=None):
    """[{name, rows: [[cell]], header=True, filter=True, widths=[...]}] -> .xlsx bytes.
    A cell is str, int, float or None (empty)."""
    sheets = list(sheets) or [{"name": "Sheet1", "rows": []}]
    names = sheet_names([s.get("name") for s in sheets])
    built = [_sheet_xml(s) for s in sheets]
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    defined = "".join(
        '<definedName name="_xlnm._FilterDatabase" localSheetId="%d" hidden="1">%s!$A$1:$%s'
        '</definedName>' % (i, escape(_quote_sheet(names[i])),
                           "$".join(re.match(r"([A-Z]+)(\d+)", ref).groups()))
        for i, (_, ref) in enumerate(built) if ref)
    book = (_HEAD + '<workbook xmlns="%s" xmlns:r="%s"><bookViews><workbookView/></bookViews>'
            '<sheets>%s</sheets>%s</workbook>') % (
        _NS, _NS_R,
        "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(n, {'"': "&quot;"}),
                                                                  i, i)
                for i, n in enumerate(names, 1)),
        "<definedNames>%s</definedNames>" % defined if defined else "")
    book_rels = (_HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
                 '2006/relationships">%s<Relationship Id="rId%d" Type="%sstyles" '
                 'Target="styles.xml"/></Relationships>') % (
        "".join('<Relationship Id="rId%d" Type="%sworksheet" Target="worksheets/sheet%d.xml"/>'
                % (i, _REL, i) for i in range(1, len(names) + 1)), len(names) + 1, _REL)
    types = (_HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
             'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
             'officedocument.spreadsheetml.sheet.main+xml"/>%s'
             '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-'
             'officedocument.spreadsheetml.styles+xml"/>'
             '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-'
             'package.core-properties+xml"/>'
             '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-'
             'officedocument.extended-properties+xml"/></Types>') % "".join(
        '<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % i
        for i in range(1, len(names) + 1))
    root_rels = (_HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                 'relationships"><Relationship Id="rId1" Type="%sofficeDocument" '
                 'Target="xl/workbook.xml"/><Relationship Id="rId2" Type="http://schemas.'
                 'openxmlformats.org/package/2006/relationships/metadata/core-properties" '
                 'Target="docProps/core.xml"/><Relationship Id="rId3" Type="%sextended-'
                 'properties" Target="docProps/app.xml"/></Relationships>') % (_REL, _REL)
    core = (_HEAD + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
            'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<dc:title>%s</dc:title><dc:creator>%s</dc:creator>'
            '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
            '</cp:coreProperties>') % (escape(clean_text(title or "Workbook")),
                                       escape(clean_text(creator)), stamp)
    app = (_HEAD + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
           'extended-properties"><Application>ViVeSec AI Box</Application></Properties>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("docProps/core.xml", core)
        z.writestr("docProps/app.xml", app)
        z.writestr("xl/workbook.xml", book)
        z.writestr("xl/_rels/workbook.xml.rels", book_rels)
        z.writestr("xl/styles.xml", _STYLES)
        for i, (xml, _) in enumerate(built, 1):
            z.writestr("xl/worksheets/sheet%d.xml" % i, xml)
    return buf.getvalue()
