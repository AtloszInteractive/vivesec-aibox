"""Builds the PDF (and DOCX) of the user manual with the ViVeSec Word template.

    python build_pdf.py                       uses reference.docx next to this script
    python build_pdf.py --template X.docx     rebuilds reference.docx from a ViVeSec template

Needs pandoc on PATH and, for the PDF, Microsoft Word (Windows). Output: manual/build/.
"""

import argparse
import datetime
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
MANUAL = TOOLS.parent
REFERENCE = TOOLS / "reference.docx"
FILTER = TOOLS / "print.lua"

TEXTS = {
    "hu": {
        "title": "Matrix AI",
        "subtitle": "Felhasználói útmutató",
        "toc": "Tartalom",
        "about": "Az útmutatóról",
        "footer": "ViVeSec  |  Matrix AI felhasználói útmutató  |  ",
        "file": "Matrix_AI_Felhasznaloi_utmutato",
    },
}

CHAPTER_LINK = re.compile(r"\[([^\]]+)\]\((?:\d\d-[\w-]+|index)\.md(?:#[\w-]*)?\)")
NAV_LINE = re.compile(r"^\[← [^\]]+\]\(index\.md\)\s*$", re.M)
HU_MONTHS = (
    "január február március április május június július augusztus szeptember október "
    "november december"
).split()

BORDER = '<w:%s w:val="single" w:sz="4" w:space="0" w:color="DDD1EE"/>'
# Styles pandoc writes but the ViVeSec template does not define. They build on the
# template's own styles (ids are the Hungarian Word ids of Body Text, Caption, …) and
# copy its table look: light purple grid, shaded first row.
PANDOC_STYLES = (
    '<w:style w:type="paragraph" w:customStyle="1" w:styleId="FirstParagraph">'
    '<w:name w:val="First Paragraph"/><w:basedOn w:val="Szvegtrzs"/>'
    '<w:next w:val="Szvegtrzs"/></w:style>'
    '<w:style w:type="paragraph" w:customStyle="1" w:styleId="Compact">'
    '<w:name w:val="Compact"/><w:basedOn w:val="Szvegtrzs"/>'
    '<w:pPr><w:spacing w:before="40" w:after="40"/></w:pPr></w:style>'
    '<w:style w:type="paragraph" w:customStyle="1" w:styleId="Date">'
    '<w:name w:val="Date"/><w:basedOn w:val="Alcm"/></w:style>'
    '<w:style w:type="paragraph" w:customStyle="1" w:styleId="CaptionedFigure">'
    '<w:name w:val="Captioned Figure"/><w:basedOn w:val="Norml"/>'
    '<w:pPr><w:keepNext/><w:spacing w:before="160" w:after="60"/><w:jc w:val="center"/></w:pPr>'
    "</w:style>"
    '<w:style w:type="paragraph" w:customStyle="1" w:styleId="ImageCaption">'
    '<w:name w:val="Image Caption"/><w:basedOn w:val="Kpalrs"/>'
    '<w:pPr><w:spacing w:after="240"/><w:jc w:val="center"/></w:pPr></w:style>'
    '<w:style w:type="paragraph" w:customStyle="1" w:styleId="BlockText">'
    '<w:name w:val="Block Text"/><w:basedOn w:val="Szvegtrzs"/>'
    '<w:pPr><w:pBdr><w:left w:val="single" w:sz="18" w:space="8" w:color="8C49E9"/></w:pBdr>'
    '<w:shd w:val="clear" w:color="auto" w:fill="F3ECFD"/><w:ind w:left="227" w:right="113"/>'
    "</w:pPr></w:style>"
    '<w:style w:type="character" w:customStyle="1" w:styleId="VerbatimChar">'
    '<w:name w:val="Verbatim Char"/>'
    '<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/><w:sz w:val="19"/></w:rPr>'
    "</w:style>"
    '<w:style w:type="table" w:customStyle="1" w:styleId="Table">'
    '<w:name w:val="Table"/><w:basedOn w:val="Normltblzat"/>'
    "<w:tblPr><w:tblBorders>"
    + "".join(BORDER % side for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    + "</w:tblBorders><w:tblCellMar>"
    '<w:top w:w="60" w:type="dxa"/><w:left w:w="113" w:type="dxa"/>'
    '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="113" w:type="dxa"/>'
    "</w:tblCellMar></w:tblPr>"
    '<w:tblStylePr w:type="firstRow"><w:rPr><w:b/></w:rPr>'
    '<w:tcPr><w:shd w:val="clear" w:color="auto" w:fill="F3ECFD"/></w:tcPr></w:tblStylePr>'
    "</w:style>"
)
PAGE_BREAK = '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def make_reference(template: Path, footer: str) -> None:
    """Keeps the template's styles, page setup, header and footer; drops its body text."""
    with zipfile.ZipFile(template) as src:
        document = src.read("word/document.xml").decode("utf-8")
        sect = re.findall(r"<w:sectPr\b.*?</w:sectPr>", document, re.S)[-1]
        body_start = document.index("<w:body>") + len("<w:body>")
        body_end = document.index("</w:body>")
        document = document[:body_start] + "<w:p/>" + sect + document[body_end:]
        with zipfile.ZipFile(REFERENCE, "w", zipfile.ZIP_DEFLATED) as out:
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename == "word/document.xml":
                    data = document.encode("utf-8")
                elif re.fullmatch(r"word/footer\d*\.xml", item.filename):
                    text = data.decode("utf-8")
                    # The first text run of the footer is the document name.
                    text = re.sub(
                        r"<w:t(?: [^>]*)?>[^<]*</w:t>",
                        lambda _m: '<w:t xml:space="preserve">%s</w:t>' % footer,
                        text,
                        count=1,
                    )
                    data = text.encode("utf-8")
                elif item.filename == "word/styles.xml":
                    text = data.decode("utf-8")
                    text = text.replace("</w:styles>", PANDOC_STYLES + "</w:styles>")
                    data = text.encode("utf-8")
                elif item.filename == "docProps/core.xml":
                    text = data.decode("utf-8")
                    text = re.sub(r"<dc:title>[^<]*</dc:title>", "<dc:title></dc:title>", text)
                    data = text.encode("utf-8")
                out.writestr(item, data)


def assemble(lang: str, texts: dict) -> str:
    """One Markdown document: the intro of index.md, then the chapters in order."""
    folder = MANUAL / lang
    index = (folder / "index.md").read_text(encoding="utf-8")
    # Drop the title line and the chapter table: the PDF has a cover and a TOC.
    intro = index.split("\n", 1)[1]
    intro = re.sub(r"\n## Fejezetek\n.*?(?=\n## )", "\n", intro, flags=re.S)
    intro = re.sub(r"^## ", "### ", intro, flags=re.M)
    parts = ["# %s\n%s" % (texts["about"], intro)]
    for chapter in sorted(folder.glob("[0-9][0-9]-*.md")):
        parts.append(chapter.read_text(encoding="utf-8"))
    text = "\n\n".join(parts)
    text = NAV_LINE.sub("", text)
    return CHAPTER_LINK.sub(r"\1", text)


def cover_page_break(docx: Path) -> None:
    """Puts the table of contents on its own page after the cover."""
    tmp = docx.with_suffix(".tmp")
    with zipfile.ZipFile(docx) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "word/document.xml":
                text = data.decode("utf-8")
                text = text.replace("<w:sdt>", PAGE_BREAK + "<w:sdt>", 1)
                data = text.encode("utf-8")
            out.writestr(item, data)
    tmp.replace(docx)


def word_to_pdf(docx: Path, pdf: Path) -> None:
    script = r"""
$ErrorActionPreference = 'Stop'
$word = New-Object -ComObject Word.Application
$word.Visible = $false
try {
  $doc = $word.Documents.Open('%s')
  foreach ($toc in $doc.TablesOfContents) { $toc.Update() }
  $doc.Fields.Update() | Out-Null
  # 17 = PDF; CreateBookmarks 1 = from headings
  $doc.ExportAsFixedFormat('%s', 17, $false, 0, 0, 0, 0, 0, $true, $true, 1, $true, $true, $false)
  $doc.Close($false)
} finally {
  $word.Quit()
}
""" % (docx, pdf)
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", default="hu", choices=sorted(TEXTS))
    parser.add_argument("--template", type=Path, help="rebuild reference.docx from this template")
    parser.add_argument("--no-pdf", action="store_true", help="only the DOCX")
    args = parser.parse_args()
    texts = TEXTS[args.lang]

    if args.template:
        make_reference(args.template, texts["footer"])
        print("reference:", REFERENCE)
    if not REFERENCE.exists():
        print("reference.docx is missing: run with --template <ViVeSec template>", file=sys.stderr)
        return 1

    build = MANUAL / "build"
    build.mkdir(exist_ok=True)
    docx = build / (texts["file"] + ".docx")
    pdf = build / (texts["file"] + ".pdf")
    today = datetime.date.today()
    date = "%d. %s %d." % (today.year, HU_MONTHS[today.month - 1], today.day)
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "manual.md"
        source.write_text(assemble(args.lang, texts), encoding="utf-8")
        subprocess.run(
            [
                "pandoc",
                str(source),
                "--from=markdown",
                "--to=docx",
                "--reference-doc=%s" % REFERENCE,
                "--resource-path=%s" % (MANUAL / args.lang),
                "--lua-filter=%s" % FILTER,
                "--toc",
                "--toc-depth=2",
                "--metadata=title:%s" % texts["title"],
                "--metadata=subtitle:%s" % texts["subtitle"],
                "--metadata=date:%s" % date,
                "--metadata=toc-title:%s" % texts["toc"],
                "--metadata=lang:%s" % args.lang,
                "--output=%s" % docx,
            ],
            check=True,
        )
    cover_page_break(docx)
    print("docx:", docx)
    if not args.no_pdf:
        word_to_pdf(docx, pdf)
        print("pdf:", pdf)
    return 0


if __name__ == "__main__":
    shutil.which("pandoc") or sys.exit("pandoc is not on PATH")
    sys.exit(main())
