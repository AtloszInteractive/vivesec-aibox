"""Organisation templates for the files the box writes (E18, first step).

An administrator of a standalone box sets them on the administration page.
They shape how a generated file looks, never what it says or who may read it:

* the DOCX frame: a Word document or template (.docx/.dotx) lends its styles,
  theme, list numbering, page size and margins, headers and footers (with
  their pictures and links) to every DOCX the box writes
  (docgen.render_docx). Its body text is not used. Heading and bullet styles
  are found by their built-in names, because Word localises the style ids (a
  Hungarian Word calls "heading 1" `Cmsor1`); a missing heading style is
  added;
* the investigation spreadsheet's layout: column order, the organisation's
  own header texts and widths (investigation_export.validate_layout),
  proposed from the header row of a sample .xlsx.
The organisation card of the first step is only read here, once, when the
company profile takes it over (company_profile.py).

Fail closed. A Word upload with anything the frame does not need that could
act on its own -- macros, embedded objects, linked or external content,
fields that fetch, DTDs, unknown picture types -- is refused as a whole,
never quietly cleaned. The kept parts come from a whitelist and their
relationships are written anew. Only the frame is stored, itself as a .docx
that passes the same checks again whenever it is read; a stored frame that no
longer does gives the built-in look. The sample spreadsheet is read for its
header row only and not kept. Pure stdlib.
"""
import hashlib
import io
import json
import os
import posixpath
import re
import sys
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
import zlib
from xml.sax.saxutils import escape, quoteattr

DEFAULT_DIR = "/data/adapter/templates"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ENTRIES = 1000
MAX_PART_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024
MAX_VERSIONS = 10
MAX_FILE_NAME = 120
MAX_HEADER_COLUMNS = 40
MAX_HEADER_TEXT = 80
MAX_HEADER_ROWS = 20

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL = R + "/"
_OOXML = "application/vnd.openxmlformats-officedocument."
DOCUMENT_TYPE = _OOXML + "wordprocessingml.document.main+xml"
MAIN_TYPES = (DOCUMENT_TYPE, _OOXML + "wordprocessingml.template.main+xml")
CORE_TYPE = "application/vnd.openxmlformats-package.core-properties+xml"
PART_TYPES = {"styles": _OOXML + "wordprocessingml.styles+xml",
              "settings": _OOXML + "wordprocessingml.settings+xml",
              "numbering": _OOXML + "wordprocessingml.numbering+xml",
              "header": _OOXML + "wordprocessingml.header+xml",
              "footer": _OOXML + "wordprocessingml.footer+xml",
              "theme": _OOXML + "theme+xml"}
PICTURE_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif",
                 "bmp": "image/bmp", "tiff": "image/tiff", "emf": "image/x-emf",
                 "wmf": "image/x-wmf", "svg": "image/svg+xml"}
_PICTURE_EXT = {"jpg": "jpeg", "tif": "tiff"}
# Fields a header or footer may hold: page numbers, dates, document properties.
# Any other (INCLUDEPICTURE, INCLUDETEXT, LINK, DDE, DATABASE ...) may fetch
# from elsewhere when the fields are updated.
SAFE_FIELDS = frozenset((
    "", "=", "PAGE", "NUMPAGES", "SECTIONPAGES", "SECTION", "DATE", "TIME", "CREATEDATE",
    "SAVEDATE", "PRINTDATE", "TITLE", "SUBJECT", "AUTHOR", "KEYWORDS", "COMMENTS",
    "LASTSAVEDBY", "FILENAME", "STYLEREF", "DOCPROPERTY", "REVNUM", "IF", "HYPERLINK"))
# A4 portrait with 2 cm margins: the built-in look, and what a template
# without its own page setup gets.
DEFAULT_PAGE = {"w": 11906, "h": 16838, "orient": None, "top": 1134, "right": 1134,
                "bottom": 1134, "left": 1134, "header": 708, "footer": 708, "gutter": 0}
_MARGINS = ("top", "right", "bottom", "left", "header", "footer", "gutter")
# (key, built-in name, outline level, size in half-points, space before/after)
_HEADINGS = (("h1", "heading 1", 0, 32, 240, 120), ("h2", "heading 2", 1, 26, 200, 100))
_XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_RID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]{0,63}$")
_LINK = re.compile(r"^(?:https?://|mailto:)[^\s\"<>]{1,2000}$", re.I)
_MEASURE = re.compile(r"^(-?\d+(?:\.\d+)?)(mm|cm|in|pt|pc|pi)?$")
_UNITS = {None: 1.0, "mm": 1440 / 25.4, "cm": 1440 / 2.54, "in": 1440.0, "pt": 20.0,
          "pc": 240.0, "pi": 240.0}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SVG_BAD = re.compile(r"<script|<foreignobject|@import|javascript:|\son[a-z]+\s*=")
_SVG_LINK = re.compile(r"""(?:href|src)\s*=\s*["']\s*(?!#|data:image/)""")
_SVG_URL = re.compile(r"""url\(\s*["']?\s*(?!#|data:image/)""")
_VERSION_ID = re.compile(r"^[0-9a-f]{24}$")
_CELL_REF = re.compile(r"^([A-Z]{1,3})\d*$")


class TemplateInvalid(ValueError):
    """An upload that cannot be used; `code` names the reason for the page."""

    def __init__(self, code, detail=""):
        detail = str(detail)[:200]
        super().__init__("%s (%s)" % (code, detail) if detail else code)
        self.code = code
        self.detail = detail


def _q(ns, name):
    return "{%s}%s" % (ns, name)


def _w(name):
    return _q(W, name)


# -- reading an OOXML package --------------------------------------------------------


class _Package:
    """An OOXML zip read with limits. Part names match without case, as OPC
    wants, so two parts that differ only in case make the package unusable."""

    def __init__(self, raw, kind):
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            raise TemplateInvalid("not_" + kind)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise TemplateInvalid("too_large")
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(bytes(raw)))
            infos = self.zip.infolist()
        except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError):
            raise TemplateInvalid("not_" + kind)
        if len(infos) > MAX_ENTRIES:
            raise TemplateInvalid("too_large", "%d parts" % len(infos))
        self.parts = {}
        for info in infos:
            key = info.filename.casefold()
            if key in self.parts:
                raise TemplateInvalid("broken", "part %s twice" % info.filename)
            self.parts[key] = info
        self.total = 0

    def names(self):
        return [info.filename for info in self.parts.values()]

    def read(self, name):
        info = self.parts.get(name.casefold())
        if info is None:
            return None
        if info.file_size > MAX_PART_BYTES or self.total + info.file_size > MAX_TOTAL_BYTES:
            raise TemplateInvalid("too_large", name)
        try:
            with self.zip.open(info) as handle:
                data = handle.read(MAX_PART_BYTES + 1)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError,
                OSError, ValueError):
            raise TemplateInvalid("broken", name)
        if len(data) > MAX_PART_BYTES:
            raise TemplateInvalid("too_large", name)
        self.total += len(data)
        return data

    def need(self, name):
        data = self.read(name)
        if data is None:
            raise TemplateInvalid("broken", "%s is missing" % name)
        return data


def _xml(data, what):
    """One XML part. A DTD (entity expansion, external entities) or anything
    but UTF-8 refuses the upload."""
    body = data[3:] if data[:3] == b"\xef\xbb\xbf" else data
    if body.lstrip()[:1] != b"<":
        raise TemplateInvalid("broken", what)
    if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise TemplateInvalid("unsupported_content", "%s: DTD" % what)
    try:
        return ET.fromstring(body)
    except ET.ParseError:
        raise TemplateInvalid("broken", what)


def _content_types(pkg, kind):
    data = pkg.read("[Content_Types].xml")
    if data is None:
        raise TemplateInvalid("not_" + kind)
    root = _xml(data, "[Content_Types].xml")
    defaults = {(e.get("Extension") or "").casefold(): e.get("ContentType") or ""
                for e in root.iter(_q(TYPES_NS, "Default"))}
    overrides = {(e.get("PartName") or "").lstrip("/").casefold(): e.get("ContentType") or ""
                 for e in root.iter(_q(TYPES_NS, "Override"))}
    return defaults, overrides


def _type_of(name, types):
    defaults, overrides = types
    return overrides.get(name.casefold()) or defaults.get(
        posixpath.splitext(name)[1][1:].casefold(), "")


def _rels_name(part):
    folder, base = posixpath.split(part)
    return posixpath.join(folder, "_rels", base + ".rels")


def _rels(pkg, part):
    """{Id: (Type, Target, external)} of one part ("" is the package itself)."""
    name = _rels_name(part)
    data = pkg.read(name)
    if data is None:
        return {}
    out = {}
    for rel in _xml(data, name).iter(_q(PKG, "Relationship")):
        rid, kind, target = rel.get("Id"), rel.get("Type"), rel.get("Target")
        if not rid or not _RID.match(rid) or not kind or target is None or rid in out:
            raise TemplateInvalid("broken", name)
        out[rid] = (kind, target, (rel.get("TargetMode") or "Internal") == "External")
    return out


def _resolve(part, target):
    """The package part an internal relationship of `part` points at."""
    path = urllib.parse.unquote(target or "")
    if not path or any(c in path for c in "\\:?#\x00"):
        raise TemplateInvalid("unsupported_layout", (target or "")[:80])
    path = path[1:] if path.startswith("/") else posixpath.join(posixpath.dirname(part), path)
    path = posixpath.normpath(path)
    if path in (".", "..") or path.startswith(("../", "/")):
        raise TemplateInvalid("unsupported_layout", target[:80])
    return path


def _single(rels, part, kind, required=False):
    found = [(target, external) for rel_type, target, external in rels.values()
             if rel_type == REL + kind]
    if len(found) > 1:
        raise TemplateInvalid("broken", "%s twice" % kind)
    if not found:
        if required:
            raise TemplateInvalid("unsupported_layout", "no %s" % kind)
        return None
    target, external = found[0]
    if external:
        raise TemplateInvalid("linked_content", kind)
    return _resolve(part, target)


def _on(element):
    """A Word on/off element: present and not switched off."""
    return element is not None and (element.get(_w("val")) or "true").casefold() \
        not in ("0", "false", "off")


def _no_links(root, what):
    """No picture or object pulled in by an attribute (VML src/href, a file or
    network path), which no relationship would show."""
    for element in root.iter():
        for key, value in element.attrib.items():
            local = key.rsplit("}", 1)[-1].casefold()
            text = (value or "").strip().casefold()
            if (local in ("href", "src") and text and not text.startswith("#")) \
                    or text.startswith(("file:", "\\\\")):
                raise TemplateInvalid("linked_content", "%s: %s" % (what, local))


def _check_fields(root, what):
    """Every field of a header or footer must be a SAFE_FIELDS one. A field's
    instruction may be spread over several runs, and fields nest."""
    open_fields = []

    def check(instruction):
        words = instruction.split()
        word = words[0].upper() if words else ""
        if word.startswith("="):
            word = "="
        if word not in SAFE_FIELDS:
            raise TemplateInvalid("unsafe_field", "%s: %s" % (what, word[:40]))

    for element in root.iter():
        if element.tag == _w("fldSimple"):
            check(element.get(_w("instr")) or "")
        elif element.tag == _w("fldChar"):
            kind = element.get(_w("fldCharType"))
            if kind == "begin":
                open_fields.append([])
            elif kind == "end" and open_fields:
                check("".join(open_fields.pop()))
        elif element.tag in (_w("instrText"), _w("delInstrText")):
            if open_fields:
                open_fields[-1].append(element.text or "")
            else:
                check(element.text or "")
    for parts in open_fields:
        check("".join(parts))


def _svg_ok(data):
    try:
        text = data.decode("utf-8-sig").casefold()
    except UnicodeDecodeError:
        return False
    if _SVG_BAD.search(text) or _SVG_LINK.search(text) or _SVG_URL.search(text):
        raise TemplateInvalid("linked_content", "svg picture")
    return _xml(data, "svg picture").tag in ("svg", "{http://www.w3.org/2000/svg}svg")


def _picture_ok(ext, data):
    """The picture is what its name says (magic bytes)."""
    if ext == "png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == "jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if ext == "gif":
        return data[:6] in (b"GIF87a", b"GIF89a")
    if ext == "bmp":
        return data.startswith(b"BM")
    if ext == "tiff":
        return data[:4] in (b"II*\x00", b"MM\x00*")
    if ext == "emf":
        return data[40:44] == b" EMF"
    if ext == "wmf":
        return data[:4] in (b"\xd7\xcd\xc6\x9a", b"\x01\x00\x09\x00", b"\x02\x00\x09\x00")
    if ext == "svg":
        return _svg_ok(data)
    return False


def _twips(element, name, low, high, default):
    raw = element.get(_w(name))
    if raw is None:
        return default
    match = _MEASURE.match(raw.strip())
    if not match:
        raise TemplateInvalid("broken", "page setup %s=%r" % (name, raw[:20]))
    value = int(round(float(match.group(1)) * _UNITS[match.group(2)]))
    if not low <= value <= high:
        raise TemplateInvalid("broken", "page setup %s=%r" % (name, raw[:20]))
    return value


def _mm(twips):
    return round(twips * 25.4 / 1440)


# -- the DOCX frame ---------------------------------------------------------------------


class DocxFrame:
    """The parts of a Word template the box keeps; package() writes a .docx
    of them around a body. Part names are canonical (word/header1.xml,
    word/media/image1.png); the relationship ids the parts use are kept."""

    def __init__(self):
        self.parts = {}       # part name -> (bytes, content type)
        self.part_rels = {}   # part name -> [(id, "image"|"hyperlink", part name or URL)]
        self.doc_parts = []   # [(relationship kind, part name)] the document points at
        self.refs = []        # [("header"|"footer", "default"|"first"|"even", part name)]
        self.page = dict(DEFAULT_PAGE)
        self.title_page = False
        self.even_odd = False
        self.compat = 15
        self.styles = {"h1": "Heading1", "h2": "Heading2", "bullet": None}
        self.summary = {}
        self._media = {}      # source part -> canonical part name

    # -- reading -----------------------------------------------------------------------
    def _picture(self, pkg, source):
        if source in self._media:
            return self._media[source]
        ext = posixpath.splitext(source)[1][1:].casefold()
        ext = _PICTURE_EXT.get(ext, ext)
        if ext not in PICTURE_TYPES:
            raise TemplateInvalid("picture_type", ext or "?")
        data = pkg.need(source)
        if not _picture_ok(ext, data):
            raise TemplateInvalid("picture_type", ext)
        name = "word/media/image%d.%s" % (len(self._media) + 1, ext)
        self._media[source] = name
        self.parts[name] = (data, PICTURE_TYPES[ext])
        return name

    def _take(self, pkg, source, name, kind, root_tag, fields=False):
        """Copy one part with its whitelisted relationships: pictures inside
        the package and external hyperlinks. Anything else refuses the upload."""
        data = pkg.need(source)
        root = _xml(data, source)
        if root.tag != root_tag:
            raise TemplateInvalid("broken", source)
        _no_links(root, source)
        if fields:
            _check_fields(root, source)
        kept = []
        for rid, (rel_type, target, external) in sorted(_rels(pkg, source).items()):
            if rel_type == REL + "hyperlink":
                if not external or not _LINK.match(target):
                    raise TemplateInvalid("linked_content", "link in %s" % source)
                kept.append((rid, "hyperlink", target))
            elif rel_type == REL + "image":
                if external:
                    raise TemplateInvalid("linked_content", "linked picture in %s" % source)
                kept.append((rid, "image", self._picture(pkg, _resolve(source, target))))
            else:
                raise TemplateInvalid("unsupported_content", "%s in %s" % (
                    rel_type.rsplit("/", 1)[-1][:40], source))
        self.parts[name] = (data, PART_TYPES[kind])
        if kept:
            self.part_rels[name] = kept
        return root

    def _page_setup(self, sect):
        size = sect.find(_w("pgSz"))
        if size is not None:
            self.page["w"] = _twips(size, "w", 1000, 31680, self.page["w"])
            self.page["h"] = _twips(size, "h", 1000, 31680, self.page["h"])
            self.page["orient"] = "landscape" if size.get(_w("orient")) == "landscape" else None
        margins = sect.find(_w("pgMar"))
        if margins is not None:
            for key in _MARGINS:
                low = -31680 if key in ("top", "bottom") else 0
                self.page[key] = _twips(margins, key, low, 31680, self.page[key])
        self.title_page = _on(sect.find(_w("titlePg")))

    def _headers_footers(self, pkg, sect, rels, main):
        taken, seen = {}, set()
        for element in sect:
            local = element.tag.rsplit("}", 1)[-1]
            if local not in ("headerReference", "footerReference"):
                continue
            kind = local[:-len("Reference")]
            place = element.get(_w("type")) or "default"
            rel = rels.get(element.get(_q(R, "id")) or "")
            if place not in ("default", "first", "even") or (kind, place) in seen \
                    or rel is None or rel[0] != REL + kind:
                raise TemplateInvalid("broken", "%s reference" % kind)
            if rel[2]:
                raise TemplateInvalid("linked_content", kind)
            seen.add((kind, place))
            source = _resolve(main, rel[1])
            if source not in taken:
                count = sum(1 for k, _ in self.doc_parts if k == kind) + 1
                name = "word/%s%d.xml" % (kind, count)
                self._take(pkg, source, name, kind, _w("hdr" if kind == "header" else "ftr"),
                           fields=True)
                self.doc_parts.append((kind, name))
                taken[source] = name
            self.refs.append((kind, place, taken[source]))

    def _style_ids(self, styles, numbering):
        """Find the heading and bullet styles by their built-in names; add a
        heading style the template lacks."""
        by_name, ids, normal = {}, set(), None
        for style in styles.findall(_w("style")):
            sid = style.get(_w("styleId")) or ""
            ids.add(sid)
            if style.get(_w("type")) != "paragraph" or not sid:
                continue
            if normal is None and (style.get(_w("default")) or "").casefold() in ("1", "true", "on"):
                normal = sid
            name = style.find(_w("name"))
            key = " ".join((name.get(_w("val")) if name is not None else "").split()).casefold()
            by_name.setdefault(key, style)
        added = []
        for key, name, level, size, before, after in _HEADINGS:
            style = by_name.get(name)
            if style is not None:
                self.styles[key] = style.get(_w("styleId"))
                continue
            sid = next(c for c in ("Heading%d" % (level + 1), "VivesecHeading%d" % (level + 1),
                                   "VivesecHeading%dx" % (level + 1)) if c not in ids)
            self._add_style(sid, name, level, size, before, after, normal)
            ids.add(sid)
            self.styles[key] = sid
            added.append(name)
        bullet = by_name.get("list bullet")
        if bullet is not None and numbering is not None:
            num = bullet.find("%s/%s/%s" % (_w("pPr"), _w("numPr"), _w("numId")))
            known = {n.get(_w("numId")) for n in numbering.findall(_w("num"))}
            if num is not None and num.get(_w("val")) not in (None, "0") \
                    and num.get(_w("val")) in known:
                self.styles["bullet"] = bullet.get(_w("styleId"))
        return added

    def _add_style(self, sid, name, level, size, before, after, based_on):
        data, ctype = self.parts["word/styles.xml"]
        text = data.decode("utf-8-sig")
        end = text.rfind("</w:styles>")
        if ('xmlns:w="%s"' % W) not in text or end < 0:
            raise TemplateInvalid("unsupported_layout", "styles")
        style = ('<w:style w:type="paragraph" w:styleId=%s><w:name w:val="%s"/>%s<w:qFormat/>'
                 '<w:pPr><w:keepNext/><w:spacing w:before="%d" w:after="%d"/>'
                 '<w:outlineLvl w:val="%d"/></w:pPr><w:rPr><w:b/><w:sz w:val="%d"/></w:rPr>'
                 '</w:style>') % (quoteattr(sid), name,
                                  "<w:basedOn w:val=%s/>" % quoteattr(based_on) if based_on else "",
                                  before, after, level, size)
        self.parts["word/styles.xml"] = ((text[:end] + style + text[end:]).encode("utf-8"), ctype)

    def _summarise(self, styles, theme, added):
        fonts = {}
        if theme is not None:
            for key, tag in (("headings", "majorFont"), ("body", "minorFont")):
                latin = theme.find(".//%s/%s" % (_q(A, tag), _q(A, "latin")))
                if latin is not None and latin.get("typeface"):
                    fonts[key] = latin.get("typeface")[:60]
        default = styles.find("%s/%s/%s/%s" % (_w("docDefaults"), _w("rPrDefault"), _w("rPr"),
                                               _w("rFonts")))
        if default is not None and default.get(_w("ascii")):
            fonts["body"] = default.get(_w("ascii"))[:60]
        page = self.page
        self.summary = {
            "page": {"width_mm": _mm(page["w"]), "height_mm": _mm(page["h"]),
                     "landscape": page["orient"] == "landscape"},
            "margins_mm": {key: _mm(page[key]) for key in ("top", "right", "bottom", "left")},
            "headers": sum(1 for kind, _ in self.doc_parts if kind == "header"),
            "footers": sum(1 for kind, _ in self.doc_parts if kind == "footer"),
            "pictures": len(self._media),
            "links": sum(1 for rels in self.part_rels.values()
                         for _, kind, _ in rels if kind == "hyperlink"),
            "fonts": fonts, "added_styles": added,
            "bullets": "template" if self.styles["bullet"] else "symbol",
            "first_page": self.title_page, "even_odd": self.even_odd}

    # -- writing -----------------------------------------------------------------------
    def package(self, body, core):
        """A .docx of this frame around `body` (w:p elements; "" for none)
        with `core` (docProps/core.xml)."""
        doc_rels, rid_of = [], {}
        for i, (kind, part) in enumerate(self.doc_parts, 1):
            rid_of[part] = "rId%d" % i
            doc_rels.append(("rId%d" % i, REL + kind, part, False))
        doc_rels.append(("rId%d" % (len(doc_rels) + 1), REL + "settings", "word/settings.xml",
                         False))
        refs = "".join('<w:%sReference w:type="%s" r:id="%s"/>' % (kind, place, rid_of[part])
                       for kind, place, part in self.refs)
        page = self.page
        sect = ('<w:sectPr>%s<w:pgSz w:w="%d" w:h="%d"%s/><w:pgMar %s/>%s</w:sectPr>' % (
            refs, page["w"], page["h"],
            ' w:orient="landscape"' if page["orient"] == "landscape" else "",
            " ".join('w:%s="%d"' % (key, page[key]) for key in _MARGINS),
            "<w:titlePg/>" if self.title_page else ""))
        document = (_XML_HEAD + '<w:document xmlns:w="%s" xmlns:r="%s"><w:body>%s%s</w:body>'
                    '</w:document>') % (W, R, body or "<w:p/>", sect)
        settings = (_XML_HEAD + '<w:settings xmlns:w="%s">%s<w:compat><w:compatSetting '
                    'w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" '
                    'w:val="%d"/></w:compat></w:settings>') % (
            W, "<w:evenAndOddHeaders/>" if self.even_odd else "", self.compat)
        overrides = [("word/document.xml", DOCUMENT_TYPE),
                     ("word/settings.xml", PART_TYPES["settings"]),
                     ("docProps/core.xml", CORE_TYPE)]
        overrides += [(name, ctype) for name, (_, ctype) in sorted(self.parts.items())]
        types = (_XML_HEAD + '<Types xmlns="%s"><Default Extension="rels" ContentType='
                 '"application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="xml" ContentType="application/xml"/>%s</Types>') % (
            TYPES_NS, "".join('<Override PartName=%s ContentType=%s/>' % (
                quoteattr("/" + name), quoteattr(ctype)) for name, ctype in overrides))
        root_rels = _relationships([("rId1", REL + "officeDocument", "word/document.xml", False),
                                    ("rId2", "http://schemas.openxmlformats.org/package/2006/"
                                     "relationships/metadata/core-properties",
                                     "docProps/core.xml", False)], "")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", types)
            z.writestr("_rels/.rels", root_rels)
            z.writestr("docProps/core.xml", core)
            z.writestr("word/document.xml", document)
            z.writestr("word/_rels/document.xml.rels",
                       _relationships(doc_rels, "word/document.xml"))
            z.writestr("word/settings.xml", settings)
            for name, (data, _) in sorted(self.parts.items()):
                z.writestr(name, data)
            for name, rels in sorted(self.part_rels.items()):
                z.writestr(_rels_name(name), _relationships(
                    [(rid, REL + kind, target, kind == "hyperlink") for rid, kind, target in rels],
                    name))
        return buf.getvalue()


def _relative(target, folder):
    """`target` (a part name) as seen from the part folder `folder`."""
    to, start = target.split("/"), folder.split("/") if folder else []
    common = 0
    while common < len(to) - 1 and common < len(start) and to[common] == start[common]:
        common += 1
    return "/".join([".."] * (len(start) - common) + to[common:])


def _relationships(entries, part):
    """A .rels part; internal targets are written relative to `part`."""
    items = []
    for rid, rel_type, target, external in entries:
        if external:
            items.append('<Relationship Id=%s Type=%s Target=%s TargetMode="External"/>'
                         % (quoteattr(rid), quoteattr(rel_type), quoteattr(target)))
        else:
            relative = _relative(target, posixpath.dirname(part))
            items.append('<Relationship Id=%s Type=%s Target=%s/>'
                         % (quoteattr(rid), quoteattr(rel_type), quoteattr(relative)))
    return (_XML_HEAD + '<Relationships xmlns="%s">%s</Relationships>') % (PKG, "".join(items))


def read_docx_frame(raw):
    """The frame of a Word document or template (bytes), or TemplateInvalid."""
    pkg = _Package(raw, "docx")
    for name in pkg.names():
        low = name.casefold()
        if low.endswith(("vbaproject.bin", "vbadata.xml")) or "/activex/" in low:
            raise TemplateInvalid("macros")
    types = _content_types(pkg, "docx")
    if any("macroenabled" in t.casefold() for t in list(types[0].values()) + list(
            types[1].values())):
        raise TemplateInvalid("macros")
    main = [_resolve("", target) for rel_type, target, external in _rels(pkg, "").values()
            if rel_type == REL + "officeDocument" and not external]
    if len(main) != 1 or _type_of(main[0], types) not in MAIN_TYPES:
        raise TemplateInvalid("not_docx")
    if main[0].casefold() != "word/document.xml":
        raise TemplateInvalid("unsupported_layout", main[0])
    main = "word/document.xml"
    document = _xml(pkg.need(main), main)
    if document.tag != _w("document"):
        raise TemplateInvalid("not_docx")
    rels = _rels(pkg, main)
    frame = DocxFrame()
    styles = frame._take(pkg, _single(rels, main, "styles", required=True), "word/styles.xml",
                         "styles", _w("styles"))
    frame.doc_parts.append(("styles", "word/styles.xml"))
    theme = None
    source = _single(rels, main, "theme")
    if source:
        theme = frame._take(pkg, source, "word/theme/theme1.xml", "theme", _q(A, "theme"))
        frame.doc_parts.append(("theme", "word/theme/theme1.xml"))
    numbering = None
    source = _single(rels, main, "numbering")
    if source:
        numbering = frame._take(pkg, source, "word/numbering.xml", "numbering", _w("numbering"))
        frame.doc_parts.append(("numbering", "word/numbering.xml"))
    source = _single(rels, main, "settings")
    data = pkg.read(source) if source else None
    if data is not None:
        settings = _xml(data, source)
        frame.even_odd = _on(settings.find(_w("evenAndOddHeaders")))
        for setting in settings.iter(_w("compatSetting")):
            if setting.get(_w("name")) == "compatibilityMode":
                value = setting.get(_w("val")) or ""
                if value.isdigit() and 11 <= int(value) <= 99:
                    frame.compat = int(value)
    body = document.find(_w("body"))
    if body is not None and len(body) and body[-1].tag == _w("sectPr"):
        frame._page_setup(body[-1])
        frame._headers_footers(pkg, body[-1], rels, main)
    added = frame._style_ids(styles, numbering)
    frame._summarise(styles, theme, added)
    return frame


def frame_core(title):
    return (_XML_HEAD + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/'
            '2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:title>%s</dc:title><dc:creator>ViVeSec AI</dc:creator></cp:coreProperties>'
            % escape(title or "Document"))


# -- the sample spreadsheet -------------------------------------------------------------


def _clean_text(value, limit):
    return " ".join(_CONTROL.sub(" ", value or "").split())[:limit]


def _column_index(ref):
    match = _CELL_REF.match(ref or "")
    if not match:
        raise TemplateInvalid("broken", "cell %r" % (ref or "")[:12])
    index = 0
    for letter in match.group(1):
        index = index * 26 + ord(letter) - 64
    return index - 1


def _shared_text(item):
    parts = []
    for child in item:
        if child.tag == _q(S, "t"):
            parts.append(child.text or "")
        elif child.tag == _q(S, "r"):
            text = child.find(_q(S, "t"))
            parts.append(text.text or "" if text is not None else "")
    return "".join(parts)


def _cell_text(cell, shared):
    kind = cell.get("t")
    if kind == "inlineStr":
        inline = cell.find(_q(S, "is"))
        return _shared_text(inline) if inline is not None else ""
    value = cell.find(_q(S, "v"))
    text = (value.text or "") if value is not None else ""
    if kind == "s":
        try:
            return shared[int(text)]
        except (ValueError, IndexError):
            raise TemplateInvalid("broken", "shared string %r" % text[:12])
    return text


def read_xlsx_header(raw):
    """The header row of the first sheet of a sample spreadsheet: the first
    row with any text, as [{label, width}] in column order."""
    pkg = _Package(raw, "xlsx")
    _content_types(pkg, "xlsx")
    main = [_resolve("", target) for rel_type, target, external in _rels(pkg, "").values()
            if rel_type == REL + "officeDocument" and not external]
    if len(main) != 1:
        raise TemplateInvalid("not_xlsx")
    book = _xml(pkg.need(main[0]), main[0])
    if book.tag != _q(S, "workbook"):
        raise TemplateInvalid("not_xlsx")
    first = book.find("%s/%s" % (_q(S, "sheets"), _q(S, "sheet")))
    rels = _rels(pkg, main[0])
    rel = rels.get(first.get(_q(R, "id")) or "") if first is not None else None
    if rel is None or rel[0] != REL + "worksheet" or rel[2]:
        raise TemplateInvalid("no_header")
    shared = []
    source = _single(rels, main[0], "sharedStrings")
    if source:
        table = _xml(pkg.need(source), source)
        shared = [_shared_text(item) for item in table.findall(_q(S, "si"))]
    sheet = _xml(pkg.need(_resolve(main[0], rel[1])), "worksheet")
    widths = {}
    for col in sheet.iter(_q(S, "col")):
        try:
            low, high, width = int(col.get("min")), int(col.get("max")), float(col.get("width"))
        except (TypeError, ValueError):
            continue
        for index in range(max(low, 1), min(high, 200) + 1):
            widths[index - 1] = width
    for number, row in enumerate(sheet.iter(_q(S, "row"))):
        if number >= MAX_HEADER_ROWS:
            break
        cells, position = {}, -1
        for cell in row.findall(_q(S, "c")):
            position = _column_index(cell.get("r")) if cell.get("r") else position + 1
            text = _clean_text(_cell_text(cell, shared), MAX_HEADER_TEXT)
            if text:
                cells[position] = text
        if not cells:
            continue
        if len(cells) > MAX_HEADER_COLUMNS:
            raise TemplateInvalid("too_many_columns", str(len(cells)))
        out = []
        for index in sorted(cells):
            width = widths.get(index)
            out.append({"label": cells[index],
                        "width": round(width, 1) if width and 3 <= width <= 100 else None})
        return out
    raise TemplateInvalid("no_header")


# -- the store --------------------------------------------------------------------------


def _empty_state():
    return {"docx": {"active": None, "versions": []}, "xlsx_layout": None, "org_card": None}


def _normal_state(data):
    """The stored state in its expected shape; ValueError when it is not one."""
    if not isinstance(data, dict):
        raise ValueError("not an object")
    docx = data.get("docx") if isinstance(data.get("docx"), dict) else {}
    versions = [v for v in docx.get("versions") or [] if isinstance(v, dict)
                and isinstance(v.get("id"), str) and _VERSION_ID.match(v["id"])]
    active = docx.get("active")
    out = _empty_state()
    out["docx"] = dict(docx, versions=versions,
                       active=active if active in [v["id"] for v in versions] else None)
    for key in ("xlsx_layout", "org_card"):
        entry = data.get(key)
        out[key] = entry if isinstance(entry, dict) and "value" in entry else None
    return out


def _file_name(raw):
    base = re.split(r"[\\/]", str(raw or ""))[-1]
    return _clean_text(base, MAX_FILE_NAME) or "template.docx"


class TemplateStore:
    """The templates of this box under ADAPTER_TEMPLATES_DIR (owner-only):

        state.json        active frame, frame versions, layout, card
        docx/<id>.docx    a stored frame (read_docx_frame passes it again)

    Empty ADAPTER_TEMPLATES_DIR turns the feature off: nothing is stored
    and every file has the built-in look."""

    def __init__(self, root):
        self.root = root or None
        self._lock = threading.Lock()
        self._cache = (None, None)
        self._frames = {}

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        raw = env.get("ADAPTER_TEMPLATES_DIR")
        return cls(DEFAULT_DIR if raw is None else raw.strip())

    @property
    def configured(self):
        return bool(self.root)

    def _state_path(self):
        return os.path.join(self.root, "state.json")

    def _version_path(self, version):
        return os.path.join(self.root, "docx", version + ".docx")

    def state(self):
        """A copy of the stored state; an unreadable file reads as empty
        (the built-in look) with `error` set."""
        if not self.root:
            return _empty_state()
        path = self._state_path()
        try:
            info = os.stat(path)
        except FileNotFoundError:
            return _empty_state()
        except OSError as e:
            return dict(_empty_state(), error="state unreadable: %s" % e)
        key = (info.st_ino, info.st_mtime_ns, info.st_size)
        cached_key, cached = self._cache
        if cached_key != key:
            try:
                with open(path, encoding="utf-8") as handle:
                    cached = _normal_state(json.load(handle))
            except (OSError, ValueError) as e:
                sys.stderr.write("[adapter] templates: %s unreadable (%s) -> built-in look\n"
                                 % (path, e))
                return dict(_empty_state(), error="state unreadable")
            self._cache = (key, cached)
        return json.loads(json.dumps(cached))

    def _write_state(self, state):
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        data = (json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()
        _atomic_write(self._state_path(), data)
        self._cache = (None, None)

    def _require(self):
        if not self.root:
            raise TemplateInvalid("templates_not_configured")

    # -- the DOCX frame ----------------------------------------------------------------
    def add_docx(self, raw, name, user, now=None):
        """Store the frame of an uploaded Word file as a new version (not yet
        active); its metadata, or TemplateInvalid."""
        self._require()
        frame = read_docx_frame(raw)
        stored = frame.package("", frame_core("ViVeSec AI Box template"))
        frame = read_docx_frame(stored)
        version = hashlib.sha256(stored).hexdigest()[:24]
        meta = {"id": version, "name": _file_name(name), "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(), "uploaded_by": user,
                "uploaded_at": int(now if now is not None else time.time()),
                "summary": frame.summary}
        with self._lock:
            state = self.state()
            os.makedirs(os.path.join(self.root, "docx"), mode=0o700, exist_ok=True)
            _atomic_write(self._version_path(version), stored)
            docx = state["docx"]
            versions = [meta] + [v for v in docx["versions"] if v.get("id") != version]
            keep = versions[:MAX_VERSIONS]
            if docx["active"] and docx["active"] not in [v["id"] for v in keep]:
                keep = keep[:-1] + [v for v in versions if v["id"] == docx["active"]]
            for dropped in versions:
                if dropped not in keep and _VERSION_ID.match(dropped.get("id") or ""):
                    try:
                        os.unlink(self._version_path(dropped["id"]))
                    except OSError:
                        pass
                    self._frames.pop(dropped["id"], None)
            docx["versions"] = keep
            self._write_state(state)
        return meta

    def activate_docx(self, version, user, now=None):
        """Make a stored version the frame of every DOCX (None: the built-in
        look). KeyError for an unknown version."""
        self._require()
        with self._lock:
            state = self.state()
            docx = state["docx"]
            if version is not None and version not in [v.get("id") for v in docx["versions"]]:
                raise KeyError(version)
            if version is not None and self.docx_frame(version) is None:
                raise TemplateInvalid("broken", "the stored frame no longer reads")
            docx["active"] = version
            docx["activated_by"] = user
            docx["activated_at"] = int(now if now is not None else time.time())
            self._write_state(state)
        return state

    def docx_frame(self, version=None):
        """The frame of a stored version (default: the active one), or None
        for the built-in look -- also when the stored frame no longer reads."""
        state = self.state()
        version = version or state["docx"]["active"]
        if not version or not _VERSION_ID.match(version) \
                or version not in [v.get("id") for v in state["docx"]["versions"]]:
            return None
        frame = self._frames.get(version)
        if frame is not None:
            return frame
        try:
            with open(self._version_path(version), "rb") as handle:
                frame = read_docx_frame(handle.read(MAX_UPLOAD_BYTES + 1))
        except (OSError, TemplateInvalid) as e:
            sys.stderr.write("[adapter] templates: stored frame %s unusable (%s) -> built-in "
                             "look\n" % (version, e))
            return None
        self._frames[version] = frame
        return frame

    # -- layout and card ---------------------------------------------------------------
    def _set(self, key, value, user, now):
        self._require()
        with self._lock:
            state = self.state()
            state[key] = None if value is None else {
                "value": value, "updated_by": user,
                "updated_at": int(now if now is not None else time.time())}
            self._write_state(state)
        return state

    def set_layout(self, layout, user, now=None):
        """Store a validated spreadsheet layout (None: the default columns)."""
        return self._set("xlsx_layout", layout, user, now)

    def layout(self):
        entry = self.state().get("xlsx_layout")
        return entry.get("value") if isinstance(entry, dict) else None

    def org_card(self):
        """The organisation card of the first step, if one was saved (read
        once by the company profile)."""
        entry = self.state().get("org_card")
        return entry.get("value") if isinstance(entry, dict) else None


def _atomic_write(path, data):
    temp = "%s.%d.tmp" % (path, os.getpid())
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
