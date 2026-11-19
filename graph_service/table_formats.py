"""Readers of structured export files (E28): spreadsheets, data dumps, markup.

Every reader returns the file's tables as [(name, rows)], where rows are
[(row number, cells)] and the first row is the header. The caller picks the
table (structured_import.read_table) and turns the rows into records by the
domain profile's mapping, so a new format never changes what is extracted.

  .csv .tsv .txt   delimited text (UTF-8 or Windows-1250), one table
  .xlsx .xlsm      Excel workbook, one table per sheet (openpyxl)
  .xls             legacy Excel workbook, one table per sheet (xlrd)
  .ods             OpenDocument spreadsheet, one table per sheet (stdlib zip + XML)
  .json .jsonl     a list of objects, {"name": [objects], ...}, or one object per line
  .xml             the repeated record element(s): child elements and attributes
                   are the columns; a DOCTYPE (entities) is refused
  .sql             a dump's CREATE TABLE / INSERT INTO ... VALUES statements
                   (MySQL/MariaDB, PostgreSQL --inserts, SQL Server scripts) and
                   PostgreSQL COPY ... FROM stdin blocks; one table per table name

Nothing is executed: an SQL file is only read as text, never run.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from collections import Counter
from xml.etree import ElementTree

from person_key import iso_date

# The most a spreadsheet may unpack to (a zip bomb is not a spreadsheet).
MAX_UNPACKED = 256 * 1024 * 1024
# A cell or row repeated more often than this is the empty tail of a sheet.
MAX_REPEAT = 1000


class FormatError(ValueError):
    pass


def cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if hasattr(value, "year") and hasattr(value, "month"):
        return iso_date(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return " ".join(str(value).split())


def decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1250"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise FormatError("the file is neither UTF-8 nor Windows-1250 text")


def _bounded(rows, max_rows):
    out = []
    for n, cells in rows:
        cells = [cell(c) if not isinstance(c, str) else " ".join(c.split()) for c in cells]
        if any(cells):
            out.append((n, cells))
        if len(out) > max_rows + 1:
            raise FormatError("more than %d rows" % max_rows)
    return out


def _records(name, header, records, max_rows):
    """A table from a header and dict records (JSON, XML, SQL)."""
    rows = [(1, list(header))]
    for n, rec in enumerate(records, 2):
        rows.append((n, [rec.get(h) for h in header]))
        if len(rows) > max_rows + 1:
            raise FormatError("more than %d rows" % max_rows)
    return name, _bounded(rows, max_rows)


# -- delimited text, Excel ------------------------------------------------------------

def delimited(raw: bytes, ext: str, max_rows: int):
    text = decode(raw)
    first = next((line for line in text.splitlines() if line.strip()), "")
    sep = max((";", "\t", ","), key=first.count) if ext != ".tsv" else "\t"
    if first.count(sep) < 1:
        raise FormatError("the header has no ';', ',' or tab separated columns")
    return [("", _bounded(enumerate(csv.reader(io.StringIO(text), delimiter=sep), 1), max_rows))]


def xlsx(raw: bytes, max_rows: int):
    try:
        import openpyxl
    except ImportError:
        raise FormatError("XLSX import needs openpyxl")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001 - any parser failure is a bad file
        raise FormatError("not a readable XLSX file: %s" % type(e).__name__)
    try:
        return [(ws.title, _bounded(enumerate(ws.iter_rows(values_only=True), 1), max_rows))
                for ws in wb.worksheets]
    finally:
        wb.close()


def xls(raw: bytes, max_rows: int):
    try:
        import xlrd
    except ImportError:
        raise FormatError("XLS import needs xlrd")
    try:
        book = xlrd.open_workbook(file_contents=raw, on_demand=True)
    except Exception as e:  # noqa: BLE001
        raise FormatError("not a readable XLS file: %s" % type(e).__name__)
    tables = []
    try:
        for sheet in book.sheets():
            def values(r, sheet=sheet):
                out = []
                for c in sheet.row(r):
                    if c.ctype == xlrd.XL_CELL_DATE:
                        try:
                            out.append(xlrd.xldate_as_datetime(c.value, book.datemode))
                        except Exception:  # noqa: BLE001 - an odd date stays a number
                            out.append(c.value)
                    elif c.ctype == xlrd.XL_CELL_BOOLEAN:
                        out.append(bool(c.value))
                    else:
                        out.append(c.value)
                return out
            if sheet.nrows > max_rows + 1:
                raise FormatError("more than %d rows" % max_rows)
            tables.append((sheet.name, _bounded(((r + 1, values(r)) for r in range(sheet.nrows)),
                                                max_rows)))
    finally:
        book.release_resources()
    return tables


# -- OpenDocument spreadsheet ---------------------------------------------------------

_ODF = {"table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
        "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
        "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0"}


def _q(ns, name):
    return "{%s}%s" % (_ODF[ns], name)


def _ods_value(c) -> str:
    kind = c.get(_q("office", "value-type"))
    if kind in ("float", "percentage", "currency"):
        value = c.get(_q("office", "value"))
        try:
            return cell(float(value))
        except (TypeError, ValueError):
            pass
    if kind == "date":
        return (c.get(_q("office", "date-value")) or "")[:10]
    if kind == "boolean":
        return c.get(_q("office", "boolean-value")) or ""
    return " ".join(" ".join(p.itertext()) for p in c.iter(_q("text", "p")))


def ods(raw: bytes, max_rows: int):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            info = z.getinfo("content.xml")
            if info.file_size > MAX_UNPACKED:
                raise FormatError("the spreadsheet unpacks to more than %d MB"
                                  % (MAX_UNPACKED // 2 ** 20))
            content = z.read(info)
    except (zipfile.BadZipFile, KeyError):
        raise FormatError("not a readable ODS file")
    root = _parse_xml(content)
    tables = []
    for t in root.iter(_q("table", "table")):
        rows, n = [], 0
        for r in t.iter(_q("table", "table-row")):
            repeat = int(r.get(_q("table", "number-rows-repeated")) or 1)
            cells = []
            for c in r:
                if c.tag not in (_q("table", "table-cell"), _q("table", "covered-table-cell")):
                    continue
                times = int(c.get(_q("table", "number-columns-repeated")) or 1)
                value = _ods_value(c)
                cells.extend([value] * (min(times, MAX_REPEAT) if value else min(times, 1)))
            while cells and not cells[-1]:
                cells.pop()
            for _ in range(min(repeat, MAX_REPEAT) if cells else 1):
                n += 1
                if cells:
                    rows.append((n, cells))
                if len(rows) > max_rows + 1:
                    raise FormatError("more than %d rows" % max_rows)
        tables.append((t.get(_q("table", "name")) or "", _bounded(rows, max_rows)))
    return tables


# -- JSON ---------------------------------------------------------------------------------

def _header_of(records):
    header, seen = [], set()
    for rec in records:
        for k in rec:
            if k not in seen:
                seen.add(k)
                header.append(k)
    return header


def json_tables(raw: bytes, ext: str, max_rows: int):
    text = decode(raw)
    try:
        data = json.loads(text)
    except ValueError:
        try:  # JSON Lines: one object per line
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        except ValueError as e:
            raise FormatError("not a readable JSON file: %s" % e)
    lists = {}
    if isinstance(data, list):
        lists[""] = data
    elif isinstance(data, dict):
        lists = {k: v for k, v in data.items()
                 if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)}
        if not lists:
            lists[""] = [data]
    out = []
    for name, records in lists.items():
        records = [r for r in records if isinstance(r, dict)]
        if records:
            out.append(_records(name, _header_of(records), records, max_rows))
    if not out:
        raise FormatError("the JSON holds no list of objects")
    return out


# -- XML --------------------------------------------------------------------------------

def _parse_xml(content: bytes):
    head = content[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in content.lower():
        raise FormatError("an XML file with a DOCTYPE or entities is not accepted")
    try:
        return ElementTree.fromstring(content)
    except ElementTree.ParseError as e:
        raise FormatError("not a readable XML file: %s" % e)


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _xml_record(el) -> dict:
    rec = {_local(k): v for k, v in el.attrib.items()}
    for child in el:
        name = _local(child.tag)
        if not name:
            continue
        value = " ".join(" ".join(child.itertext()).split())
        if not value and child.attrib and not len(child):
            value = "; ".join("%s=%s" % (_local(k), v) for k, v in child.attrib.items())
        rec[name] = "%s; %s" % (rec[name], value) if rec.get(name) and value else \
            (value or rec.get(name, ""))
    return rec


def xml_tables(raw: bytes, max_rows: int):
    root = _parse_xml(raw)
    # A record element: repeated under one parent, with child elements or attributes.
    counts = Counter()
    for parent in root.iter():
        tags = Counter(_local(c.tag) for c in parent if len(c) or c.attrib)
        for tag, n in tags.items():
            if n >= 2 or parent is root:
                counts[tag] += n
    if not counts:
        raise FormatError("the XML has no repeated record elements")
    out = []
    for tag, _ in counts.most_common(10):
        records = [_xml_record(el) for el in root.iter() if _local(el.tag) == tag]
        out.append(_records(tag, _header_of(records), records, max_rows))
    return out


# -- SQL dump --------------------------------------------------------------------------

_SKIP = re.compile(r"\s+|--[^\n]*")
# Words that start a statement on their own line: a SQL Server script ends
# its statements with neither ';' nor always 'GO'.
_STARTERS = frozenset((
    "INSERT", "REPLACE", "CREATE", "SET", "GO", "COPY", "DROP", "ALTER", "BEGIN", "COMMIT",
    "UPDATE", "DELETE", "SELECT", "USE", "DECLARE", "PRINT", "LOCK", "UNLOCK", "IF",
    "TRUNCATE", "GRANT", "START"))
_TOKEN_STD = re.compile(r"""
    (?P<str>[NnEeXxBb]?'(?:[^']|'')*')
  | (?P<qid>"(?:[^"]|"")*"|`(?:[^`]|``)*`|\[[^\]]*\])
  | (?P<num>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)
  | (?P<word>[A-Za-z_@#][A-Za-z0-9_$#@]*)
  | (?P<punct>.)
""", re.S | re.X)
# MySQL dumps escape with a backslash inside strings.
_TOKEN_MYSQL = re.compile(_TOKEN_STD.pattern.replace(r"'(?:[^']|'')*'",
                                                     r"'(?:[^'\\]|\\.|'')*'"), re.S | re.X)
_MYSQL_ESCAPES = {"0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a"}
_CONSTRAINT = {"primary", "key", "unique", "constraint", "index", "foreign", "check",
               "fulltext", "spatial", "exclude", "period"}
# One row of plain literals -- what nearly every dump writes -- matched at once;
# anything else (CAST(...), expressions) is read token by token.
_LIT = (r"(?:[A-Za-z_0-9]*'%s'|0[xX][0-9A-Fa-f]+|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
        r"|NULL\b|TRUE\b|FALSE\b|DEFAULT\b)")
_LIT_STD = _LIT % r"(?:[^']|'')*"
_LIT_MYSQL = _LIT % r"(?:[^'\\]|\\.|'')*"
_ROW = r"\s*\(\s*(%s(?:\s*,\s*%s)*)\s*\)"
_ROW_STD = re.compile(_ROW % (_LIT_STD, _LIT_STD), re.S | re.I)
_ROW_MYSQL = re.compile(_ROW % (_LIT_MYSQL, _LIT_MYSQL), re.S | re.I)
_LITS_STD = re.compile(_LIT_STD, re.S | re.I)
_LITS_MYSQL = re.compile(_LIT_MYSQL, re.S | re.I)
_AFTER_ROW = re.compile(r"\s*([,;]?)")


def _literal(text: str, mysql: bool) -> str:
    if "'" in text:
        return _string(text, mysql)
    if text.upper() in ("NULL", "DEFAULT"):
        return ""
    return text


def _unquote_id(text: str) -> str:
    if text[:1] in '"`' and text[-1:] == text[:1]:
        return text[1:-1].replace(text[0] * 2, text[0])
    if text[:1] == "[" and text[-1:] == "]":
        return text[1:-1]
    return text


def _string(text: str, mysql: bool) -> str:
    body = text[text.index("'") + 1:-1]
    if text[0] in "xXbB":
        return body
    if (mysql or text[0] in "eE") and "\\" in body:
        body = re.sub(r"\\(.)", lambda m: _MYSQL_ESCAPES.get(m.group(1), m.group(1)), body)
    return body.replace("''", "'")


class _Tokens:
    def __init__(self, text: str):
        self.text = text
        self.mysql = "`" in text[:200000] or "ENGINE=" in text[:200000]
        self.rx = _TOKEN_MYSQL if self.mysql else _TOKEN_STD
        self.pos = 0
        self.start = 0

    def next(self):
        """(kind, text) of the next token, or None at the end."""
        while True:
            m = _SKIP.match(self.text, self.pos)
            if m and m.end() > self.pos:
                self.pos = m.end()
                continue
            if self.text.startswith("/*", self.pos):
                # Found with one search: an unclosed comment ends the file
                # instead of being looked for again at every later "/*".
                end = self.text.find("*/", self.pos + 2)
                self.pos = len(self.text) if end < 0 else end + 2
                continue
            if self.text.startswith("#", self.pos) and (self.pos == 0 or
                                                         self.text[self.pos - 1] == "\n"):
                end = self.text.find("\n", self.pos)  # a MySQL line comment
                self.pos = len(self.text) if end < 0 else end
                continue
            break
        if self.pos >= len(self.text):
            return None
        self.start = self.pos
        m = self.rx.match(self.text, self.pos)
        if m.lastgroup == "punct" and m.group() in "'\"`[":
            # An unterminated string or name: everything after it is unreadable,
            # and looking for its end again at every later quote is quadratic.
            raise FormatError("the SQL file has an unterminated string near character %d"
                              % self.pos)
        self.pos = m.end()
        return m.lastgroup, m.group()

    def _line_start(self) -> bool:
        before = self.text.rfind("\n", 0, self.start)
        return not self.text[before + 1:self.start].strip()

    def statement(self, stop_at_values=False):
        """The tokens up to the next ';' (or a SQL Server 'GO' line). With
        `stop_at_values`, an INSERT stops right after its VALUES keyword, so its
        rows can be read straight from the text (sql_tables)."""
        out, depth = [], 0
        while True:
            tok = self.next()
            if tok is None:
                return out or None
            if tok == ("punct", ";"):
                return out
            if tok[0] == "word" and tok[1].upper() == "GO" and self._line_start():
                return out
            if out and depth == 0 and tok[0] == "word" and tok[1].upper() in _STARTERS \
                    and self._line_start():
                self.pos = self.start  # the next statement, without a ';' before it
                return out
            out.append(tok)
            if tok[0] == "punct":
                depth += {"(": 1, ")": -1}.get(tok[1], 0)
            elif stop_at_values and depth == 0 and tok[1].upper() in ("VALUES", "VALUE") \
                    and out[0][1].upper() in ("INSERT", "REPLACE"):
                return out

    def group(self):
        """The comma separated items of the parenthesis that starts here, as
        token lists; None (position unchanged) when no parenthesis starts here."""
        mark = self.pos
        if self.next() != ("punct", "("):
            self.pos = mark
            return None
        items, cur, depth = [], [], 0
        while True:
            tok = self.next()
            if tok is None:
                raise FormatError("an SQL statement ends inside a parenthesis")
            if tok == ("punct", "("):
                depth += 1
            elif tok == ("punct", ")"):
                if depth == 0:
                    items.append(cur)
                    return items
                depth -= 1
            elif tok == ("punct", ",") and depth == 0:
                items.append(cur)
                cur = []
                continue
            cur.append(tok)


def _name(tokens, i):
    """(table name, next index): schema.table -> table, quotes removed."""
    name = ""
    while i < len(tokens) and tokens[i][0] in ("word", "qid"):
        name = _unquote_id(tokens[i][1])
        i += 1
        if i < len(tokens) and tokens[i] == ("punct", "."):
            i += 1
            continue
        break
    return name, i


def _group(tokens, i):
    """The comma separated items of the parenthesis at tokens[i], as token lists."""
    if i >= len(tokens) or tokens[i] != ("punct", "("):
        return None, i
    items, cur, depth = [], [], 0
    i += 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == ("punct", "("):
            depth += 1
        elif tok == ("punct", ")"):
            if depth == 0:
                items.append(cur)
                return items, i + 1
            depth -= 1
        elif tok == ("punct", ",") and depth == 0:
            items.append(cur)
            cur = []
            i += 1
            continue
        cur.append(tok)
        i += 1
    raise FormatError("an SQL statement ends inside a parenthesis")


def _value(item, mysql: bool):
    if not item:
        return ""
    if len(item) == 1:
        kind, text = item[0]
        if kind == "str":
            return _string(text, mysql)
        if kind == "word" and text.upper() in ("NULL", "DEFAULT"):
            return ""
        return text
    if len(item) == 2 and item[0] == ("punct", "-") and item[1][0] == "num":
        return "-" + item[1][1]
    # CAST(N'2020-01-01' AS date), to_date('...', '...'), _utf8mb4'...': the first string.
    for kind, text in item:
        if kind == "str":
            return _string(text, mysql)
    return " ".join(text for _, text in item)


def _copy_block(t: _Tokens, table, columns, tables):
    """PostgreSQL COPY ... FROM stdin: tab separated lines up to '\\.'."""
    rows = tables.setdefault(table, {"columns": columns, "records": []})
    start = t.text.find("\n", t.pos)
    if start < 0:
        return
    end = t.text.find("\n\\.", start)
    block = t.text[start + 1:end if end >= 0 else len(t.text)]
    t.pos = len(t.text) if end < 0 else end + 3
    for line in block.split("\n"):
        if not line:
            continue
        values = ["" if v == "\\N" else re.sub(
            r"\\(.)", lambda m: _MYSQL_ESCAPES.get(m.group(1), m.group(1)), v)
            for v in line.split("\t")]
        rows["records"].append(dict(zip(columns, values)))


def _rows_after_values(t: _Tokens, table, columns, max_rows, count):
    """The rows of an INSERT whose head (up to VALUES) was read; the position
    ends after the statement. Returns the new row count."""
    row_rx, lits_rx = (_ROW_MYSQL, _LITS_MYSQL) if t.mysql else (_ROW_STD, _LITS_STD)
    while True:
        m = row_rx.match(t.text, t.pos)
        if m:
            values = [_literal(v, t.mysql) for v in lits_rx.findall(m.group(1))]
            t.pos = m.end()
        else:
            items = t.group()
            if items is None:
                t.statement()  # INSERT ... SELECT, or a clause we do not read
                return count
            values = [_value(v, t.mysql) for v in items]
        table["records"].append(dict(zip(columns, values)))
        count += 1
        if count > max_rows + 1:
            raise FormatError("more than %d rows" % max_rows)
        sep = _AFTER_ROW.match(t.text, t.pos)
        t.pos = sep.end()
        if sep.group(1) == ",":
            continue
        if sep.group(1) != ";":
            mark = t.pos
            tok = t.next()
            t.pos = mark
            if tok is not None and not (tok[0] == "word" and tok[1].upper() in _STARTERS):
                t.statement()  # ON DUPLICATE KEY UPDATE ..., RETURNING ...
        return count


def sql_tables(raw: bytes, max_rows: int):
    t = _Tokens(decode(raw))
    defined: dict[str, list[str]] = {}
    tables: dict[str, dict] = {}
    count = 0
    while True:
        tokens = t.statement(stop_at_values=True)
        if tokens is None:
            break
        words = [text.upper() for kind, text in tokens[:6] if kind == "word"]
        if words[:1] == ["CREATE"] and "TABLE" in words:
            i = next(k for k, tok in enumerate(tokens) if tok[1].upper() == "TABLE") + 1
            while i < len(tokens) and tokens[i][1].upper() in ("IF", "NOT", "EXISTS"):
                i += 1
            name, i = _name(tokens, i)
            items, _ = _group(tokens, i)
            cols = [_unquote_id(item[0][1]) for item in items or []
                    if item and item[0][0] in ("word", "qid")
                    and item[0][1].lower() not in _CONSTRAINT]
            if name and cols:
                defined[name] = cols
        elif words[:1] in (["INSERT"], ["REPLACE"]):
            if tokens[-1][1].upper() not in ("VALUES", "VALUE"):
                continue  # INSERT ... SELECT (read to its end): nothing to read
            i = 1
            while i < len(tokens) and tokens[i][0] == "word" and tokens[i][1].upper() in (
                    "INTO", "IGNORE", "LOW_PRIORITY", "DELAYED", "HIGH_PRIORITY"):
                i += 1
            name, i = _name(tokens, i)
            columns = None
            if i < len(tokens) and tokens[i] == ("punct", "("):
                items, i = _group(tokens, i)
                columns = [_unquote_id(item[0][1]) for item in items if item]
            columns = columns or defined.get(name)
            if not columns:
                raise FormatError("INSERT into %s names no columns and the file does not "
                                  "define the table" % name)
            table = tables.setdefault(name, {"columns": list(columns), "records": []})
            for c in columns:
                if c not in table["columns"]:
                    table["columns"].append(c)
            count = _rows_after_values(t, table, columns, max_rows, count)
        elif words[:1] == ["COPY"] and any(x[1].upper() == "STDIN" for x in tokens):
            name, i = _name(tokens, 1)
            items, i = _group(tokens, i)
            columns = [_unquote_id(item[0][1]) for item in items or [] if item] or \
                defined.get(name)
            if columns:
                _copy_block(t, name, columns, tables)
    out = [_records(name, data["columns"], data["records"], max_rows)
           for name, data in tables.items() if data["records"]]
    if not out:
        raise FormatError("the SQL file holds no INSERT or COPY data")
    return out


def tables_of(raw: bytes, ext: str, max_rows: int):
    if ext in (".csv", ".tsv", ".txt"):
        return delimited(raw, ext, max_rows)
    if ext in (".xlsx", ".xlsm"):
        return xlsx(raw, max_rows)
    if ext == ".xls":
        return xls(raw, max_rows)
    if ext == ".ods":
        return ods(raw, max_rows)
    if ext in (".json", ".jsonl", ".ndjson"):
        return json_tables(raw, ext, max_rows)
    if ext == ".xml":
        return xml_tables(raw, max_rows)
    if ext == ".sql":
        return sql_tables(raw, max_rows)
    raise FormatError("this file type cannot be imported (CSV, TSV, XLSX, XLS, ODS, JSON, "
                      "XML or SQL)")


EXTENSIONS = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".ods", ".json", ".jsonl",
              ".ndjson", ".xml", ".sql")
