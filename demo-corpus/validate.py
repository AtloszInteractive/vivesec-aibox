"""Validate the generated demo corpus: extraction, ground-truth and ACL checks.

Runs the same extractor the rag_service uses (MarkItDown for office/PDF, plain
read for text formats) over every generated file and reports:

  * documents that extract to (almost) no text — must match the declared
    `expect_empty_extraction` flag, i.e. only the deliberate scanned PDF;
  * ground-truth spot checks: key numbers from the bible must appear verbatim
    in the document that is supposed to carry them;
  * ACL bait: the salary band figures must not appear outside the HR drive;
  * near-duplicate and version-chain sanity.

Usage:
    python validate.py [--out out]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEXT_EXTS = {".md", ".txt", ".csv"}


def extract(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXTS:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    from markitdown import MarkItDown
    global _MD
    try:
        md = _MD
    except NameError:
        md = _MD = MarkItDown()
    try:
        return md.convert(path).text_content or ""
    except Exception as exc:  # noqa: BLE001
        return "!!EXTRACTION_ERROR: %s" % exc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    args = ap.parse_args()
    out_root = os.path.abspath(args.out)

    rows = []
    with io.open(os.path.join(out_root, "manifest.jsonl"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))

    texts: dict[str, str] = {}
    problems: list[str] = []
    empty_expected, empty_actual = [], []

    for row in rows:
        path = os.path.join(out_root, *row["local_path"].split("/"))
        text = extract(path)
        texts[row["source_path"]] = text
        if text.startswith("!!EXTRACTION_ERROR"):
            problems.append("EXTRACTION FAILED %s -> %s" % (row["source_path"], text[:160]))
            continue
        if row["metadata"].get("expect_empty_extraction"):
            empty_expected.append(row["source_path"])
        if len(text.strip()) < 40:
            empty_actual.append(row["source_path"])

    for p in empty_actual:
        if p not in empty_expected:
            problems.append("UNEXPECTEDLY EMPTY: %s" % p)
    for p in empty_expected:
        if p not in empty_actual:
            problems.append("EXPECTED EMPTY BUT HAS TEXT: %s" % p)

    # ---------------------------------------------------------- ground truth
    checks = [
        ("/storage/drives/finance/reports/Q2_2026_Management_Accounts.md", ["14.7", "43.1", "1.18", "27.9"]),
        ("/storage/drives/finance/reports/FY2025_Annual_Report_Summary.md", ["48.6", "5.2", "unqualified"]),
        ("/storage/drives/finance/treasury/Danubia_Covenant_Report_2026-06.md", ["3.0", "-0.55", "COMPLIANT"]),
        ("/storage/drives/legal/contracts/ViVeSec_AI_Box_Agreement_2026.pdf", ["148,000", "90 days", "9.1"]),
        ("/storage/drives/legal/contracts/Vestkraft_Supply_Agreement_2025.pdf", ["0.5%", "10%", "24 months"]),
        ("/storage/drives/hr/compensation/Salary_Bands_2026.xlsx", ["Senior engineer", "56400", "CONFIDENTIAL"]),
        ("/storage/drives/hr/policies/Utazasi_szabalyzat_v3_HU.pdf",
         ["32 EUR", "38 EUR", "45 EUR", "előzetes", "felső", "nyelvű"]),
        ("/storage/drives/hr/policies/Reisekostenrichtlinie_v3_DE.pdf",
         ["32 EUR", "38 EUR", "45 EUR", "Bußgelder", "Übernachtung"]),
        ("/storage/drives/hr/policies/Rejsepolitik_v3_DA.pdf",
         ["32 EUR", "38 EUR", "45 EUR", "Bøder", "øvrige"]),
        ("/storage/drives/engineering/projects/Helios_Project_Plan_final.docx",
         ["22 January 2027", "AUTHORITATIVE", "21 August 2026"]),
        ("/storage/drives/engineering/projects/Helios_Project_Plan_v1.docx", ["SUPERSEDED", "30 November 2026"]),
        ("/storage/drives/public/company/Company_Overview_2026.md", ["Voltara", "214", "48.6"]),
        ("/storage/drives/legal/audit/ISO27001_Surveillance_Audit_Report_2026.pdf",
         ["NC-2026-01", "NC-2026-02", "IS 742 118"]),
        ("/storage/drives/finance/budget/Budget_2026_by_function.xlsx", ["6800", "5100", "R&D"]),
    ]
    for path, needles in checks:
        text = texts.get(path)
        if text is None:
            problems.append("MISSING DOCUMENT: %s" % path)
            continue
        flat = " ".join(text.split())
        for needle in needles:
            if needle not in flat:
                problems.append("GROUND TRUTH MISS: %r not in %s" % (needle, path))

    # -------------------------------------------------------------- ACL bait
    leaked = [p for p, t in texts.items()
              if "/drives/hr/" not in p and "56,400" in t.replace(" ", "")]
    for p in leaked:
        problems.append("ACL BAIT LEAK: salary band figure appears in %s" % p)

    # Spreadsheets must not extract to "NaN" cells: the extractors turn an
    # empty cell (and the bare word "None") into NaN, which then shows up in
    # generated answers as a quoted fact.
    for row in rows:
        if row["file_type"] == "xlsx" and "NaN" in texts.get(row["source_path"], ""):
            problems.append("SPREADSHEET RENDERS NaN: %s" % row["source_path"])

    # ----------------------------------------------------- structural checks
    w25 = texts.get("/storage/drives/engineering/status/Weekly_Status_2026-W25.md", "")
    w26 = texts.get("/storage/drives/engineering/status/Weekly_Status_2026-W26.md", "")
    if not (w25 and w26 and w25 != w26):
        problems.append("NEAR-DUPLICATE PAIR W25/W26 missing or identical")

    big = next((r for r in rows if r["local_path"].endswith("GC3000_Firmware_v3_Test_Log_2026-06.txt")), None)
    if not big or big["size"] < 1_048_576:
        problems.append("LARGE FILE injection is below 1 MB (%s bytes)"
                        % (big["size"] if big else "missing"))

    # ------------------------------------------------------------- reporting
    total_chars = sum(len(t) for t in texts.values())
    print("documents checked : %d" % len(rows))
    print("total extracted   : %.2f million characters" % (total_chars / 1e6))
    print("empty extractions : %d (expected %d)" % (len(empty_actual), len(empty_expected)))
    for p in empty_actual:
        print("   empty: %s" % p)
    shortest = sorted(((len(t), p) for p, t in texts.items() if p not in empty_expected))[:5]
    print("shortest documents:")
    for n, p in shortest:
        print("   %6d chars  %s" % (n, p))
    if problems:
        print("\nPROBLEMS (%d):" % len(problems))
        for p in problems:
            print("  - %s" % p)
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
