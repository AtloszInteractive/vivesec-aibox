"""On-box OCR for scanned PDFs and image files (E10b).

Pages without a text layer (a scanned PDF, or the scanned pages of a mixed
one) and image files are read by Tesseract, one page at a time:

  * orientation: Tesseract's OSD says how far a page is turned; a confident
    answer is corrected before reading;
  * quality: the mean word confidence (0-100, weighted by word length); a page
    under RAG_OCR_MIN_QUALITY, or with no words at all, is skipped and listed,
    never indexed as if it were reliable text;
  * language: detected per page from the recognised words (hu/en/de/da), kept
    with the page.

The page image goes to Tesseract on stdin and the text comes back on stdout:
no cleartext page image or text is written to disk. Tesseract runs niced, with
a thread limit, because it shares the CPU with the services.

Configuration (fail-safe: anything unusable means "no OCR", which the index
reports as such instead of pretending the document is empty):
  RAG_OCR              auto (default) | off
  RAG_TESSERACT        path of the tesseract binary (default: from PATH)
  RAG_OCR_LANGS        Tesseract languages tried together, default hun+eng+deu+dan
                       (only the installed ones are used)
  RAG_OCR_DPI          render resolution of PDF pages, default 300
  RAG_OCR_MIN_QUALITY  pages under this mean confidence are skipped, default 40
  RAG_OCR_THREADS      OMP_THREAD_LIMIT for one Tesseract run, default 2
  RAG_OCR_MAX_PAGES    pages read per document at most, default 2000
"""
import importlib.util
import io
import os
import re
import shutil
import subprocess
import threading

import extract

MODE = (os.environ.get("RAG_OCR", "auto") or "auto").strip().lower()
TESSERACT = os.environ.get("RAG_TESSERACT", "") or shutil.which("tesseract") or ""
LANGS = [lang for lang in (os.environ.get("RAG_OCR_LANGS", "hun+eng+deu+dan") or "").split("+")
         if lang.strip()]


def _int_env(name, default, low, high):
    try:
        return min(high, max(low, int(os.environ.get(name, default))))
    except ValueError:
        return default


DPI = _int_env("RAG_OCR_DPI", 300, 72, 600)
MIN_QUALITY = _int_env("RAG_OCR_MIN_QUALITY", 40, 0, 100)
THREADS = str(_int_env("RAG_OCR_THREADS", 2, 1, 16))
MAX_PAGES = _int_env("RAG_OCR_MAX_PAGES", 2000, 1, 100000)
PAGE_TIMEOUT = 300
OSD_MIN_CONFIDENCE = 2.0

IMAGE_EXTS = extract.IMAGE_EXTS

_STOPWORDS = {
    "hu": {"a", "az", "\u00e9s", "hogy", "nem", "is", "egy", "van", "volt", "meg", "de",
           "ha", "mint", "csak", "vagy", "ez", "azt", "kell", "szerint", "\u00e1ltal"},
    "en": {"the", "and", "of", "to", "in", "is", "that", "for", "with", "as", "was", "on",
           "by", "it", "are", "this", "be", "from"},
    "de": {"der", "die", "und", "das", "ist", "nicht", "mit", "den", "von", "zu", "ein",
           "eine", "sich", "des", "im", "auf", "f\u00fcr", "wird"},
    "da": {"og", "det", "er", "at", "til", "p\u00e5", "af", "med", "som", "har", "for",
           "ikke", "der", "et", "vi", "kan", "skal", "fra"},
}
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

_LANGS_CACHE = {}
# PDFium is not thread-safe: the background reader renders while an upload
# counts pages, so every pypdfium2 call holds this lock.
_PDFIUM = threading.Lock()


def _nice():
    try:
        os.nice(10)
    except OSError:
        pass


def _tesseract(image_png, *args):
    """One Tesseract run on a PNG given on stdin; stdout as text."""
    env = dict(os.environ, OMP_THREAD_LIMIT=THREADS)
    result = subprocess.run([TESSERACT, "stdin", "stdout", *args], input=image_png,
                            capture_output=True, timeout=PAGE_TIMEOUT, env=env,
                            preexec_fn=_nice if os.name == "posix" else None)
    return result.stdout.decode("utf-8", "replace")


def installed_languages():
    if "langs" not in _LANGS_CACHE:
        langs = set()
        if TESSERACT:
            try:
                out = subprocess.run([TESSERACT, "--list-langs"], capture_output=True, timeout=30)
                langs = {line.strip() for line in out.stdout.decode("utf-8", "replace").splitlines()[1:]
                         if line.strip()}
            except (OSError, subprocess.SubprocessError):
                langs = set()
        _LANGS_CACHE["langs"] = langs
    return _LANGS_CACHE["langs"]


def languages():
    """The configured languages Tesseract actually has, as its -l argument."""
    have = installed_languages()
    return "+".join(lang for lang in LANGS if lang in have)


def available():
    if MODE == "off" or not TESSERACT:
        return False
    if importlib.util.find_spec("pypdfium2") is None or importlib.util.find_spec("PIL") is None:
        return False
    return bool(languages())


def status():
    """For /health: whether OCR runs and with which languages."""
    on = available()
    return {"enabled": on, "languages": languages() if on else "",
            "min_quality": MIN_QUALITY, "dpi": DPI}


def ext_of(path):
    return os.path.splitext(path or "")[1].lower()


def is_image(path):
    return ext_of(path) in IMAGE_EXTS


# -- which pages need reading -----------------------------------------------------

def page_count(raw, path):
    """Pages of a PDF or frames of an image; None when it cannot be told."""
    try:
        if ext_of(path) == ".pdf":
            import pypdfium2
            with _PDFIUM:
                pdf = pypdfium2.PdfDocument(raw)
                try:
                    return len(pdf)
                finally:
                    pdf.close()
        if is_image(path):
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as image:
                return max(1, getattr(image, "n_frames", 1))
    except Exception:  # noqa: BLE001 -- unreadable file: nothing to OCR
        return None
    return None


def pages_to_read(raw, path, text_pages, page_breaks=True):
    """Page numbers without a text layer: every frame of an image, the PDF pages
    the text extractor returned nothing for. Without page breaks from the text
    extractor (`page_breaks` False) only a PDF with no text at all is read."""
    if not (ext_of(path) == ".pdf" or is_image(path)):
        return []
    have = {number for number, _, _ in text_pages}
    if ext_of(path) == ".pdf" and have and not page_breaks:
        return []
    count = page_count(raw, path)
    if not count:
        return []
    return [n for n in range(1, count + 1) if n not in have][:MAX_PAGES]


# -- reading a page ---------------------------------------------------------------

def _png(image):
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def rotation(image_png):
    """Degrees to turn the page clockwise so it reads upright (0 when unsure)."""
    out = _tesseract(image_png, "--psm", "0", "-l", "osd")
    angle = re.search(r"Rotate:\s*(\d+)", out)
    confidence = re.search(r"Orientation confidence:\s*([\d.]+)", out)
    if not angle or not confidence or float(confidence.group(1)) < OSD_MIN_CONFIDENCE:
        return 0
    return int(angle.group(1)) % 360


def parse_tsv(tsv):
    """(text, quality) from Tesseract TSV: words in reading order, a line break per
    line and a blank line per paragraph; quality = mean word confidence weighted by
    word length, None without words."""
    lines, current, last_line, last_par = [], [], None, None
    total = weight = 0.0
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        word = cols[11].strip()
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        if not word or conf < 0:
            continue
        par, line = (cols[2], cols[3]), (cols[2], cols[3], cols[4])
        if line != last_line and current:
            lines.append(" ".join(current))
            current = []
            if par != last_par:
                lines.append("")
        current.append(word)
        last_line, last_par = line, par
        total += conf * len(word)
        weight += len(word)
    if current:
        lines.append(" ".join(current))
    text = "\n".join(lines).strip()
    return text, (round(total / weight) if weight else None)


def detect_language(text):
    """hu/en/de/da from function words, else from letters only one of them uses
    (a form has few function words); None when it cannot be told."""
    words = [w.casefold() for w in _WORD_RE.findall(text or "")]
    scores = {code: sum(1 for w in words if w in stop) for code, stop in _STOPWORDS.items()}
    best = sorted(scores.items(), key=lambda kv: -kv[1])
    if best and best[0][1] >= 3 and (len(best) < 2 or best[0][1] > best[1][1]):
        return best[0][0]
    letters = (text or "").casefold()
    marks = {"hu": sum(letters.count(c) for c in "\u0151\u0171"),
             "da": sum(letters.count(c) for c in "\u00e6\u00f8\u00e5"),
             "de": sum(letters.count(c) for c in "\u00df\u00e4")}
    if not any(marks.values()):
        accents = sum(letters.count(c) for c in "\u00e1\u00e9\u00ed\u00f3\u00fa")
        return "hu" if accents >= 3 else None
    code, count = max(marks.items(), key=lambda kv: kv[1])
    return code if list(marks.values()).count(count) == 1 else None


def read_image(image):
    """One page image -> {"text", "quality", "lang", "rotation", "status"}; status
    ok, low_quality (under the threshold) or no_text."""
    gray = image.convert("L")
    turn = rotation(_png(gray))
    if turn:
        gray = gray.rotate(-turn, expand=True)
    text, quality = parse_tsv(_tesseract(_png(gray), "-l", languages(), "--psm", "3", "tsv"))
    if not text or quality is None:
        status = "no_text"
    elif quality < MIN_QUALITY:
        status = "low_quality"
    else:
        status = "ok"
    return {"text": text if status == "ok" else "", "quality": quality,
            "lang": detect_language(text) if status == "ok" else None,
            "rotation": turn, "status": status}


def page_images(raw, path, numbers):
    """(page number, PIL image) for the wanted pages, rendered one at a time."""
    wanted = set(numbers)
    if ext_of(path) == ".pdf":
        import pypdfium2
        for number in sorted(wanted):
            with _PDFIUM:
                pdf = pypdfium2.PdfDocument(raw)
                try:
                    if not 1 <= number <= len(pdf):
                        continue
                    page = pdf[number - 1]
                    image = page.render(scale=DPI / 72).to_pil()
                    page.close()
                finally:
                    pdf.close()
            yield number, image
        return
    from PIL import Image, ImageSequence
    with Image.open(io.BytesIO(raw)) as image:
        for number, frame in enumerate(ImageSequence.Iterator(image), 1):
            if number in wanted:
                yield number, frame.copy()


def read_pages(raw, path, numbers, still_wanted=None):
    """{page number: result} for the given pages; stops early when
    `still_wanted()` turns false (the document was replaced or deleted)."""
    out = {}
    for number, image in page_images(raw, path, numbers):
        if still_wanted is not None and not still_wanted():
            break
        try:
            out[number] = read_image(image)
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            out[number] = {"text": "", "quality": None, "lang": None, "rotation": 0,
                           "status": "failed", "error": str(error)[:200]}
    return out
